# HARP sweep server — the generate phase as many TAPIS jobs

The original generate phase runs a whole Cheetah campaign inside **one**
container, so one TAPIS job runs every sweep combination on one machine.
The sweep server splits the sweep into **many TAPIS jobs**, spreads them across
several execution systems (different hardware), follows them until they all end,
and merges the profiling CSVs into one file in a storage location you choose in
the UI. The server reaches every system and file through TAPIS
(Systems, Files, Apps and Jobs). It never touches a cluster file system
directly.

```
 Browser UI ──► HARP sweep server ──► TAPIS Jobs ──► job on system A ─┐
   (login,        expand combos,        (one job       job on system B ─┼─► harp_profile.csv
    sweep form,   split into jobs,       per chunk)    job on system C ─┘   archived by TAPIS to
    storage       throttle per system,                                      <storage>/<campaign>/jobs/<job>/
    picker)       poll status          ◄── TAPIS Files: merge all CSVs ──► <storage>/<campaign>/<app>_<timestamp>.csv
```

## Pieces

| Path | What it does |
|---|---|
| `harp_server/plan.py` | Turns the profiling page's plan into sweeps. Reads each app's command and parameters from its TAPIS notes and resolves each selected hardware configuration. Every profiling job uses 1 node and defaults to 1 core, 4 GB per requested core, and runs × timeout + 10 minutes, capped by the Tapis logical-queue limits. GPU-labelled queues request one GPU and add `--nv`; per-queue concurrency is shared between configurations. |
| `harp_server/sweep.py` | Validates the spec, expands every parameter combination (SD / FS / test_data) and splits it into jobs. **split** spreads the jobs round-robin over the systems (by weight). **replicate** runs every job on every system so you can profile across hardware. |
| `harp_server/campaign.py` | Submits jobs without exceeding each system's *max concurrent jobs*, polls TAPIS for job status, reads each running job's progress through TAPIS Files, merges the CSVs when every job has ended, and lets you cancel a sweep. Each campaign's state is saved as a JSON file, so it survives a server restart. |
| `harp_server/tapis_gateway.py` | The only code that talks to TAPIS (tapipy): login, systems and queues, apps, files, jobs. |
| `harp_server/queue_catalog.py` | Loads and persists the optional daily OSC Slurm probe for Ascend, Pitzer, and Cardinal and classifies Tapis queue-to-partition mappings without modifying Tapis. |
| `harp_server/app.py` | The REST API, and serves `profiling.html` (the UI) at `/`. |
| `../job_runner/harp_job_runner.py` | Runs inside each job's container. It executes its combinations × repetitions, measures walltime and hardware, and writes `harp_profile.csv` to the TAPIS output directory. |

The merged CSV has the same columns as `Post_Execution_Scripts/basic/DataScrapper.py`
(`run_config, run_type, sys_*, run_<param>, walltime`). It is named `<application>_<timestamp>.csv`,
which is the name the existing **build** phase looks for. Runs that fail go to
`harp_failures.csv` instead, so they never pollute the training data. A
`manifest.json` lists every job with its TAPIS UUID, system and status.

## Setup

1. **Build and push the job container** for your application (from the repo root):
   ```bash
   docker build -f DockerFiles/Dockerfile_App_EulerNumber_Sweep -t <hub>/harp-sweep-eulernumber:1.0.0 .
   docker push <hub>/harp-sweep-eulernumber:1.0.0
   ```
   For your own application, start from `DockerFiles/Dockerfile_App_Template_Sweep`.
2. **Register the TAPIS app** with `Notebooks/Create_HARP_Sweep_App_TAPIS.ipynb` (tapipy). It
   creates the app, or updates it if it already exists, and can run one small test job. The GitHub
   workflow `.github/workflows/build-sweep-image.yml` builds and pushes the image to
   `ghcr.io/<owner>/harp-sweep-eulernumber:1.0.0`, so you don't need Docker locally. Make that
   package public once so the clusters can pull it.
3. **Register the systems and credentials** as in `Notebooks/Executing_HARP_using_TAPIS*.ipynb`.
4. **Just for you, on your laptop:** you need Python 3.11 or 3.12 and this repository. On macOS or Linux:
   ```bash
   ./harp_server/scripts/run_local.sh      # first run installs fastapi, uvicorn and tapipy into harp_server/.venv
   ```
   Open `http://localhost:8000`. It listens on this machine only, so plain http is fine. Keep the laptop awake
   while sweeps run (e.g. `caffeinate -i ./harp_server/scripts/run_local.sh` on a Mac); if it sleeps or you stop
   the server, the jobs already in TAPIS keep running, and after you start it again and log in, the server
   picks the sweeps back up. State is kept in `~/.harp_server`.

   On Windows PowerShell, create `harp_server/.venv`, install both `requirements.txt` and
   `requirements-build.txt`, then run `python -m uvicorn harp_server.app:app --host 127.0.0.1 --port 8000`
   from the `harp_server` directory. The root [README](../README.md#tutorial-run-harp-ui-on-your-computer)
   contains the exact commands.

5. **For other people, run the server over HTTPS** (it receives TAPIS access tokens, so it must use HTTPS):
   ```bash
   pip install -r harp_server/requirements.txt
   cd harp_server
   # testing only: a self-signed certificate (browsers will show a warning)
   ./scripts/make_dev_cert.sh certs <server-hostname>
   HARP_SSL_CERT=certs/cert.pem HARP_SSL_KEY=certs/key.pem python3 -m harp_server.serve
   ```
   Open `https://<server-hostname>:8443`. For a server other people use, get a real
   certificate (from your institution or Let's Encrypt) and point `HARP_SSL_CERT` / `HARP_SSL_KEY`
   at it. If HTTPS is handled by nginx, Caddy or Apache in front of the server, run
   `HARP_BEHIND_PROXY=1 HARP_PORT=8000 python3 -m harp_server.serve` and have the proxy forward
   to port 8000 with an `X-Forwarded-Proto` header.

   The server refuses logins over plain `http://` except from `localhost` (for development).
   Responses carry HSTS, a strict Content-Security-Policy and no-store caching for the API.

   Environment variables:
   - `HARP_SSL_CERT`, `HARP_SSL_KEY`: certificate and key (PEM).
   - `HARP_HOST`, `HARP_PORT`: address to listen on. Default `0.0.0.0:8443`.
   - `HARP_ALLOW_HTTP=1`: accept logins over plain HTTP. Never use this on a shared server.
   - `HARP_SERVER_DATA`: where campaign state is kept. Default `~/.harp_server`.
   - `HARP_POLL_SECONDS`: how often the server polls TAPIS. Default `15`.
   - `HARP_TAPIS_BASE_URL`: tenant used for TapisUI token handoff. Default `https://icicleai.tapis.io`.
   - `HARP_PORTAL_ORIGINS`: exact comma-separated TapisUI origins allowed to frame HARP and hand off a token.
     When unset, framing remains disabled.
   - `HARP_SESSION_SAME_SITE`: session-cookie policy (`strict`, `lax` or `none`). Use `none` for the hosted iframe.
   - `HARP_OSC_QUEUE_PROBE`: enable the independent OSC Slurm partition probe.
   - `HARP_OSC_SSH_USER`, `HARP_OSC_SSH_KEY`, `HARP_OSC_SSH_KNOWN_HOSTS`: dedicated noninteractive probe identity and pinned host keys.
   - `HARP_QUEUE_REFRESH_SECONDS`: probe interval; default `86400` (once per day).
   - `HARP_QUEUE_MAX_AGE_SECONDS`: stop trusting a persisted snapshot after this age; default `172800`.
   - `HARP_QUEUE_PROBE_TIMEOUT_SECONDS`: timeout for each fixed, read-only `sinfo` invocation.

   To deploy this server as an ICICLE Tapis Pod, including its persistent volume and TapisUI authentication
   handshake, follow [`deploy/README.md`](../deploy/README.md).

## Using the UI

Open `https://<server-hostname>:8443/` (or `http://localhost:8000` with `scripts/run_local.sh`). The whole UI is one
page, `profiling.html` (repository root), with three main tabs, each with its own steps:

| Main tab | Sub-tabs |
|---|---|
| **HARP Generate** | 1 Configure: pick a Tapis app, set up SD / FS / test_data sweeps, tick available systems and queues, request cores/memory/time for the one node used by each job, and enter each system's scheduler options (for example `-A PAS2271`). Unavailable systems and queues are hidden unless **Show unavailable** is enabled. **Add to plan** warns before continuing when a selected system has no allocation. 2 Review & submit: validate every sweep, choose the archive/results location, confirm, and submit the plan all-or-nothing. 3 Profiled data: inspect and export successful timing rows. 4 Jobs: poll profiling and build jobs, inspect concise failure categories and sanitized raw details, and cancel a sweep. |
| **HARP Build** | 1 Training data: pick the app and the sweeps to learn from (data checks folded away). 2 Models & where: models and training sets, where the build runs (a TAPIS system as a job of the HARP build app, or this computer) and where the models are saved. 3 Trained models: every build with its best model and scores. |
| **HARP Estimate** | 1 Configure: pick an app, give one value per parameter (one run), tick the queues and cores it would get, add it to the estimate list (the model's independent examples). 2 Select model: per app, its trained models best first; the best is preselected. 3 Estimates: apply the selected saved models and compare predicted run times. A target partition must have successful profiling data so HARP can supply its measured hardware features. |

At startup the independent Apps, Systems, Campaigns, and Jobs requests run together. System access badges transition from **CHECKING** to **AVAILABLE** or **UNAVAILABLE** in the background, and Build destinations populate from inventory without waiting for profile CSV downloads. Short-lived client/server caches and in-flight request reuse prevent repeated tab changes from re-downloading the same data. The login screen returns when the Tapis session expires.

The **?** buttons explain the terms: SD (scaled-down), FS (full-scale) and test_data runs; sweeps and campaigns (one
submitted sweep is one campaign: the server's record of its TAPIS jobs and its results folder); the models; the
training sets; where a build runs; how models are scored.

**Each user sees only their own work.** Sweeps, jobs, profiled data and builds belong to the TAPIS user *and*
tenant that created them (`alice@icicleai.tapis.io`); everything is read through that user's own TAPIS token, and the
plan and allocations the page keeps in the browser are kept per user too.

For direct, standalone use, paste a current TAPIS access token (JWT) for your tenant. HARP validates it with TAPIS,
keeps it on the server, immediately clears the form, and gives the browser only a session cookie that JavaScript
cannot read. HARP does not accept TAPIS usernames or passwords. When the token expires, running sweeps pause
(`WAITING_FOR_LOGIN`) and resume as soon as you provide a fresh token.

When HARP is opened inside the ICICLE TapisUI, there is no second login form: the iframe requests the access token
from its exact parent origin with `postMessage`, the server validates it through `tapipy`, and then creates the same
HttpOnly session. The token is never put in the iframe URL or browser storage. The portal handshake runs before the
fallback form is displayed, so the token screen normally never appears inside TapisUI.

**Runs done while a job runs.** The job runner rewrites `harp_progress.json` (runs done, failed, total,
current run) in the job's output folder after every run. While a job is RUNNING, the server asks TAPIS for the
job's `execSystemOutputDir` (`getJob`) and reads that file through TAPIS Files on the execution system. When the
job ends it reads the archived `harp_job_summary.json` for the final count. The runner also rewrites
`harp_profile.csv` after every run, so runs that finished are kept even if the job hits its time limit.

### Build an estimator (the build phase, from the Profiled data tab)

1. **Sweeps to learn from.** Tick the app's sweeps. Training needs SD, FS and test_data runs (at least 2, 4 and 1).
   The card says whether there is enough; **Data checks** shows how the data was cleaned: the server writes the
   sweeps' rows in the format the pipeline reads (`run_config, run_type, sys_*, run_<param>, walltime`), adds what each
   job was given (`sys_alloc_cores`, `sys_alloc_mem_mb`), turns true/false into 1/0, and leaves out runs without a time
   and columns not filled in every run. **Download training CSV** gives you the file.
2. **Models to build.** Any of LR (linear regression), NN (neural network) and DTR (decision tree), trained on any of
   SD only, SD+25FS, SD+50FS, SD+75FS. Each comes padded and unpadded.
3. **Where.** Run the build as a **TAPIS job** of the HARP build app on a system/queue you pick (1 node, 4 cores,
   up to 60 minutes, capped by the queue; your allocation from Review is used), or on **this computer**. Pick the
   TAPIS folder for the models.

Either way `job_runner/harp_build_runner.py` runs the pipeline's own build modules (`pipeline/modules/pipeline.py`:
`data_preprocessor` = outliers, scaling, PCA; `model_trainer`), and the results land in
`<folder>/builds/<app>_<timestamp>/`: the training CSV, `full_dataset_pca.csv`, `model_commons.csv` (every score),
`models/<model>_<training set>_<padded yes/no>_<time>.pkl|.h5` and `harp_build_summary.json`. As a TAPIS job the
server stages the training CSV as a file input (`<folder>/builds/.../input/`), TAPIS archives the job's output folder
there, and the job shows in **Jobs** as a build job. **Trained models** lists each build with its best model and
scores (average error and how often it predicts too low, on the test_data runs).

**The build app.** `DockerFiles/Dockerfile_HARP_Build` (pipeline + runner, pandas < 3, scikit-learn, TensorFlow CPU)
is built and pushed by the GitHub workflow as `ghcr.io/<owner>/harp-build:1.0.0`. Register it with
`Notebooks/Create_HARP_Sweep_App_TAPIS.ipynb` and `APP = "build"`; its notes carry `role: "build"`, which is how the
server finds it (it is not listed as an app to profile). For builds on this computer the server needs
`requirements-build.txt`; `scripts/run_local.sh` installs it.

### API

| Route | Used for |
|---|---|
| `POST /api/login`, `POST /api/logout`, `GET /api/me` | Session (TAPIS token kept on the server). |
| `GET /api/config`, `GET /api/health`, `GET /healthz` | Public portal bootstrap settings and Pod health checks. |
| `GET /api/tapis/apps` | Your TAPIS apps with their sweep parameters (from the app notes). |
| `GET /api/tapis/inventory` | All visible systems plus execution systems from one Tapis request. |
| `GET /api/tapis/exec-systems` | Systems you can run on, with their queues and limits (`batchLogicalQueues`). |
| `GET /api/tapis/systems` | Every system, for the results folder (storage-only systems too). |
| `GET /api/tapis/systems/<id>/access` | Fresh read-only Tapis Files access check used by the UI status badge. |
| `GET /api/tapis/queue-probe` | Health and timestamps for the independent OSC Slurm probe, without credentials. |
| `POST /api/plan/preview` | Check a plan: per sweep, its summary and each job's resolved hardware, or why it is refused. |
| `POST /api/plan/submit` | Submit a plan, all or nothing. |
| `GET /api/campaigns`, `POST /api/campaigns/<id>/cancel` | Sweeps and cancelling one. |
| `GET /api/jobs` | Every TAPIS job, with hardware and runs done. |
| `GET /api/apps/<id>/profile`, `GET /api/apps/<id>/profile.csv` | An app's profiled data, merged across its sweeps. |
| `POST /api/apps/<id>/training-data`, `GET /api/apps/<id>/training-data.csv?sweeps=a,b` | Pool and standardize sweeps for the build phase. |
| `GET /api/build-options` | The models and training sets HARP can build, and the TAPIS build app if registered. |
| `POST /api/apps/<id>/builds`, `GET /api/builds?app_id=` | Build models from those sweeps (`models`, `training_sets`, `run_on`: a TAPIS system/queue or this computer); builds with their status and scores. |
| `POST /api/estimates` | Apply selected completed models to app parameters and one or more system/queue resource targets. |

A plan looks like:

```json
{"storage": {"system_id": "pitzer", "path": "/fs/scratch/PAS2271/harp_runs"},
 "allocations": {"pitzer": "-A PAS2271"},
 "sweeps": [{"app_id": "harp-sweep-euler", "name": "sd", "run_type": "SD", "repetitions": 3, "timeout_min": 20,
             "params": {"method": ["pow", "factorial"], "n": ["10", "100"]},
             "targets": [{"system_id": "pitzer", "queue": "serial"},
                         {"system_id": "pitzer", "queue": "serial", "cores": 8}]}]}
```
Leave `cores`, `memory_mb` or `max_minutes` out to use the defaults from the queue.

`DEMO/ui/` has an offline copy of the page with sample data (it mirrors these rules in JavaScript).

## Tests

```bash
python3 -m pytest harp_server/tests -q
```
The tests use an in-memory fake of TAPIS (`tests/fake_tapis.py`). The job runner is tested by
really running the Euler example. The suite also runs the local model-training pipeline, applies a saved model through
the Estimate API, validates remote Tapis build requests (including GPU queues), and checks transient polling recovery.
