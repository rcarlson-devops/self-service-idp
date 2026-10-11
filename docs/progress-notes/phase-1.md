# Phase 1: Foundation

**Status:** Complete (2026-10-01). Notes revised 2026-10-07 and again 2026-10-10 so they match the current repo; where something changed after Phase 1, it says so.

**Exit criterion:** push to Git, and the app deploys automatically with no manual `kubectl apply`. Met.

## What was built

- A local k3d cluster (1 server, 2 agents, k3s v1.35.5) created by one script, `bootstrap/bootstrap.sh`
- Argo CD v3.5.3 installed from a pinned Kustomize base, with the UI exposed declaratively
- The app-of-apps pattern: `argocd/root.yaml` is the only manual `kubectl apply`; everything else is discovered from Git
- A Go hello-world service with a Helm chart, built and pushed to GHCR by GitHub Actions
- CI that commits an immutable `sha-<short>` tag back to `environments/dev/values-dev.yaml`, so Git records what is deployed. CI never touches the cluster.

In Phase 1 the service and its chart lived in this repo. Phase 3 moved service source into repos generated from a template; the shared chart stayed here.

## The loop

1. A developer pushes code under `app/`.
2. GitHub Actions runs `go vet` and `go test`, builds the image, and pushes `sha-<short>` to GHCR.
3. The workflow commits the new tag to `environments/dev/values-dev.yaml`.
4. Argo CD detects the Git change and rolls out the new image.

## Metrics

| Metric | Value | Notes |
|---|---|---|
| Full rebuild (cluster delete to bootstrap complete) | 1 m 22 s | `time ./bootstrap/bootstrap.sh`. This was before Crossplane and CloudNativePG existed. Later phases rebuild more, so the number is not comparable with them (see Phase 2 and the README) |
| CI duration (commit to bot tag commit) | ~65 s | From git timestamps; an upper bound. Generated service repos later measured 62 s to 118 s |
| Push to running pod, end to end | ~1 m 45 s | One sample. Argo CD polls Git on an interval and there is no webhook, so this varies from run to run; a later measurement of the polling wait is in the Phase 3 notes |

The end-to-end figure is a single sample, and it landed early in the poll cycle. Do not read it as typical.

## Evidence

**Argo CD showing the app Synced and Healthy**

![Argo CD first app](images/argocd-first-app-screenshot.png)

**Successful GitHub Actions run**

![GitHub Actions success](images/github-actions-successful-screenshot.png)

**Bot commit writing the image tag back to Git**

![Bot commit](images/bot-commit-screenshot.png)

## Problems hit and what they taught me

- **Hand-edited Service.** The Argo CD UI was reachable only because of a manual edit to the `argocd-server` Service. I found it through the `last-applied-configuration` annotation and moved it into the Kustomize patch, so a rebuilt cluster is reachable with no hand edits.
- **Reproducibility.** I proved the bootstrap by deleting the cluster and rebuilding from scratch.
- **SSH vs HTTPS repo URL.** A deploy-key-dependent SSH URL meant a fresh clone could not bootstrap. I switched to HTTPS on a public repo, so bootstrap needs no credentials.
- **Image tag default.** A never-published `dev-latest` default let a misconfiguration deploy silently. The chart now uses `required` for `image.tag`, so it fails at render time.
- **Server-side apply.** The ApplicationSet CRD is too large for client-side apply ("annotation too long"), so Argo CD is installed with server-side apply.

## What changed after Phase 1

- **Images are public now.** Phase 1 used private GHCR images with a pull secret created from environment variables by `bootstrap.sh`. Generated service repos and their images are public, the cluster pulls anonymously, and the pull-secret hook has been removed from the chart. Public images are a stated limitation, not a goal.
- **The chart's `fullname` now uses the release name.** Phase 1 ignored it, which would have made every generated service collide.
- **The Go app still has no `/metrics` endpoint.** That is a decision, not an oversight: Phase 5 covers container-level metrics only.
- **The app and chart gained a database connection (Phase 4).** The service now has a `/db` endpoint and reads `DB_HOST`, `DB_USER`, `DB_PASSWORD` and `DB_NAME`; the chart supplies them, with the three credentials coming from a Kubernetes Secret (`database.secretName`, default `db-credentials`). The Phase 1 loop above is unchanged. See [Phase 4](phase-4.md).
- **Vault and External Secrets are now installed** alongside Argo CD, CloudNativePG and Crossplane, so a rebuild with `bootstrap.sh` now has two more Applications and a manual Vault step. The 1 m 22 s figure above predates all of that.

## Known gaps from this phase

- Argo CD picks up changes by polling, and a local k3d cluster cannot receive a webhook. A webhook or a refresh trigger would remove the wait on a real cluster.
- The single end-to-end sample above is not a benchmark.
