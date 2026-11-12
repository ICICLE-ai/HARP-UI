import json
import subprocess

from harp_server.queue_catalog import LiveQueueCatalog, parse_sinfo


def test_parse_sinfo_aggregates_partition_rows_and_removes_default_marker():
    parsed = parse_sinfo("nextgen*|up|idle\nnextgen*|up|alloc\nquad|down|down\n")
    assert parsed["nextgen"] == {"available": True, "scheduler_states": ["up"],
                                  "node_states": ["idle", "alloc"]}
    assert parsed["quad"]["available"] is False


def test_live_catalog_uses_strict_noninteractive_ssh_and_caches_result(tmp_path):
    key = tmp_path / "id_ed25519"
    known = tmp_path / "known_hosts"
    key.write_text("test key")
    known.write_text("ascend.osc.edu test-host-key")
    calls = []

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "nextgen*|up|idle\nquad|down|down\n", "")

    catalog = LiveQueueCatalog(enabled=True, username="alice", key_path=str(key),
                               known_hosts=str(known), hosts={"ascend": "ascend.osc.edu"}, runner=runner)
    catalog.refresh()
    snapshot = catalog.snapshot("ascend")
    assert snapshot["partitions"]["nextgen"]["available"] is True
    assert snapshot["partitions"]["quad"]["available"] is False
    argv, kwargs = calls[0]
    assert "BatchMode=yes" in argv and "PasswordAuthentication=no" in argv
    assert "StrictHostKeyChecking=yes" in argv and "sinfo --all" in argv[-1]
    assert kwargs["timeout"] == 15 and kwargs["check"] is False


def test_disabled_catalog_reports_unavailable_without_starting_a_thread():
    catalog = LiveQueueCatalog(enabled=False, hosts={"ascend": "ascend.osc.edu"})
    catalog.start()
    assert catalog.snapshot("ascend") is None
    assert "disabled" in catalog.error("ascend")
    assert catalog._thread is None


def test_environment_defaults_refresh_daily_and_keep_snapshot_for_two_days(monkeypatch):
    monkeypatch.delenv("HARP_QUEUE_REFRESH_SECONDS", raising=False)
    monkeypatch.delenv("HARP_QUEUE_MAX_AGE_SECONDS", raising=False)
    catalog = LiveQueueCatalog.from_env()
    assert catalog.refresh_seconds == 86400
    assert catalog.max_age_seconds == 172800


def test_live_snapshot_survives_restart_without_storing_credentials(tmp_path):
    key = tmp_path / "id_ed25519"
    known = tmp_path / "known_hosts"
    cache = tmp_path / "state" / "osc_queue_catalog.json"
    key.write_text("private material")
    known.write_text("ascend.osc.edu test-host-key")

    def runner(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, "nextgen*|up|idle\n", "")

    first = LiveQueueCatalog(enabled=True, username="alice", key_path=str(key),
                             known_hosts=str(known), hosts={"ascend": "ascend.osc.edu"},
                             runner=runner, cache_path=str(cache))
    first.refresh()
    assert cache.is_file()

    persisted = cache.read_text()
    assert "nextgen" in persisted
    assert "private material" not in persisted
    assert "alice" not in persisted

    restarted = LiveQueueCatalog(enabled=True, username="alice", key_path=str(key),
                                 known_hosts=str(known), hosts={"ascend": "ascend.osc.edu"},
                                 runner=runner, cache_path=str(cache))
    assert restarted.snapshot("ascend")["partitions"]["nextgen"]["available"] is True
    assert json.loads(persisted)["version"] == 1


def test_environment_cache_defaults_to_persistent_server_data(monkeypatch, tmp_path):
    monkeypatch.setenv("HARP_SERVER_DATA", str(tmp_path))
    monkeypatch.delenv("HARP_QUEUE_CACHE_FILE", raising=False)
    catalog = LiveQueueCatalog.from_env()
    assert catalog.cache_path == str(tmp_path / "osc_queue_catalog.json")

