"""
Every access to a compute system or storage location goes through this
module, using the TAPIS Systems, Files, Apps and Jobs services. The server
never touches cluster file systems directly.
"""

import threading
import time
from datetime import datetime, timezone

TERMINAL_STATUSES = {"FINISHED", "FAILED", "CANCELLED"}


def _plain(obj):
    """tapipy returns nested TapisResult objects; turn them into plain JSON data."""
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    if hasattr(obj, "__dict__") and not isinstance(obj, type):
        return {k: _plain(v) for k, v in vars(obj).items() if not k.startswith("_")}
    return obj


class TapisError(RuntimeError):
    pass


def _get(obj, name, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


UNLIMITED = 2147483647   # what TAPIS stores for "no limit"


def _limit(v):
    """A queue maximum, or None when TAPIS has none (unset, 0, negative or the unlimited sentinel)."""
    return v if isinstance(v, (int, float)) and 0 < v < UNLIMITED else None


def _system_view(s):
    """The static system fields HARP needs from a TAPIS System definition."""
    default = _get(s, "batchDefaultLogicalQueue")
    queues = [{"name": _get(q, "name"), "hpc_queue": _get(q, "hpcQueueName"),
               "description": _get(q, "description"), "default": _get(q, "name") == default,
               "max_jobs": _limit(_get(q, "maxJobs")), "max_jobs_per_user": _limit(_get(q, "maxJobsPerUser")),
               "min_nodes": _get(q, "minNodeCount"), "max_nodes": _limit(_get(q, "maxNodeCount")),
               # Keep the literal value too. The Systems API commonly uses 0
               # for an unset limit, but the Jobs service rejects a one-node
               # request when a logical queue actually advertises maxNodeCount=0.
               "configured_max_nodes": _get(q, "maxNodeCount"),
               "min_cores": _get(q, "minCoresPerNode"), "max_cores": _limit(_get(q, "maxCoresPerNode")),
               "min_memory_mb": _get(q, "minMemoryMB"), "max_memory_mb": _limit(_get(q, "maxMemoryMB")),
               "min_minutes": _get(q, "minMinutes"), "max_minutes": _limit(_get(q, "maxMinutes"))}
              for q in (_get(s, "batchLogicalQueues") or []) if _get(q, "name") is not None]
    return {"id": _get(s, "id"), "host": _get(s, "host"), "description": _get(s, "description") or "",
            "system_type": _get(s, "systemType"), "enabled": _get(s, "enabled", True) is not False,
            "can_exec": bool(_get(s, "canExec")), "can_run_batch": bool(_get(s, "canRunBatch")),
            "effective_user": _get(s, "effectiveUserId"),
            "authn_method": _get(s, "defaultAuthnMethod"),
            "batch_scheduler": _get(s, "batchScheduler"),
            "runtimes": [_get(r, "runtimeType") for r in (_get(s, "jobRuntimes") or [])],
            "default_queue": default, "queues": queues,
            "job_working_dir": _get(s, "jobWorkingDir"), "root_dir": _get(s, "rootDir")}


class TapisGateway:
    """Thin, thread-safe wrapper around a logged-in tapipy client."""

    def __init__(self, client, username, base_url, expires_at=None):
        self.client = client
        self.username = username
        self.base_url = base_url
        self._fixed_expiry = expires_at  # only used when there is no client (tests)
        self._lock = threading.Lock()
        self._job_details_cache = (0.0, {})
        self._system_access_cache = {}

    @property
    def expires_at(self):
        """When this session stops working, in epoch seconds (None = unknown).

        If TAPIS issued a refresh token, tapipy renews the access token by
        itself, so the session lasts until the refresh token expires.
        """
        if self.client is None:
            return self._fixed_expiry
        for token in (getattr(self.client, "refresh_token", None), getattr(self.client, "access_token", None)):
            exp = _get(_get(token, "claims", {}), "exp") if token else None
            if exp:
                return float(exp)
        return None

    def expired(self, margin=60):
        exp = self.expires_at
        return exp is not None and time.time() > exp - margin

    @classmethod
    def login(cls, base_url, username=None, password=None, access_token=None):
        """Log in to TAPIS on the server side with tapipy.

        Either a username/password (exchanged for a token with get_tokens())
        or an existing access token. Any token is checked with TAPIS
        (get_userinfo), so a forged one is rejected. The password is dropped
        from memory as soon as the token is issued; the token stays on the
        server and is never sent to the browser.
        """
        try:
            from tapipy.tapis import Tapis
        except ImportError as e:
            raise TapisError("tapipy is not installed on the HARP server") from e
        try:
            if access_token:
                client = Tapis(base_url=base_url, access_token=access_token)
                username = _get(client.authenticator.get_userinfo(), "username")
            else:
                client = Tapis(base_url=base_url, username=username, password=password)
                client.get_tokens()
        except Exception as e:
            msg = str(e).replace(password, "***") if password else str(e)
            raise TapisError(f"TAPIS login failed: {msg}") from None
        finally:
            password = None
        client.password = None
        if not username:
            raise TapisError("TAPIS did not return a username for this token")
        return cls(client, username, base_url)

    def _call(self, fn, *args, **kwargs):
        # tapipy refreshes expiring tokens itself; we only serialize calls and
        # turn its many exception types into one.
        with self._lock:
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                raise TapisError(str(e)) from e

    # ----------------------------------------------------------------- systems
    def list_systems(self):
        """All visible systems in one request, with their configured queues.

        Previously HARP fetched a summary and then fetched every execution
        system again one-by-one. allAttributes avoids that N+1 startup delay.
        """
        systems = self._call(self.client.systems.getSystems, listType="ALL", limit=-1,
                             select="allAttributes")
        return [self._with_cached_access(_system_view(s)) for s in systems]

    def get_system(self, system_id):
        """A system and its batch queues, as TAPIS defines them (LogicalQueue).
        A max of 0 or below (not set) or 2147483647 (TAPIS's 'unlimited') becomes None: no limit."""
        return self._with_cached_access(
            _system_view(self._call(self.client.systems.getSystem, systemId=system_id)))

    def exec_systems(self):
        """Every system the user can run jobs on, each with its queues."""
        return [s for s in self.list_systems() if s["can_exec"]]

    def _with_cached_access(self, system):
        cached = getattr(self, "_system_access_cache", {}).get(system["id"])
        return {**system, "access": dict(cached) if cached else {
            "system_id": system["id"], "status": "unknown", "accessible": None,
            "checked_at": None, "error": None}}

    def check_system_access(self, system_id, refresh=False):
        """Test real remote access exactly as TapisUI does: list `/` with Files.

        This is read-only and checks the whole Tapis-to-host authentication path.
        It does not infer access from static credential metadata in the System definition.
        The browser requests one fresh check per system when the page loads. Plan
        validation then reuses that result rather than polling the remote host.
        A failed listing means access is unavailable; the underlying cause may be
        missing credentials, invalid credentials, host downtime, or another
        Files/SSH error.
        """
        cached = self._system_access_cache.get(system_id)
        if cached and not refresh:
            return dict(cached)
        try:
            self._call(self.client.files.listFiles, systemId=system_id, path="/", limit=1)
            result = {"system_id": system_id, "status": "accessible", "accessible": True,
                      "checked_at": datetime.now(timezone.utc).isoformat(), "error": None}
        except TapisError as e:
            result = {"system_id": system_id, "status": "unavailable", "accessible": False,
                      "checked_at": datetime.now(timezone.utc).isoformat(), "error": str(e)}
        self._system_access_cache[system_id] = result
        return dict(result)

    def list_apps(self):
        apps = self._call(self.client.apps.getApps, listType="ALL", limit=-1, select="allAttributes")
        out = []
        for a in apps:
            attrs = _get(a, "jobAttributes")
            params = _get(attrs, "parameterSet")
            out.append({
                # the app's own job defaults; HARP overrides them per job
                "defaults": {"cores_per_node": _get(attrs, "coresPerNode"), "memory_mb": _get(attrs, "memoryMB"),
                             "max_minutes": _get(attrs, "maxMinutes")},
                "id": _get(a, "id"), "version": _get(a, "version"),
                "image": _get(a, "containerImage"), "runtime": _get(a, "runtime"),
                "description": _get(a, "description") or "",
                "notes": _plain(_get(a, "notes")) or {},
                "app_args": [{"name": _get(x, "name"), "arg": _get(x, "arg")}
                             for x in (_get(params, "appArgs") or [])],
            })
        return out

    # ------------------------------------------------------------------- files
    def mkdir(self, system_id, path):
        self._call(self.client.files.mkdir, systemId=system_id, path=path)

    def upload(self, system_id, path, content: bytes):
        name = path.rstrip("/").rsplit("/", 1)[-1]
        self._call(self.client.files.insert, systemId=system_id, path=path, file=(name, content))

    def download(self, system_id, path) -> bytes:
        data = self._call(self.client.files.getContents, systemId=system_id, path=path)
        return data if isinstance(data, bytes) else str(data).encode()

    # -------------------------------------------------------------------- jobs
    def submit_job(self, request: dict) -> str:
        job = self._call(self.client.jobs.submitJob, **request)
        return _get(job, "uuid")

    def job_status(self, uuid) -> str:
        return _get(self._call(self.client.jobs.getJobStatus, jobUuid=uuid), "status")

    def list_job_details(self, uuids=None, limit=1000):
        """Recent jobs keyed by UUID, with full failure messages for HARP jobs.

        The TAPIS JobListDTO deliberately omits lastMessage and remoteResultInfo,
        so failed jobs must be hydrated with getJob before their errors can be
        classified. Only local HARP UUIDs are hydrated to avoid an N+1 request
        for unrelated jobs in the user's account.
        """
        wanted = set(uuids or ())
        cached_at, cached = getattr(self, "_job_details_cache", (0.0, {}))
        if time.monotonic() - cached_at < 10 and wanted.issubset(cached):
            return cached
        jobs = self._call(self.client.jobs.getJobList, listType="MY_JOBS", limit=limit)
        details = {_get(j, "uuid"): {"status": _get(j, "status"), "condition": _get(j, "condition"),
                                      "message": None, "remote_outcome": _get(j, "remoteOutcome"),
                                      "remote_result_info": None}
                   for j in jobs if _get(j, "uuid")}
        for uuid in wanted:
            summary = details.get(uuid, {})
            if summary.get("status") not in {"FAILED", "CANCELLED"}:
                continue
            try:
                job = self._call(self.client.jobs.getJob, jobUuid=uuid)
            except TapisError:
                continue
            messages = list(dict.fromkeys(str(x).strip() for x in
                            (_get(job, "lastMessage"), _get(job, "remoteResultInfo")) if x))
            details[uuid] = {"status": _get(job, "status") or summary.get("status"),
                             "condition": _get(job, "condition") or summary.get("condition"),
                             "message": "\n".join(messages) or None,
                             "remote_outcome": _get(job, "remoteOutcome"),
                             "remote_result_info": _get(job, "remoteResultInfo")}
        self._job_details_cache = (time.monotonic(), details)
        return details

    def cancel_job(self, uuid):
        self._call(self.client.jobs.cancelJob, jobUuid=uuid)

    def transfer(self, src_system, src_path, dst_system, dst_path):
        """Copy a file or folder between TAPIS systems with a Files transfer task (no job,
        no allocation: TAPIS uses your credentials on both systems). Returns the task id."""
        task = self._call(self.client.files.createTransferTask, tag="harp", elements=[
            {"sourceURI": f"tapis://{src_system}/{src_path.lstrip('/')}",
             "destinationURI": f"tapis://{dst_system}/{dst_path.lstrip('/')}"}])
        return _get(task, "uuid")

    def transfer_status(self, task_id):
        """ACCEPTED, IN_PROGRESS, COMPLETED, FAILED, CANCELLED, ..."""
        return _get(self._call(self.client.files.getTransferTask, transferTaskId=task_id), "status")

    def job_output_dir(self, uuid):
        """(system, path) of the job's output folder on the execution system,
        readable through TAPIS Files while the job runs."""
        job = self._call(self.client.jobs.getJob, jobUuid=uuid)
        return _get(job, "execSystemId"), _get(job, "execSystemOutputDir")

