from harp_server.job_errors import classify_job_failure


def classify(message="", condition="JOB_LAUNCH_FAILURE", queue="gpu", system="ascend-tapis"):
    return classify_job_failure("FAILED", condition, message, system, queue)


def test_scheduler_failures_have_actionable_categories():
    unavailable = classify("Batch job submission failed: Required partition not available (inactive or drain)")
    assert unavailable["code"] == "queue_unavailable" and "gpu" in unavailable["explanation"]
    assert unavailable["explanation"].endswith("This is a TAPIS configuration issue.")

    worker = classify("JOBS_WORKER_PROCESSING_ERROR JobWorker failed: JOBS_SUBMIT_ERROR2 "
                      "SingularityRunSlurmLauncher received exit code 1: sbatch: error: Batch job submission failed: "
                      "Required partition not available (inactive or drain)", queue="batch")
    assert worker["code"] == "queue_unavailable"
    assert worker["label"] == "Queue unavailable" and worker["explanation"] == (
        "TAPIS queue 'batch' maps to an unavailable Ascend partition. This is a TAPIS configuration issue.")

    resources = classify("Batch job submission failed: Requested node configuration is not available", queue="debug")
    assert resources["code"] == "resources_unavailable" and resources["explanation"] == (
        "TAPIS queue 'debug' has no matching resources.")

    account = classify("ERROR: Job invalid: Must specify account for job")
    assert account["code"] == "allocation"

    bad_queue = classify("JOBS_Q_EXCEEDED_MAX_NODES requested 1 nodes. The queue maximum is 0.", queue="batch")
    assert bad_queue["code"] == "queue_configuration"
    assert bad_queue["explanation"].endswith("This is a TAPIS configuration issue.")

    missing_gpu = classify(
        "JOBS_WORKER_PROCESSING_ERROR SingularityRunSlurmLauncher received exit code 1: "
        "sbatch: error: QOSMinGRES sbatch: error: Batch job submission failed: "
        "Job violates accounting/QOS policy (job submit limit, user's size and/or time limits)",
        queue="gpu",
        system="cardinal-tapis",
    )
    assert missing_gpu["code"] == "queue_configuration"
    assert missing_gpu["label"] == "Queue configuration"
    assert missing_gpu["explanation"] == (
        "TAPIS queue 'gpu' did not add the GPU request required by Cardinal. "
        "This is a TAPIS configuration issue."
    )


def test_auth_container_and_terminal_conditions_are_classified():
    assert classify("SSH_POOL_MISSING_CREDENTIALS Missing credentials")["code"] == "authentication"
    assert classify("FATAL: Apptainer image format not recognized", "SCHEDULER_TERMINATED")["code"] == "container"
    assert classify("", "SCHEDULER_OUT_OF_MEMORY")["code"] == "out_of_memory"
    assert classify("", "SCHEDULER_TIMEOUT")["code"] == "time_limit"


def test_tapis_job_name_limit_is_concise():
    result = classify("TAPIS_JSON_VALIDATION_FAILURE JSON validation error: "
                      "#/name: expected maxLength: 64, actual: 72", condition="")
    assert result["code"] == "job_request"
    assert result["label"] == "Job request"
    assert result["explanation"] == "TAPIS rejected a generated job name longer than 64 characters."

