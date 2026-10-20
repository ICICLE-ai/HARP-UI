"""Register the HARP template and launch or migrate its ICICLE Tapis Pod.

The default behavior is additive and never deletes existing resources. Passing
``--replace-existing-pod`` explicitly permits replacement of the Pod when its
immutable template reference differs. The persistent volume is never deleted.
Credentials are read only from environment variables.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from tapipy.errors import BaseTapyException
from tapipy.tapis import Tapis


DEPLOY_DIR = Path(__file__).resolve().parent
DEFAULT_BASE_URL = "https://icicleai.tapis.io"
DEFAULT_IMAGE = "ghcr.io/icicle-ai/harp-ui:2.0.0"
DEFAULT_TEMPLATE_TAG = "2.0.0-icicle.1"
TEMPLATE_TAG_DEFINITION = "template-tag-harp-2.0.0-icicle.1.json"


def load_json(filename: str) -> dict[str, Any]:
    with (DEPLOY_DIR / filename).open(encoding="utf-8") as stream:
        return json.load(stream)


def tapis_client(base_url: str) -> Tapis:
    token = os.environ.get("TAPIS_ACCESS_TOKEN")
    if token:
        return Tapis(base_url=base_url, access_token=token)

    username = os.environ.get("TAPIS_USERNAME")
    password = os.environ.get("TAPIS_PASSWORD")
    if not username or not password:
        raise SystemExit(
            "Set TAPIS_ACCESS_TOKEN, or both TAPIS_USERNAME and TAPIS_PASSWORD."
        )

    client = Tapis(
        base_url=base_url,
        username=username,
        password=password,
        account_type=os.environ.get("TAPIS_ACCOUNT_TYPE", "user"),
    )
    client.get_tokens()
    return client


def is_not_found(error: BaseTapyException) -> bool:
    response = getattr(error, "response", None)
    return response is not None and getattr(response, "status_code", None) == 404


def get_or_none(operation: Callable[..., Any], **kwargs: Any) -> Any | None:
    try:
        return operation(**kwargs)
    except BaseTapyException as error:
        if is_not_found(error):
            return None
        raise


def value(resource: Any, name: str, default: Any = None) -> Any:
    if isinstance(resource, dict):
        return resource.get(name, default)
    return getattr(resource, name, default)


def wait_for_status(
    client: Tapis, pod_id: str, expected_status: str, timeout: int
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pod = client.pods.get_pod(pod_id=pod_id)
        status = value(pod, "status", "UNKNOWN")
        if status == expected_status:
            return
        if status == "ERROR":
            raise SystemExit(f"Pod {pod_id} entered ERROR state")
        time.sleep(5)
    raise SystemExit(
        f"Pod {pod_id} did not reach {expected_status} within {timeout} seconds"
    )


def wait_for_absent(client: Tapis, pod_id: str, timeout: int) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if get_or_none(client.pods.get_pod, pod_id=pod_id) is None:
            return
        time.sleep(5)
    raise SystemExit(f"Pod {pod_id} was not deleted within {timeout} seconds")


def create_resources(
    client: Tapis,
    image: str,
    pod_id: str,
    template_tag: str,
    replace_existing_pod: bool,
    timeout: int,
) -> str:
    volume = get_or_none(client.pods.get_volume, volume_id="harpdata")
    if volume is None:
        client.pods.create_volume(
            volume_id="harpdata",
            description="Persistent campaign and build state for HARP",
            size_limit=1024,
        )
        print("Created volume harpdata")
    else:
        print("Using existing volume harpdata")

    template_definition = load_json("template-harp.json")
    template_id = template_definition["template_id"]
    template = get_or_none(client.pods.get_template, template_id=template_id)
    if template is None:
        client.pods.add_template(**template_definition)
        print(f"Created template {template_id}")
    else:
        print(f"Using existing template {template_id}")

    tag_definition = load_json(TEMPLATE_TAG_DEFINITION)
    tag_definition["tag"] = template_tag
    tag_definition["pod_definition"]["image"] = image
    tag_definition["commit_message"] = (
        f"HARP {template_tag} using the ICICLE-owned image"
    )
    tag = template_tag
    existing_tag = get_or_none(
        client.pods.get_template_tag,
        template_id=template_id,
        tag_id=tag,
    )
    if isinstance(existing_tag, list):
        existing_tag = existing_tag[0] if existing_tag else None
    if existing_tag is None:
        client.pods.add_template_tag(**tag_definition)
        print(f"Created immutable template tag {template_id}:{tag}")
    else:
        existing_definition = value(existing_tag, "pod_definition", {})
        existing_image = value(existing_definition, "image")
        if existing_image and existing_image != image:
            raise SystemExit(
                f"Template tag {template_id}:{tag} already uses {existing_image}. "
                "Template tags are immutable; choose a new version tag for a new image."
            )
        print(f"Using existing template tag {template_id}:{tag}")

    target_template = f"{template_id}:{tag}"
    pod_definition = load_json("pod-harp-from-template.json")
    pod_definition["pod_id"] = pod_id
    pod_definition["template"] = target_template

    pod = get_or_none(client.pods.get_pod, pod_id=pod_id)
    if pod is None:
        pod = client.pods.create_pod(**pod_definition)
        print(f"Created Pod {pod_id} from {target_template}")
    else:
        current_template = str(value(pod, "template", "")).split("@", 1)[0]
        if current_template == target_template:
            print(f"Using existing Pod {pod_id} from {target_template}")
        elif not replace_existing_pod:
            raise SystemExit(
                f"Pod {pod_id} currently uses {current_template or 'a direct image'}, "
                f"not {target_template}. The Pods update API cannot change image or "
                "template. Re-run with --replace-existing-pod to stop, delete, and "
                "recreate only the Pod; the persistent volume will be preserved."
            )
        else:
            mounts = value(pod, "volume_mounts", {})
            data_mount = value(mounts, "/var/lib/harp", {})
            if value(data_mount, "source_id") != "harpdata":
                raise SystemExit(
                    f"Refusing to replace Pod {pod_id}: /var/lib/harp is not backed "
                    "by the expected harpdata volume."
                )

            status = value(pod, "status", "UNKNOWN")
            if status != "STOPPED":
                print(f"Stopping Pod {pod_id} before template migration")
                client.pods.stop_pod(pod_id=pod_id)
                wait_for_status(client, pod_id, "STOPPED", timeout)

            print(
                f"Deleting Pod {pod_id}; persistent volume harpdata is not deleted"
            )
            client.pods.delete_pod(pod_id=pod_id)
            wait_for_absent(client, pod_id, timeout)
            pod = client.pods.create_pod(**pod_definition)
            print(f"Recreated Pod {pod_id} from {target_template}")

    hostname = urlparse(client.base_url).hostname
    return f"https://{pod_id}.pods.{hostname}"


def wait_for_pod(client: Tapis, pod_id: str, endpoint: str, timeout: int) -> None:
    deadline = time.monotonic() + timeout
    last_status = None
    while time.monotonic() < deadline:
        pod = client.pods.get_pod(pod_id=pod_id)
        status = value(pod, "status", "UNKNOWN")
        if status != last_status:
            print(f"Pod status: {status}")
            last_status = status
        if status == "ERROR":
            raise SystemExit(f"Pod {pod_id} entered ERROR state")
        if status == "AVAILABLE":
            break
        time.sleep(10)
    else:
        raise SystemExit(f"Pod did not become AVAILABLE within {timeout} seconds")

    health_url = f"{endpoint}/healthz"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(health_url, timeout=10) as response:
                if response.status == 200:
                    print(f"Healthy endpoint: {endpoint}")
                    return
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(5)
    raise SystemExit(f"Pod is AVAILABLE, but {health_url} did not become healthy")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.environ.get("TAPIS_BASE_URL", DEFAULT_BASE_URL),
        help="Tapis tenant base URL",
    )
    parser.add_argument(
        "--image",
        default=os.environ.get("HARP_IMAGE", DEFAULT_IMAGE),
        help="Public container image used by the immutable template tag",
    )
    parser.add_argument(
        "--template-tag",
        default=os.environ.get("HARP_TEMPLATE_TAG", DEFAULT_TEMPLATE_TAG),
        help="Immutable HARP template tag to create or use",
    )
    parser.add_argument("--pod-id", default="harp")
    parser.add_argument(
        "--replace-existing-pod",
        action="store_true",
        help=(
            "Stop, delete, and recreate an existing Pod when it uses another "
            "template. The harpdata persistent volume is preserved."
        ),
    )
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="Return immediately after resource creation",
    )
    parser.add_argument("--timeout", type=int, default=600)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    client = tapis_client(args.base_url.rstrip("/"))
    endpoint = create_resources(
        client,
        args.image,
        args.pod_id,
        args.template_tag,
        args.replace_existing_pod,
        args.timeout,
    )
    if not args.no_wait:
        wait_for_pod(client, args.pod_id, endpoint, args.timeout)
    else:
        print(f"Expected endpoint: {endpoint}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
