from harp_server.job_errors import classify_job_failure


def classify(message="", condition="JOB_LAUNCH_FAILURE", queue="gpu"):
    return classify_job_failure("FAILED", condition, message, "ascend-tapis", queue)


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


def test_auth_container_and_terminal_conditions_are_classified():
    assert classify("SSH_POOL_MISSING_CREDENTIALS Missing credentials")["code"] == "authentication"
    assert classify("FATAL: Apptainer image format not recognized", "SCHEDULER_TERMINATED")["code"] == "container"
    assert classify("", "SCHEDULER_OUT_OF_MEMORY")["code"] == "out_of_memory"
    assert classify("", "SCHEDULER_TIMEOUT")["code"] == "time_limit"

