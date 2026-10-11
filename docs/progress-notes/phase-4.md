# Phase 4: Guardrails (in progress)

**Status:** in progress. Written 2026-10-10. Only the first piece, 4a (the database credentials path), has been started, and it works by hand for one service. Nothing else in Phase 4 has been started.

**Exit criteria for Tier A work (from the build plan):** policy violations (unsigned image, root, no limits, `latest`) are blocked automatically; secrets never live in Git in plaintext; a service reaches its database with no credential in Git; tenants cannot reach each other by default.

Of those four, one is partly met: a service reaches its database with no credential in Git, for one service (`form-test`), with the manifests applied by hand.

Times are UTC unless stated. "Observed" means I read the command output; "reported" means I did it and am recording it without keeping the output.

## Where each piece stands

| Piece | Status |
|---|---|
| 4a. Database credentials path (Vault + External Secrets) | **Works by hand for `form-test`.** Not automated; rotation not decided |
| 4b. Supply chain (Trivy, SBOM, cosign, Kyverno `verifyImages`) | Not started |
| 4c. Policy baseline (Kyverno) | Not started. The chart already sets non-root, read-only root filesystem, dropped capabilities and requests and limits, which is what the policies would check |
| 4d. Isolation (default-deny NetworkPolicy, quotas, AppProject limits) | Not started |
| 4e. Split the `team` input from the service name | Not started |
| 4f, 4g. Decommission workflow, reachable URL (Tier B) | Not started |

## 4a: the credentials path

### The problem

A new service's database is created in its own namespace (`<service>-db`) with a generated password in a Secret named `postgres-db-app`. A pod cannot read a Secret from another namespace, so something has to carry the password to the service's namespace, and I did not want it in Git.

### What I built

```
postgres-db-app            (namespace <service>-db, made by CloudNativePG)
   |  PushSecret
   v
Vault: secret/tenants/<service>/db
   |  ExternalSecret
   v
db-credentials             (namespace <service>, read by the app)
```

- **Chart.** `charts/app/values.yaml` has `database.secretName` (default `db-credentials`). The Deployment sets `DB_HOST` as a plain value built from the release name (`postgres-db-rw.<release>-db.svc.cluster.local`) and `DB_USER`, `DB_PASSWORD` and `DB_NAME` with one `secretKeyRef` each, not `envFrom`, so the container sees only the three keys I list. I checked the render with `helm template` and then a server-side dry run, because `helm template` alone accepted a broken `valueFrom`.
- **App (template repo `go-app-template`).** A `/db` endpoint that opens one connection with a 3 second timeout and runs `SELECT current_database()`. The connection is made when `/db` is called, not at startup, so the service starts before its database exists (the service is Ready in about 7 s and the database takes 22 to 67 s). `/db` is deliberately not part of `/readyz`, so a database outage cannot take every pod out of rotation. If some but not all of the four `DB_*` variables are set, the app refuses to start and the error names the missing variables, never their values. An AI assistant wrote this Go at my request, because I do not know Go; I checked it with `go test` and a real query. The code itself was read back from the template repo on 2026-10-10 and matches this description.
- **Vault.** Standalone mode, not dev mode, on purpose: I wanted the setup to be as close to production as a local cluster allows, which means a one-time `vault operator init`, three `vault operator unseal` runs, and unsealing again after any pod restart. Helm chart 0.34.1 as the Argo CD Application `hashicorp-vault`, with TLS disabled (`global.tlsDisable: true`). The unseal keys and root token are in the `pass` password store, never in Git.
- **External Secrets Operator.** Helm chart 2.12.0 as the Application `external-secrets-operator`. A `ClusterSecretStore` named `vault-backend` (in `argocd/apps/30-workloads/`) logs in to Vault with Kubernetes auth and holds no token.
- **Vault configuration** (in Vault's storage, not in Git; the steps are in `docs/setup.md`, step 6): KV version 2 at `secret/`, Kubernetes auth, policy `eso` limited to `secret/data/tenants/*` and `secret/metadata/tenants/*` with no `delete`, and role `eso` bound to the External Secrets service account.
- **Per-service objects** (`vault-and-eso/`, applied by hand, **not** synced by Argo CD): a `PushSecret` in `<service>-db` and an `ExternalSecret` in `<service>`. The folder is a holding place, not a production design.

### Decisions

| Decision | Reason |
|---|---|
| Separate variables (`DB_USER`, `DB_PASSWORD`, `DB_NAME`) and a computed `DB_HOST`, not the Secret's `fqdn-uri` | Clearer to read and test; the app builds the URL and escapes special characters |
| Vault + External Secrets, not the simpler External Secrets Kubernetes provider reading the CloudNativePG Secret directly | Shows more of how a real secrets path works. I knew the simpler option and chose this one on purpose |
| Vault in standalone mode | Closer to production; the cost is manual init and unseal, and a documented list of what a local cluster cannot do (auto-unseal, high availability, TLS) |
| Kubernetes auth with one shared role (`eso`) | The root token is for one-time setup only. The cost: the shared identity can read every tenant's path, so a service is not limited to its own secret. A role per service needs automation that does not exist yet |
| CloudNativePG stays the source of truth | If Vault is wiped, the `PushSecret` writes the password back |

### Problems hit and what they taught me

- **A root token in a Kubernetes Secret.** My first attempt gave External Secrets the Vault root token in a Secret, which defeats the point of keeping the root token for setup only. I deleted the Secret and the local file that held the token, checked that neither reached Git, and replaced it with Kubernetes auth. A token pasted into a chat was revoked rather than left to expire.
- **A bare `403 permission denied` on login.** Vault logged nothing at the default level. The cause, found by reading `vault read auth/kubernetes/config` instead of guessing, was `kubernetes_host` set to `https://127.0.0.1:6443`: from inside Vault's pod that address is the pod itself. The fix was `https://kubernetes.default.svc:443`. Three theories along the way (the token issuer, a stale token, a log line) were wrong or never established; that is worth recording.
- **A policy that was too narrow.** The first `PushSecret` failed with a `403` on a `PUT` to `secret/metadata/tenants/...`. The error named the path. I added `create` and `update` on the metadata path and left `delete` out on purpose, rather than widening the policy. Lesson: check what a provider actually needs before saying a policy is sufficient. The assistant had said it was without checking.
- **A pgx dependency raised the Go version and broke the Docker build.** The Dockerfile base had to move to `golang:1.25-alpine`, and a Docker build failed on a missing `go.sum` entry even though `go test` passed locally: the build context did not bring `go.sum` in. The template's Dockerfile now copies it. `go.mod` requires `go 1.25.0` and pgx v5.11.0.

### Test: Vault sealed (one run, 2026-10-10)

I deleted the Vault pod in a throwaway window to see what breaks. Observed:

- Vault came back `Initialized true`, `Sealed true`: the data is on a persistent volume and survived the restart.
- The already-synced `db-credentials` Secret stayed in place. The running service kept its database connection (`/db` stayed connected).
- The `ExternalSecret` went to `SecretSyncedError`. The `vault-backend` store still said `Valid` at first, then reported `InvalidProviderConfig` (`503 Vault is sealed`) for about 11 minutes: its status lags the seal.
- A new throwaway `ExternalSecret` could not sync (`503` on the Kubernetes auth login).
- Reported: restarting the service while Vault was sealed gave a working pod. The app needs the Secret, not Vault, when it starts.
- After the unseal nothing needed doing. The `db-credentials` `ExternalSecret` was Ready again about 4 minutes 19 seconds after the Vault pod became Ready (one run; the pod's Ready time may trail the unseal by a probe period).
- The `PushSecret` refreshes about hourly (01:30 and 02:30). The 02:30 refresh came about two minutes after the unseal and succeeded; none fell inside the sealed window.

So a sealed Vault blocks new services and credential changes, not running ones. **Not tested:** what a `PushSecret` does while Vault is sealed. This was the first deliberate failure drill; no postmortem has been written.

### Not decided or not built

- **Rotation.** Environment variables from a Secret are read when a container starts (believed from standard Kubernetes behavior; not tested here), so a refreshed Secret does not change a running pod. I have not decided between documenting that a restart is needed and adding something that restarts a Deployment when its Secret changes. I also have not checked whether CloudNativePG rotates the password by itself.
- **Automation.** Per service, something must create a `PushSecret` in `<service>-db`, an `ExternalSecret` in `<service>`, and, if each service is to have its own identity, a Vault policy and role. Candidate homes are the form's blueprints, the Crossplane Composition, or the chart. The open problem is ordering: the `<service>-db` namespace and `postgres-db-app` must exist before the `PushSecret` can sync, and the pod cannot start until the Secret exists.
- **End-to-end test.** I have not generated a new service through the form since the chart started reading the Secret. I expect its pod to sit in `CreateContainerConfigError` until the Secret exists, but I have not seen it. Existing generated repos do not pick up template changes, so this needs a fresh service.
- **Vault configuration is not code.** A rebuild means repeating `docs/setup.md` step 6 by hand. A script or Crossplane's Vault provider are options; I deferred them because the provider needs its own powerful credential, cannot work while Vault is sealed, and is heavy for k3d (all believed, not checked).

## Known gaps and honest limits

- Vault is a local approximation: single replica, plain HTTP inside the cluster, no auto-unseal, keys held by one person in `pass` (back up the GPG key).
- The shared `eso` role can read every tenant's path.
- `/db` is unauthenticated and opens a connection per call. Database connections are probably unencrypted (the driver's TLS fallback was not checked).
- Deleting a service does not clean its Vault entry or its hand-applied manifests.
- The `vault-backend` store sits in the root Application's path next to the External Secrets Application and has not been tested on a fresh rebuild.
- The form's reserved-name list lacks `hashicorp-vault`, `external-secrets-operator`, `kube-public` and `kube-node-lease`.
- The template's CI workflow still sets up Go 1.22 while `go.mod` requires 1.25.0; the Docker build uses 1.25. Not yet aligned.

## Metrics

Not re-measured in this phase. Adding Vault and External Secrets changes the rebuild figure (5 m 08 s was measured before they existed, and the manual Vault init and unseal steps are not in it). Re-measure when 4a closes.

## What's next

1. Decide rotation (document the restart requirement, or add a restarter).
2. Automate the per-service credentials objects, then generate a new service through the form to prove it end to end, with the timing script.
3. Then 4b to 4e (supply chain, policies, isolation, the `team` input).
