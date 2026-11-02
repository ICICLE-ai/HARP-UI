"""Turn verbose TAPIS/Slurm failures into stable, user-facing categories."""


FAILURE_LABELS = {
    "authentication": "System authentication",
    "allocation": "Allocation/account",
    "queue_unavailable": "Queue unavailable",
    "queue_configuration": "Queue configuration",
    "job_limit": "Queue job limit",
    "resources_unavailable": "Resources unavailable",
    "out_of_memory": "Out of memory",
    "time_limit": "Time limit",
    "container": "Container launch",
    "staging": "Input/job staging",
    "remote_access": "Remote system access",
    "application": "Application error",
    "cancelled": "Cancelled",
    "unknown": "Other TAPIS error",
}


def classify_job_failure(status, condition=None, message=None, system_id=None, queue=None):
    """Return a short category and explanation while preserving the raw message."""
    condition = (condition or "").upper()
    raw = (message or "").strip()
    text = f"{condition} {raw}".lower()
    where = f" on {system_id}" if system_id else ""
    selected = f"TAPIS queue {queue!r}" if queue else "The TAPIS queue"
    system_key = str(system_id or "").lower()
    cluster = next((name for key, name in (("ascend", "Ascend"), ("pitzer", "Pitzer"),
                                           ("cardinal", "Cardinal"))
                    if key in system_key), "HPC")

    if status == "CANCELLED" or "cancelled_by_user" in text:
        code, explanation = "cancelled", "Job was cancelled."
    elif any(x in text for x in ("ssh_pool_missing_credentials", "missing credentials", "authentication failed")):
        code, explanation = "authentication", f"TAPIS credentials are missing for the execution system{where}."
    elif any(x in text for x in ("must specify account", "invalid account", "invalid qos", "scheduler account")):
        code, explanation = "allocation", "The scheduler rejected the account or QOS."
    elif any(x in text for x in ("required partition not available", "invalid partition", "inactive or drain", "partition is down")):
        code, explanation = "queue_unavailable", (f"{selected} maps to an unavailable {cluster} partition. "
                                                   "This is a TAPIS configuration issue.")
    elif any(x in text for x in ("jobs_q_exceeded_max_nodes", "jobs_q_exceeded_max_cores", "jobs_q_exceeded_max_memory",
                                 "jobs_q_exceeded_max_minutes", "queue maximum is 0")):
        code, explanation = "queue_configuration", (f"{selected} rejects these resources. "
                                                     "This is a TAPIS configuration issue.")
    elif any(x in text for x in ("jobs_q_exceeded_max_jobs", "queue limit exceeded", "maximum number of jobs")):
        code, explanation = "job_limit", "The TAPIS queue job limit was reached."
    elif "requested node configuration is not available" in text or "requested resources are not available" in text:
        code, explanation = "resources_unavailable", f"{selected} has no matching resources."
    elif condition == "SCHEDULER_OUT_OF_MEMORY" or any(x in text for x in ("out of memory", "oom-kill", "oom_kill")):
        code, explanation = "out_of_memory", "Job exceeded its memory allocation."
    elif condition in {"SCHEDULER_TIMEOUT", "SCHEDULER_DEADLINE"} or "time limit" in text:
        code, explanation = "time_limit", "Job exceeded its time limit."
    elif any(x in text for x in ("apptainer", "singularity", "image format not recognized", "container image")):
        code, explanation = "container", "Container failed to start."
    elif condition in {"JOB_UNABLE_TO_STAGE_INPUTS", "JOB_UNABLE_TO_STAGE_JOB", "JOB_FILES_SERVICE_ERROR"}:
        code, explanation = "staging", "TAPIS could not stage the job files."
    elif condition in {"JOB_REMOTE_ACCESS_ERROR", "JOB_EXECUTION_MONITORING_ERROR", "JOB_QUEUE_MONITORING_ERROR"}:
        code, explanation = "remote_access", f"TAPIS could not reach the execution system{where}."
    elif condition in {"SCHEDULER_TERMINATED", "JOB_REMOTE_OUTCOME_ERROR"} or "user_app_failure" in text or "exit code" in text:
        code, explanation = "application", "Application or container exited with an error."
    else:
        code, explanation = "unknown", "Unrecognized TAPIS failure."

    return {"code": code, "label": FAILURE_LABELS[code], "explanation": explanation,
            "condition": condition or None, "raw": raw or None}

