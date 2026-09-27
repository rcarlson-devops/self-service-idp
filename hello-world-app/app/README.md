# hello-world

Go HTTP service used as the sample app for Phase 1 of the internal developer
platform build. The app itself still isn't the point, but it's now built the
way a real platform-hosted service would be: structured logging, fail-fast
config validation, and — critically — a readiness/liveness split with a real
shutdown drain, so rolling deploys through Argo CD don't drop requests.

## Endpoints

- `GET /` — JSON payload with a message, hostname, version, environment, and timestamp
- `GET /version` — JSON `{version, environment}`, for scripts that just want to verify what's deployed
- `GET /healthz` — liveness: plain-text `ok`, essentially never fails while the process is alive
- `GET /readyz` — readiness: `ok` normally, `503` from the moment shutdown begins until the process exits
- `GET /internal/prestop` — used by Kubernetes' `preStop` hook only; not meant to be called directly

## Configuration

All via environment variables, all optional:

| Variable | Default | Notes |
|---|---|---|
| `PORT` | `8080` | |
| `ENVIRONMENT` | `unknown` | Must be `dev`, `staging`, `prod`, or unset — anything else fails startup |
| `SHUTDOWN_TIMEOUT_SECONDS` | `10` | Max time to let in-flight requests finish during shutdown; must be a positive integer |
| `PRESTOP_DELAY_SECONDS` | `0` | How long `/internal/prestop` sleeps before responding; `0` disables it |

Invalid values (a non-numeric timeout, an unrecognized environment) make the
app exit immediately with a clear error instead of starting up with settings
nobody intended — see `config_test.go` for every case this covers.

## Graceful shutdown & zero-downtime deploys

This is the part that actually matters once Argo CD starts doing rolling
updates, so it's worth spelling out the sequence when a pod is terminated:

1. Kubernetes marks the pod NotReady and calls the `preStop` hook
   (`GET /internal/prestop`) at the same time it starts propagating that
   removal to endpoints/ingress. The app is still fully up and `/readyz`
   still returns `ok` during this window.
2. `/internal/prestop` sleeps for `PRESTOP_DELAY_SECONDS`, giving routing
   time to actually update before traffic could be cut off mid-request.
3. Once `preStop` returns, Kubernetes sends `SIGTERM`. The app immediately
   flips `/readyz` to `503` (so anything still routing here backs off) and
   calls `http.Server.Shutdown`, which stops accepting new connections but
   lets in-flight ones finish, up to `SHUTDOWN_TIMEOUT_SECONDS`.
4. If requests haven't finished by then, the process exits anyway —
   Kubernetes will hard-kill it at `terminationGracePeriodSeconds` regardless.

The Helm chart's `terminationGracePeriodSeconds` is set to
`preStopSleepSeconds + shutdownTimeoutSeconds` plus a small buffer, so
Kubernetes never kills the pod mid-drain.

## Run locally

```bash
go run .
# in another terminal
curl localhost:8080/
curl localhost:8080/version
curl localhost:8080/healthz
curl localhost:8080/readyz
ENVIRONMENT=staging PORT=9090 go run .   # try it with real config
```

Press Ctrl+C to see the graceful shutdown sequence log to stdout.

## Run tests

```bash
go test ./... -v
go test ./... -cover     # coverage summary
```

Covers config validation (every valid/invalid env var combination),
handler behavior for all five endpoints, the readiness-drain transition,
that probe traffic is excluded from logs, and a full-server integration
test over a real listener.

## Build and run the container

```bash
docker build -t hello-world:1.0.0 .
docker run -p 8080:8080 hello-world:1.0.0
curl localhost:8080/
```

The build stage runs `go vet` and `go test` before compiling, so a broken
test fails `docker build` itself, not just CI. The image is a multi-stage,
`CGO_ENABLED=0` static binary on top of `distroless/static-debian12:nonroot`
— no shell, runs as non-root by default (satisfies the Kyverno "no root
containers" policy from Phase 4 with no extra work), which is also why the
`preStop` hook uses `httpGet` against the app itself rather than a shell
`sleep` command that wouldn't exist in this image.

## Push to a registry

Pick whichever registry your cluster/Argo CD setup can pull from (GitHub
Container Registry is a common free choice for a portfolio project):

```bash
docker tag hello-world:1.0.0 ghcr.io/<your-username>/hello-world:1.0.0
docker push ghcr.io/<your-username>/hello-world:1.0.0
```

Then update `charts/hello-world/values.yaml`'s `image.repository` (and each
`environments/<env>/values-<env>.yaml`'s `image.tag`) to point at your registry.

## Multi-environment layout (Helm)

```
hello-world-app/
  app/                              # Go source, Dockerfile, go.mod
  charts/hello-world/
    Chart.yaml
    values.yaml                     # defaults (dev-shaped)
    templates/
      deployment.yaml
      service.yaml
      _helpers.tpl
      NOTES.txt
    environments/
      dev/values-dev.yaml           # 1 replica, tracks `dev-latest`, low resources
      staging/values-staging.yaml   # 2 replicas, tracks `staging-latest`
      prod/values-prod.yaml         # 3 replicas, pinned to an explicit semver tag
argo/
  application-dev.yaml       # auto-sync, helm environments/dev/values-dev.yaml
  application-staging.yaml   # auto-sync, helm environments/staging/values-staging.yaml
  application-prod.yaml      # manual sync only — approval gate before prod
.github/workflows/
  build-and-push.yml         # build/test/push on every push to main
  promote.yml                 # manual retag-and-promote to staging/prod
```

One chart, one set of templates — everything that differs between
environments (replica count, resource limits, image tag, the `ENVIRONMENT`
env var, an `environment` label) lives in the three `values-*.yaml` files,
layered on top of `values.yaml`'s defaults. Preview what Argo CD will
actually apply for any environment with:

```bash
helm template hello-world ./charts/hello-world -f ./charts/hello-world/environments/dev/values-dev.yaml
helm lint ./charts/hello-world -f ./charts/hello-world/environments/dev/values-dev.yaml
```

Or install/upgrade directly against a cluster (useful before Argo CD is
wired up):

```bash
helm upgrade --install hello-world ./charts/hello-world \
  -f ./charts/hello-world/environments/dev/values-dev.yaml \
  --namespace hello-world-dev --create-namespace
```

Before committing, replace `ghcr.io/OWNER/hello-world` in `values.yaml` and
`https://github.com/OWNER/REPO.git` in the `argo/` files with your actual
registry/repo. Argo CD's Application manifests point at the same chart path
(`hello-world-app/charts/hello-world`) for all three environments and select
the environment purely via `spec.source.helm.valueFiles`.

## CI/CD: build once, promote everywhere

Two workflows in `.github/workflows/`:

- **`build-and-push.yml`** — on every push to `main`: runs `go vet`/`go test`,
  then builds and pushes the image tagged `sha-<short-sha>` (immutable) and
  `dev-latest`. The dev overlay tracks `dev-latest`, so dev updates
  automatically on every merge.
- **`promote.yml`** — a manual `workflow_dispatch` you trigger from the
  Actions tab. It **retags** an existing `sha-*` image as `staging-latest`
  or as a semver version for prod, using `docker buildx imagetools create`
  — no rebuild happens, so the bits that reach prod are exactly the bits
  that were tested in dev.

The promotion flow in practice:

1. Merge to `main` → `build-and-push.yml` produces `sha-abc1234` and updates
   dev automatically.
2. Verify it in dev, then run `promote` with `source_tag=sha-abc1234`,
   `target_environment=staging`.
3. `environments/staging/values-staging.yaml`'s `image.tag` is already `staging-latest`, so
   there's no Git diff for Argo CD to detect — this is the known trade-off
   of floating tags. `pullPolicy: Always` means the *next* time a pod
   starts it gets the new image, but nothing forces that to happen now, so
   trigger it explicitly:
   ```bash
   kubectl -n hello-world-staging rollout restart deployment/hello-world
   ```
   (In a more mature setup you'd replace this manual step with something
   like Argo CD Image Updater or switch staging to immutable `sha-*` tags
   the same way prod uses semver — floating tags are a reasonable shortcut
   for a portfolio project, but worth naming as a known limitation if asked
   about it in an interview.)
4. Once verified in staging, run `promote` again with
   `target_environment=prod` and a `version` like `1.0.0`.
5. Edit `charts/hello-world/environments/prod/values-prod.yaml`'s `image.tag` to `"1.0.0"`,
   commit, push. Because the prod `Application` has no `automated:` sync
   policy, it shows as **OutOfSync** in the Argo CD UI until someone
   deliberately clicks **Sync** — a manual approval gate before prod.

## Deploy via Argo CD

1. Push this whole directory to the Git repo Argo CD watches.
2. Apply the three `Application` manifests in `argo/` (or add them to
   Argo CD's own App-of-Apps if you're using that pattern).
3. Confirm: editing and pushing a change to `charts/hello-world/environments/dev/values-dev.yaml`
   (e.g., bumping `replicaCount`) results in an automatic sync to the
   `hello-world-dev` namespace with no manual `kubectl apply` or `helm
   upgrade`.

That last step is the Phase 1 exit criteria — capture a screenshot of the
Argo CD sync once it happens; you'll want it for the README/portfolio later.
The promotion flow above is worth its own screenshot too — it's a strong
answer to "how do you handle releases across environments?" in an interview.
