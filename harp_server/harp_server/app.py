"""
HARP sweep server: the REST API behind the profiling page (profiling.html),
running the generate phase as many TAPIS jobs across several systems.

Run with HTTPS:  python3 -m harp_server.serve   (see README for certificates)
"""

import csv
import io
import os
import secrets
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import Body, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse

from .campaign import (ACTIVE_CAMPAIGN_STATUSES, PROFILE_FILE_NAME, CampaignManager, CampaignStore,
                       campaign_view, is_status_check_error, owner_of)
from .build import MODELS, TRAINING_SETS, BuildManager, build_view, standardize, to_csv
from .job_errors import classify_job_failure
from .plan import build_plan, harp_app, is_gpu, resolve_target
from .queue_catalog import LiveQueueCatalog
from .sweep import SpecError
from .tapis_gateway import TapisError, TapisGateway, login_error_message

# The profiling page lives at the repository root.
PROFILING_PAGE = os.environ.get(
    "HARP_PROFILING_PAGE",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "profiling.html"))
MAX_PROFILE_ROWS = 5000
# That page is a single file with inline script/style and Google Fonts.
SESSION_COOKIE = "harp_session"
LOCAL_CLIENTS = {"127.0.0.1", "::1", "localhost"}
# Manually transcribed from OSC's current "Partition time and job size limits"
# tables. HARP links to these references; it does not modify TAPIS definitions.
OSC_PARTITION_CATALOGS = {
    "ascend": {
        "name": "Ascend",
        "partitions": {"nextgen", "quad", "debug-nextgen", "debug-quad"},
        "url": "https://www.osc.edu/book/export/html/6459",
    },
    "pitzer": {
        "name": "Pitzer",
        "partitions": {"cpu", "cpu-exp", "longcpu", "gpu", "gpu-exp", "gpu-quad",
                       "debug-cpu", "debug-exp", "gpudebug", "gpudebug-exp", "hugemem", "largemem"},
        "url": "https://www.osc.edu/resources/technical_support/supercomputers/pitzer/batch_limit_rules",
    },
    "cardinal": {
        "name": "Cardinal",
        "partitions": {"cpu", "longcpu", "cache", "gpu", "debug", "hugemem"},
        "url": "https://www.osc.edu/resources/technical_support/supercomputers/cardinal/batch_limit_rules",
    },
}


def _osc_catalog_entry(system):
    """Identify an official OSC TAPIS system by ID or OSC execution host."""
    system_id = str(system.get("id") or "").lower()
    host = str(system.get("host") or "").lower().rstrip(".")
    for key, catalog in OSC_PARTITION_CATALOGS.items():
        if system_id == f"{key}-tapis" or host == f"{key}.osc.edu":
            return key, catalog
    return None


def _osc_partition_catalog(system):
    entry = _osc_catalog_entry(system)
    return entry[1] if entry else None


def _queue_availability(system, queue, live_catalog=None):
    """Classify a TAPIS logical queue using live Slurm data when available."""
    tapis_queue = (queue.get("name") or "default queue").strip()
    partition = (queue.get("hpc_queue") or tapis_queue).strip()
    if queue.get("configured_max_nodes") == 0:
        return {"code": "invalid_tapis_configuration", "source": "tapis",
                "detail": "This queue has been configured incorrectly on TAPIS."}
    entry = _osc_catalog_entry(system)
    if not entry:
        return {"code": "unverified", "source": "tapis",
                "detail": ("Tapis defines this logical queue, but HARP has no live scheduler "
                           "check confirming that its HPC queue is available.")}
    cluster, catalog = entry
    snapshot = live_catalog.snapshot(cluster) if live_catalog else None
    if not snapshot:
        error = live_catalog.error(cluster) if live_catalog else "live probe is not configured"
        return {"code": "unverified", "source": "documentation",
                "detail": f"Live {catalog['name']} partition check unavailable: {error}."}
    live_partition = snapshot["partitions"].get(partition)
    if live_partition is None:
        return {"code": "invalid_tapis_configuration", "source": "live_slurm",
                "checked_at": snapshot["checked_at"],
                "detail": (f"This queue maps to HPC queue {partition!r}, which is not in the live "
                           f"{catalog['name']} Slurm partition list.")}
    if not live_partition["available"]:
        return {"code": "partition_unavailable", "source": "live_slurm",
                "checked_at": snapshot["checked_at"],
                "detail": f"HPC partition {partition!r} is currently unavailable on {catalog['name']}."}
    return {"code": "available", "source": "live_slurm", "checked_at": snapshot["checked_at"],
            "detail": f"HPC partition {partition!r} is available on {catalog['name']}."}


def _queue_void_reason(system, queue, live_catalog=None):
    """A HARP-only queue overlay. It never changes the TAPIS System."""
    availability = _queue_availability(system, queue, live_catalog)
    return availability["detail"] if availability["code"] in {
        "invalid_tapis_configuration", "partition_unavailable"} else None


def _decorate_queues(system, live_catalog=None):
    for queue in system.get("queues") or []:
        queue["gpu"] = is_gpu(queue)
        availability = _queue_availability(system, queue, live_catalog)
        queue["availability_code"] = availability["code"]
        queue["availability_detail"] = availability["detail"]
        queue["availability_source"] = availability["source"]
        queue["void_reason"] = availability["detail"] if availability["code"] in {
            "invalid_tapis_configuration", "partition_unavailable"} else None
    entry = _osc_catalog_entry(system)
    if entry:
        cluster, catalog = entry
        snapshot = live_catalog.snapshot(cluster) if live_catalog else None
        if snapshot:
            system["queue_reference"] = {"label": f"Live {catalog['name']} partitions",
                                         "url": catalog["url"], "source": "live_slurm",
                                         "checked_at": snapshot["checked_at"],
                                         "partitions": sorted(snapshot["partitions"])}
        else:
            system["queue_reference"] = {
                "label": f"{catalog['name']} documentation (not used for validation)",
                "url": catalog["url"], "source": "documentation", "partitions": []}
    return system


def _origins(value):
    """Normalize an exact HTTP(S) origin allowlist for CSP and portal auth."""
    values = value if isinstance(value, (list, tuple)) else str(value or "").split(",")
    out = []
    for raw in values:
        raw = str(raw).strip().rstrip("/")
        if not raw:
            continue
        parsed = urlsplit(raw)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or
                parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password):
            raise ValueError(f"invalid HARP portal origin {raw!r}; use an exact https://host[:port] origin")
        origin = f"{parsed.scheme}://{parsed.hostname}"
        if parsed.port:
            origin += f":{parsed.port}"
        if origin not in out:
            out.append(origin)
    return out


def _same_site(value):
    value = str(value or "strict").strip().lower()
    if value not in {"strict", "lax", "none"}:
        raise ValueError("HARP_SESSION_SAME_SITE must be strict, lax or none")
    return value


def is_https(request: Request) -> bool:
    # Behind a reverse proxy, run uvicorn with --proxy-headers so the
    # X-Forwarded-Proto header sets the scheme.
    return request.url.scheme == "https"


def is_local(request: Request) -> bool:
    return bool(request.client) and request.client.host in LOCAL_CLIENTS


def create_app(data_dir=None, poll_interval=None, login=TapisGateway.login, start_poller=True, build_python=None,
               portal_origins=None, tapis_base_url=None, session_same_site=None, queue_catalog=None):
    data_dir = data_dir or os.environ.get("HARP_SERVER_DATA", os.path.expanduser("~/.harp_server"))
    poll_interval = poll_interval or int(os.environ.get("HARP_POLL_SECONDS", "15"))
    portal_origins = _origins(os.environ.get("HARP_PORTAL_ORIGINS", "")
                              if portal_origins is None else portal_origins)
    tapis_base_url = (tapis_base_url or os.environ.get("HARP_TAPIS_BASE_URL") or
                      "https://icicleai.tapis.io").strip().rstrip("/")
    if not tapis_base_url.startswith("https://"):
        raise ValueError("HARP_TAPIS_BASE_URL must be an https URL")
    session_same_site = _same_site(os.environ.get("HARP_SESSION_SAME_SITE", "strict")
                                   if session_same_site is None else session_same_site)
    frame_ancestors = "'self' " + " ".join(portal_origins) if portal_origins else "'none'"
    response_security_headers = {
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                                    f"img-src 'self' data:; connect-src 'self'; frame-ancestors {frame_ancestors}; "
                                    "form-action 'self'; base-uri 'none'"),
    }
    if not portal_origins:
        response_security_headers["X-Frame-Options"] = "DENY"
    profiling_csp = ("default-src 'self'; script-src 'self' 'unsafe-inline'; "
                     "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                     "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; "
                     f"frame-ancestors {frame_ancestors}; form-action 'self'; base-uri 'none'")
    manager = CampaignManager(CampaignStore(data_dir), poll_interval=poll_interval)
    builds = BuildManager(data_dir, python=build_python)
    queue_catalog = queue_catalog or LiveQueueCatalog.from_env(data_dir=data_dir)
    sessions = {}  # session token -> TapisGateway

    @asynccontextmanager
    async def lifespan(_app):
        queue_catalog.start()
        if start_poller:
            manager.start()
        try:
            yield
        finally:
            manager.stop()
            queue_catalog.stop()

    app = FastAPI(title="HARP sweep server", lifespan=lifespan)
    app.state.manager = manager
    app.state.builds = builds
    app.state.queue_catalog = queue_catalog
    manager.tickers.append(builds.tick)
    allow_http = os.environ.get("HARP_ALLOW_HTTP") == "1"

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for name, value in response_security_headers.items():
            if name not in response.headers:  # a route may set a stricter/looser one
                response.headers[name] = value
        if is_https(request):
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def gateway(request: Request) -> TapisGateway:
        key = request.cookies.get(SESSION_COOKIE, "")
        gw = sessions.get(key)
        if gw is None:
            raise HTTPException(401, "log in to TAPIS first")
        if gw.expired():
            sessions.pop(key, None)
            raise HTTPException(401, "your TAPIS token expired, log in again")
        return gw

    def tapis(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except TapisError as e:
            raise HTTPException(502, f"TAPIS: {e}")

    def owned(cid, gw):
        c = manager.get(cid, owner_of(gw))
        if c is None:
            raise HTTPException(404, "campaign not found")
        return c

    # ------------------------------------------------------------ auth
    @app.post("/api/login")
    def login_route(request: Request, response: Response, body: dict = Body(...)):
        """Validate a TAPIS JWT and keep it behind an HttpOnly HARP session."""
        if not (is_https(request) or is_local(request) or allow_http):
            raise HTTPException(403, "log in over https:// - tokens are never accepted over plain http")
        base_url = (body.get("base_url") or "").strip().rstrip("/")
        if not base_url.startswith("https://"):
            raise HTTPException(400, "base_url must be an https TAPIS tenant URL, e.g. https://icicleai.tapis.io")
        access_token = (body.get("access_token") or "").strip()
        if not access_token:
            raise HTTPException(400, "enter a TAPIS access token")
        try:
            gw = login(base_url, access_token=access_token)
        except TapisError as e:
            raise HTTPException(401, login_error_message(e))
        token = secrets.token_urlsafe(32)
        sessions[token] = gw
        manager.register_gateway(gw)
        response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite=session_same_site,
                            secure=is_https(request))
        return {"username": gw.username, "base_url": gw.base_url, "expires_at": gw.expires_at}

    @app.post("/api/logout")
    def logout(request: Request, response: Response):
        sessions.pop(request.cookies.get(SESSION_COOKIE, ""), None)
        response.delete_cookie(SESSION_COOKIE)
        return {"ok": True}

    @app.get("/api/me")
    def me(gw: TapisGateway = Depends(gateway)):
        return {"username": gw.username, "base_url": gw.base_url, "expires_at": gw.expires_at}

    @app.get("/api/config")
    def public_config():
        """Non-secret settings used to bootstrap a TapisUI portal session."""
        return {"tapis_base_url": tapis_base_url, "portal_origins": portal_origins,
                "portal_auth": bool(portal_origins), "portal_auth_protocol": 1}

    @app.get("/api/health")
    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    # ----------------------------------------------------------- TAPIS browse
    @app.get("/api/tapis/systems")
    def systems(gw: TapisGateway = Depends(gateway)):
        return tapis(gw.list_systems)

    @app.get("/api/tapis/exec-systems")
    def exec_systems(gw: TapisGateway = Depends(gateway)):
        """Systems the user can run jobs on, each with its queues (TAPIS batchLogicalQueues)."""
        out = tapis(gw.exec_systems)
        for s in out:
            _decorate_queues(s, queue_catalog)
        return out

    @app.get("/api/tapis/inventory")
    def tapis_inventory(gw: TapisGateway = Depends(gateway)):
        """All visible systems and execution systems from one TAPIS request."""
        systems = tapis(gw.list_systems)
        for s in systems:
            _decorate_queues(s, queue_catalog)
        return {"systems": systems, "exec_systems": [s for s in systems
                                                     if s.get("can_exec") and s.get("enabled") is not False]}

    @app.get("/api/tapis/queue-probe")
    def queue_probe_status(_gw: TapisGateway = Depends(gateway)):
        """Live OSC Slurm probe health without exposing SSH configuration."""
        return queue_catalog.status()

    @app.get("/api/tapis/systems/{system_id}/access")
    def tapis_system_access(system_id: str, gw: TapisGateway = Depends(gateway)):
        """Fresh page-load access check using the same `/` Files listing as TapisUI."""
        return gw.check_system_access(system_id, refresh=True)

    @app.get("/api/tapis/apps")
    def apps(gw: TapisGateway = Depends(gateway)):
        """The user's TAPIS apps with their sweep parameters (from the app notes)."""
        return [harp_app(a) for a in tapis(gw.list_apps)]

    # ------------------------------------------------------------ campaigns
    def plan_or_400(plan, gw):
        if not isinstance(plan, dict):
            raise HTTPException(400, "send the plan as a JSON object")
        if not plan.get("sweeps"):
            raise HTTPException(400, "the plan has no sweeps")
        validator = lambda system, queue: _queue_void_reason(system, queue, queue_catalog)
        return tapis(build_plan, plan, gw, validator)

    def checked(item):
        """A built sweep, also run through the campaign validator and splitter."""
        if "error" in item:
            return {"name": item["name"], "ok": False, "error": item["error"], "targets": item["targets"]}
        spec = item["spec"]
        if not (spec["storage"]["system_id"] and spec["storage"]["path"].startswith("/")):
            # the results folder is reported on its own (storage_error); check the sweep without it
            spec = {**spec, "storage": {"system_id": "unset", "path": "/unset"}}
        try:
            _, _, summary = manager.preview(spec)
        except ValueError as e:
            return {"name": item["name"], "ok": False, "error": str(e), "targets": item["targets"]}
        return {"name": item["name"], "ok": True, "summary": summary, "targets": item["targets"],
                "sweep_name": item["spec"]["name"]}

    @app.post("/api/plan/preview")
    def preview_plan(plan: dict = Body(...), gw: TapisGateway = Depends(gateway)):
        """Check every sweep of the plan against TAPIS (apps, systems, queue limits).
        Each comes back with its summary and the exact hardware each job will ask for."""
        built, storage_error = plan_or_400(plan, gw)
        sweeps = [checked(b) for b in built]
        return {"ok": not storage_error and all(x["ok"] for x in sweeps),
                "storage_error": storage_error, "sweeps": sweeps}

    @app.post("/api/plan/submit")
    def submit_plan(plan: dict = Body(...), gw: TapisGateway = Depends(gateway)):
        """Launch every sweep of the plan. Nothing is submitted unless every sweep is valid."""
        built, storage_error = plan_or_400(plan, gw)
        if storage_error:
            raise HTTPException(400, storage_error)
        for x in map(checked, built):
            if not x["ok"]:
                raise HTTPException(400, f"sweep {x['name']}: {x['error']}")
        created = []
        for b in built:
            try:
                created.append(campaign_view(manager.create(b["spec"], gw), include_jobs=False))
            except TapisError as e:
                raise HTTPException(502, f"TAPIS refused sweep {b['name']} after "
                                    f"{len(created)} were submitted: {e}")
        return created

    @app.get("/api/jobs")
    def all_jobs(gw: TapisGateway = Depends(gateway)):
        """Every TAPIS job of every sweep, for the jobs table."""
        rows = []
        for c in manager.list(owner_of(gw)):
            target_of = {t["key"]: t for t in c["spec"]["targets"]}
            active_by_target = {}
            for candidate in c["jobs"]:
                if candidate.get("uuid") and candidate["status"] not in {"FINISHED", "FAILED", "CANCELLED", "SUBMIT_FAILED"}:
                    active_by_target[candidate["target"]] = active_by_target.get(candidate["target"], 0) + 1
            for j in c["jobs"]:
                t = target_of.get(j["target"], {})
                status_explanation = None
                if j["status"] == "NOT_SUBMITTED":
                    if c["status"] == "WAITING_FOR_LOGIN":
                        status_explanation = "Waiting in HARP for you to log in again; no TAPIS job exists yet."
                    else:
                        active, limit = active_by_target.get(j["target"], 0), t.get("max_concurrent_jobs") or 1
                        status_explanation = (f"Waiting in HARP for a target slot ({active} of {limit} active); "
                                              "no TAPIS job exists yet." if active >= limit else
                                              "Waiting for HARP's next submission pass; no TAPIS job exists yet.")
                rows.append({"name": j["name"], "sweep": c["spec"]["name"], "sweep_id": c["id"],
                             "app_id": t.get("app_id"), "system_id": j["system_id"],
                             "queue": t.get("queue"), "cores_per_node": t.get("cores_per_node"),
                             "memory_mb": t.get("memory_mb"), "gpus_per_node": t.get("gpus_per_node"),
                             "run_type": j["run_type"],
                             "combinations": len(j["combinations"]), "status": j["status"],
                             "runs_total": len(j["combinations"]) * c["spec"]["repetitions"],
                             "progress": j.get("progress"),
                             "uuid": j["uuid"], "error": j["error"], "kind": "profiling",
                             "status_explanation": status_explanation,
                             "submitted_at": j["submitted_at"], "ended_at": j["ended_at"]})
        for b in builds.list(owner_of(gw)):
            if not b.get("job"):
                continue
            j, w = b["job"], b["where"]
            rows.append({"name": j["name"], "sweep": f"build {b['name']}", "sweep_id": None, "build_id": b["id"],
                         "app_id": b["app_id"], "system_id": j["system_id"], "queue": j.get("queue"),
                         "cores_per_node": w.get("cores_per_node"), "memory_mb": w.get("memory_mb"),
                         "gpus_per_node": w.get("gpus_per_node"),
                         "run_type": "build", "combinations": 0, "status": j["status"], "runs_total": 0,
                         "progress": None, "uuid": j["uuid"], "error": b["error"] if b["status"] == "FAILED" else None,
                         "kind": "build", "submitted_at": j["submitted_at"], "ended_at": j.get("ended_at")})
        # One list call enriches all local HARP jobs with TAPIS's terminal
        # condition and message. If Jobs is temporarily unavailable, the local
        # campaign table remains usable.
        details = {}
        local_uuids = {r["uuid"] for r in rows if r.get("uuid")}
        if local_uuids:
            try:
                details = gw.list_job_details(local_uuids)
            except TapisError:
                pass
        for row in rows:
            detail = details.get(row.get("uuid"), {})
            if detail.get("status"):
                row["status"] = detail["status"]
                if row["status"] in {"FINISHED", "FAILED", "CANCELLED"} and is_status_check_error(row.get("error")):
                    row["error"] = None
            message = detail.get("message") or row.get("error")
            condition = detail.get("condition")
            row["condition"] = condition
            if row["status"] in {"FAILED", "SUBMIT_FAILED", "CANCELLED"}:
                row["failure"] = classify_job_failure(row["status"], condition, message,
                                                      row.get("system_id"), row.get("queue"))
            else:
                row["failure"] = None
        return rows

    @app.get("/api/campaigns")
    def list_campaigns(gw: TapisGateway = Depends(gateway)):
        return [campaign_view(c, include_jobs=False) for c in manager.list(owner_of(gw))]

    @app.post("/api/campaigns/{cid}/cancel")
    def cancel(cid: str, gw: TapisGateway = Depends(gateway)):
        c = owned(cid, gw)
        if c["status"] not in ACTIVE_CAMPAIGN_STATUSES:
            raise HTTPException(409, f"campaign is already {c['status']}")
        manager.cancel(c)
        return campaign_view(c)

    # ------------------------------------------------------- per-app results
    csv_cache = {}  # (system, path) -> bytes; a merged campaign CSV never changes

    def app_campaigns(app_id, gw):
        return [c for c in manager.list(owner_of(gw))
                if app_id in {t["app_id"] for t in c["spec"]["targets"]}]

    def hardware_label(t):
        """One hardware configuration as text, e.g. "pitzer/serial · 40 cores · 156 GB"."""
        parts = [t["system_id"] + (f"/{t['queue']}" if t.get("queue") else "")]
        if t.get("cores_per_node"):
            parts.append(f"{t['cores_per_node']} cores")
        if t.get("memory_mb"):
            parts.append(f"{round(t['memory_mb'] / 1024)} GB")
        if "--nv" in (t.get("container_args") or ""):
            parts.append("GPU")
        return " · ".join(parts)

    def app_rows(app_id, gw, campaign_ids=None):
        """Every profiling row collected for an app, across all its campaigns (or the ones given).
        Each row also says what its job was given: sys_alloc_cores, sys_alloc_mem_mb."""
        columns, rows, missing = ["campaign", "system", "hardware", "sys_alloc_cores", "sys_alloc_mem_mb"], [], []
        for c in app_campaigns(app_id, gw):
            if campaign_ids is not None and c["id"] not in campaign_ids:
                continue
            # run_config is "<job name>.run-<i>.iteration-<r>"; map it back to the job's system
            job_system = {j["name"]: j["system_id"] for j in c["jobs"]}
            targets = {t["key"]: t for t in c["spec"]["targets"]}
            job_hw = {j["name"]: hardware_label(targets[j["target"]]) for j in c["jobs"] if j["target"] in targets}
            job_t = {j["name"]: targets.get(j["target"], {}) for j in c["jobs"]}
            system = c["spec"]["storage"]["system_id"]
            merged = (c.get("result") or {}).get("csv_path")
            # Once a sweep is merged read its one CSV; while it runs, read the CSV
            # of every job that has finished so progress shows up straight away.
            paths = [merged] if merged else [f"{j['archive_dir']}/{PROFILE_FILE_NAME}"
                                             for j in c["jobs"] if j["status"] == "FINISHED"]
            for path in paths:
                key = (system, path)
                if key not in csv_cache:
                    try:
                        csv_cache[key] = gw.download(*key)
                    except TapisError as e:
                        missing.append({"campaign": c["spec"]["name"], "error": str(e)})
                        continue
                reader = csv.DictReader(io.StringIO(csv_cache[key].decode("utf-8")))
                for name in reader.fieldnames or []:
                    if name not in columns:
                        columns.append(name)
                for r in reader:
                    job = (r.get("run_config") or "").split(".run-")[0]
                    t = job_t.get(job, {})
                    rows.append({"campaign": c["spec"]["name"], "system": job_system.get(job, ""),
                                 "hardware": job_hw.get(job, ""), "sys_alloc_cores": t.get("cores_per_node", ""),
                                 "sys_alloc_mem_mb": t.get("memory_mb", ""), **r})
        if "walltime" in columns:  # keep walltime last, like the CSVs
            columns.remove("walltime")
            columns.append("walltime")
        return columns, rows, missing

    @app.get("/api/apps/{app_id}/profile")
    def app_profile(app_id: str, gw: TapisGateway = Depends(gateway)):
        columns, rows, missing = app_rows(app_id, gw)
        return {"app_id": app_id,
                "campaigns": [campaign_view(c, include_jobs=False) for c in app_campaigns(app_id, gw)],
                "columns": columns, "rows": rows[:MAX_PROFILE_ROWS], "total_rows": len(rows),
                "missing": missing}

    @app.get("/api/apps/{app_id}/profile.csv")
    def app_profile_csv(app_id: str, gw: TapisGateway = Depends(gateway)):
        columns, rows, _ = app_rows(app_id, gw)
        if not rows:
            raise HTTPException(404, "no profiling data for this app yet")
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=columns, restval="")
        writer.writeheader()
        writer.writerows(rows)
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in app_id)
        return Response(out.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{safe}_profile.csv"'})

    # ------------------------------------------------------------- build phase
    def training_data(app_id, sweeps, gw):
        if not isinstance(sweeps, list) or not sweeps:
            raise HTTPException(400, "pick at least one sweep")
        mine = {c["id"] for c in app_campaigns(app_id, gw)}
        unknown = [x for x in sweeps if x not in mine]
        if unknown:
            raise HTTPException(404, f"no sweep {unknown[0]!r} for this app")
        columns, rows, missing = app_rows(app_id, gw, set(sweeps))
        header, table, report = standardize(columns, rows)
        report["unreadable"] = missing
        return header, table, report

    @app.post("/api/apps/{app_id}/training-data")
    def training_data_preview(app_id: str, body: dict = Body(...), gw: TapisGateway = Depends(gateway)):
        """Pool the chosen sweeps and standardize them for the build phase: what it would train on."""
        header, table, report = training_data(app_id, body.get("sweeps"), gw)
        return {"columns": header, "sample": table[:8], **report}

    @app.get("/api/apps/{app_id}/training-data.csv")
    def training_data_csv(app_id: str, sweeps: str = "", gw: TapisGateway = Depends(gateway)):
        header, table, _ = training_data(app_id, [x for x in sweeps.split(",") if x], gw)
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in app_id)
        return Response(to_csv(header, table), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{safe}_training.csv"'})

    BUILD_CORES, BUILD_MINUTES = 4, 60   # a build job's defaults, capped by the queue

    @app.get("/api/build-options")
    def build_options(gw: TapisGateway = Depends(gateway)):
        """The models HARP can build, and the TAPIS build app (if one is registered) to run them."""
        apps = [harp_app(a) for a in tapis(gw.list_apps)]
        build_app = next((a for a in apps if a["role"] == "build"), None)
        return {"models": MODELS, "training_sets": TRAINING_SETS,
                "build_app": {k: build_app[k] for k in ("id", "version", "image")} if build_app else None}

    @app.post("/api/apps/{app_id}/builds")
    def start_build(app_id: str, body: dict = Body(...), gw: TapisGateway = Depends(gateway)):
        """Standardize the chosen sweeps and build models with the HARP pipeline, as a TAPIS job
        with the HARP build app or on this computer; results go to a TAPIS folder."""
        storage = body.get("storage") or {}
        if not storage.get("system_id") or not str(storage.get("path", "")).startswith("/"):
            raise HTTPException(400, "pick a TAPIS system and an absolute folder for the models")
        models = [m for m in body.get("models") or list(MODELS) if m in MODELS]
        sets = [t for t in body.get("training_sets") or list(TRAINING_SETS) if t in TRAINING_SETS]
        if not models or not sets:
            raise HTTPException(400, "pick at least one model and one training set")
        header, table, report = training_data(app_id, body.get("sweeps"), gw)
        if not report["ready"]:
            raise HTTPException(400, "not enough data to build: " + "; ".join(report["problems"]))
        apps = [harp_app(a) for a in tapis(gw.list_apps)]
        label = next((a["label"] for a in apps if a["id"] == app_id), app_id)
        run_on = body.get("run_on") or {"mode": "local"}
        where = {"mode": "local"}
        if run_on.get("mode") == "tapis":
            build_app = next((a for a in apps if a["role"] == "build"), None)
            if build_app is None:
                raise HTTPException(
                    400, "no HARP build app in TAPIS yet: register it with the app notebook (APP = 'build')")
            try:
                system = tapis(gw.get_system, run_on.get("system_id"))
                q = next((x for x in system.get("queues") or [] if x.get("name") == run_on.get("queue")), {})
                reason = _queue_void_reason(system, q, queue_catalog)
                if reason:
                    raise SpecError(reason)
                cores = min(BUILD_CORES, q.get("max_cores") or BUILD_CORES)
                t = resolve_target(system, run_on.get("queue"), {"cores": cores, "max_minutes": min(BUILD_MINUTES, q.get("max_minutes") or BUILD_MINUTES)},
                                   1, BUILD_MINUTES, 1, run_on.get("scheduler_options"))
            except SpecError as e:
                raise HTTPException(400, str(e))
            where = {"mode": "tapis", "app_id": build_app["id"], "app_version": build_app["version"], **{
                k: t[k] for k in ("system_id", "queue", "cores_per_node", "memory_mb", "max_minutes",
                                  "scheduler_options", "gpus_per_node", "container_args")}}
        try:
            b = builds.start(owner_of(gw), app_id, label, body["sweeps"], header, table, report,
                             {"system_id": storage["system_id"], "path": storage["path"].strip()}, gw,
                             models=models, training_sets=sets, where=where)
        except TapisError as e:
            raise HTTPException(502, f"TAPIS refused the build: {e}")
        return build_view(b)

    @app.get("/api/builds")
    def list_builds(app_id: str = "", gw: TapisGateway = Depends(gateway)):
        return [build_view(b) for b in builds.list(owner_of(gw), app_id or None)]

    @app.post("/api/estimates")
    def estimate_runs(body: dict = Body(...), gw: TapisGateway = Depends(gateway)):
        """Predict wall time for configured runs with explicitly selected HARP models."""
        entries, selected = body.get("entries"), body.get("models")
        if not isinstance(entries, list) or not entries or len(entries) > 100:
            raise HTTPException(400, "add between 1 and 100 estimate entries")
        if not isinstance(selected, dict):
            raise HTTPException(400, "select a trained model for every app")

        owner = owner_of(gw)
        profile_cache, groups = {}, {}
        for entry in entries:
            app_id, values, targets = entry.get("app_id"), entry.get("values"), entry.get("targets")
            choice = selected.get(app_id) if app_id else None
            if not app_id or not isinstance(values, dict) or not isinstance(targets, list) or not targets:
                raise HTTPException(400, "each estimate needs an app, values, and at least one target")
            if not choice or not choice.get("build_id") or not choice.get("model"):
                raise HTTPException(400, f"select a trained model for {app_id}")
            build = builds.builds.get(choice["build_id"])
            if not build or build.get("owner") != owner or build.get("app_id") != app_id:
                raise HTTPException(404, f"trained model build not found for {app_id}")
            if app_id not in profile_cache:
                profile_cache[app_id] = app_rows(app_id, gw)[1]

            key = (choice["build_id"], choice["model"])
            group = groups.setdefault(key, {"features": [], "references": []})
            for target_index, target in enumerate(targets):
                system_id, queue = target.get("system_id"), target.get("queue")
                hardware_key = system_id + (f"/{queue}" if queue else "")
                reference = next((r for r in profile_cache[app_id]
                                  if r.get("system") == system_id
                                  and str(r.get("hardware", "")).split(" · ", 1)[0] == hardware_key), None)
                if reference is None:
                    raise HTTPException(400, f"No profiled hardware data exists for {hardware_key}; profile that partition first.")
                feature_row = {}
                for feature in build.get("report", {}).get("features", []):
                    if feature == "sys_alloc_cores":
                        value = target.get("cores") or 1
                    elif feature == "sys_alloc_mem_mb":
                        value = target.get("memory_mb") or (int(target.get("cores") or 1) * 4096)
                    elif feature.startswith("run_"):
                        value = values.get(feature[4:])
                    else:
                        value = reference.get(feature)
                    if value is None or str(value).strip() == "":
                        raise HTTPException(400, f"Cannot estimate {hardware_key}: feature {feature} is unavailable.")
                    feature_row[feature] = value
                group["features"].append(feature_row)
                group["references"].append({"entry_id": entry.get("id"), "target_index": target_index,
                                            "app_id": app_id, "system_id": system_id, "queue": queue})

        results = []
        for (build_id, model_name), group in groups.items():
            try:
                prediction = builds.estimate(owner, build_id, model_name, group["features"], gw)
            except ValueError as e:
                raise HTTPException(400, str(e))
            except TapisError as e:
                raise HTTPException(502, f"Could not read the selected model from TAPIS: {e}")
            except RuntimeError as e:
                raise HTTPException(500, f"Could not run the selected model: {e}")
            metric = prediction["model"]
            for reference, seconds in zip(group["references"], prediction["seconds"]):
                results.append({**reference, "seconds": seconds, "build_id": build_id,
                                "model": model_name, "model_type": metric["RegModel"],
                                "adjust_factor": metric.get("ADJ_FACTOR") or 1})
        return {"estimates": results}

    # ------------------------------------------------------------------ page
    @app.get("/")
    @app.get("/profiling")
    @app.get("/profiling.html")
    def profiling_page():
        if not os.path.isfile(PROFILING_PAGE):
            raise HTTPException(404, "profiling.html not found")
        return FileResponse(PROFILING_PAGE, headers={"Content-Security-Policy": profiling_csp,
                                                     "Cache-Control": "no-store"})

    return app


app = create_app() if os.environ.get("HARP_SERVER_NO_DEFAULT_APP") is None else None
