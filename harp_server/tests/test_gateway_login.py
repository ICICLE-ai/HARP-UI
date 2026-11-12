import sys
import time
import types

import pytest

from harp_server.tapis_gateway import TapisError, TapisGateway


class FakeTapis:
    """Mimics tapipy.tapis.Tapis(base_url, access_token=...)."""
    valid = {"good-token": "alice"}
    passwords = {"alice": "pw"}
    last = None

    def __init__(self, base_url, access_token=None, username=None, password=None):
        FakeTapis.last = self
        self.base_url = base_url
        self.username, self.password = username, password
        self.refresh_token = None
        self.access_token = types.SimpleNamespace(claims={"exp": time.time() + 3600}) if access_token else None
        user = self.valid.get(access_token)

        def get_userinfo():
            if user is None:
                raise RuntimeError("401 invalid JWT")
            return types.SimpleNamespace(username=user)
        self.authenticator = types.SimpleNamespace(get_userinfo=get_userinfo)

    def get_tokens(self):
        if self.passwords.get(self.username) != self.password:
            raise RuntimeError(f"Invalid username/password combination for {self.username}:{self.password}")
        self.access_token = types.SimpleNamespace(claims={"exp": time.time() + 4 * 3600})


@pytest.fixture(autouse=True)
def fake_tapipy(monkeypatch):
    mod = types.ModuleType("tapipy.tapis")
    mod.Tapis = FakeTapis
    monkeypatch.setitem(sys.modules, "tapipy", types.ModuleType("tapipy"))
    monkeypatch.setitem(sys.modules, "tapipy.tapis", mod)


def test_login_takes_username_from_tapis_not_from_the_client():
    gw = TapisGateway.login("https://icicle.tapis.io", access_token="good-token")
    assert gw.username == "alice"
    assert not gw.expired()
    assert gw.expires_at > time.time()


def test_forged_token_is_rejected():
    with pytest.raises(TapisError, match="login failed"):
        TapisGateway.login("https://icicle.tapis.io", access_token="forged")


def test_password_login_gets_token_and_forgets_password():
    gw = TapisGateway.login("https://icicle.tapis.io", username="alice", password="pw")
    assert gw.username == "alice"
    assert FakeTapis.last.password is None
    assert gw.expires_at > time.time() + 3 * 3600


def test_wrong_password_error_does_not_echo_the_password():
    with pytest.raises(TapisError) as e:
        TapisGateway.login("https://icicle.tapis.io", username="alice", password="hunter2")
    assert "hunter2" not in str(e.value) and "***" in str(e.value)


def test_refresh_token_extends_the_session():
    gw = TapisGateway.login("https://icicle.tapis.io", username="alice", password="pw")
    FakeTapis.last.refresh_token = types.SimpleNamespace(claims={"exp": time.time() + 24 * 3600})
    assert gw.expires_at > time.time() + 23 * 3600


def test_expired():
    gw = TapisGateway(None, "a", "u", expires_at=time.time() + 30)
    assert gw.expired()  # inside the 60s safety margin
    assert not TapisGateway(None, "a", "u").expired()


def test_list_apps_flattens_tapis_results():
    gw = TapisGateway.login("https://icicle.tapis.io", username="alice", password="pw")
    app = types.SimpleNamespace(
        id="harp-sweep-yolo", version="1.0.0", containerImage="docker://x", runtime="SINGULARITY",
        description="YOLO", notes=types.SimpleNamespace(harp=types.SimpleNamespace(command="python3 t.py {m}")),
        jobAttributes=types.SimpleNamespace(parameterSet=types.SimpleNamespace(
            appArgs=[types.SimpleNamespace(name="spec", arg="x")])))
    gw.client.apps = types.SimpleNamespace(getApps=lambda **kw: [app])
    [out] = gw.list_apps()
    assert out["notes"] == {"harp": {"command": "python3 t.py {m}"}}
    assert out["app_args"] == [{"name": "spec", "arg": "x"}]


def test_queue_limits_treat_unset_and_unlimited_as_no_limit():
    from types import SimpleNamespace as NS
    from harp_server.tapis_gateway import TapisGateway
    q = NS(name="batch", hpcQueueName="batch", description=None, maxJobs=0, maxJobsPerUser=2147483647,
           minNodeCount=0, maxNodeCount=0, minCoresPerNode=1, maxCoresPerNode=96, minMemoryMB=0,
           maxMemoryMB=2147483647, minMinutes=0, maxMinutes=10080)
    sysdef = NS(id="ascend", host="ascend.osc.edu", description="", systemType="LINUX", enabled=True, canExec=True,
                hasCredentials=True, effectiveUserId="alice",
                canRunBatch=True, batchScheduler="SLURM", jobRuntimes=[], batchDefaultLogicalQueue="batch",
                batchLogicalQueues=[q], jobWorkingDir="/fs/scratch/PAS2271", rootDir="/")
    gw = TapisGateway.__new__(TapisGateway)
    gw._call = lambda fn, **kw: sysdef
    gw.client = NS(systems=NS(getSystem=None))
    [out] = gw.get_system("ascend")["queues"]
    assert (out["max_jobs"], out["max_jobs_per_user"], out["max_nodes"], out["max_memory_mb"]) == (None, None, None, None)
    assert out["configured_max_nodes"] == 0
    assert (out["max_cores"], out["max_minutes"]) == (96, 10080)


def test_system_inventory_uses_one_detailed_tapis_request():
    from types import SimpleNamespace as NS
    calls = []
    system = NS(id="ascend", host="ascend.osc.edu", description="", systemType="LINUX", enabled=True,
                canExec=True, canRunBatch=True, hasCredentials=False, effectiveUserId="alice",
                batchScheduler="SLURM", jobRuntimes=[], batchDefaultLogicalQueue=None,
                batchLogicalQueues=[], jobWorkingDir="/tmp", rootDir="/")
    gw = TapisGateway.__new__(TapisGateway)
    gw._call = lambda fn, **kw: calls.append(kw) or [system]
    gw.client = NS(systems=NS(getSystems=None))
    [out] = gw.exec_systems()
    assert len(calls) == 1 and calls[0]["select"] == "allAttributes"
    assert out["access"]["status"] == "unknown"


def test_system_access_uses_read_only_root_listing_until_explicit_refresh():
    from types import SimpleNamespace as NS
    calls = []
    gw = TapisGateway.__new__(TapisGateway)
    gw._lock = __import__("threading").Lock()
    gw._system_access_cache = {}
    gw.client = NS(files=NS(listFiles=lambda **kw: calls.append(kw) or []))
    first = gw.check_system_access("ascend")
    second = gw.check_system_access("ascend")
    refreshed = gw.check_system_access("ascend", refresh=True)
    assert first["accessible"] is True and second == first and refreshed["accessible"] is True
    assert calls == [{"systemId": "ascend", "path": "/", "limit": 1}] * 2


def test_system_access_reports_remote_failure_without_claiming_why():
    from types import SimpleNamespace as NS
    def fail(**kw):
        raise RuntimeError("401 invalid remote credentials")
    gw = TapisGateway.__new__(TapisGateway)
    gw._lock = __import__("threading").Lock()
    gw._system_access_cache = {}
    gw.client = NS(files=NS(listFiles=fail))
    result = gw.check_system_access("ascend")
    assert result["accessible"] is False
    assert result["status"] == "unavailable"
    assert "401" in result["error"]


def test_failed_job_list_entries_are_hydrated_with_full_tapis_message():
    from types import SimpleNamespace as NS
    summary = NS(uuid="job-1", status="FAILED", condition="JOB_LAUNCH_FAILURE")
    full = NS(uuid="job-1", status="FAILED", condition="JOB_LAUNCH_FAILURE",
              lastMessage="JOBS_SUBMIT_ERROR2: Required partition not available (inactive or drain)",
              remoteResultInfo=None, remoteOutcome="FAILED")
    client = NS(jobs=NS(getJobList=lambda **kw: [summary], getJob=lambda **kw: full))
    gw = TapisGateway(client, "alice", "https://fake.tapis.io")
    detail = gw.list_job_details({"job-1"})["job-1"]
    assert detail["condition"] == "JOB_LAUNCH_FAILURE"
    assert "Required partition not available" in detail["message"]
