# Self-Service Internal Developer Platform

> I built an internal developer platform that lets a developer go from "I need a new service with a database" to a running, observed, deployed application through a single self-service request — no tickets, no waiting on ops.

---

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
| Cluster | Kind / k3d | Reproducible, runs locally, no cloud cost to evaluate |

## The Golden Path: New Service in Under 10 Minutes

<!-- Fill in once Phase 3 is done. Keep this as literal, numbered, copy-pasteable steps — this is the doc a real developer on your platform would follow. -->

1. Go to the Backstage portal and select **Create → New Service**
2. Fill in service name, team/owner, and whether it needs a database
3. Submit — the platform handles the rest
4. Your new repo appears with CI already configured
5. Watch the deployment sync automatically in Argo CD
6. Your service is live, with metrics already visible in Grafana

## Before / After

<!-- This is the number a recruiter remembers. Fill in once you've timed both the manual and self-service paths. -->

| | Before (manual) | After (this platform) |
|---|---|---|
| Time to a running service with infra | ~2 days | ~X minutes |
| People/teams involved | Developer + ops ticket + review | Developer, alone |
| Guardrails applied | Manual review, inconsistent | Automatic, every time |
| Secrets in Git | Sometimes, by accident | Never — enforced |

## What's Next at Scale

<!-- Show product thinking — this is what separates "I did a tutorial" from "I think about platforms." A few sentences each is enough. -->

- **Multi-tenancy:** namespace-per-team isolation with resource quotas, rather than a shared flat cluster
- **Cost tracking:** tag-based cost attribution surfaced back in Backstage so teams see what their infra costs
- **More templates:** expand beyond one service type to cover common patterns (worker/queue consumer, scheduled job, static site)
- **Self-service beyond day 1:** scaling, rollback, and decommissioning through the same portal, not just creation

## Running This Locally

<!-- Fill in once you've nailed down exact setup steps. Keep it copy-pasteable. -->

```bash
# 1. Spin up a local cluster
kind create cluster --name platform-demo

# 2. Install Argo CD
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

# ...continue with Crossplane, Backstage, Kyverno, ESO setup
```

## About This Project

Built by [Robert Carlson](https://www.linkedin.com/in/YOUR-LINKEDIN) as a hands-on demonstration of platform engineering practices — self-service infrastructure, GitOps, policy-as-code, and observability by default — following 10+ years in DevOps and cloud engineering across AWS, Azure, and Kubernetes environments.
