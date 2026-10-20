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
publishes `ghcr.io/icicle-ai/harp-ui:<release-version>`. The workflow can also be dispatched
manually for a release candidate. Pull requests build and test the image but
do not push it.

The official public image is `ghcr.io/icicle-ai/harp-ui:2.0.0` ([package
page](https://github.com/ICICLE-ai/HARP-UI/pkgs/container/harp-ui)). Its published
digest is `sha256:512660d0d1eb856edbea0c91929b774c62d99b26ebcc2bdac8985e4c3d3fa588`.
The image namespace is allowlisted for the `icicleai` tenant. The original
`harp:2.0.0` template remains available for rollback because it references the
legacy image; new deployments use `harp:2.0.0-icicle.1`.

From the repository root:

```bash
docker build -f harp_server/Dockerfile -t ghcr.io/icicle-ai/harp-ui:<version> .
docker push ghcr.io/icicle-ai/harp-ui:<version>
```

Make the GHCR package public/readable by the Pods cluster. A Pods administrator
must also add the exact image reference to the tenant's `/pods/images`
allowlist before the template tag can be registered. Pass that image to the
deployment script; no registry credential is stored in the Pod template.

## 2. Create persistent storage

Campaign and build records are JSON files under `HARP_SERVER_DATA`. A volume is
required so they survive Pod replacement:

```python
t.pods.create_volume(
    volume_id="harpdata",
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
- `template-tag-harp-2.0.0.json` is the original immutable template revision
  backed by the pre-transfer image and is retained for rollback.
- `template-tag-harp-2.0.0-icicle.1.json` adds the current immutable revision
  `harp:2.0.0-icicle.1`, backed by `ghcr.io/icicle-ai/harp-ui:2.0.0`.
- `pod-harp-from-template.json` creates the small `harp` instance from that
  versioned template and supplies its concrete `harpdata` volume ID. The
  reusable template keeps the volume source as a required placeholder because
  Tapis Pods does not permit literal tenant volume IDs in template tags.

By default, the deployment helper creates missing resources but refuses to
replace an existing Pod. From the repository root:

```bash
export TAPIS_BASE_URL=https://icicleai.tapis.io
export TAPIS_ACCESS_TOKEN='<token>'
export HARP_IMAGE=ghcr.io/icicle-ai/harp-ui:2.0.0
export HARP_TEMPLATE_TAG=2.0.0-icicle.1
python deploy/deploy_pod.py
```

The Pods update API cannot change the image or template of an existing Pod. To
migrate the existing `harp` Pod, opt in to a brief stop/delete/recreate cycle:

```bash
python deploy/deploy_pod.py --replace-existing-pod
```

The helper verifies that `/var/lib/harp` is backed by `harpdata` before
replacement. It deletes only the Pod, never the persistent volume, then
recreates Pod ID `harp` from the new template. The endpoint therefore remains
`https://harp.pods.icicleai.tapis.io`. Any Pod-specific permissions must be
reapplied after recreation.

PowerShell equivalent:

```powershell
$env:TAPIS_BASE_URL = "https://icicleai.tapis.io"
$env:TAPIS_ACCESS_TOKEN = "<token>"
$env:HARP_IMAGE = "ghcr.io/icicle-ai/harp-ui:2.0.0"
$env:HARP_TEMPLATE_TAG = "2.0.0-icicle.1"
python deploy/deploy_pod.py --replace-existing-pod
```

Username/password authentication is also supported:

```bash
export TAPIS_USERNAME='<username>'
export TAPIS_PASSWORD='<password>'
python deploy/deploy_pod.py \
  --image ghcr.io/icicle-ai/harp-ui:2.0.0 \
  --template-tag 2.0.0-icicle.1 \
  --replace-existing-pod
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
