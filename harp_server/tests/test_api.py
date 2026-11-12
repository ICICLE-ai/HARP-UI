import json
import pytest
from fastapi.testclient import TestClient

from harp_server.app import _decorate_queues, create_app
from harp_server.tapis_gateway import TapisError, login_error_message
from fake_tapis import FakeGateway
from specs import euler_spec


class Ctx(tuple):
    """(client, manager, gateways), plus .app for building extra clients."""


@pytest.fixture
def ctx(tmp_path):
    gateways = {}

    def login(base_url, username=None, password=None, access_token=None):
        if access_token:  # fake tokens look like "token-<username>"
            if not access_token.startswith("token-"):
                raise TapisError("bad token")
            username = access_token[len("token-"):]
        elif password != "secret":
            raise TapisError("bad credentials")
        return gateways.setdefault(username, FakeGateway(username))

    app = create_app(data_dir=str(tmp_path), login=login, start_poller=False)
    client = TestClient(app, base_url="https://testserver")
    c = Ctx((client, app.state.manager, gateways))
    c.app = app
    return c


def _create(ctx, spec, user="alice"):
    """Start a campaign straight from a spec (the page goes through /api/plan/submit)."""
    _, manager, gateways = ctx
    return manager.create(spec, gateways[user])


def _login(client, user="alice"):
    r = client.post("/api/login", json={"base_url": "https://fake.tapis.io", "access_token": f"token-{user}"})
    assert r.status_code == 200, r.text


def test_requires_login(ctx):
    client, _, _ = ctx
    assert client.get("/api/campaigns").status_code == 401
    r = client.post("/api/login", json={"base_url": "https://fake.tapis.io", "access_token": "forged"})
    assert r.status_code == 401
    assert client.post("/api/login", json={"base_url": "https://fake.tapis.io", "access_token": "token-a"}).status_code == 200
    assert client.post("/api/login", json={"base_url": "http://insecure", "access_token": "token-a"}).status_code == 400


def test_nested_tapipy_login_errors_are_concise():
    nested = ("TAPIS login failed: message: Unable to retrieve sites and tenants from the Tenants API. "
              "e: message: Invalid Tapis token.")
    assert login_error_message(nested) == (
        "Invalid or expired TAPIS access token. Get a new token and try again.")
    assert login_error_message("Not enough segments") == (
        "Invalid or expired TAPIS access token. Get a new token and try again.")
    assert login_error_message("Invalid or expired TAPIS access token. Get a new token and try again.") == (
        "Invalid or expired TAPIS access token. Get a new token and try again.")
    assert login_error_message("Unable to retrieve sites and tenants from the Tenants API") == (
        "Could not reach the TAPIS Tenants service. Check the tenant URL and try again.")


def test_login_with_access_token_sets_a_secure_session_cookie_only(ctx):
    client, _, _ = ctx
    r = client.post("/api/login", json={"base_url": "https://fake.tapis.io", "access_token": "token-alice"})
    assert r.status_code == 200
    assert set(r.json()) == {"username", "base_url", "expires_at"}  # token stays server-side
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=strict" in cookie
    bad = client.post("/api/login", json={"base_url": "https://fake.tapis.io", "access_token": "forged"})
    assert bad.status_code == 401
    assert client.post("/api/login", json={"base_url": "https://fake.tapis.io"}).status_code == 400
    credentials = {"base_url": "https://fake.tapis.io", "username": "alice", "password": "secret"}
    assert client.post("/api/login", json=credentials).status_code == 400


def test_tokens_are_refused_over_plain_http_from_other_hosts(ctx):
    creds = {"base_url": "https://fake.tapis.io", "access_token": "token-alice"}
    remote = TestClient(ctx.app, base_url="http://harp.example.org", client=("203.0.113.7", 50000))
    r = remote.post("/api/login", json=creds)
    assert r.status_code == 403 and "https" in r.json()["detail"]
    local = TestClient(ctx.app, base_url="http://localhost", client=("127.0.0.1", 50000))
    r = local.post("/api/login", json=creds)
    assert r.status_code == 200
    assert "secure" not in r.headers["set-cookie"].lower()  # dev on localhost


def test_security_headers(ctx):
    client, _, _ = ctx
    r = client.get("/")
    assert r.headers["strict-transport-security"].startswith("max-age=")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"
    assert client.get("/api/campaigns").headers["cache-control"] == "no-store"


def test_health_and_public_portal_config_do_not_require_login(ctx):
    client, _, _ = ctx
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/config").json() == {
        "tapis_base_url": "https://icicleai.tapis.io",
        "portal_origins": [],
        "portal_auth": False,
        "portal_auth_protocol": 1,
    }


def test_portal_mode_allows_only_configured_frame_ancestors_and_uses_iframe_cookie(tmp_path):
    def login(base_url, username=None, password=None, access_token=None):
        if access_token != "token-alice-token-alice-token-alice":
            raise TapisError("bad token")
        return FakeGateway("alice")

    app = create_app(data_dir=str(tmp_path), login=login, start_poller=False,
                     portal_origins=["https://icicleai.tapis.io", "http://localhost:3000/"],
                     tapis_base_url="https://icicleai.tapis.io/", session_same_site="none")
    client = TestClient(app, base_url="https://harp.pods.icicleai.tapis.io")
    page = client.get("/")
    assert "frame-ancestors 'self' https://icicleai.tapis.io http://localhost:3000" in \
        page.headers["content-security-policy"]
    assert "x-frame-options" not in page.headers
    assert client.get("/api/config").json()["portal_auth"] is True
    response = client.post("/api/login", json={"base_url": "https://icicleai.tapis.io",
                                                "access_token": "token-alice-token-alice-token-alice"})
    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert "secure" in cookie and "httponly" in cookie and "samesite=none" in cookie


def test_portal_origin_rejects_paths_and_wildcards(tmp_path):
    with pytest.raises(ValueError, match="portal origin"):
        create_app(data_dir=str(tmp_path), start_poller=False,
                   portal_origins=["https://icicleai.tapis.io/path"])
    with pytest.raises(ValueError, match="portal origin"):
        create_app(data_dir=str(tmp_path), start_poller=False, portal_origins=["*"])


def test_expired_token_logs_out_and_pauses_campaigns(ctx):
    client, manager, gateways = ctx
    _login(client)
    c = _create(ctx, euler_spec())
    gateways["alice"].expires_at = 1  # long ago
    r = client.get("/api/campaigns")
    assert r.status_code == 401 and "expired" in r.json()["detail"]
    manager.tick()
    assert manager.campaigns[c["id"]]["status"] == "WAITING_FOR_LOGIN"
    assert gateways["alice"].submitted == []

    gateways["alice"].expires_at = None  # a fresh token
    _login(client)
    manager.tick()
    assert manager.campaigns[c["id"]]["status"] == "RUNNING"
    assert gateways["alice"].submitted


def test_users_only_see_their_own_campaigns(ctx):
    client, _, _ = ctx
    _login(client, "alice")
    c = _create(ctx, euler_spec())
    _login(client, "bob")
    assert client.get("/api/campaigns").json() == [] and client.get("/api/jobs").json() == []
    assert client.post(f"/api/campaigns/{c['id']}/cancel").status_code == 404


def test_profiling_page_is_served_with_its_own_csp(ctx):
    client, _, _ = ctx
    r = client.get("/profiling.html")
    assert r.status_code == 200 and "Profiling" in r.text
    assert r.headers["cache-control"] == "no-store"
    csp = r.headers["content-security-policy"]
    assert "fonts.googleapis.com" in csp and "'unsafe-inline'" in csp
    assert 'name="access_token"' in r.text
    assert 'name="username"' not in r.text and 'name="password"' not in r.text
    assert 'id="sysShowUnavailable"' in r.text and 'id="reviewShowUnavailable"' in r.text
    assert 'id="eShowUnavailable"' in r.text
    assert "queue-status" in r.text and "label: 'CHECKING'" in r.text
    assert "function renderBuildDestinations()" in r.text
    assert "accessTimer = setTimeout(refreshSystemAccess, 750)" in r.text
    assert "invalid_tapis_configuration" in r.text and "partition_unavailable" in r.text
    assert "Execution systems and logical queues" in r.text
    assert "Scheduler options" in r.text and 'class="queue-tag default"' in r.text
    assert "Nodes per job" in r.text and "Cores Requested" in r.text and "Memory Requested" in r.text
    assert "Time Limit Requested" in r.text
    assert "nodeCount=1" in r.text and "coresPerNode" in r.text and "memoryMB" in r.text and "maxMinutes" in r.text
    assert "function syncQueuesFromUi()" in r.text and "el.checked && !el.disabled" in r.text
    assert "function syncSweepFromUi()" in r.text and "SW.params[p.name].raw = input.value" in r.text
    assert "queueSweepValidationRefresh" in r.text
    assert "addEventListener('change', queueSweepValidationRefresh)" in r.text
    assert "const cfgFingerprint" in r.text and "cfgFingerprint(q, c, sw)" in r.text
    assert "cfgText(q, c, sw)" not in r.text
    assert "const systems = SYSTEMS.filter(s => showAll || (!systemUnavailable(s) && activeQlist(s).length));" in r.text
    assert "const startupCampaigns = loadCampaigns(0)" in r.text
    assert "const jobsReady = loadJobs(startupCampaigns)" in r.text
    assert "const appsReady = loadApps(startupCampaigns)" in r.text
    assert "const systemsReady = loadSystems()" in r.text
    assert "if (jobsRequest) return jobsRequest" in r.text
    assert 'id="eRun"' in r.text and "api('/api/estimates'" in r.text
    assert 'id="bRunSystem"' in r.text and 'id="bRunQueue"' in r.text
    assert 'id="bShowUnavailable"' in r.text
    assert "function renderBuildPartitions(preferred = null)" in r.text
    assert 'id="bRun"' not in r.text
    assert client.get("/").text == r.text   # the page is the whole UI
    assert "unsafe-inline" not in client.get("/api/me").headers["content-security-policy"].split("script-src")[1].split(";")[0]


def _finish_all(manager, gw, csv_for):
    manager.tick()
    for _ in range(20):
        for u in gw.submitted:
            if gw.jobs[u]["status"] != "FINISHED":
                gw.finish(u, csv_for(u))
        manager.tick()
        if all(j["uuid"] for c in manager.campaigns.values() for j in c["jobs"]) and \
           all(c["status"] not in ("RUNNING", "FINALIZING") for c in manager.campaigns.values()):
            break


def test_app_profile_merges_all_campaigns_of_an_app(ctx):
    client, manager, gateways = ctx
    _login(client)
    _create(ctx, euler_spec(name="sweep-a"))
    _create(ctx, euler_spec(name="sweep-b"))
    gw = gateways["alice"]
    _finish_all(manager, gw, lambda u: b"run_type,run_n,walltime\nSD,10,0.5\n")

    r = client.get("/api/apps/harp-sweep-euler/profile").json()
    assert len(r["campaigns"]) == 2 and all(c["status"] == "DONE" for c in r["campaigns"])
    assert r["columns"] == ["campaign", "system", "hardware", "sys_alloc_cores", "sys_alloc_mem_mb", "run_type", "run_n", "walltime"]
    assert r["total_rows"] == 8 and {row["campaign"] for row in r["rows"]} == {"sweep-a", "sweep-b"}

    csv_text = client.get("/api/apps/harp-sweep-euler/profile.csv").text.splitlines()
    assert csv_text[0] == "campaign,system,hardware,sys_alloc_cores,sys_alloc_mem_mb,run_type,run_n,walltime" and len(csv_text) == 9

    assert client.get("/api/apps/other-app/profile").json()["total_rows"] == 0
    assert client.get("/api/apps/other-app/profile.csv").status_code == 404


def test_rows_are_tagged_with_the_system_that_ran_them(ctx):
    client, manager, gateways = ctx
    _login(client)
    _create(ctx, euler_spec(name="hw"))
    gw = gateways["alice"]
    def csv_for(u):
        job = gw.jobs[u]["request"]["name"]
        return f"run_config,run_type,walltime\n{job}.run-0.iteration-0,SD,1.0\n".encode()
    _finish_all(manager, gw, csv_for)
    rows = client.get("/api/apps/harp-sweep-euler/profile").json()["rows"]
    assert {r["system"] for r in rows} == {"pitzer", "stampede"}
    view = client.get("/api/campaigns").json()[0]
    assert [h["system_id"] for h in view["hardware"]] == ["pitzer", "stampede"]


def test_two_configurations_of_one_queue_are_told_apart(ctx):
    client, manager, gateways = ctx
    _login(client)
    spec = euler_spec(name="cfg")
    base = dict(spec["targets"][0], queue="serial", memory_mb=8192)
    spec["targets"] = [dict(base, cores_per_node=2), dict(base, cores_per_node=40, memory_mb=163840)]
    _create(ctx, spec)
    gw = gateways["alice"]
    def csv_for(u):
        job = gw.jobs[u]["request"]["name"]
        return f"run_config,run_type,walltime\n{job}.run-0.iteration-0,SD,1.0\n".encode()
    _finish_all(manager, gw, csv_for)
    assert sorted({j["request"]["coresPerNode"] for j in gw.jobs.values()}) == [2, 40]
    rows = client.get("/api/apps/harp-sweep-euler/profile").json()["rows"]
    assert {r["hardware"] for r in rows} == {"pitzer/serial · 2 cores · 8 GB", "pitzer/serial · 40 cores · 160 GB"}
    jobs = client.get("/api/jobs").json()
    assert {j["cores_per_node"] for j in jobs} == {2, 40}


def test_app_profile_shows_finished_jobs_while_sweep_runs(ctx):
    client, manager, gateways = ctx
    _login(client)
    spec = euler_spec(name="live")
    for t in spec["targets"]:
        t["max_concurrent_jobs"] = 10
    c = _create(ctx, spec)
    manager.tick()
    gw = gateways["alice"]
    gw.finish(gw.submitted[0], b"run_type,walltime\nSD,0.5\nSD,0.6\n")
    manager.tick()
    assert manager.campaigns[c["id"]]["status"] == "RUNNING"
    r = client.get("/api/apps/harp-sweep-euler/profile").json()
    assert r["total_rows"] == 2 and r["campaigns"][0]["status"] == "RUNNING"


def test_exec_systems_come_with_their_queues(ctx):
    client, _, _ = ctx
    _login(client)
    systems = client.get("/api/tapis/exec-systems").json()
    assert [s["id"] for s in systems] == ["pitzer", "stampede"]
    assert systems[0]["queues"][0]["name"] == "serial" and systems[0]["queues"][0]["default"]


def test_inventory_returns_execution_and_storage_systems_with_access_state(ctx):
    client, _, _ = ctx
    _login(client)
    inventory = client.get("/api/tapis/inventory").json()
    assert {s["id"] for s in inventory["systems"]} == {"pitzer", "stampede", "storage"}
    assert [s["id"] for s in inventory["exec_systems"]] == ["pitzer", "stampede"]
    assert all(s["access"]["status"] == "unknown" for s in inventory["systems"])


@pytest.mark.parametrize(("system_id", "cluster", "valid", "invalid"), [
    ("ascend-tapis", "Ascend", "nextgen", "batch"),
    ("pitzer-tapis", "Pitzer", "cpu-exp", "serial"),
    ("cardinal-tapis", "Cardinal", "cache", "parallel"),
])
def test_harp_only_queue_overlay_uses_each_osc_partition_catalog(system_id, cluster, valid, invalid):
    class LiveCatalog:
        def snapshot(self, key):
            return {"checked_at": "2026-11-04T12:00:00+00:00",
                    "partitions": {valid: {"available": True}, "down": {"available": False}}}

        def error(self, key):
            return None

    system = {"id": system_id, "queues": [
        {"name": "profile", "hpc_queue": invalid, "configured_max_nodes": 4},
        {"name": "documented", "hpc_queue": valid, "configured_max_nodes": 16},
        {"name": "disabled", "hpc_queue": valid, "configured_max_nodes": 0},
        {"name": "offline", "hpc_queue": "down", "configured_max_nodes": 4},
    ]}
    _decorate_queues(system, LiveCatalog())
    invalid_queue, valid_queue, disabled_queue, offline_queue = system["queues"]
    assert invalid_queue["void_reason"] == (
        f"This queue maps to HPC queue {invalid!r}, which is not in the live {cluster} Slurm partition list.")
    assert invalid_queue["availability_code"] == "invalid_tapis_configuration"
    assert valid_queue["void_reason"] is None
    assert valid_queue["availability_code"] == "available"
    assert disabled_queue["void_reason"] == "This queue has been configured incorrectly on TAPIS."
    assert offline_queue["availability_code"] == "partition_unavailable" and offline_queue["void_reason"]
    assert system["queue_reference"]["label"] == f"Live {cluster} partitions"
    assert valid in system["queue_reference"]["partitions"]
    assert invalid not in system["queue_reference"]["partitions"]


def test_osc_queue_is_unverified_not_voided_without_live_probe():
    system = {"id": "ascend-tapis", "queues": [
        {"name": "legacy", "hpc_queue": "not-in-the-docs", "configured_max_nodes": 4},
    ]}
    _decorate_queues(system)
    queue = system["queues"][0]
    assert queue["availability_code"] == "unverified" and queue["void_reason"] is None
    assert system["queue_reference"]["source"] == "documentation"


def test_queue_without_live_probe_is_unverified_by_default():
    system = {"id": "other-tapis", "queues": [
        {"name": "normal", "hpc_queue": "normal", "configured_max_nodes": 4},
    ]}
    _decorate_queues(system)
    queue = system["queues"][0]
    assert queue["availability_code"] == "unverified"
    assert queue["availability_source"] == "tapis" and queue["void_reason"] is None


def test_system_access_endpoint_performs_file_listing_probe(ctx):
    client, _, gateways = ctx
    _login(client)
    result = client.get("/api/tapis/systems/pitzer/access").json()
    client.get("/api/tapis/systems/pitzer/access")
    assert result["system_id"] == "pitzer" and result["accessible"] is True
    assert gateways["alice"].access_checks[-2:] == [("pitzer", True), ("pitzer", True)]


def test_all_jobs_lists_every_job_with_app_and_queue(ctx):
    client, manager, _ = ctx
    _login(client)
    _create(ctx, euler_spec(name="a")); _create(ctx, euler_spec(name="b"))
    jobs = client.get("/api/jobs").json()
    assert len(jobs) == 8 and {j["sweep"] for j in jobs} == {"a", "b"}
    j = next(j for j in jobs if j["system_id"] == "pitzer")
    assert j["app_id"] == "harp-sweep-euler" and j["queue"] == "serial" and j["status"] == "NOT_SUBMITTED"
    assert "no TAPIS job exists yet" in j["status_explanation"]


def test_failed_job_includes_classified_tapis_error(ctx):
    client, manager, gateways = ctx
    _login(client)
    _create(ctx, euler_spec(name="failure"))
    manager.tick()
    gw = gateways["alice"]
    uuid = gw.submitted[0]
    gw.jobs[uuid].update(status="FAILED", condition="JOB_LAUNCH_FAILURE",
                         message="Batch job submission failed: Required partition not available (inactive or drain)")
    job = next(j for j in client.get("/api/jobs").json() if j["uuid"] == uuid)
    assert job["failure"]["code"] == "queue_unavailable"
    assert job["failure"]["raw"].startswith("Batch job submission failed")


def test_finished_job_hides_persisted_status_check_error(ctx):
    client, manager, gateways = ctx
    _login(client)
    _create(ctx, euler_spec(name="recovered"))
    manager.tick()
    gw = gateways["alice"]
    local_job = next(j for c in manager.campaigns.values() for j in c["jobs"] if j.get("uuid"))
    local_job.update(status="FINISHED", error="status check failed: connection aborted")
    gw.jobs[local_job["uuid"]]["status"] = "FINISHED"
    job = next(j for j in client.get("/api/jobs").json() if j["uuid"] == local_job["uuid"])
    assert job["status"] == "FINISHED" and job["error"] is None and job["failure"] is None


def test_jobs_show_runs_done_while_running_and_after(ctx):
    client, manager, gateways = ctx
    _login(client)
    spec = euler_spec(name="prog")
    spec["targets"] = spec["targets"][:1]
    _create(ctx, spec)
    gw = gateways["alice"]
    cid = list(manager.campaigns)[0]
    manager.tick()
    uuid = gw.submitted[0]
    gw.jobs[uuid]["status"] = "RUNNING"
    # nothing written yet: no progress, no error
    manager.tick()
    job = next(j for j in client.get("/api/jobs").json() if j["uuid"] == uuid)
    assert job["progress"] is None and job["runs_total"] == job["combinations"] * spec["repetitions"]
    # the runner writes its progress file in the output folder on the execution system
    gw.files[("pitzer", f"/exec/{uuid}/harp_progress.json")] = json.dumps(
        {"runs_done": 2, "runs_failed": 1, "runs_total": job["runs_total"], "current": "x.run-0.iteration-2"}).encode()
    manager.tick()
    job = next(j for j in client.get("/api/jobs").json() if j["uuid"] == uuid)
    assert (job["progress"]["runs_done"], job["progress"]["runs_failed"]) == (2, 1)
    # once archived, the job summary gives the final count
    req = gw.jobs[uuid]["request"]
    gw.files[(req["archiveSystemId"], req["archiveSystemDir"] + "/harp_job_summary.json")] = json.dumps(
        {"succeeded_runs": job["runs_total"], "failed_runs": 0}).encode()
    gw.finish(uuid, b"run_config,run_type,walltime\n")
    manager.tick()
    j = next(x for x in client.get("/api/jobs").json() if x["uuid"] == uuid)
    assert j["progress"]["runs_done"] == j["runs_total"] and j["progress"]["runs_failed"] == 0
