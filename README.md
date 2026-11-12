# HARP UI - HPC Application Runtime Predictor

HARP profiles scientific applications across high-performance computing (HPC) systems, builds regression models from the resulting execution history, and predicts application runtime and resource needs. **HARP UI** is the browser front end for that workflow: one web page, backed by a small FastAPI server, that turns a profiling sweep into many Tapis jobs across several systems, collects their results, trains runtime estimators, and lets you pick the best model, all through your own Tapis account. It runs on your computer, on a shared HTTPS server, or as an ICICLE Tapis Pod designed to be embedded in TapisUI.

> Looking for the earlier notebook (Colab + Tapis container) workflow or installable OSC/Linux module (`module load harp`)? Start with the [legacy execution notebooks](Notebooks/) and [legacy HARP overview](DEMO/readMe.md).

**Tags:** Software, AI4CI

### License

[![License](https://img.shields.io/badge/License-BSD_3--Clause-blue.svg)](https://opensource.org/licenses/BSD-3-Clause)

HARP is distributed under the [BSD 3-Clause License](LICENSE).

## References

- [HARP source repository](https://github.com/ICICLE-ai/HARP-UI)
- [Deployed HARP service](https://harp.pods.icicleai.tapis.io)
- [HARP sweep-server reference](harp_server/README.md) (API, environment variables, internals)
- [Tapis Pods deployment guide](deploy/README.md)
- [Tapis Pods Service API](https://tapis.readthedocs.io/en/latest/technical/pods.html)
- [Offline UI demo](DEMO/ui/README.md)
- [Legacy HARP overview and notebook links](DEMO/readMe.md)
- [Tapis documentation](https://tapis.readthedocs.io/)
- [CODAR Cheetah](https://github.com/CODARcode/cheetah)

## Acknowledgements

*National Science Foundation (NSF) funded AI institute for Intelligent Cyberinfrastructure with Computational Learning in the Environment (ICICLE) (OAC 2112606)*

## Issue reporting

Report defects and feature requests through [GitHub Issues](https://github.com/ICICLE-ai/HARP-UI/issues). Include the HARP version, where the UI runs (laptop, HTTPS server, Tapis Pod / TapisUI), the Tapis tenant, reproduction steps, and relevant logs. Never include access tokens or passwords.

## Start here

Choose the path that matches what you want to do:

| Goal | Recommended path | Tapis or HPC access required? |
|---|---|---|
| Explore the interface without submitting jobs | [Try the offline demo](#tutorial-explore-harp-without-tapis) | No |
| Profile an application on an HPC system | [Run your first profiling sweep](#tutorial-run-your-first-profiling-sweep) | Yes |
| Develop or test HARP UI locally | [Run HARP UI on your computer](#tutorial-run-harp-ui-on-your-computer) | A token is needed for live Tapis data |
| Make an application available in HARP | [Package an application for sweeps](#package-your-application-for-sweeps) | Yes |
| Deploy or update the hosted service | [Deploy as an ICICLE Tapis Pod](#deploy-as-an-icicle-tapis-pod-tapisui) | Tapis Pods administration |

HARP's browser workflow has three stages:

1. **Generate:** run controlled application configurations through Tapis and collect timing and hardware data.
2. **Build:** train and compare runtime estimators from successful profiling runs.
3. **Estimate:** predict runtime for new parameter and hardware configurations.

HARP does not create cluster accounts, allocations, system credentials, or live scheduler partitions. Those must already be available through the selected Tapis tenant.

---

# Tutorials

## Explore HARP without Tapis

Use this tutorial to understand the interface before using a cluster. The offline demo uses sample applications, systems, jobs, profiling data, and trained models; it never contacts Tapis.

### Prerequisites

- A current desktop browser.
- A local copy of this repository.

### Steps

1. Open [`DEMO/ui/profiling.html`](DEMO/ui/profiling.html) in a browser.
2. Enter any username and password on the demo login screen. These values stay in the demo and are not sent anywhere.
3. Use the top-level navigation to inspect **HARP Generate**, **HARP Build**, and **HARP Estimate**.
4. Under **Generate**, configure a sample sweep, add it to the plan, review it, and create demo jobs.
5. Open **Jobs** and **Profiled data** to inspect job states, failures, hardware, parameters, and walltimes.
6. Open **Build** and **Estimate** to follow the model-training and prediction stages.

> **Screenshot slot — `docs/images/tutorial-offline-overview.png`:** Show the offline demo after login with the Generate, Build, and Estimate navigation visible. Replace this callout with an image when the screenshot is available.

### Expected result

You can navigate the complete HARP workflow and interact with sample data without a Tapis account. Closing or refreshing the demo cannot create, cancel, or change a real job.

## Open the hosted service

### Prerequisites

- An account on the ICICLE Tapis tenant.
- A current Tapis access token when opening HARP directly.
- Credentials and an allocation on any HPC system where you intend to submit jobs.

### Steps

1. Open [https://harp.pods.icicleai.tapis.io](https://harp.pods.icicleai.tapis.io).
2. Paste a current ICICLE Tapis access token when prompted. Do not put the token in a URL, issue, screenshot, or repository file.
3. Confirm that the page shows the applications and systems available to your Tapis identity.
4. If HARP is enabled in your TapisUI deployment, you may instead sign in to TapisUI and open the **HARP** service. The embedded service receives the portal session automatically and should not show a second login screen.

> **Screenshot slot — `docs/images/tutorial-hosted-service.png`:** Show HARP's first authenticated screen with the username visible but all private paths, allocation names, and tokens removed.

> **Screenshot slot — `docs/images/tutorial-tapisui-service.png`:** After the TapisUI integration is merged, show where HARP appears in the Services navigation and the embedded page without a second login prompt.

### Expected result

The **Generate** page loads with the applications and execution systems visible to your Tapis identity. Seeing a system or queue means it is configured in Tapis; it does not guarantee that the remote scheduler partition is currently active.

## Run HARP UI on your computer

This starts the real FastAPI service locally. Unlike the offline demo, it uses your Tapis token and can submit real jobs.

### Prerequisites

- Windows, macOS, or Linux.
- Python 3.11 or 3.12.
- `git` and this repository.
- A Tapis access token for live testing.

### Steps

```bash
git clone https://github.com/ICICLE-ai/HARP-UI.git
cd HARP-UI
./harp_server/scripts/run_local.sh
```

Open `http://localhost:8000`. The first run creates `harp_server/.venv` and installs FastAPI, Uvicorn and tapipy, plus pandas, scikit-learn and TensorFlow for local model builds (set `HARP_NO_BUILD=1` to skip those). The server listens on `127.0.0.1` only. State is kept in `~/.harp_server`.

On Windows PowerShell, the equivalent setup is:

```powershell
py -3.12 -m venv harp_server/.venv
& .\harp_server\.venv\Scripts\Activate.ps1
python -m pip install -r harp_server/requirements.txt -r harp_server/requirements-build.txt
Push-Location harp_server
python -m uvicorn harp_server.app:app --host 127.0.0.1 --port 8000
```

Keep the computer awake while sweeps run. On macOS, use `caffeinate -i ./harp_server/scripts/run_local.sh`. If the computer sleeps or the server stops, jobs already accepted by Tapis keep running; start HARP again and log in to resume tracking them.

> **Screenshot slot — `docs/images/tutorial-local-login.png`:** Show the local HARP login form at `http://localhost:8000` with an empty token field.

### Expected result

`http://localhost:8000/healthz` returns `{"status":"ok"}`, and the browser shows the same HARP interface as the hosted service. Stopping the local server does not cancel jobs already accepted by Tapis.

## Generate, build, and estimate runtime

This tutorial follows the complete three-stage workflow with one profiling application. The exact systems and partitions depend on the current Tapis inventory and live scheduler checks; use only entries marked **AVAILABLE**. The example uses the parameter values `1, 2`, one repetition, and the OSC scheduler option `-A PAS2271`. Replace those values with ones appropriate for your application and allocation.

Red rectangles in the tutorial images identify the control or result used in that step. Clean, unannotated copies are kept beside them in `docs/images/`.

### Prerequisites

- A current Tapis session and a HARP profiling app visible to your account.
- Working Tapis credentials for Ascend and Cardinal.
- An allocation valid on both selected systems.
- At least two available Ascend partitions, one available Cardinal partition, and a fourth available partition for the held-out test sweep.
- A writable Tapis system and absolute results folder.

### Stage 1: Generate profiling data

1. Open **HARP Generate → Configure**, select the profiling app, and wait for system and partition badges to change from **CHECKING** to **AVAILABLE** or **UNAVAILABLE**. Leave **Show unavailable** off while choosing targets.
2. Configure an `SD` sweep. Give the varying parameter the list `1, 2`, keep one repetition, and select two available Ascend partitions plus one available Cardinal partition. Enter `-A PAS2271` in the scheduler-options field for both systems. Each HARP job requests one node; cores, memory, and time are per-node requests, not the displayed queue maxima.

   ![HARP Generate configured for an SD sweep with epochs 1 and 2](docs/images/tutorial-generate-configure-sd-annotated.png)

   ![Two selected Ascend partitions and the approved Ascend scheduler allocation](docs/images/tutorial-generate-targets-sd-ascend-annotated.png)

   ![One selected Cardinal partition and the approved Cardinal scheduler allocation](docs/images/tutorial-generate-targets-sd-cardinal-annotated.png)

3. Select **Add to plan**. If HARP warns that a selected system is missing an allocation, go back and fill it in instead of continuing.
4. Change the run type and name to create an `FS` sweep with the same parameter values and three profiling targets, then add it to the plan.
5. Create a `test_data` sweep with the same values but select only one other available Ascend or Cardinal partition. Add it to the plan. Held-out test rows score the trained models and are never used for fitting.

   ![The Generate plan containing SD, FS, and test_data sweeps](docs/images/tutorial-generate-plan-three-sweeps-annotated.png)

6. Open **Review & submit**. Confirm that the plan contains all three sweeps, every target requests one node, the requested cores/memory/time are within the Tapis logical-queue limits, and the scheduler options are present. Choose the writable results location, tick the confirmation box, and submit the whole plan.

   ![The ready three-sweep plan on Review and submit](docs/images/tutorial-generate-review-annotated.png)

7. Open **Jobs**. HARP and Tapis move jobs through pre-submission, scheduler, execution, and archiving states. The page polls while work is active; a successful status response replaces transient connection errors from an earlier poll.

   ![Profiling jobs moving through active Tapis states](docs/images/tutorial-generate-jobs-running-annotated.png)

8. Wait until all three sweeps have finished. A sweep can be **DONE WITH ERRORS** if only some jobs succeeded; open its jobs and failure details before deciding whether its successful rows are suitable for training.

   ![Tutorial sweep summaries after polling reaches terminal states](docs/images/tutorial-generate-jobs-finished-annotated.png)

9. Open **Profiled data** and verify that SD, FS, and test_data rows are present.

   ![Profiled-data metrics split by run type and hardware](docs/images/tutorial-generate-profiled-data-annotated.png)

### Stage 2: Build runtime models

1. Open **HARP Build → Training data** and select only the three sweeps created above. The data check must show at least 2 SD rows, 4 FS rows, and 1 test_data row. Queue count alone does not guarantee this—only successfully profiled rows count.

   ![Only the tutorial sweeps selected with passing training-data coverage](docs/images/tutorial-build-training-data-annotated.png)

2. Open **Models & where**. Choose the desired LR, NN, and/or DTR models and training sets. Choose a Tapis system and partition or **This computer** for the build, then choose the Tapis system and absolute folder where model artifacts will be saved.

   ![Model choices, local build target, and remote model destination](docs/images/tutorial-build-models-where-annotated.png)

3. Select **Build models**. A remote build appears in **Generate → Jobs**; a local build runs in the HARP server process. When it finishes, open **Trained models** and compare average error and the percentage of predictions that were too low. The best-scoring model is listed first.

   ![A local HARP build in the training state](docs/images/tutorial-build-running-annotated.png)

   ![The completed build with the best model and evaluation scores](docs/images/tutorial-build-trained-models-annotated.png)

### Stage 3: Estimate a new run

1. Open **HARP Estimate → Configure**, choose the same app, and enter one value per parameter rather than a comma-separated sweep. Select one available partition and the cores per node that the prospective job would request, then add it to the estimate list.

   ![One YOLO run and one available Ascend partition selected for estimation](docs/images/tutorial-estimate-configure-annotated.png)

2. Open **Select model**. HARP orders completed models by score and preselects the best available model; confirm the choice.

   ![The best completed runtime model selected automatically](docs/images/tutorial-estimate-select-model-annotated.png)

3. Open **Estimates**, select **Estimate run times**, and wait for the prediction to appear. The result is a model prediction, not a scheduler reservation or guarantee.

   ![The estimate request while the selected model is running](docs/images/tutorial-estimate-generating.png)

   ![The generated runtime estimate for the selected app and partition](docs/images/tutorial-estimate-result-annotated.png)

### Expected result

The three profiling sweeps provide enough successful SD, FS, and test_data rows to train at least one estimator. The build produces saved model artifacts and scores, and Estimate returns a predicted runtime for the new app/partition configuration.

### If a job fails

Open **Generate → Jobs → Failure details**. HARP presents a concise cause and identifies Tapis configuration problems separately from resource, allocation, container, and application failures. Preserve the Tapis job ID and sanitized raw detail when reporting the issue; do not include a token.

![A selected sweep showing only its concise Tapis job-name validation failure](docs/images/tutorial-generate-job-name-failure-annotated.png)

---

# How-To Guides

## Prerequisites

HARP UI performs application, job, and file operations through Tapis (Systems, Files, Apps, and Jobs); it never touches a cluster file system directly. The optional OSC queue validator separately runs only `sinfo` through a dedicated noninteractive SSH identity. Before profiling an application you need:

1. **Execution systems and credentials** registered in Tapis for each cluster (e.g. OSC Pitzer, TACC). The notebooks [`Executing_HARP_using_TAPIS*.ipynb`](Notebooks/) show how.
2. **An allocation account** on each system.
3. **A sweep container** for your application, and **a Tapis app** for it whose `notes.harp` block describes its command, work folder and parameters.

## Understand the system and queue lists

HARP asks Tapis which execution systems and logical queues are visible to the current user. Inventory, apps, campaigns, and jobs begin loading together so selectors can populate without waiting for profiling CSVs. Systems and partitions initially show **CHECKING**. After the essential page data appears, HARP performs the same read-only access check as TapisUI—one Tapis Files listing of `/` per system—in a controlled sequence so slow remote checks do not flood the Tapis session. Tapis provides the logical-queue definitions, while an optionally configured server-side probe validates OSC partitions against live Slurm state.

- A visible system may still require the user to finish cluster authentication or obtain an allocation.
- **AVAILABLE** on a system means the Files listing succeeded through Tapis. **UNAVAILABLE** can mean missing or invalid credentials, host downtime, or another Files/SSH failure; HARP preserves the returned Tapis detail for diagnosis.
- A visible logical queue may map to a scheduler partition that is temporarily inactive, drained, restricted, or unavailable for the requested resources.
- HARP does not submit probe jobs to discover queues. When the optional OSC queue probe is configured, the server runs the fixed, read-only command `sinfo --all --noheader --format='%P|%a|%t'` on Ascend, Pitzer, and Cardinal at startup and on a refresh interval. Live Slurm output—not the OSC documentation—is used to validate the mapped partition.
- The probe snapshot is saved under `HARP_SERVER_DATA`, reused after a Pod restart, refreshed once every 24 hours by default, and ignored after its configured maximum age. It uses the Pod's dedicated OSC SSH identity, independently of whoever signs in to HARP.
- A queue begins as **CHECKING**, becomes green **AVAILABLE** after system access and current live scheduler data agree, or becomes red **INVALID** / **UNAVAILABLE** when HARP cannot safely offer it. A missing or unavailable partition from a current live result is disabled only in HARP; HARP never changes or deletes the Tapis System definition.
- **Show unavailable** reveals disabled systems and queues for diagnosis without allowing them to be selected.
- Tapis logical queues are still configuration owned by the relevant Tapis system administrator and must be corrected there when a mapping is wrong.
- Queue limits published by Tapis are validation limits, not a promise that a matching node is free or that requesting the maximum will produce a valid node shape.

Every profiling job currently sends `nodeCount=1`. **Cores requested** maps to Tapis `coresPerNode`, **memory requested** maps to `memoryMB`, and **time limit requested** maps to `maxMinutes`. HARP defaults to one core, 4 GB per requested core, and `runs per job × run timeout + 10 minutes`, capped by the logical queue. GPU-labelled queues additionally request one GPU and launch Singularity/Apptainer with `--nv`.

For a first test, choose a system you already use successfully through Tapis, select a development or debug queue when appropriate, and request conservative resources.

> **Screenshot slot — `docs/images/howto-system-queue-status.png`:** Show system access/status indicators and configured queues. Include one usable and one unavailable example without exposing account details.

## Troubleshoot a failed Tapis job

Open **HARP Generate → Jobs**, expand **Failure details**, and locate the failed Tapis job. Preserve the job ID, selected system and queue, classified reason, Tapis condition, and raw message when asking an administrator for help.

| Message or category | What it usually means | What to try |
|---|---|---|
| Missing credentials or remote access failure | Tapis cannot authenticate to the execution system as the required effective user | Authenticate the system in Tapis or ask the system owner to verify its effective-user and credential configuration |
| Must specify account / invalid account | The scheduler account option is absent or invalid | Add the correct site allocation, such as `-A <project>`, and verify that your cluster identity belongs to it |
| Tapis queue maps to an unknown scheduler partition | The Tapis logical queue's `hpcQueueName` is absent from the current live Slurm partition list | This is a Tapis system configuration issue. Choose another available queue and report the Tapis queue → Slurm partition mapping to the system administrator |
| Required partition not available / inactive / drain | The mapped scheduler partition exists but is not currently accepting the job | Choose another **AVAILABLE** partition or ask the system administrator when it will return |
| Requested node configuration is not available | No node matches the requested cores, memory, GPU, or other resource shape | Reduce the request and use a configuration supported by that queue |
| `QOSMinGRES` / accounting-QOS policy | A GPU partition requires a GPU resource or the request violates that account's QOS limits | Select a GPU-labelled HARP queue so one GPU and `--nv` are requested, then verify the account/QOS with the system administrator |
| Job launch directory or working-directory failure | The Tapis System's `jobWorkingDir` / effective-user path is invalid for the remote host | This is a Tapis system configuration issue; report the execution system and failing path to its administrator |
| Out of memory | The launched application exceeded its memory allocation | Increase memory, reduce application batch size, or reduce concurrent work inside the job |
| Time limit | The scheduler stopped the job at its walltime | Increase the limit within the queue maximum or make the sweep smaller |
| Container launch | Apptainer/Singularity could not pull, convert, cache, or start the image | Verify the image is public and compatible; give the raw message to the system administrator if cache conversion failed |
| Application error | The job launched but the application returned a nonzero exit code | Open `tapisjob.out`, reproduce with one small parameter combination, and fix the application command or inputs |

The Jobs table uses short summaries such as **Tapis configuration issue**, **partition unavailable**, or **GPU/QOS request rejected**. The full Tapis and scheduler text remains under the expandable raw failure detail. A later successful status poll clears a transient connection error instead of leaving a completed job marked with the old polling failure.

> **Screenshot slot — `docs/images/howto-failure-details.png`:** Show the failure summary cards and one expanded raw message. Sanitize usernames, paths, job IDs, and allocations if the screenshot will be public.

## Find job output and profiling results

Each profiling job writes its live output under the Tapis job output directory. Tapis archives the job output into the campaign folder selected on **Review & submit**. HARP then merges successful profiling rows into the campaign CSV used by **Profiled data** and **HARP Build**.

Use the Jobs page for the Tapis job ID and status, the Profiled data page for successful timing rows, and the selected results folder for archived files such as `tapisjob.out`, progress and summary JSON, and profiling CSV data.

> **Screenshot slot — `docs/images/howto-results-location.png`:** Show the submitted campaign's results location and the corresponding Profiled data view. Use a non-sensitive example path.

## Recover after a token expires or HARP restarts

Jobs already accepted by Tapis continue running even when HARP loses its session. HARP pauses local tracking until the owner signs in again, then resumes polling and finalization from saved campaign state.

1. Start or reopen HARP.
2. Sign in with a current Tapis session.
3. Open **Jobs** and confirm the sweep resumes updating.
4. If the remote job already ended, allow HARP time to retrieve its final state and merge available results.

> **Screenshot slot — `docs/images/howto-resume-session.png`:** Show a paused or waiting-for-login sweep and the same sweep updating after login.

## Package your application for sweeps

1. Build and push the job container, starting from [`DockerFiles/Dockerfile_App_Template_Sweep`](DockerFiles/Dockerfile_App_Template_Sweep) (the Euler example is [`Dockerfile_App_EulerNumber_Sweep`](DockerFiles/Dockerfile_App_EulerNumber_Sweep)):
   ```bash
   docker build -f DockerFiles/Dockerfile_App_EulerNumber_Sweep -t <hub>/harp-sweep-eulernumber:1.0.0 .
   docker push <hub>/harp-sweep-eulernumber:1.0.0
   ```
   Make the image public so the clusters can pull it.
2. Register (or update) the Tapis app with [`Notebooks/Create_HARP_Sweep_App_TAPIS.ipynb`](Notebooks/Create_HARP_Sweep_App_TAPIS.ipynb). It can also run one small test job.

Inside each job, [`job_runner/harp_job_runner.py`](job_runner/harp_job_runner.py) runs its parameter combinations x repetitions, measures walltime and hardware, and writes `harp_profile.csv` and `harp_progress.json` to the Tapis output directory.

> **Screenshot slot — `docs/images/howto-register-sweep-app.png`:** Show the created Tapis app's image, runtime, command metadata, and `notes.harp` parameter definition. Do not include credentials.

## Register the build app (Tapis model training)

[`DockerFiles/Dockerfile_HARP_Build`](DockerFiles/Dockerfile_HARP_Build) packages the pipeline and [`job_runner/harp_build_runner.py`](job_runner/harp_build_runner.py). Push it as `harp-build:1.0.0` and register it with `Create_HARP_Sweep_App_TAPIS.ipynb` using `APP = "build"`; its notes carry `role: "build"`, which is how the UI finds it. Without it, builds can still run on the computer hosting the server.

## Run HARP UI as a shared HTTPS server

The server receives Tapis access tokens, so anything other than `localhost` must use HTTPS.

```bash
pip install -r harp_server/requirements.txt
cd harp_server
./scripts/make_dev_cert.sh certs <server-hostname>      # testing only: self-signed
HARP_SSL_CERT=certs/cert.pem HARP_SSL_KEY=certs/key.pem python3 -m harp_server.serve
```

Open `https://<server-hostname>:8443`. For real use, point `HARP_SSL_CERT` / `HARP_SSL_KEY` at an institutional or Let's Encrypt certificate. Behind nginx, Caddy or Apache, run `HARP_BEHIND_PROXY=1 HARP_PORT=8000 python3 -m harp_server.serve` and forward with an `X-Forwarded-Proto` header.

| Variable | Purpose | Default |
|---|---|---|
| `HARP_SSL_CERT`, `HARP_SSL_KEY` | Certificate and key (PEM) | required unless behind a proxy |
| `HARP_HOST`, `HARP_PORT` | Listen address | `0.0.0.0:8443` |
| `HARP_BEHIND_PROXY` | Serve plain HTTP and trust the proxy's forwarded headers | off |
| `HARP_SERVER_DATA` | Campaign and build state directory | `~/.harp_server` |
| `HARP_POLL_SECONDS` | Tapis polling interval | `15` |
| `HARP_TAPIS_BASE_URL` | Tenant used to validate tokens | `https://icicleai.tapis.io` |
| `HARP_PORTAL_ORIGINS` | Exact TapisUI origins allowed to frame HARP and hand off tokens | unset (framing disabled) |
| `HARP_SESSION_SAME_SITE` | Session cookie policy (`strict`, `lax`, `none`; `none` for the iframe) | |
| `HARP_ALLOW_HTTP` | Accept logins over plain HTTP (never on a shared server) | off |
| `HARP_OSC_QUEUE_PROBE` | Refresh live OSC Slurm partitions over noninteractive SSH | off |
| `HARP_OSC_SSH_USER` | OSC username used only by the live partition probe | unset |
| `HARP_OSC_SSH_KEY`, `HARP_OSC_SSH_KNOWN_HOSTS` | Mounted SSH key and pinned host-key files | unset |
| `HARP_QUEUE_REFRESH_SECONDS` | Live partition refresh interval | `86400` (24 hours) |
| `HARP_QUEUE_MAX_AGE_SECONDS` | Stop trusting a live snapshot after this age | `172800` (48 hours) |

## Deploy as an ICICLE Tapis Pod (TapisUI)

Build and publish the server image ([`harp_server/Dockerfile`](harp_server/Dockerfile)), create the `harpdata` volume, and register the Pod template and Pod with [`deploy/deploy_pod.py`](deploy/deploy_pod.py):

```bash
docker build -f harp_server/Dockerfile -t ghcr.io/icicle-ai/harp-ui:2.0.0 .
docker push ghcr.io/icicle-ai/harp-ui:2.0.0

export TAPIS_BASE_URL=https://icicleai.tapis.io
export TAPIS_ACCESS_TOKEN='<token>'
export HARP_IMAGE=ghcr.io/icicle-ai/harp-ui:2.0.0
python deploy/deploy_pod.py
curl https://harp.pods.icicleai.tapis.io/healthz      # {"status":"ok"}
```

Full steps, portal settings and operational constraints (run a single replica) are in [`deploy/README.md`](deploy/README.md).

When configured, HARP runs `sinfo` on Ascend, Pitzer, and Cardinal at Pod startup and once every 24 hours. A successful snapshot remains usable for up to 48 hours, allowing one temporary probe failure without immediately hiding queue validation. Live Slurm output—not OSC's documentation—is used to validate each TAPIS `hpcQueueName`. The probe requires a dedicated Pod-mounted SSH identity and invokes only this fixed read-only command; HARP cannot derive SSH credentials from a TAPIS access token and never persists that token.

> **Screenshot slot — `docs/images/howto-pod-deployment.png`:** Show the HARP Pod or template in the Tapis Pods interface with a healthy status and the approved image reference. Redact tenant administration details.

## Run the tests

```bash
cd harp_server && python3 -m pytest -q tests
```

The tests use an in-memory fake of Tapis ([`tests/fake_tapis.py`](harp_server/tests/fake_tapis.py)); the job runner is tested by really running the Euler example.

## Rebuild the offline demo

After changing [`profiling.html`](profiling.html), regenerate the demo copy with `python3 DEMO/ui/build_demo.py`.

---

# Explanation

## Why HARP

Each HPC workflow has its own compute, memory and I/O profile, and users routinely over- or under-request resources because they lack a history of runs to go by. HARP generates that history systematically, with many "scaled-down" (SD) runs, a few selective "full-scale" (FS) runs and held-out test_data runs, then trains regression models to estimate runtime for a target application on a given system and queue.

> **Diagram slot — `docs/images/explanation-harp-pipeline.png`:** Show the current Generate → Build → Estimate data flow, including Tapis Systems, Apps, Jobs, Files, campaign storage, profiling CSVs, model artifacts, and estimates. Replace this callout with the maintained architecture diagram.

## From notebooks to the UI

Earlier versions ran the whole Cheetah campaign inside **one** container, so one Tapis job ran every sweep combination on one machine, driven from a notebook or an installed module. HARP UI keeps the same pipeline and data format but changes how the generate phase runs:

```
 Browser UI ──► HARP server ──► Tapis Jobs ──► job on system A ─┐
  (Generate,     expand combos,   (one job      job on system B ─┼─► harp_profile.csv per job
   Build,        split into jobs,  per chunk)   job on system C ─┘
   Estimate)     throttle, poll  ◄── Tapis Files: merge ──► <storage>/<campaign>/<app>_<timestamp>.csv
```

- **Split** spreads jobs round-robin over the selected systems; **replicate** runs every job on every system to profile across hardware.
- Each job's hardware comes from its queue (cores, memory, walltime) and is checked against the queue's Tapis limits before submission.
- The server respects each system's concurrent-job limit, reads live progress through Tapis Files, and merges CSVs when every job ends. Failed runs go to `harp_failures.csv`, so they never pollute training data.
- The merged CSV keeps the pipeline's columns (`run_config, run_type, sys_*, run_<param>, walltime`), so the existing build modules (`pipeline/modules/pipeline.py`: preprocessing with outlier removal, scaling and PCA, then model training) consume it unchanged.

## The three tabs

| Tab | Steps |
|---|---|
| **HARP Generate** | Configure sweeps → Review & submit → Profiled data → Jobs |
| **HARP Build** | Training data → Models & where → Trained models |
| **HARP Estimate** | Configure → Select model → Estimates |

The **?** buttons in the UI explain each term: SD / FS / test_data runs, sweeps and campaigns, models, training sets, and how models are scored (average error and how often a model predicts too low on test_data runs).

## Security and multi-user isolation

- HARP accepts Tapis access tokens only, never usernames or passwords. The token stays on the server; the browser gets an HttpOnly session cookie that JavaScript cannot read.
- Inside TapisUI, the iframe receives the token from its exact parent origin via `postMessage`. Tokens are never placed in URLs or browser storage.
- Every sweep, job, dataset and build belongs to the Tapis user **and** tenant that created it, and is read through that user's own token.
- When a token expires, running sweeps pause (`WAITING_FOR_LOGIN`) and resume once the user signs in again. Campaign state is stored as JSON, so it survives server restarts.

## Repository layout

| Path | Contents |
|---|---|
| [`profiling.html`](profiling.html) | The single-page UI |
| [`harp_server/`](harp_server/) | FastAPI server: plan, sweep, campaign tracking, Tapis gateway, builds, tests |
| [`job_runner/`](job_runner/) | Code that runs inside profiling and build jobs |
| [`pipeline/`](pipeline/) | HARP data preprocessing and model training modules |
| [`DockerFiles/`](DockerFiles/) | Sweep, build and legacy HARP container definitions |
| [`deploy/`](deploy/) | Tapis Pods templates and deployment script |
| [`Notebooks/`](Notebooks/) | Tapis system/app registration notebooks |
| [`examples/`](examples/) | Euler number and YOLO target applications |
| [`DEMO/`](DEMO/) | Offline UI demo and screenshots |
| [`cheetah/`](cheetah/), [`install-script-source/`](install-script-source/), [`Post_Execution_Scripts/`](Post_Execution_Scripts/) | Used by the legacy container and module workflows |

## Supported systems

HARP UI runs jobs on any Tapis-registered execution system you have credentials and an allocation for. It has been used with OSC (Pitzer, Owens) and TACC systems, on CPU and GPU queues (GPU queues get `--nv` automatically).
