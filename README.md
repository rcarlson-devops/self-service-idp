# Self-Service Internal Developer Platform

> I built an internal developer platform that lets a developer go from "I need a new service with a database" to a running, observed, deployed application through a single self-service request — no tickets, no waiting on ops.

That is the target. This README separates what is built and measured today from what is still planned. The platform provisions the database and reports it Ready, and the credentials path (the CloudNativePG Secret, Vault, External Secrets, the service's own Secret) is created automatically for every new service: the database's Composition creates the `PushSecret` and the shared chart creates the `ExternalSecret`. I have confirmed this end to end on three freshly generated services. Image scanning, signing, and verification of the signature and SBOM attestation are in place, and so are network policies and a resource quota for each service and its database (see [Network Isolation and Quotas](#network-isolation-and-quotas)). A `decommission` workflow removes a service and what the form created for it (see [Decommissioning a Service](#decommissioning-a-service)). A running service is reachable from the machine that runs the cluster over plain HTTP at `<service>.localhost:8081` (see [Reaching a Service](#reaching-a-service)). Prometheus and Grafana run in the `monitoring` namespace. One dashboard shows a service's memory, CPU and restarts, and a second, Service Traffic, shows its request rate and latency by route (see [Observability](#observability)). On 2026-10-17 both showed up for `dev-env-1` and `dev-env-5`; before that, the first had been checked on two earlier services and the second on `dev-env-3`. All six phases are done as scoped. What is not built is listed under [Known Limitations](#known-limitations) and [What's Next at Scale](#whats-next-at-scale).

---

## Project Status

Built in phases; this table is the honest picture of what exists today.

| Phase | Scope | Status |
|---|---|---|
| 1. Foundation | k3d cluster, Argo CD (app-of-apps), containerized Go service, CI to GHCR, GitOps deploy | **Done** |
| 2. Self-service infrastructure | Crossplane + CloudNativePG: one `PostgresDatabase` request creates a namespace, team RBAC, and a Postgres cluster | **Done** |
| 3. Self-service request | GitHub Actions form: one typed request creates the service's own repo from a template and commits its Argo CD Application and database request to `argocd/tenants/`; Argo CD and Crossplane do the rest | **Working end to end** (six runs, each ending in a running service and a Ready database). Input checks and preflight are done; failure handling and a timing summary were postponed (see Known Limitations) |
| 4. Guardrails | The service's database connection (External Secrets + Vault), image scanning and signing, Kyverno policies, default-deny network policies and quotas | **Done.** Database credentials: Vault (standalone mode) and External Secrets are installed and configured, and a new service gets its credentials with no manual step (three fresh services tested). I also tested what happens when Vault is sealed. A changed password reaches a running pod only after a restart; that is documented, not automated. Image scanning (Trivy), SBOM generation, keyless cosign signing and Kyverno verification of the signature and the SBOM attestation are done and enforced: an unsigned image is rejected at admission (see [Image Verification](#image-verification)). Each service namespace has default-deny network policies and a resource quota, and each database namespace has ingress policies; in the tests a pod outside the service could no longer reach the database (see [Network Isolation and Quotas](#network-isolation-and-quotas)). The shared chart also gives each service an Ingress and a network policy that lets Traefik reach it, so it answers at `http://<service>.localhost:8081` (see [Reaching a Service](#reaching-a-service)). A `decommission` workflow removes a service, its repository, its image package, its tenant folder and its Vault entry (see [Decommissioning a Service](#decommissioning-a-service)). Not built: egress policy and a quota on the database namespace. Known problems and fixes: [troubleshooting guide](docs/troubleshooting.md) |
| 5. Observability | Container-level metrics and a per-service dashboard by default (Prometheus + Grafana) | **Done.** Prometheus and Grafana are installed with Alertmanager off. The kubelet on each node is scraped, so container metrics exist for every namespace. The Service Resources dashboard shows memory, CPU and restarts per pod for the namespace chosen in a drop-down (checked on four services, most recently `dev-env-1` and `dev-env-5`). The template's app serves `/metrics`, and the shared chart gives each service a ServiceMonitor and a network policy so that Prometheus can scrape it. A second dashboard, Service Traffic, shows request rate and p95 latency by route (checked on three services: `dev-env-3`, `dev-env-1` and `dev-env-5`). Both dashboards load from ConfigMaps in Git. Prometheus data is not stored on a volume (see [Observability](#observability)) |
| 6. Docs and metrics | Architecture diagram, golden path, measured numbers | **Done.** The numbers were measured on 2026-10-17 (see Measured below). The architecture diagram and the golden path are in this README |

**Measured** (local 3-node k3d cluster on one laptop, an Intel Core i7-1260P with 16 GB of RAM, under WSL2 limited to 10 GB of memory and 8 GB of swap; everything below was measured on 2026-10-17):

Cluster numbers come from the timestamps Argo CD records on each Application, a poll every 5 seconds for the moment every Application is Healthy, and a memory sample every 2 seconds. Service numbers come from timestamps recorded by GitHub Actions, Argo CD, Crossplane and Kubernetes. Results are listed per run; with this few samples I give no averages. The two service runs were on the third cluster build: `dev-env-1` first, then `dev-env-5` with `dev-env-1` still deployed, and they are listed in that order. Nobody refreshed or synced anything in Argo CD during either run.

| Measurement | Result | Notes |
|---|---|---|
| Full rebuild: start of `bootstrap.sh` to every Application Synced and Healthy | **480 s, 506 s and 598 s** (8 m 00 s, 8 m 26 s and 9 m 58 s) | Three builds, each started with `k3d cluster delete`, so cold. I did not measure a warm rebuild or check whether images were pulled again. The end is the first 5-second poll at which all 14 Applications were Synced and Healthy: the root, six at wave 0, six at wave 1 and the tenants Application at wave 2. It includes creating the cluster, installing Argo CD and the Vault setup. Run 3 was slower at every stage and used the most swap; I did not find why |
| `bootstrap.sh` alone | 161 s, 157 s and 243 s | Creates the cluster, installs Argo CD, applies the root Application, then waits for `vault-0` and sets Vault up. It returns while Argo CD is still working through the sync waves |
| Root Application created (cluster and Argo CD up) | 107 s, 113 s and 158 s | Creation timestamp of the root Application, counted from the start of `bootstrap.sh` |
| Sync wave 0 (the operators and the monitoring stack) | Wave 1 Applications created at 469 s, 493 s and 583 s | Wave 1 starts when every wave 0 Application is Healthy, so this is the end of wave 0. It is nearly all of the build |
| Sync waves 1 and 2 | 9 s, 9 s and 13 s | From the wave 1 Applications being created to the root Application becoming Healthy (at 478 s, 502 s and 596 s) |
| Peak memory in use during the build | 7,965 MiB, 7,406 MiB and 7,790 MiB, with 1,033 MiB, 1,426 MiB and 1,842 MiB of swap | Inside WSL2, which shows about 9.7 GiB. In use means total minus available memory, so reclaimable cache is not counted |
| Form submit to tenant commit | 199 s and 155 s | Includes validation, repository creation, the new repository's first CI run and the form waiting for its image tag. The tag commit landed 166 s and 128 s after that CI run started, and the whole CI run took 188 s and 146 s. CI includes Trivy, the SBOM, signing and attestation |
| Argo CD detection delay (commit to the Application appearing) | 196 s and 133 s | Two samples. Argo CD's default polling interval is 3 minutes and nothing in this cluster changes it, so the first sample exceeded it. I have not explained the excess; Argo's reconcile jitter is a candidate I have not confirmed |
| Service platform time | 92 s and 56 s | Application creation to the pod being Ready. It includes waiting for the database and then for the `db-credentials` Secret |
| Database request to Ready | 76 s and 32 s | From the `PostgresDatabase` being created to Ready, so it is part of the platform time. Image pulls for the database are the likely reason the first service on a fresh cluster took longer; not confirmed, because the timing script reads only the service namespace's events and not the database namespace's |
| **Form submit to service Ready** | **487 s and 344 s** (8 m 07 s and 5 m 44 s) | 199 s form to tenant commit, 196 s Argo CD detection delay and 92 s platform time; then 155 s, 133 s and 56 s |

**Where a rebuild's time goes.** Almost all of it is wave 0, and waves 1 and 2 together added 9 to 13 s. Within wave 0, the last Application to become Healthy was `external-secrets-operator` in the first build and `kube-prometheus-stack` in the other two. `kube-prometheus-stack` also had the longest sync of any Application except the root (297 s, 363 s and 391 s), and Argo CD's last operation on it shows one retry in every build. `external-secrets-operator` finished its sync within 10 s but took 162 to 244 s more to become Healthy. The CloudNativePG operator became Healthy 56 s, 56 s and 161 s after its sync finished and was never the last. In every build Argo CD started the syncs of `cloudnativepg-operator` and `external-secrets-operator` well after the other four wave 0 Applications. Memory in use peaked at 7.2 to 7.8 GiB of the 9.7 GiB WSL2 shows, and swap was in use in every build. I have not tested whether that explains the delays, and I have not looked into the retry. The root Application's own sync time says little: it spans its children, and in the first build a later sync overwrote it.

**The headline metric,** developer request to deployed service, is measured: 487 s and 344 s from submitting the form to a Ready service. The two largest parts were the form run, which waits for the new repository's first CI run to publish an image (199 s and 155 s), and Argo CD noticing the commit (196 s and 133 s). The platform's own work, from the Application appearing to the pod being Ready, was 92 s and 56 s, and most of that was waiting for the database (76 s and 32 s). The pod cannot start until the `db-credentials` Secret exists: the `ExternalSecret` created it 5 s and 10 s after the database was Ready, and the pod was Ready 11 s and 14 s after that. The service image was pulled in 5.0 s and 3.8 s. I report Argo CD's polling wait separately from platform time, because where a commit lands in the poll cycle changes it.

Known problems and their fixes are in the [troubleshooting guide](docs/troubleshooting.md).

## The Problem

At most organizations, getting a new service into production means filing a ticket, waiting on a platform/ops team to provision infrastructure by hand, and looping through review cycles that can take days. This project asks: what if a developer could request "a new service with a database" the same way they request a pull request review — and get it in minutes, with security and observability built in by default instead of bolted on afterward?

## What This Platform Does

**Target experience (steps 1 to 5 work today; step 6 works in part; step 7 works in part, see [Observability](#observability)).** A developer fills out one form (a GitHub Actions workflow with typed inputs). That single action:

1. Validates the request before it lands
2. Creates the service's own repository from a template; that repository's CI builds and pushes the first image
3. Commits the service's Argo CD Application and its database request to `argocd/tenants/`, with no ticket and no human approval step
4. Provisions the backing infrastructure through Crossplane (namespace, database, RBAC)
5. Deploys the service via GitOps — no manual `kubectl apply`, ever
6. Applies guardrails automatically: images from the platform's registry must be signed and attested, each service and its database get network policies, the service gets a resource quota, and no plaintext secrets reach Git
7. Surfaces observability (metrics, dashboards) with zero extra setup

**Working today:** steps 1 to 5. The form validates the request, creates the service's repository from `go-app-template` through the GitHub REST API (with a fine-grained token), waits for that repository's CI to publish the first image, and commits the Application and the database request to `argocd/tenants/`. Argo CD then deploys the service from the shared chart plus the service repo's values file, and Crossplane builds the database, with no `kubectl apply`. This has run end to end six times. **Credentials are automatic too:** the database's Composition creates a `PushSecret` that copies the database password from the CloudNativePG Secret into Vault, and the shared chart creates an `ExternalSecret` that turns it into a `db-credentials` Secret in the service's namespace. On an earlier run the service started with no manual step and its `/db` endpoint reported connected. A new service's pod cannot start until that Secret exists (see the measurements above). **Image verification works too** (see [Image Verification](#image-verification)). **Network isolation and a quota work too** (see [Network Isolation and Quotas](#network-isolation-and-quotas)). **Removing a service works too** (see [Decommissioning a Service](#decommissioning-a-service)). **A running service is reachable from the host too** (see [Reaching a Service](#reaching-a-service)). **Metrics work in part:** Prometheus and Grafana run. One dashboard shows per-pod memory, CPU and restarts (checked on four services), and a second, Service Traffic, shows request rate and latency by route for services built from the updated template (checked on three) (see [Observability](#observability)). Behind the form, the database request is one small manifest:

```yaml
apiVersion: rcarlsondevops.org/v1alpha1
kind: PostgresDatabase
metadata:
  name: example-postgresdatabase
spec:
  team: example-team
  instances: 2      # 1 to 3, required
  size: small       # small | medium | large = 1Gi / 2Gi / 3Gi per instance
```

Crossplane turns that into a Namespace (`<name>-db`), a CloudNativePG `Cluster` named `postgres-db` inside it, and a RoleBinding that gives the `spec.team` group the built-in `edit` role in that namespace only. The full request reference, including every field, error message, and generated Secret and Service name, is in [`docs/database-request-format.md`](docs/database-request-format.md).

## Architecture

What is built so far. Prometheus and Grafana are drawn: the kubelet on each node and each service's `/metrics` endpoint feed Prometheus, and the dashboards reach Grafana as ConfigMaps that Argo CD syncs from Git. The credentials path is drawn with dashed lines for the data flow: the Composition creates the `PushSecret` and the shared chart creates the `ExternalSecret`. The request path from the developer's machine to a service, and to Grafana and Prometheus, goes through Traefik. The form generates the files in `argocd/tenants/`; they can also still be placed by hand. `bootstrap.sh` creates the cluster and installs Argo CD, then initializes, unseals and configures Vault; Argo CD installs everything else from Git.

```mermaid
flowchart LR
    Dev[Developer] -->|runs the form| Form[Self-service form, GitHub Actions]
    Form -->|generates repo from template| Svc[Service repo from go-app-template]
    Form -->|commits tenant files| Git[(Platform repo)]
    Svc -->|CI pushes, signs and attests the image| GHCR[(GHCR)]
    Svc -->|CI commits image tag and digest| Vals[values-dev.yaml in the service repo]
    Git[(Platform repo)] -->|sync| Argo[Argo CD]
    Argo --> CP[Crossplane, functions, XRD and Composition]
    Argo --> CNPG[CloudNativePG operator]
    Argo --> Ten[Tenants Application, wave 2, watches argocd/tenants]
    Ten --> App[Service Application]
    Ten --> Req[PostgresDatabase request]
    Vals -.->|second source| App
    App --> Dep[Deployment and Service]
    GHCR -.->|image pulled| Dep
    Req --> CP
    CP --> NS[Namespace]
    CP --> DB[CNPG Cluster]
    CP --> RB[RoleBinding]
    CP --> DNP[Ingress NetworkPolicies on the database namespace]
    CNPG -.->|runs| DB
    DB -.->|postgres-db-app Secret| PS[PushSecret, from the Composition]
    PS -.-> Vault[(Vault, standalone)]
    Vault -.-> ES[ExternalSecret, from the chart]
    ES -.->|db-credentials Secret| Dep
    CP --> PS
    App --> ES
    App --> NP[NetworkPolicies and ResourceQuota, from the chart]
    App --> Ing[Ingress, from the chart]
    Dev -->|HTTP, host port 8081| Trf[Traefik, built into k3s]
    Ing -.->|host name picks the service| Trf
    Trf -->|allowed by the allow-traefik policy| Dep
    Argo --> Kyv[Kyverno]
    Argo --> Pol[kyverno-policies Application, wave 1]
    Pol --> IVP[ImageValidatingPolicy image-check]
    IVP -.-> Kyv
    Kyv -.->|verifies signature and SBOM attestation when pods are created| Dep
    Argo --> KPS[kube-prometheus-stack: Prometheus, Grafana, kube-state-metrics]
    Kbl[kubelet on each node] -.->|container metrics| KPS
    App --> SM[ServiceMonitor, from the chart]
    SM -.->|tells Prometheus what to scrape| KPS
    KPS -.->|scrapes /metrics, allowed by allow-prometheus| Dep
    Argo --> MonIng[monitoring-ingress Application: Ingress for Grafana and Prometheus]
    MonIng -.->|host name picks the service| Trf
    Trf -->|grafana.localhost and prometheus.localhost| KPS
    Argo --> Dash[Dashboard ConfigMaps]
    Dash -.->|loaded by the Grafana sidecar| KPS
    Boot[bootstrap.sh] -->|creates the cluster, installs Argo CD, applies the root app| Argo
    Boot -.->|initializes, unseals and configures Vault, keys go to pass| Vault
    Argo --> Vault
    Argo --> ESO[External Secrets Operator]
    Argo --> VS[ClusterSecretStore vault-backend, wave 1]
    ESO -.->|runs| PS
    ESO -.->|runs| ES
    VS -.->|Kubernetes auth, role eso| Vault
```

## Stack

| Layer | Tool | Status | Why |
|---|---|---|---|
| Cluster | k3d (k3s v1.35.5), 1 server + 2 agents | In use | Reproducible, runs locally, no cloud cost to evaluate |
| GitOps delivery | Argo CD v3.5.3, app-of-apps | In use | Git as the single source of truth; no manual deploys |
| Infrastructure provisioning | Crossplane v2 (chart 2.4.2), pipeline-mode Composition | In use | Kubernetes-native IaC — infra requests are just another API call to the cluster |
| Crossplane functions | function-go-templating v0.13.0, function-auto-ready v0.7.0, function-sequencer v0.6.0 | In use | Template the resources, report readiness, order creation |
| Database | CloudNativePG (chart 0.29.1) | In use | Kubernetes-native Postgres operator; no cloud provider needed locally |
| Service template | GitHub template repository `go-app-template` | In use | The golden path for a service lives in its own repo, so it can be tested and versioned separately from the platform |
| CI | GitHub Actions in each service repo, images in GHCR | In use | Standard, free, widely recognized |
| Supply chain | Trivy (scans, CycloneDX SBOM), cosign (keyless signing and SBOM attestation) | In use | Every image is scanned, signed and attested in its own repo's CI |
| Tenant discovery | One recursive Argo CD Application (sync wave 2) on `argocd/tenants/` | In use | One folder per service; picks up generated Applications and database requests, after the database API is installed |
| Self-service front door | GitHub Actions (`workflow_dispatch` form) | In use | One typed form as the single entry point; no extra tooling to host |
| Policy as code | Kyverno v1.19.1 (chart 3.9.1) | In use for image verification (`ImageValidatingPolicy`) | Guardrails enforced automatically, not reviewed manually |
| Secrets | External Secrets Operator (chart 2.12.0) + HashiCorp Vault (chart 0.34.1, standalone mode) | In use; created automatically for each service | No plaintext secrets ever committed to Git; Git holds only references. Standalone rather than dev mode on purpose: I wanted it as close to a production deployment as a local cluster allows, so it has to be initialized and unsealed (`bootstrap.sh` does both and keeps the keys in `pass`) |
| Ingress | Traefik (built into k3s), one Ingress per service from the shared chart, and one for Grafana and Prometheus | In use | Ships with k3s; reaches each service at `<service>.localhost` and Grafana and Prometheus at `grafana.localhost` and `prometheus.localhost`, on host port 8081, plain HTTP, local only |
| Network isolation and quotas | Kubernetes NetworkPolicy and ResourceQuota | In use; created automatically for each service | Each service namespace is closed by default and opened one flow at a time; the database namespace accepts connections only from its service, its own instances and the database operator. Enforcement was observed on the local cluster |
| Observability | kube-prometheus-stack (chart 92.3.1; Prometheus, Grafana, kube-state-metrics, node-exporter; Alertmanager off) | In use | Container metrics from the kubelet for every namespace, and application metrics from services built from the updated template; two dashboards load from Git |

## Design Decisions Worth Knowing

- **Small, validated API.** Three inputs. Limits live in the schema, so bad requests (`instances: 500`, a team name with a space, a missing `team`) are rejected at admission with a clear message. Seven such cases were tested against the live API server.
- **The XRD is the contract, the Composition is the implementation.** `size` maps to storage inside the Composition, so the mapping can change without breaking developers.
- **No Crossplane provider.** The Composition emits Kubernetes objects directly, so nothing cloud-specific is needed locally.
- **Namespace per database, fixed Cluster name.** Secret and Service names (`postgres-db-app`, `postgres-db-rw`, `-ro`, `-r`) are predictable, so templates and docs can rely on them.
- **Least privilege for Crossplane.** It may hand out only the `edit` role (the `bind` verb restricted with `resourceNames`), not any role in the cluster. The first version was too wide; I found that by testing the negative case with `kubectl auth can-i`, fixed it, and re-verified.
- **Team access by group, not user.** Membership changes in the identity provider, not in the Composition.
- **Git records what is deployed.** Each service's CI pushes a `sha-<short>` image tag and commits the tag and the image digest to its own `environments/dev/values-dev.yaml`; CI never touches the cluster. By design, the platform repo holds only the Application that points at the service repo, so a new image never needs a commit to the platform repo. I confirmed this on the cluster with a hand-built service: the Deployment's image came from the service repo's values file.
- **One repository per service.** The platform repo is the control plane (shared chart, Argo CD configuration, Crossplane API, generated Applications); service source lives in repos generated from the template. If a service team would own and change a file, it belongs in the template; if the platform owns it, it stays here.
- **One Application per service, two sources.** The generated Application combines the shared chart from this repo with the service repo's values file. The Application name, Helm release name, service name and namespace are the same string, so object names and selectors line up.
- **The team name also becomes a pod label.** The form passes `team` to the service's Application as a Helm parameter, `extraLabels.team`, and the shared chart adds it to the pod labels next to `cost-center`. I checked this on a service whose name and team differ. The chart builds its selectors from separate labels, so an extra label cannot change what a Deployment or Service selects.
- **The URL comes from the shared chart.** Each service gets an Ingress and an `allow-traefik` network policy from the chart, so the form has no step for it. The host name is the Helm release name plus `.localhost`, which is the service name.
- **One folder per service, watched by one recursive Application.** `argocd/tenants/<service>/` holds the service's Application and, optionally, a `PostgresDatabase` request. The watching Application runs at sync wave 2 so the database API exists before any request is applied. A request committed there reached Ready while that Application stayed Healthy.
- **Sync waves order the build.** Wave 0 holds the six Applications that install operators and the monitoring stack: `cloudnativepg-operator`, `crossplane-operator`, `external-secrets-operator`, `hashicorp-vault`, `kube-prometheus-stack` and `kyverno`. Wave 1 holds the Applications that depend on something installed in wave 0: `crossplane-functions`, `database-api`, `kyverno-policies`, `vault-backend`, `grafana-dashboards` and `monitoring-ingress`. Wave 2 is the tenants Application. Wave 1 started only after every wave 0 Application was Healthy, and in the three builds on 2026-10-17 waves 1 and 2 together took 9 to 13 s. `vault-backend` needed no retry in any of them; an earlier layout failed it on a fresh build (see the [troubleshooting guide](docs/troubleshooting.md)). `vault-backend`, `database-api` and `crossplane-functions` carry a `retry` block (limit 5, 30 s backoff, factor 1, 3 m cap). `kube-prometheus-stack` has none, so I do not know where its one retry per build comes from.
- **Blueprints are valid YAML with blank values, not placeholders.** The form fills them with `yq` by path and fails the run if any blank is left; plain `envsubst` would also blank Argo CD's `$values` reference. The fill and the check were tested on a copy first, and the form has since run end to end.
- **Every service gets a database.** The form no longer asks whether one is needed; the database request is the heart of what the platform provides.
- **The form waits for the first image tag with its own polling loop.** It watches the tag in the new repo's values file, which is the thing the next step needs, instead of waiting on a generic CI-finished signal.
- **Vault runs in standalone mode, deliberately.** Dev mode starts initialized and unsealed with a known root token, which hides the parts of running Vault that matter. Standalone mode starts uninitialized and sealed, so something has to run `vault operator init` and unseal it. `bootstrap.sh` does that and keeps the root token and three unseal keys in `pass`, and on a re-run it reads them back from there to unseal (steps in [`docs/setup.md`](docs/setup.md)). That GPG-protected store is the cost of the more realistic setup: a person has to be there to unseal. I drew the line at what a single-node local cluster can do honestly. External Secrets logs in to Vault with Vault's Kubernetes auth, not the root token: a role bound to its own service account, with a policy limited to the `tenants/` paths. I checked that it can write and read there and is denied elsewhere. It can also delete there, which the decommission workflow relies on (see [Decommissioning a Service](#decommissioning-a-service)). The first version of the policy was too narrow (a 403 on the metadata path), and I fixed it by adding that path rather than widening the policy. The role is shared by every service for now; a role per service is not built. The root token is used only for Vault's setup, which `bootstrap.sh` applies on every run, so it stays in `pass` and I do not revoke it. [`docs/setup.md`](docs/setup.md) has a short "Vault trust model" section on what that costs and what production would do instead. Features that only make sense in production (auto-unseal from a cloud KMS, a multi-replica Raft cluster, TLS to Vault) are listed under limitations instead of being faked.
- **The credentials path keeps the database password out of Git and out of the service's code.** CloudNativePG generates the password in its own Secret in the database's namespace. An External Secrets `PushSecret` copies it into Vault, and an `ExternalSecret` in the service's namespace turns it into a `db-credentials` Secret that the shared chart reads with `secretKeyRef`. The service needs that Secret but not Vault when it starts: with Vault sealed, I restarted the service and the new pod still came up.

## Image Verification

Every service image is built in its own repository's CI, scanned with Trivy, signed with cosign and given a CycloneDX SBOM attestation. Signing is keyless: the signature is tied to the workflow's GitHub OIDC identity and recorded in public Sigstore. Signing and attestation use the image digest, and the Deployment references the image by tag and digest.

Kyverno enforces the signature in the cluster. The `kyverno-policies` Argo CD Application (sync wave 1, after Kyverno at wave 0) syncs `kyverno-policies/`, which holds one `ImageValidatingPolicy`, `image-check`. The policy applies when pods are created, checks images under `ghcr.io/rcarlson-devops/`, and passes an image only if it has a valid signature and a signed CycloneDX SBOM attestation, both from a workflow on `main` of a repository under the `rcarlson-devops` account. One policy covers every generated repository, because the identity is matched by pattern and not by repository name.

The policy ran in Audit first (violations reported, nothing blocked) and was then switched to Deny. A signed image passes. An unsigned image under the registry path is rejected at admission:

```
Error from server: admission webhook "ivpol.validate.kyverno.svc-fail" denied the request: Policy image-check failed: Failed to verify the image signature
```

A new service generated by the form started with the policy in Deny. The limits of this check are listed under [Known Limitations](#known-limitations).

## Network Isolation and Quotas

Service namespaces and database namespaces are closed by default and opened one flow at a time. The service side comes from the shared chart, so Argo CD applies it with the service. The database side comes from the database Composition, so Crossplane applies it with the database. Neither needs a step in the form.

**Service namespace (from the chart):**

| Object | Effect |
|---|---|
| `default-deny` | Selects every pod in the namespace and blocks traffic that no other policy allows, outgoing and incoming. While it was the only policy affecting incoming traffic, a request through Traefik returned a 502 |
| `allow-dns` | Lets the pods resolve names |
| `allow-psql` | Lets the pods open port 5432 to the service's own database namespace, `<service>-db`, and nowhere else |
| `allow-traefik` | Lets Traefik's pods in `kube-system` reach the service's pods on the container port. After it synced, requests through Traefik reached the service |
| `allow-prometheus` | Lets Prometheus's pods in `monitoring` reach the service's pods on the container port, so the ServiceMonitor can scrape `/metrics`. The `dev-env-3` target was up with it in place; I did not run the scrape without it |
| `resource-quota` | Caps the namespace at 4 pods, 200m and 128Mi of requests, and 800m and 256Mi of limits |

**Database namespace (from the Composition):**

| Object | Effect |
|---|---|
| `default-deny` | Blocks incoming traffic that no other policy allows |
| `allow-service` | Port 5432 from the service's namespace |
| `allow-inter-namespace` | Ports 5432 and 8000 from the database namespace itself, for traffic between instances |
| `allow-cnpg` | Port 8000 from the CloudNativePG operator's namespace |

What I tested, each blocked case against a control that has to work:

- From a service namespace, a pod could not reach the Kubernetes API, which a pod in a namespace without policies could. The service's `/db` endpoint still reported connected.
- Against the database, a pod in the `default` namespace and a pod in another service's namespace could not connect to port 5432. The service's own namespace could.
- A pod with no requests and limits was rejected with an error naming the quota. A rolling restart of the service fit under the quota.
- A new service with two database instances, created after all of this, reached two of two healthy instances.

The limits of this setup are listed under [Known Limitations](#known-limitations).

## Reaching a Service

A service is reachable from the machine that runs the cluster at `http://<service>.localhost:8081`. It is plain HTTP and local only.

- k3s's built-in Traefik listens on ports 80 and 443 of the cluster's load balancer. `bootstrap/k3d-config.yaml` maps host port 8081 to port 80 there, bound to `127.0.0.1`.
- The shared chart creates an Ingress for each service: class `traefik`, host `<service>.localhost`, path `/`, backend the service's Service on `service.port`.
- The chart also creates the `allow-traefik` network policy (see [Network Isolation and Quotas](#network-isolation-and-quotas)). Without it the default-deny policy blocks Traefik and the request returns a 502.

```bash
curl http://<service>.localhost:8081
```

If your system does not resolve `*.localhost`, send the host name as a header instead:

```bash
curl -H 'Host: <service>.localhost' http://localhost:8081
```

A cluster created before the port mapping was in the k3d config needs it added once:

```bash
k3d cluster edit dev-cluster --port-add 8081:80@loadbalancer
```

The config file binds the port to `127.0.0.1`. This command does not name a bind address, and I have not checked which interface it binds.

Grafana and Prometheus are reached the same way (see [Observability](#observability)).

The limits are under [Known Limitations](#known-limitations).

## Observability

Prometheus and Grafana run in the `monitoring` namespace. The Argo CD Application `argocd/apps/operators/kube-prometheus-stack.yaml` installs the `kube-prometheus-stack` Helm chart (version 92.3.1, sync wave 0) with the settings in `kube-prometheus-stack/dev-values.yaml`. The pods are Prometheus, Grafana, the Prometheus operator, kube-state-metrics and one node-exporter per node. Alertmanager is turned off, and so are the chart's default dashboards. Prometheus keeps 6 hours of data with a size cap of 3GiB, and has requests of 100m CPU and 512Mi memory and limits of 500m CPU and 1Gi memory. Grafana has a 1Gi `local-path` volume. Prometheus only selects ServiceMonitors that carry the label `release: kube-prometheus-stack` (`serviceMonitorSelector` in the values file).

**What is collected.** The kubelet on each of the three nodes is scraped through three endpoints (`/metrics`, `/metrics/cadvisor` and `/metrics/probes`), and all nine targets were up. The cAdvisor endpoint gives CPU and memory per container. Container memory for a service namespace (`dev-env`) showed up in Prometheus and in Grafana's Explore view while that namespace's `default-deny` policy applied, so these metrics needed no allow rule.

**Application metrics.** The template's app serves `/metrics` on the same port as its other routes (commit `381802e` in `go-app-template`): a request counter (`http_requests_total`, by method, route and status), a duration histogram (`http_request_duration_seconds`, by method and route) and the Go runtime and process metrics. Probe and `/metrics` requests are not counted. Three things in the shared chart let Prometheus collect them:

- The Service names its port `http`.
- A `ServiceMonitor` named `metrics`, in the service's namespace, tells Prometheus to scrape that port at `/metrics` every 60 seconds. It carries the label `release: kube-prometheus-stack`, which is what Prometheus selects on.
- The `allow-prometheus` network policy lets Prometheus's pods in `monitoring` reach the app's port (see [Network Isolation and Quotas](#network-isolation-and-quotas)).

On `dev-env-3`, a service built from the updated template, the target is up in Prometheus and `http_requests_total` has one series for `/` and one for `/db`. `dev-env` and `dev-env-2`, built before the endpoint existed, had none, and a request to `/metrics` on `dev-env` returned the same JSON as `/`.

**The dashboards.** Service Resources has three panels: Pod Memory Usage, Pod CPU Usage and Pod Restarts. A `namespace` drop-down at the top lists the cluster's namespaces and every panel follows it. I checked it on `dev-env` and `dev-env-2`, and the panels followed the drop-down for every namespace I selected. On 2026-10-17 `dev-env-1` and `dev-env-5` also showed up on it; I did not record what their panels showed.

```
sum by (pod) (container_memory_working_set_bytes{namespace="$namespace", container !=""})
sum by (pod) (rate(container_cpu_usage_seconds_total{namespace="$namespace", container!=""}[5m]))
sum by (pod) (kube_pod_container_status_restarts_total{namespace="$namespace"})
```

The second dashboard, Service Traffic, shows traffic for services that serve `/metrics`. It has two panels, Request rate by route and p95 latency by route, and a `namespace` drop-down filled by `label_values(http_requests_total, namespace)`, so it lists only namespaces that have that metric. Only `dev-env-3` was listed when I built it. On 2026-10-17 `dev-env-1` and `dev-env-5` showed up on it too; I did not record what their panels showed.

```
sum by (route) (rate(http_requests_total{namespace="$namespace"}[5m]))
histogram_quantile(0.95, sum by (le, route) (rate(http_request_duration_seconds_bucket{namespace="$namespace"}[5m])))
```

Both dashboards are stored in Git as ConfigMaps in `grafana-dashboards/`: `grafana-dashboard-service-resources` in `service-resources-dashboard.yaml` and `grafana-dashboard-service-traffic` in `service-traffic-dashboard.yaml`, each with the label `grafana_dashboard: "1"`. The `grafana-dashboards` Application (sync wave 1) applies that folder to `monitoring`. Grafana's dashboard sidecar watches all namespaces for that label and loads the ConfigMaps. For each dashboard I deleted the copy I had built by hand in the Grafana UI, and after Argo CD synced the ConfigMap the dashboard appeared again. The Service Resources JSON is in Grafana's classic format. This version of Grafana exports a newer format by default; I did not record which format Service Traffic uses, but the sidecar loaded it.

**Opening it.** Grafana and Prometheus are reached through Traefik, the same way as a service (see [Reaching a Service](#reaching-a-service)):

- Grafana: `http://grafana.localhost:8081`
- Prometheus: `http://prometheus.localhost:8081`

`ingress/monitoring.yaml` defines the Ingress for both, and `argocd/apps/workloads/monitoring-ingress.yaml` is the Argo CD Application that applies it. Both addresses answered when I tested them. The `monitoring` namespace has no network policies, so no allow rule was needed. They are plain HTTP and local only. I added no authentication in front of Prometheus's UI.

**Logging in to Grafana.** The admin password in the chart's Grafana Secret did not log me in, and I did not find out why. I reset the admin password with Grafana's CLI inside the Grafana pod instead; the command is in [`docs/setup.md`](docs/setup.md), step 4. Grafana keeps its account data on its 1Gi volume, which a cluster rebuild discards, so I expect to repeat the reset after a rebuild (not tested). The new password is not in Vault or Git.

**What it costs.** One reading of `kubectl top`, taken with the monitoring stack running and the cluster idle: the monitoring pods used about 1010 MiB of memory and 51m of CPU together (Prometheus 532 MiB, Grafana 390 MiB). The three nodes were using 2080, 678 and 3232 MiB of memory (5990 MiB in total) and 1 to 6% of CPU.

The limits are listed under [Known Limitations](#known-limitations).

## Decommissioning a Service

The `decommission` workflow removes a service. It takes two inputs, `service` and `confirm`, which must match. The `validate` job checks the name against a pattern (lowercase letters, numbers and hyphens, not starting or ending with a hyphen) and refuses reserved names, including the platform repository, the template repository, the platform's own Application and namespace names, and any name ending in `-db`. It prints a warning, without stopping, if the repository or the tenant folder does not exist. Because the runner can reach GitHub but not the local cluster or Vault, the workflow does the Git side only and the cluster does the rest. After validation, three jobs run in parallel, independent of each other:

1. **Remove the tenant folder.** It deletes `argocd/tenants/<service>/` and pushes to `main`. Argo CD's automated prune then removes the service's Application and its `PostgresDatabase` request. A missing folder is skipped with a note in the run summary, so a service whose folder is already gone can still be cleaned up.
2. **Delete the service repository,** if a repository with that name exists on the account. A missing repository is skipped.
3. **Delete the GHCR package.** A missing package is skipped. This job uses a second token (see Known Limitations).

Each job writes what it did to the run summary, and each checks that its target exists before deleting it, so running the workflow again picks up whatever is left.

The Vault entry is removed by the cluster, not by the workflow. The database's `PushSecret` has `deletionPolicy: Delete`, so when Argo CD prunes the request, External Secrets deletes the copy of the password in Vault.

`argocd/tenants/` also holds one permanent ConfigMap, `tenants-sentinel`. Argo CD refuses an automated prune that would leave an Application with nothing in it, so without the sentinel the last tenant could not be removed. With it, every tenant is removed the same way.

What I tested: I removed every tenant with the workflow, created a new service with the form, and removed that one too. For each removal I checked that the repository, the GHCR package, the tenant folder and the Vault entry were gone. The `<service>-db` namespace was deleted each time, but the `<service>` namespace was not (see Known Limitations). Tenants that predate the workflow had left Vault entries that it cannot remove, because their `PushSecret` no longer existed. I deleted those by hand. The limits are under [Known Limitations](#known-limitations).

## Tradeoffs: What I Chose, What I Gave Up

Each choice below has an alternative and a price. The price is the part worth reading.

| I chose | Instead of | What it costs |
|---|---|---|
| Vault plus External Secrets, with a `PushSecret` that copies the database password into Vault | External Secrets reading the CloudNativePG Secret directly, with no Vault | One more stateful component to run. It has to be unsealed after every restart (`bootstrap.sh` does it from keys in `pass`, with a GPG prompt), and while it is sealed no new service or credential change can sync (running services are unaffected; I tested this once). I chose it because it exercises real secrets management, not because the simpler route could not work |
| Crossplane (a `PostgresDatabase` API) for per-service infrastructure, no Terraform inside the platform | Terraform for the database and namespace | Everything Argo CD applies is applied continuously, with no reviewed plan step. Terraform stays where I would use it for real: the slow-changing foundation (see What's Next) |
| One generated repository per service | Every service in the platform repo | More repositories to manage, and a change to the template does not reach services that were already generated |
| The form commits tenant files straight to `main` | A pull request with human approval | No review gate on generated commits (deliberate: no tickets, no waiting). It also means branch protection on `main` would block the form's commit |
| A fine-grained personal access token for repository creation | A GitHub App with short-lived tokens | Simple to set up, but tied to one person and broader than I would accept at scale. I have not checked whether a GitHub App can create repositories on a personal account |
| Keyless cosign signing (GitHub OIDC identity, public Sigstore) | Signing with a key I manage | Verification depends on public Sigstore being reachable from the cluster (it works from this one). The identity check is a pattern that accepts any repository under the account |
| Image verification that fails closed (`failurePolicy: Fail`) | Letting pods through when Kyverno is unreachable | While Kyverno is down, creating pods is rejected in every namespace except `kyverno` and `kube-system`. The outage case is untested |
| Network policies and a quota in the shared chart, and ingress policies in the Composition | Leaving namespaces open, or writing the policies by hand for each service | Each new flow needs an explicit allow rule. The reachable URL needed one (`allow-traefik`). Container metrics from the kubelet needed none. Scraping the app's `/metrics` has its own rule, `allow-prometheus`; I did not test the scrape without it. A mistake in the chart reaches every service on the next sync |
| Ingress-only policies on the database namespace, with no quota there | Also restricting the database pods' outgoing traffic and capping the namespace | Outgoing traffic from the database pods is unrestricted and nothing caps that namespace. The database pod declares no CPU or memory requests, so a quota there would need defaults added first |
| A local 3-node k3d cluster | A managed cluster such as EKS | No cloud cost and a fully reproducible rebuild. The Kubernetes API is the same, but there is no real cloud IAM, load balancer or managed storage, and I make no production claims |

**What it costs to run:** nothing in cloud spend, since everything runs on one laptop (an Intel Core i7-1260P with 16 GB of RAM). I have one reading of the laptop's memory and CPU use with the monitoring stack running: 5990 MiB of memory in use across the three nodes and 1 to 6% CPU, at idle (see [Observability](#observability)). During the three cluster builds on 2026-10-17, memory in use inside WSL2 peaked at 7.2 to 7.8 GiB with 1.0 to 1.8 GiB of swap in use, against a WSL2 limit of 10 GB of memory and 8 GB of swap. I have not checked how much memory Docker gives the nodes, or what happens as services are added.

## The Golden Path: New Service in Under 10 Minutes

_Measured on a local cluster: 487 s and 344 s from submitting the form to a Ready service, with the service's credentials set up automatically (two runs, 2026-10-17). The metrics step works in part: see [Observability](#observability)._

1. Open the platform repo's **Actions** tab, select the self-service workflow, and click **Run workflow**
2. Fill in the service and team name, the number of database instances, and the database size
3. Submit — the workflow validates the request, creates the service's repo from the template, waits for its first image, and commits the service's Application to `argocd/tenants/`
4. Argo CD syncs the new commit and Crossplane provisions the infrastructure
5. Watch the rollout in Argo CD
6. Your service answers at `http://<service>.localhost:8081`
7. Open Grafana at `http://grafana.localhost:8081`, open the Service Resources dashboard, choose the service's namespace, and see its memory, CPU and restarts. The Service Traffic dashboard shows request rate and latency by route for services built from the updated template (checked on three services; see [Observability](#observability))

## What's Next at Scale

- **Multi-tenancy:** the service and database namespaces have network policies and the service namespace has a quota. Still open: egress policy and a quota on the database namespace, policies on the platform namespaces, and Argo CD project limits per team
- **Cost tracking:** tag-based cost attribution surfaced back to teams (for example in Grafana) so they see what their infra costs
- **More service types:** expand beyond one service type to cover common patterns (worker/queue consumer, scheduled job, static site)
- **Self-service beyond day 1:** scaling and rollback through the same self-service workflow, not just creation. Decommissioning exists as a separate `decommission` workflow
- **Database hardening:** backups, a resize path, conditional synchronous replication, read-only and admin role options, and CPU/memory in `size`
- **Narrower Crossplane permissions:** its Namespace rule currently allows all verbs, which is fine locally but too broad for a shared cluster
- **The foundation layer as Terraform:** `bootstrap.sh` is a script because the cluster is a disposable local k3d cluster and k3d has no officially maintained Terraform support. For real clusters, networking, and identity, Terraform is where I would put that slow-changing layer, run by a human with reviewed plans and remote state, leaving Crossplane for per-request resources and Argo CD for everything in Git
- **Faster change detection:** Argo CD polls Git on an interval (the default interval is 3 minutes; the measured delay was 196 s and 133 s), and a local k3d cluster is not reachable by a GitHub webhook. A webhook (or a refresh trigger) would remove that wait on a real cluster
- **A GitHub App instead of a personal token:** the self-service form creates repositories with a fine-grained personal access token tied to one person. A GitHub App with narrow, short-lived installation tokens is the scalable replacement
- **Alerting and metric storage:** Alertmanager is off and Prometheus keeps 6 hours of data without a volume. A shared cluster would need alert rules, a route to people, and storage that outlives the Prometheus pod
- **TLS, real DNS and tracing:** services are reached over plain HTTP at a `.localhost` name. This version has no certificate management (cert-manager), no real DNS and no distributed tracing
- **A production-grade Vault:** auto-unseal from a cloud KMS so a restart needs no human, a multi-replica Raft cluster, TLS between External Secrets and Vault, and unseal key shares split between several people, and a Vault role and policy per service instead of one shared role. The local setup has one replica, plain HTTP inside the cluster, one shared role, and unseal keys held by one person

## Repository Layout

```
.
├── .github/workflows/       The self-service form and the decommission workflow
├── argocd/
│   ├── root.yaml            App-of-apps root; the only Argo resource applied by hand
│   ├── apps/                Argo CD Application manifests (plus the Vault store, see platform/)
│   │   ├── operators/       CloudNativePG, Crossplane, Vault, External Secrets, Kyverno, kube-prometheus-stack
│   │   ├── platform/        Crossplane functions and API, the kyverno-policies Application, and the Vault ClusterSecretStore
│   │   └── workloads/       The Application that watches argocd/tenants/, the grafana-dashboards Application, and the monitoring-ingress Application
│   └── tenants/             One folder per service (its Application and its database request), plus the tenants-sentinel ConfigMap
├── bootstrap/               bootstrap.sh, k3d config, pinned Argo CD install, and scripts/ (the Vault setup script and its tests)
├── charts/app/              Shared Helm chart used by every service, including its Ingress, network policies, quota and ServiceMonitor
├── crossplane/
│   ├── functions/           The three Crossplane Function packages
│   └── api/                 Crossplane RBAC; database/ holds the XRD and Composition (which also creates the database namespace's network policies)
├── self-service/
│   └── templates/           Application and database blueprints the form fills in (not synced by Argo CD)
├── kyverno-policies/        The ImageValidatingPolicy, synced by the kyverno-policies Application
├── kube-prometheus-stack/   Helm values for the monitoring stack (dev-values.yaml)
├── grafana-dashboards/      Dashboard ConfigMaps, synced by the grafana-dashboards Application
├── ingress/                 Ingress for Grafana and Prometheus (monitoring.yaml), synced by the monitoring-ingress Application
└── docs/                    Request format doc, one-time setup, and the troubleshooting guide
```

Service source code does not live in this repo. Each service is generated into its own repository from the `go-app-template` template repo. `kyverno-policies/` sits outside `argocd/apps/` so that its own Application applies it and the root Application does not. Folders for later work (such as `observability/`) are added as those phases start. The Argo CD Applications for Vault and External Secrets are `hashicorp-vault` and `external-secrets-operator`. Ordering between components comes from sync-wave annotations on the Applications, not from folder name prefixes: wave 0 is the operators and the monitoring stack, wave 1 the Applications that depend on them, and wave 2 the tenants Application (see Design Decisions Worth Knowing). `grafana-dashboards/` and `ingress/` sit outside `argocd/apps/` for the same reason. `kube-prometheus-stack/` holds only the values file that the Application of the same name reads.

## Running This Locally

Everything outside the bootstrap script is deployed by Argo CD from this repo.

**Prerequisites:** Docker, [k3d](https://k3d.io) v5.x, and `kubectl`. `bootstrap.sh` also sets Vault up, which needs `pass` (the Linux password store) with a GPG key, because the root token and unseal keys are kept there; the script runs `pass ls` first and stops if it fails. It also needs Python 3 with the `venv` module (on Debian and Ubuntu, the `python3-venv` package) and access to PyPI, because it installs `bootstrap/scripts/requirements.txt` into a `.venv` at the repo root, and local port 18200 free, since it reaches Vault through a `kubectl port-forward` on that port. Before it builds the cluster it checks `pass`, that Python can create a virtual environment and that the port is free, and it stops with a message if any of them fails. The self-service form needs a one-time GitHub setup (template repo, access token, Actions secret); see [`docs/setup.md`](docs/setup.md).

On WSL2 the cluster needs more memory than I expected: memory in use peaked at 7.2 to 7.8 GiB during a build. The laptop has an Intel Core i7-1260P and 16 GB of RAM. My `.wslconfig` sets `memory=10GB` and `swap=8GB`. Before I raised the limit the machine froze during a build; I believe it ran out of memory but did not confirm it.

**1. Clone the repo**

```bash
git clone https://github.com/rcarlson-devops/self-service-idp.git
cd self-service-idp
```

**2. Run the bootstrap**

```bash
./bootstrap/bootstrap.sh
```

This creates the k3d cluster (`bootstrap/k3d-config.yaml`), installs a pinned version of Argo CD (`bootstrap/argocd/`) and applies `argocd/root.yaml`. It then sets Vault up. It waits for the `vault-0` pod (up to `VAULT_WAIT_SECONDS`, default 1800, then it stops with an error). If Vault is not initialized, it initializes it with three key shares and a threshold of three and stores the root token and three unseal keys in `pass`. If it is initialized, it reads them from `pass` and unseals Vault. Either way it then configures Vault for External Secrets. From that point Argo CD pulls everything else from Git in three sync waves: the operators and the monitoring stack, then the Crossplane functions, the database API and the other Applications that depend on them, then the Application that watches `argocd/tenants/`. Expect 8 to 10 minutes from the start until every Application is Healthy (480 s, 506 s and 598 s in the three cold builds I timed; see the measurements above). `bootstrap.sh` itself returns after about 2.5 to 4 minutes while Argo CD is still working, and almost all of the rest is wave 0. It is safe to re-run. Do not edit `bootstrap.sh` while it runs: an edit during one run produced `ubectl: command not found`.

**3. Open Argo CD**

The UI is at <https://localhost:8080> (self-signed certificate). Log in as `admin` with the generated password:

```bash
kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath='{.data.password}' | base64 -d; echo
```

The UI is not reached through Traefik. `bootstrap/argocd/kustomization.yaml` exposes `argocd-server` as a LoadBalancer Service on ports 8888 and 8443, and `bootstrap/k3d-config.yaml` maps `127.0.0.1:8080` to port 8888 on the cluster's load balancer. Port 8443 is not published to the host.

**4. Check that everything synced**

```bash
kubectl -n argocd get applications     # all should end up Synced / Healthy (14 before any service exists)
```

Vault's root token and unseal keys are in `pass` under `infrastructure/vault/`, never in Git. A Vault pod restart leaves Vault sealed; re-run `./bootstrap/bootstrap.sh`, which reads the keys from `pass` (you get a GPG prompt) and unseals it. The manual commands are in [`docs/setup.md`](docs/setup.md), steps 5 and 6; step 7 there explains how a service's credentials work.

**5. Request a service and its database**

Run the self-service form (see the golden path below). It creates the service's repository and commits the service's Application and its `PostgresDatabase` request to `argocd/tenants/<service>/`; Argo CD applies them on its next poll. Committing finished manifests there by hand also works. Then check:

```bash
kubectl get postgresdatabase           # READY=True after about a minute (32 s and 76 s measured)
kubectl get cluster,pods -n <service>-db
```

Once the service's pod is Ready, open `http://<service>.localhost:8081` (see [Reaching a Service](#reaching-a-service)). Its memory, CPU and restarts are on the Service Resources dashboard, and its request rate and latency are on the Service Traffic dashboard (see [Observability](#observability)).

To remove a service, run the `decommission` workflow (see [Decommissioning a Service](#decommissioning-a-service)). Removing a service's folder from `argocd/tenants/` by hand also removes the service, its database and the data, but not its repository, image or Vault entry.

**Tear down**

```bash
k3d cluster delete dev-cluster
```

This removes the cluster and everything in it, including Vault's contents. It does not remove what the form created on GitHub: each generated service repository and its published image stay until you delete them by hand. Run the `decommission` workflow for each service first if you want those removed; deleting the cluster does not do it.

### How a service's code change is built

Each service repo, generated from `go-app-template`, carries its own CI:

1. Push a change under `app/` to `main`.
2. GitHub Actions vets and tests the code, scans it with Trivy, builds the image, pushes it to GHCR tagged `sha-<short-sha>`, scans the image, generates a CycloneDX SBOM, signs the image digest with cosign and attaches the SBOM as a signed attestation.
3. The workflow commits the image repository, tag and digest to the service repo's `environments/dev/values-dev.yaml`.

Rolling that image out is done by an Application in `argocd/tenants/` that combines the shared chart from this repo with that values file. The form generates that Application.

## Known Limitations

- Application metrics reach Prometheus only from services built from the updated template. The chart creates a ServiceMonitor for every service, but a service generated before `/metrics` existed (`dev-env` and `dev-env-2` were) has no endpoint to scrape, and a change to the template does not reach existing repositories. Only `dev-env-3`, `dev-env-1` and `dev-env-5` were checked. The scrape has its own allow rule, `allow-prometheus`, because the service namespace has a `default-deny` policy; I did not run the scrape without it. Kubelet metrics needed no rule, probably because Prometheus connects to the kubelet on the node and not to the service's pods; that is not tested.
- Prometheus data is not stored on a volume. `kube-prometheus-stack/dev-values.yaml` sets no `storageSpec` for Prometheus, and the only persistent volume claim in `monitoring` is Grafana's (1Gi, `local-path`). Without a claim the chart's default is a temporary volume (not checked in this cluster; the pod's volumes were not read), so I expect the data to be lost when the pod is recreated, for example on a cluster rebuild. It keeps 6 hours of data.
- Alertmanager is off, so there are no alerts.
- There are two shared dashboards, each with a namespace drop-down, not one dashboard per service. Service Resources was checked on `dev-env`, `dev-env-2`, `dev-env-1` and `dev-env-5`, and Service Traffic on `dev-env-3`, `dev-env-1` and `dev-env-5`.
- The latency histogram uses the client library's default buckets (`prometheus.DefBuckets`), and the lowest one is 5 ms. When every request to a route finishes faster than that, the p95 panel shows a value inside that first bucket, not a measured one. On `dev-env-3`, `/` showed 4.75 ms, which is 0.95 of 5 ms, so the reading means "under 5 ms".
- The dashboard has no panel for usage against the quota. A `kube_resourcequota` query returned no data and I did not find out why.
- Grafana uses the chart's own admin account. Its password is not in Vault and not managed by External Secrets, and the password in the chart's Secret did not work for me, so I reset it by hand with Grafana's CLI (see [Observability](#observability)). Grafana's data is on its 1Gi volume, which does not survive a cluster rebuild, though the dashboard does because it comes from Git.
- The kubelet scrape and the `dev-env-3` target are the only targets I checked. I did not look at the other targets the chart creates, such as the control-plane components.
- Generated service repositories are public in this version, and so are their images.
- The self-service form creates repositories with a fine-grained personal access token, which is broader than I would accept at scale.
- Argo CD polls Git every 3 minutes by default, and I measured 196 s and 133 s before it noticed a new tenant commit. In those two runs it was the second-largest part of the form-to-service time, after the form run itself, which waits for the new repository's CI. The interval is configurable and I left it at the default. A webhook cannot reach a local k3d cluster.
- Credentials are created automatically, with caveats. The `PushSecret` comes from the database's Composition and the `ExternalSecret` from the shared chart, so a new service needs no manual step (three fresh services tested: one on 2026-10-12 and two on 2026-10-17). Ordering is not enforced: each piece retries until the one before it exists. In both 2026-10-17 runs the `ExternalSecret` first reported that the Vault entry did not exist, and the pod reported `secret "db-credentials" not found`; the `ExternalSecret` created the Secret 5 s and 10 s after the database became Ready, and the pod was Ready 11 s and 14 s after that.
- A changed database password reaches a running service only after the pod restarts. A change in the CloudNativePG Secret flows through the `PushSecret` (the Composition sets no refresh interval, so it uses the External Secrets default) and the `ExternalSecret` (15 s) into `db-credentials`, but environment variables are read when a container starts, so a running pod keeps the old value until it is restarted (`kubectl rollout restart deploy/<service>`). That is standard Kubernetes behavior; I have not tested it here. I did not add a restarter such as Stakater Reloader.
- The form has two inputs: `service` (the repository, Application and namespace name) and `team` (the group that gets the `edit` role in the service's database namespace). The form checks `team` against a pattern, `dev-team-` followed by at least one character, so any `dev-team-<name>` is accepted and anyone who can run the form can grant that group access. A fixed list of team names would be stricter. That is harmless on a local cluster.
- Removing a service is a Git change, and Argo CD prunes automatically: deleting a service's folder deletes the service and, if it has one, its database and data. The `decommission` workflow adds the repository, the image package and, through the `PushSecret`, the Vault entry. Before the `tenants-sentinel` ConfigMap existed, deleting the only tenant's folder left the tenants Application OutOfSync with the service still running, because automated sync with prune refuses a change that would remove everything in an Application; the sentinel keeps the folder from ever rendering empty. When `form-test` was removed (before the workflow existed), its database namespace (`form-test-db`, created by Crossplane) was deleted, but its service namespace (`form-test`, created by Argo CD's `CreateNamespace` sync option) stayed behind. After a decommission, the leftover namespace contained only the `kube-root-ca.crt` ConfigMap, which Kubernetes adds to every namespace (checked on `dev-env`). The `decommission` workflow behaves the same way: the `<service>-db` namespace is deleted and the `<service>` namespace is not. The generated Application has `CreateNamespace=true`, and the namespaces of `dev-env` and `dev-env-2` carry only the `kubectl.kubernetes.io/last-applied-configuration` annotation and no Argo CD tracking annotation or label. That is consistent with Argo CD not tracking a namespace it creates this way, so pruning the Application leaves it behind; I have not confirmed that this is the cause. The empty namespace has to be deleted by hand (`kubectl delete namespace <service>`).
- Failure handling is not built: if a form run stops half-way, the repository it created stays, and running the form again with the same name fails until that repository is removed by hand. Input checks run before anything is created: name rules, reserved names (including any name ending in `-db`), an existing repository of the same name, and an existing tenant folder of the same name. The form's reserved-name list is shorter than the `decommission` workflow's. The form reserves `vault` and `external-secrets` but not `hashicorp-vault`, `external-secrets-operator`, `kube-public`, `kube-node-lease`, `kyverno`, `kyverno-policies`, `go-app-template` or `self-service-idp`, all of which the `decommission` workflow refuses (it also refuses every `kube-*` name). Both lists also reserve the names the monitoring stack and its Ingress use (the namespace, the Application names and the Grafana and Prometheus host names), so a service cannot take them. Two runs started at the same moment can still both pass these checks. A run that times out while waiting for the image tag (the step limit is 5 minutes) is cancelled with GitHub's generic timeout message, not a specific one.
- Reaching a service works on the local cluster only. It is plain HTTP, with no TLS or certificate management and no real DNS. The host name suffix `.localhost` is written into the chart's Ingress and is not a chart value. Host port 8081 comes from the k3d config, so a cluster built without that mapping needs the `k3d cluster edit` command in [Reaching a Service](#reaching-a-service). `*.localhost` resolved without a `Host` header on one machine (the operating system is not recorded here); other systems may need the header.
- The CI bot commits directly to `main` in each service repo, which would not work with branch protection.
- The service's `/db` endpoint is unauthenticated and opens a new database connection on every call. That is acceptable on a local cluster and is not something to expose. I have not set `sslmode` in the connection URL; I believe the driver tries TLS and falls back to plain without verifying the certificate, but I have not checked which one is used here, so treat database connections as unencrypted.
- Database volumes cannot be resized (the local storage class disallows expansion), and deleting a request deletes its data.
- Two guardrails are built: image verification, covering the signature and the SBOM attestation (see Image Verification), and network isolation with a quota (see Network Isolation and Quotas). Kyverno enforces only the image check. Secrets management covers the database password only: it reaches each service through Vault and External Secrets with nothing in Git, but every service shares one Vault role that can read, write and delete every service's path under `tenants/`, and the password also still sits in the Secret CloudNativePG generates, which is where Vault's copy comes from.
- Network isolation covers the service and database namespaces only. The platform's own namespaces (Argo CD, Vault, External Secrets, Crossplane, CloudNativePG, Kyverno) have no policies. In the database namespace only incoming traffic is restricted: outgoing traffic from the database pods is not, and the namespace has no quota or default resource requests, because the database pod declares none. In the service namespace the incoming rules are the ones for Traefik and Prometheus; incoming traffic from other sources was not tested. Container metrics from the kubelet reach Prometheus with `default-deny` in place.
- The quota is the same for every service: 4 pods, 200m and 128Mi of requests, 800m and 256Mi of limits. A pod that declares no requests and limits is rejected, so the chart has to set them. A service that needs more has to change the shared chart, which changes every service.
- The network policies and the quota come from the shared chart, so they reach a service on the next sync. A service created before they existed received them the same way.
- Image verification accepts a signature and attestation from any repository under the `rcarlson-devops` account: the policy matches the signing workflow's identity with a pattern, so any workflow on `main` in any of them can produce an image that passes.
- Only images under `ghcr.io/rcarlson-devops/` are checked. Images from other registries are not verified.
- The template's CI references `aquasecurity/trivy-action` at `@master`, not at a version or commit, in three steps (the file-system scan, the image scan and the SBOM). A change on that branch changes what runs in every generated repository's CI. The other actions are pinned to a major version, and `cosign-installer` to `v4.1.2`.
- The image policy's webhook fails closed. If Kyverno cannot be reached, creating pods is rejected in every namespace except `kyverno` and `kube-system`, which includes the platform's own namespaces. Kyverno's namespace is excluded so it can restart itself. This case has not been tested.
- Admission checks run when a pod is created. Pods that are already running are reported on by Kyverno's background scan but are not stopped.
- Vault is a local approximation of a production deployment. It runs in standalone mode as a single replica, so it has no fault tolerance; it uses plain HTTP inside the cluster; and it is not auto-unsealed, because auto-unseal needs a cloud KMS that a local free-tier setup does not have. `bootstrap.sh` initializes and unseals it, and on a re-run it reads the unseal keys from `pass`, which asks for the GPG passphrase, so a person still has to be there. The root token and three unseal keys are held by one person in `pass`. All three keys are needed to unseal, and they sit in the same store, so the split adds no protection here. A cluster rebuild means a new init and new keys, and Vault's contents are lost. A Vault pod restart leaves it sealed until someone unseals it; re-running `bootstrap.sh` does that (a re-run read the keys from `pass` and unsealed Vault on a real cluster). I tested what a sealed Vault does by deleting the Vault pod: its data survived (it is on a persistent volume) and it came back sealed. While it was sealed, the Secret already synced into the service's namespace stayed in place, the running service kept its database connection, and a restarted service pod still came up, because it needs the Secret and not Vault. New Secrets could not be synced, and the External Secrets store reported errors until Vault was unsealed (its status lagged the seal at first). After the unseal, syncing resumed with no action. Argo CD kept `vault-backend` and the `ClusterSecretStore` Healthy while Vault was sealed, so check `kubectl get clustersecretstore vault-backend` instead; I believe, from a third-party source I did not verify, that Argo CD has no built-in health check for that kind. A sealed Vault therefore blocks new services and credential changes but not running ones. I did not test what the `PushSecret` does while Vault is sealed.
- Package deletion uses a second token. A fine-grained personal access token could not delete packages, so the `decommission` workflow uses a classic token with only the `delete:packages` scope (GitHub adds `read:packages` with it), stored as its own Actions secret and used only in the package-delete step. A classic token cannot be limited to one repository, so it can delete any package on the account. It has no expiry and nothing rotates it. I did not replace the tokens with a GitHub App.
- Decommissioning while Vault is sealed is untested. I expect it to fail or stall, because External Secrets has to reach Vault to delete the entry; I have not checked what happens, or whether the entry would be removed after an unseal.
- The `decommission` workflow does not check how a repository was created. It deletes the repository named in `service` if one exists on the account, so any repository whose name is not reserved can be deleted this way, not only generated services. The name pattern, the reserved list and the `confirm` input are the only guards.
- The three `decommission` jobs run independently, so if one fails the other two still complete, and nothing reports what is left afterwards. The run summary and job logs show which job failed. Running the workflow again is safe, because each job skips a target that is already gone.
- A rebuild with existing entries in `argocd/tenants/` was not retested after Vault's setup moved into `bootstrap.sh`: the three rebuilds on 2026-10-17 started with no services.
- Timings are from one local machine (an Intel Core i7-1260P with 16 GB of RAM, WSL2 limited to 10 GB of memory and 8 GB of swap) and from small samples: three cluster rebuilds and two service deployments, all on 2026-10-17. All three rebuilds were cold, and I did not measure a warm one. The two service runs were on the same cluster, one after the other, so the difference in their database times (76 s and 32 s) may come from the first service on a fresh cluster pulling images; I did not confirm that. Argo CD's wait to notice a commit (196 s) exceeded its 3-minute poll interval in one run, and I have not explained that. The CloudNativePG operator was slow to become Healthy in two earlier rebuilds and I never found the cause; in the three rebuilds on 2026-10-17 it was not the last Application to become Healthy, and the slowest were `external-secrets-operator` and `kube-prometheus-stack`, which I have not investigated either. Argo CD's last operation on `kube-prometheus-stack` shows one retry in each build, which I have not looked into.

## About This Project

Built by [Robert Carlson](https://www.linkedin.com/in/robertjohncarlson) as a hands-on demonstration of platform engineering practices — self-service infrastructure, GitOps, policy-as-code, and observability by default — following 10+ years in IT and systems engineering, including 6+ in CI/CD and IaC.
