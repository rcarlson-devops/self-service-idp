# Service app

The Go source for this service. CI builds it into a container image, and the platform deploys it to Kubernetes through Argo CD. Day to day, this directory is the part you change.

## Layout

| Path | Purpose |
|---|---|
| `Dockerfile` | Builds the binary to `/out/app` and copies it to `/app/app` in the final image, which runs it as the entrypoint |
| `go.mod` | Go module definition |
| `*.go`, `*_test.go` | Application code and its tests |

## Run the tests

From this directory:

```sh
go test ./... -cover
```

## Build and run locally

From this directory:

```sh
docker build -t my-service:local .
docker run --rm -p 8080:8080 my-service:local
```

<!-- TODO: confirm the port the app listens on and change 8080 above if it differs. -->

## Configuration

<!-- TODO: list the environment variables the app reads (see config.go), with defaults. -->

## How it gets deployed

1. A push to `main` that changes the app triggers the build workflow in `.github/workflows/`.
2. The workflow runs the checks, builds the image, and pushes it to GitHub Container Registry tagged `sha-<short commit>`.
3. The workflow then commits that tag to `environments/dev/values-dev.yaml` in this repo.
4. Argo CD, which watches this repo, notices the change and rolls the new image out. Argo polls roughly every three minutes, so allow a short delay.

CI never talks to the cluster directly.

## What not to edit by hand

- `environments/dev/values-dev.yaml` is written by CI. Manual edits will be overwritten by the next build.
- Deployment settings (the Helm chart, the Argo CD Application, the database) are owned by the platform, not this repo. See the platform repo: https://github.com/rcarlson-devops/self-service-idp
