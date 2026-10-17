# Deploy HARP with Tapis Pods

This deployment runs the HARP FastAPI server as a long-lived Tapis Pod at
`https://harp.pods.icicleai.tapis.io`. TapisUI embeds that URL and hands the
signed-in user's access token to HARP with an origin-checked `postMessage`
exchange. HARP validates the token with TAPIS and keeps it server-side; the
browser receives only an HttpOnly session cookie.

The hosted application remains the existing `profiling.html` interface. It
does not need to be rewritten as React: React is used only by the small
TapisUI extension component that owns the iframe and token handoff.

## 1. Build and publish the server image

Tapis Pods deploys an OCI/Docker image; it does not deploy source code or a
GitHub Release directly. Publishing a GitHub Release such as `v2.0.0` runs
`.github/workflows/build-server-image.yml`, which builds, smoke-tests, and
publishes `ghcr.io/manikyaswathi/harp-ui:2.0.0`. The workflow can also be dispatched
manually for a release candidate. Pull requests build and test the image but
do not push it.

The initial image owner is `manikyaswathi`, matching this repository's current
GitHub owner. If HARP moves to the ICICLE organization, publish the image as
`ghcr.io/icicle-ai/harp-ui:<version>` and update the Pod image; the Pod ID,
volume, endpoint, and TapisUI route do not need to change.

From the repository root:

```bash
docker build -f harp_server/Dockerfile -t ghcr.io/manikyaswathi/harp-ui:2.0.0 .
docker push ghcr.io/manikyaswathi/harp-ui:2.0.0
```

Make the GHCR package public/readable by the Pods cluster. Pass that image to
the deployment script; no registry credential is stored in the Pod template.

## 2. Create persistent storage

Campaign and build records are JSON files under `HARP_SERVER_DATA`. A volume is
required so they survive Pod replacement:

```python
t.pods.create_volume(
    volume_id="harp-data",
    description="Persistent campaign and build state for HARP",
    size_limit=1024,
)
```

The manifest mounts this volume at `/var/lib/harp`. Access tokens are never
written there; they remain in server memory and users authenticate again after
a Pod restart.

## 3. Register the reusable template and create the Pod

The deployment resources are split along the same boundary as the Pods API:

- `template-harp.json` creates the shared `harp` template.
- `template-tag-harp-2.0.0.json` adds immutable version `harp:2.0.0` with the
  image, runtime configuration, volume mount, networking, and resource limits.
- `pod-harp-from-template.json` creates the small `harp` instance from that
  versioned template.

The additive deployment helper creates missing resources and never replaces or
deletes existing ones. From the repository root:

```bash
export TAPIS_BASE_URL=https://icicleai.tapis.io
export TAPIS_ACCESS_TOKEN='<token>'
export HARP_IMAGE=ghcr.io/manikyaswathi/harp-ui:2.0.0
python deploy/deploy_pod.py
```

Username/password authentication is also supported:

```bash
export TAPIS_USERNAME='<username>'
export TAPIS_PASSWORD='<password>'
python deploy/deploy_pod.py --image ghcr.io/manikyaswathi/harp-ui:2.0.0
```

Credentials are read from the process environment and are not written to a
manifest. `pod-harp.json` remains available as the equivalent direct,
non-template Pod definition for troubleshooting.

Pods terminates HTTPS and forwards traffic to port 8000. Do not enable the
Pods `tapis_auth` option for this integration: that gate authenticates access
to the URL but does not give the application the TAPIS access token it needs
for Apps, Systems, Jobs, and Files. The TapisUI handshake supplies that token.

Verify:

```bash
curl https://harp.pods.icicleai.tapis.io/healthz
# {"status":"ok"}
```

## 4. Portal configuration

Production settings in the manifest:

| Variable | Purpose |
|---|---|
| `HARP_TAPIS_BASE_URL` | Tenant used to validate the handed-off token. |
| `HARP_PORTAL_ORIGINS` | Exact comma-separated TapisUI origins allowed to frame HARP and exchange tokens. |
| `HARP_SESSION_SAME_SITE` | `none` permits the secure session cookie in an iframe. |
| `HARP_SERVER_DATA` | Mounted persistent-state directory. |
| `HARP_PROXY_IPS` | Proxies whose forwarded HTTPS headers Uvicorn trusts. |

The production template permits only `https://icicleai.tapis.io`. To exercise
the extension from the standard local TapisUI development server, create a
separate test Pod or temporarily override `HARP_PORTAL_ORIGINS` to include
`http://localhost:3000`; do not broaden the production template.

Without `HARP_PORTAL_ORIGINS`, HARP keeps `X-Frame-Options: DENY` and
`frame-ancestors 'none'`. This secure default preserves standalone behavior.

The matching TapisUI change belongs under
`packages/icicle-tapisui-extension/src/pages/HARP/`. It listens only to the
HARP Pod origin and responds only to the matching iframe window. Tokens are
never placed in a URL, query string, local storage, or iframe attribute.

## 5. TAPIS resources HARP expects

Portal authentication does not replace execution-system setup. Each user must
still have TAPIS credentials and permissions for the selected systems and
storage locations, plus a valid allocation account.

Profile applications need a `notes.harp` block defining their command,
working directory, and parameters. Register the shared HARP build app with
`role: "build"` so production model training runs as a TAPIS job rather than
inside the web Pod.

## Operational constraints

Run one HARP web-server replica. Sessions and TAPIS clients are held in memory,
and the background poller is not coordinated across replicas. Persistent JSON
state lets campaigns survive restarts; they enter `WAITING_FOR_LOGIN` until
their owner opens HARP again and supplies a fresh portal token.
