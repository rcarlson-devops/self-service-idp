# Self-Service Internal Developer Platform

> I built an internal developer platform that lets a developer go from "I need a new service with a database" to a running, observed, deployed application through a single self-service request — no tickets, no waiting on ops.

---

## Project Status

Built in phases; this table is the honest picture of what exists today.

| Phase | Scope | Status |
|---|---|---|
| 1. Foundation | k3d cluster, Argo CD (app-of-apps), containerized Go service, CI to GHCR, GitOps deploy | **Done** |
| 2. Self-service infrastructure | Crossplane + CloudNativePG: namespace, RBAC, and a Postgres database from one claim | Planned |
| 3. Developer portal | Backstage software template: repo + CI + infra from one form | Planned |
| 4. Guardrails | Kyverno policies, External Secrets + Vault | Planned |
| 5. Observability | Prometheus + Grafana by default for every service | Planned |
| 6. Docs and metrics | Architecture diagram, golden path, before/after numbers | Ongoing |

**Measured so far:** the entire platform (cluster, Argo CD, and the app) rebuilds from nothing in under 3 minutes with a single script.

## The Problem

<!-- Replace with 3-5 sentences framed around a real org's pain point. Example prompts to answer: -->
<!-- - How long does it currently take a developer to get a new service running with infra? -->
<!-- - How many teams/tickets/people are typically involved? -->
<!-- - What does that delay cost (velocity, context-switching, ops toil)? -->

At most organizations, getting a new service into production means filing a ticket, waiting on a platform/ops team to provision infrastructure by hand, and looping through review cycles that can take days. This project asks: what if a developer could request "a new service with a database" the same way they request a pull request review — and get it in minutes, with security and observability built in by default instead of bolted on afterward?

## What This Platform Does

A developer fills out one form in a self-service portal. That single action:

1. Creates a new repository from a standardized template
2. Sets up a CI pipeline automatically
3. Requests and provisions the backing infrastructure (namespace, database, RBAC)
4. Deploys the service via GitOps — no manual `kubectl apply`, ever
5. Applies security and compliance guardrails automatically (no root containers, required labels, no plaintext secrets)
6. Surfaces observability (metrics, dashboards) with zero extra setup

## Architecture

<!-- Insert one clear diagram here — a PNG or draw.io export showing: Backstage -> Git -> Argo CD -> Kubernetes cluster, with Crossplane provisioning infra and Kyverno/ESO enforcing policy. Keep it to one picture, not a wall of text. -->

```
[ Diagram placeholder — architecture.png ]
```

## Stack

| Layer | Tool | Why |
|---|---|---|
| Developer portal / catalog | Backstage | Industry-standard developer portal; single entry point for the golden path |
| Infrastructure provisioning | Crossplane | Kubernetes-native IaC — infra requests are just another API call to the cluster |
| GitOps delivery | Argo CD | Git as the single source of truth; no manual deploys |
| Policy as code | Kyverno | Guardrails enforced automatically, not reviewed manually |
| Secrets | External Secrets Operator + Vault (dev) | No plaintext secrets ever committed to Git |
| Observability | Prometheus + Grafana | Metrics available by default for anything deployed through the platform |
| CI | GitHub Actions | Standard, free, widely recognized |
| Cluster | k3d (k3s in Docker) | Reproducible, runs locally, no cloud cost to evaluate |

## The Golden Path: New Service in Under 10 Minutes

<!-- Fill in once Phase 3 is done. Keep this as literal, numbered, copy-pasteable steps — this is the doc a real developer on your platform would follow. -->

1. Go to the Backstage portal and select **Create → New Service**
2. Fill in service name, team/owner, and whether it needs a database
3. Submit — the platform handles the rest
4. Your new repo appears with CI already configured
5. Watch the deployment sync automatically in Argo CD
6. Your service is live, with metrics already visible in Grafana

## What's Next at Scale

<!-- Show product thinking — this is what separates "I did a tutorial" from "I think about platforms." A few sentences each is enough. -->

- **Multi-tenancy:** namespace-per-team isolation with resource quotas, rather than a shared flat cluster
- **Cost tracking:** tag-based cost attribution surfaced back in Backstage so teams see what their infra costs
- **More templates:** expand beyond one service type to cover common patterns (worker/queue consumer, scheduled job, static site)
- **Self-service beyond day 1:** scaling, rollback, and decommissioning through the same portal, not just creation

## Running This Locally

Everything outside the bootstrap script is deployed by Argo CD from this repo.

**Prerequisites:** Docker, [k3d](https://k3d.io) v5.x, and `kubectl`.

**1. Clone the repo**

```bash
git clone https://github.com/rcarlson-devops/self-service-idp.git
cd self-service-idp
```

**2. Provide registry credentials.** The sample app's container image is in a private GHCR package, so the cluster needs a pull secret. Create a GitHub personal access token with the `read:packages` scope and export it. (This is a temporary manual step; it moves to External Secrets in Phase 4.)

```bash
export GHCR_USER=<your-github-username>
export GHCR_TOKEN=<your-token>
```

**3. Run the bootstrap**

```bash
./bootstrap/bootstrap.sh
```

This creates the k3d cluster (`bootstrap/k3d-config.yaml`), installs a pinned version of Argo CD (`bootstrap/argocd/`), creates the pull secret, and applies `argocd/root.yaml`. From that point Argo CD pulls everything else from Git. It is safe to re-run.

**4. Open Argo CD**

The UI is at <https://localhost:8080> (self-signed certificate). Log in as `admin` with the generated password:

```bash
kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath='{.data.password}' | base64 -d; echo
```

**5. Check the sample app**

```bash
kubectl -n argocd get applications          # root and hello-world-dev should be Synced / Healthy
kubectl -n hello-world-dev get pods
kubectl -n hello-world-dev port-forward svc/hello-world 9090:80
curl localhost:9090/version
```

**Tear down**

```bash
k3d cluster delete dev-cluster
```

### How a code change reaches the cluster

1. Push a change under `app/` to `main`.
2. GitHub Actions vets, tests, builds the image, and pushes it to GHCR tagged `sha-<short-sha>`.
3. The workflow commits that tag to `environments/dev/values-dev.yaml`.
4. Argo CD detects the new commit and rolls out the new image. No `kubectl apply` anywhere.

## About This Project

Built by [Robert Carlson](https://www.linkedin.com/in/robertjohncarlson) as a hands-on demonstration of platform engineering practices — self-service infrastructure, GitOps, policy-as-code, and observability by default — following 10+ years in DevOps and cloud engineering across AWS, Azure, and Kubernetes environments.
