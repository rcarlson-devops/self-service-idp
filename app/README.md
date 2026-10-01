# hello-world

Go HTTP service used as the sample app for the internal developer platform
build. The app itself isn't the point, but it's built the way a real
platform-hosted service would be: structured logging, fail-fast config
validation, and a readiness/liveness split with a real shutdown drain, so
rolling deploys through Argo CD don't drop requests.

For how this service is built, delivered, and deployed (CI, Helm chart,
Argo CD, bootstrap), see the [root README](../README.md).

## Endpoints

- `GET /` : JSON payload with a message, hostname, version, environment, and timestamp
- `GET /version` : JSON `{version, environment}`, for scripts that just want to verify what's deployed
- `GET /healthz` : liveness: plain-text `ok`, essentially never fails while the process is alive
- `GET /readyz` : readiness: `ok` normally, `503` from the moment shutdown begins until the process exits
- `GET /internal/prestop` : used by Kubernetes' `preStop` hook only; not meant to be called directly

## Configuration

All via environment variables, all optional:

| Variable | Default | Notes |
|---|---|---|
| `PORT` | `8080` | |
| `ENVIRONMENT` | `unknown` | Must be `dev`, `staging`, `prod`, or unset; anything else fails startup |
| `SHUTDOWN_TIMEOUT_SECONDS` | `10` | Max time to let in-flight requests finish during shutdown; must be a positive integer |
| `PRESTOP_DELAY_SECONDS` | `0` | How long `/internal/prestop` sleeps before responding; `0` disables it |

Invalid values (a non-numeric timeout, an unrecognized environment) make the
app exit immediately with a clear error instead of starting up with settings
nobody intended. See `config_test.go` for the cases covered.

## Graceful shutdown and zero-downtime deploys

This matters once Argo CD does rolling updates. The sequence when a pod is
terminated:

1. Kubernetes marks the pod NotReady and calls the `preStop` hook
   (`GET /internal/prestop`) while it starts propagating that removal to
   endpoints and ingress. The app is still fully up and `/readyz` still
   returns `ok` during this window.
2. `/internal/prestop` sleeps for `PRESTOP_DELAY_SECONDS`, giving routing
   time to update before traffic could be cut off mid-request.
3. Once `preStop` returns, Kubernetes sends `SIGTERM`. The app immediately
   flips `/readyz` to `503` (so anything still routing here backs off) and
   calls `http.Server.Shutdown`, which stops accepting new connections but
   lets in-flight ones finish, up to `SHUTDOWN_TIMEOUT_SECONDS`.
4. If requests haven't finished by then, the process exits anyway.
   Kubernetes hard-kills it at `terminationGracePeriodSeconds` regardless.

The Helm chart sets `terminationGracePeriodSeconds` to cover the preStop
delay plus the shutdown timeout and a small buffer, so Kubernetes doesn't
kill the pod mid-drain.

## Run locally

```bash
cd app
go run .
# in another terminal
curl localhost:8080/
curl localhost:8080/version
curl localhost:8080/healthz
curl localhost:8080/readyz
ENVIRONMENT=dev PORT=9090 go run .   # try it with real config
```

Press Ctrl+C to see the graceful shutdown sequence log to stdout.

## Run tests

```bash
cd app
go test ./... -v
go test ./... -cover
```

Covers config validation, handler behavior for all five endpoints, the
readiness-drain transition, that probe traffic is excluded from logs, and a
full-server integration test over a real listener.

## Build and run the container

```bash
cd app
docker build -t hello-world:local .
docker run -p 8080:8080 hello-world:local
curl localhost:8080/
```

The build stage runs `go vet` and `go test` before compiling, so a broken
test fails `docker build` itself, not just CI. The image is a multi-stage,
`CGO_ENABLED=0` static binary on `distroless/static-debian12:nonroot`: no
shell, and it runs as non-root by default (which should satisfy the planned
Kyverno "no root containers" policy with no extra work). The lack of a shell
is also why the `preStop` hook uses `httpGet` against the app itself rather
than a shell `sleep`.

## Build and deploy

Images are built and pushed by GitHub Actions, and deployed by Argo CD from
the Helm chart in `charts/hello-world/`. Don't push images or edit tags by
hand; see the root README for the full flow.
