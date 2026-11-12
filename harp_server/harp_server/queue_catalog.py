"""Live OSC Slurm partition discovery through a non-interactive SSH probe."""

import json
import os
import subprocess
import threading
import time
from datetime import datetime, timezone


OSC_HOSTS = {
    "ascend": "ascend.osc.edu",
    "pitzer": "pitzer.osc.edu",
    "cardinal": "cardinal.osc.edu",
}

CACHE_FILENAME = "osc_queue_catalog.json"


def parse_sinfo(output):
    """Parse `sinfo --all -h -o %P|%a|%t` into partition availability records."""
    partitions = {}
    for raw in str(output or "").splitlines():
        parts = [value.strip() for value in raw.split("|", 2)]
        if len(parts) != 3 or not parts[0]:
            continue
        name, availability, node_state = parts
        name = name.rstrip("*")
        record = partitions.setdefault(name, {"available": False, "scheduler_states": [], "node_states": []})
        availability = availability.lower()
        node_state = node_state.lower()
        record["available"] = record["available"] or availability == "up"
        if availability and availability not in record["scheduler_states"]:
            record["scheduler_states"].append(availability)
        if node_state and node_state not in record["node_states"]:
            record["node_states"].append(node_state)
    return partitions


class LiveQueueCatalog:
    """Refresh live OSC Slurm partitions without retaining interactive credentials.

    The SSH identity and known_hosts file are mounted into the Pod. The service
    only runs `sinfo`; it never reads TAPIS access tokens or accepts passwords.
    """

    def __init__(self, enabled=False, username=None, key_path=None, known_hosts=None,
                 refresh_seconds=86400, max_age_seconds=172800, timeout_seconds=15,
                 hosts=None, runner=None, cache_path=None):
        self.enabled = bool(enabled)
        self.username = (username or "").strip()
        self.key_path = key_path
        self.known_hosts = known_hosts
        self.cache_path = cache_path
        self.refresh_seconds = max(30, int(refresh_seconds))
        self.max_age_seconds = max(self.refresh_seconds, int(max_age_seconds))
        self.timeout_seconds = max(3, int(timeout_seconds))
        self.hosts = dict(hosts or OSC_HOSTS)
        self.runner = runner or subprocess.run
        self._snapshots = {}
        self._errors = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._cache_error = None
        if self.enabled:
            self._load_cache()

    @classmethod
    def from_env(cls, data_dir=None):
        enabled = os.environ.get("HARP_OSC_QUEUE_PROBE", "").strip().lower() in {"1", "true", "yes", "on"}
        cache_path = os.environ.get("HARP_QUEUE_CACHE_FILE", "").strip()
        if not cache_path:
            state_dir = data_dir or os.environ.get("HARP_SERVER_DATA") or os.path.expanduser("~/.harp_server")
            cache_path = os.path.join(state_dir, CACHE_FILENAME)
        return cls(
            enabled=enabled,
            username=os.environ.get("HARP_OSC_SSH_USER"),
            key_path=os.environ.get("HARP_OSC_SSH_KEY"),
            known_hosts=os.environ.get("HARP_OSC_SSH_KNOWN_HOSTS"),
            refresh_seconds=os.environ.get("HARP_QUEUE_REFRESH_SECONDS", "86400"),
            max_age_seconds=os.environ.get("HARP_QUEUE_MAX_AGE_SECONDS", "172800"),
            timeout_seconds=os.environ.get("HARP_QUEUE_PROBE_TIMEOUT_SECONDS", "15"),
            cache_path=cache_path,
        )

    def _load_cache(self):
        """Load a still-current, credential-free snapshot from persistent state."""
        if not self.cache_path or not os.path.isfile(self.cache_path):
            return
        try:
            with open(self.cache_path, encoding="utf-8") as stream:
                payload = json.load(stream)
            saved = payload.get("snapshots", {})
            now = time.time()
            snapshots = {}
            for cluster, snapshot in saved.items():
                if cluster not in self.hosts or not isinstance(snapshot, dict):
                    continue
                if snapshot.get("host") != self.hosts[cluster] or not isinstance(snapshot.get("partitions"), dict):
                    continue
                checked_epoch = float(snapshot.get("checked_epoch") or 0)
                if checked_epoch <= 0 or now - checked_epoch > self.max_age_seconds:
                    continue
                snapshots[cluster] = snapshot
            with self._lock:
                self._snapshots.update(snapshots)
            self._cache_error = None
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._cache_error = f"could not load queue snapshot: {exc}"

    def _save_cache(self):
        """Atomically persist live partition data without any SSH credentials."""
        if not self.cache_path:
            return
        with self._lock:
            snapshots = {
                cluster: {**snapshot, "partitions": {
                    name: {**record} for name, record in snapshot["partitions"].items()
                }}
                for cluster, snapshot in self._snapshots.items()
            }
        payload = {
            "version": 1,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "snapshots": snapshots,
        }
        cache_dir = os.path.dirname(os.path.abspath(self.cache_path))
        temp_path = f"{self.cache_path}.tmp-{os.getpid()}-{threading.get_ident()}"
        try:
            os.makedirs(cache_dir, exist_ok=True)
            with open(temp_path, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, indent=2, sort_keys=True)
                stream.write("\n")
            os.replace(temp_path, self.cache_path)
            self._cache_error = None
        except OSError as exc:
            self._cache_error = f"could not save queue snapshot: {exc}"
            try:
                os.unlink(temp_path)
            except OSError:
                pass

    def _configuration_error(self):
        if not self.enabled:
            return "live OSC partition probe is disabled"
        if not self.username:
            return "HARP_OSC_SSH_USER is not set"
        if not self.key_path or not os.path.isfile(self.key_path):
            return "HARP_OSC_SSH_KEY does not name a mounted key file"
        if not self.known_hosts or not os.path.isfile(self.known_hosts):
            return "HARP_OSC_SSH_KNOWN_HOSTS does not name a mounted known_hosts file"
        return None

    def _argv(self, host):
        return [
            "ssh", "-o", "BatchMode=yes", "-o", "PasswordAuthentication=no",
            "-o", "StrictHostKeyChecking=yes", "-o", f"ConnectTimeout={self.timeout_seconds}",
            "-o", f"UserKnownHostsFile={self.known_hosts}", "-i", self.key_path,
            f"{self.username}@{host}",
            "LC_ALL=C sinfo --all --noheader --format='%P|%a|%t'",
        ]

    def _probe(self, cluster, host):
        result = self.runner(self._argv(host), capture_output=True, text=True,
                             timeout=self.timeout_seconds, check=False)
        if result.returncode:
            detail = (result.stderr or result.stdout or f"ssh exited {result.returncode}").strip().splitlines()[-1]
            raise RuntimeError(detail[:300])
        partitions = parse_sinfo(result.stdout)
        if not partitions:
            raise RuntimeError("sinfo returned no partitions")
        now = time.time()
        return {"cluster": cluster, "host": host, "source": "live_slurm",
                "checked_at": datetime.now(timezone.utc).isoformat(), "checked_epoch": now,
                "partitions": partitions}

    def refresh(self):
        error = self._configuration_error()
        if error:
            with self._lock:
                self._errors = {cluster: error for cluster in self.hosts}
            return
        updated = False
        for cluster, host in self.hosts.items():
            try:
                snapshot = self._probe(cluster, host)
            except Exception as exc:
                with self._lock:
                    self._errors[cluster] = str(exc)
            else:
                with self._lock:
                    self._snapshots[cluster] = snapshot
                    self._errors.pop(cluster, None)
                updated = True
        if updated:
            self._save_cache()

    def snapshot(self, cluster):
        with self._lock:
            snapshot = self._snapshots.get(cluster)
            if not snapshot or time.time() - snapshot["checked_epoch"] > self.max_age_seconds:
                return None
            return {**snapshot, "partitions": {name: {**value}
                                                for name, value in snapshot["partitions"].items()}}

    def error(self, cluster):
        with self._lock:
            return self._errors.get(cluster) or self._configuration_error()

    def status(self):
        return {cluster: {"live": self.snapshot(cluster) is not None, "error": self.error(cluster)}
                for cluster in self.hosts}

    def _run(self):
        while not self._stop.is_set():
            self.refresh()
            self._stop.wait(self.refresh_seconds)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        if not self.enabled:
            self.refresh()
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="harp-osc-queue-probe", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=min(2, self.timeout_seconds))

