"""Run one OSC queue refresh and print only non-secret diagnostic data."""

from __future__ import annotations

import json
import sys
from pathlib import Path


SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from harp_server.queue_catalog import LiveQueueCatalog  # noqa: E402


def main() -> int:
    catalog = LiveQueueCatalog.from_env()
    catalog.refresh()
    clusters = {}
    ok = True

    for cluster in catalog.hosts:
        snapshot = catalog.snapshot(cluster)
        error = catalog.error(cluster)
        if snapshot is None:
            ok = False
            clusters[cluster] = {"live": False, "error": error}
            continue

        clusters[cluster] = {
            "live": True,
            "checked_at": snapshot["checked_at"],
            "host": snapshot["host"],
            "partition_count": len(snapshot["partitions"]),
            "partitions": snapshot["partitions"],
            "latest_error": error,
        }

    print(json.dumps({"ok": ok, "clusters": clusters}, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

