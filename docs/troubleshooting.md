# Troubleshooting log

Problems I hit while building the platform, what caused them, and how I fixed them. Each entry gives the symptom, the cause and the fix. Where I did not fully establish the cause, the entry says so.

## Argo CD and bootstrap

### The Argo CD UI was only reachable because of a hand edit
- **Symptom:** the UI was reachable, but only because I had edited the `argocd-server` Service by hand.
- **Cause:** the edit was not in Git, so a rebuilt cluster would not have had it.
- **Fix:** I found the edit through the `last-applied-configuration` annotation and moved it into the Kustomize patch. I then deleted the cluster and rebuilt it from scratch to prove it.

### A fresh clone could not bootstrap
- **Symptom:** a fresh clone could not bootstrap the cluster.
- **Cause:** the repo URL was an SSH URL that depended on a deploy key.
- **Fix:** I switched to HTTPS on a public repo, so bootstrap needs no credentials.

### A missing image tag deployed silently
- **Symptom:** a misconfiguration deployed without any error.
- **Cause:** the chart defaulted `image.tag` to a never-published `dev-latest`.
- **Fix:** the chart now uses `required` for `image.tag`, so rendering fails when it is missing.

### Argo CD would not install with client-side apply
- **Symptom:** the install failed with "annotation too long".
- **Cause:** the ApplicationSet CRD is too large for client-side apply.
- **Fix:** Argo CD is installed with server-side apply.

### The tenants Application stayed OutOfSync after a tenant folder was deleted
- **Symptom:** after I deleted the only tenant's folder, the tenants Application stayed OutOfSync and the service kept running. Earlier I had worked around it by deleting the Applications by hand without finding the cause.
- **Cause:** found by reading `status.conditions` on the Application: a `SyncError` saying `auto-sync will wipe out all resources`. The folder held only `.gitkeep`, so the sync would have left the Application with nothing, and Argo CD's automated sync with prune refuses that.
- **Fix:** none needed. Once a new tenant existed, the next sync pruned the old one. Pruning deletes databases and their data, so I left the safeguard on; removing the last tenant needs a manual step.
- **Related observations:**
  - The service Application carries the `resources-finalizer.argocd.argoproj.io` finalizer, so deleting an Application by hand also deletes what it deployed.
  - After the prune, the database namespace (created by Crossplane) was deleted but the service namespace (created by Argo CD's `CreateNamespace` option) stayed behind, empty as far as I could see.

### An Application failed its first sync with `no endpoints available for service "external-secrets-webhook"`
- **Symptom:** after a fresh rebuild, `vault-backend` failed with `no endpoints available for service "external-secrets-webhook"`. Several Git-sourced Applications showed sync status Unknown in the same snapshot.
- **Cause (likely, not isolated):** External Secrets' webhook had no running pod yet when `vault-backend` tried to create the `ClusterSecretStore`, and `vault-backend` had no `retry`. I inferred this from the message and did not look at the webhook pod at the time.
- **Fix:** the sync waves put External Secrets at wave 0 and `vault-backend` at wave 1, and the Applications that failed their first sync have a `retry` block. In the three rebuilds on 2026-10-17, `vault-backend` synced without a retry every time.
- **Not tested:** whether the waves or the retry alone would have been enough.
- **Lesson:** a sync wave only orders what happens within one sync, so the change has to be tested with a fresh `k3d cluster delete` and bootstrap, not by pushing to a running cluster.

### The bootstrap's wait for Vault failed with `only available for RollingUpdate strategy type`
- **Symptom:** `kubectl rollout status` on the Vault StatefulSet failed with that message.
- **Cause:** `rollout status` works on a StatefulSet only with the RollingUpdate update strategy, so Vault's StatefulSet does not use it. I did not check which strategy it has.
- **Fix:** the script waits for the `vault-0` pod to exist and then for its phase to be Running. It does not wait for Ready, because a standalone Vault is not Ready until it is unsealed, which is what the script does next.

### `kubectl` returned EOF after restarting WSL
- **Symptom:** after a WSL restart, `kubectl` returned `EOF`.
- **Cause:** the proxy in the k3d load balancer container, which sits in front of the API server, had gone stale. I did not investigate why.
- **Fix:** `docker restart k3d-dev-cluster-serverlb`.
- **Related:** `wsl --shutdown` did not free the memory while VS Code windows attached to WSL were open, because they kept `vmmemWSL` alive. Close them first.

## Crossplane and RBAC

### Crossplane could not create the Namespace
- **Symptom:** `cannot compose resources` with a `forbidden` error for the `crossplane` ServiceAccount.
- **Cause:** Crossplane v2 needs explicit permission to compose any kind beyond its defaults.
- **Fix:** an aggregated ClusterRole. The label `rbac.crossplane.io/aggregate-to-crossplane` folds it into Crossplane's own role, so no separate binding is needed.

### A rule that was accepted but granted nothing
- **Symptom:** Namespace creation was still forbidden after I added the role.
- **Cause:** I had listed `namespaces` under the `postgresql.cnpg.io` API group. A rule applies every listed resource to every listed group, and Namespaces live in the core group (`""`). Subresources such as `clusters/status` are also separate resource strings.
- **Fix:** one rule per API group, with `clusters/status` included.
- **Lesson:** Kubernetes accepts rules that match nothing without a warning. The error messages list exactly what Crossplane asks for, so copy from them. The Cluster error was also masking the Namespace error, because Crossplane reports one failure per reconcile.

### Events looked current but were minutes old
- **Lesson:** events are history. Check the XR's current `status.conditions`, the `SYNCED` and `READY` columns, and whether the composed objects exist before changing anything.

### `spec.size: field not declared in schema`
- **Symptom:** the API server rejected the composed Cluster.
- **Cause:** my template put `size` directly under `spec`, but CNPG's storage size lives under `spec.storage.size`.
- **Why render missed it:** `crossplane render` never talks to a real API server, so it cannot check a resource against its CRD.
- **Fix:** move the field.

### Readiness and sequencing
- **Symptom:** `namespaces ... not found` when the Cluster was applied. After I added `function-sequencer`, the log said creation was being delayed because the namespace was not fully ready.
- **Cause:** in a function pipeline nothing is ready until something says so. The Namespace never became ready, so the sequencer waited forever.
- **Fix:** add `function-auto-ready` before the sequencer. It treats a Namespace as ready once it exists.
- **Not tested:** whether the sequencer is strictly needed, or whether Crossplane's own retries would have settled the ordering. The earlier "Namespace never created" symptom was really the RBAC problem above, so the ordering theory was never isolated.

### `crossplane render` showed only the first resource
- **Cause:** with nothing observed, the Namespace never counts as ready, so the sequencer holds everything else back.
- **Lesson:** render is good for checking template output (names, labels, storage) and bad for anything that depends on a live cluster: RBAC, readiness, webhooks. A passing render has been wrong twice.

### The RoleBinding failed four separate ways
1. **No namespace on the object.** The XR is cluster-scoped, so a namespaced composed resource needs an explicit `metadata.namespace`.
2. **Missing verbs.** The Crossplane warning listed `update` and `delete` as missing on `rolebindings`.
3. **Privilege escalation prevention.** Kubernetes refused to create a binding to `edit` because Crossplane did not hold all of `edit`'s permissions. The fix was the `bind` verb on `clusterroles`, restricted with `resourceNames` to that one role.
4. **Readiness.** A RoleBinding has no status, so `function-auto-ready` never marked it ready. The fix is the `gotemplating.fn.crossplane.io/ready: "True"` annotation on that resource only. I confirmed it was needed, after finding a source claiming otherwise, from the package docs and the live cluster.

### The RoleBinding existed but access checks said `no`
- **Cause:** a `User` subject only matches an identity with that exact user name, and `--as-group` presents group membership.
- **Fix:** `kind: Group` with `apiGroup: rbac.authorization.k8s.io`.

### The `bind` permission was too wide, and a test caught it
- **Symptom:** `kubectl auth can-i bind clusterroles/cluster-admin --as=system:serviceaccount:crossplane-system:crossplane` said `yes`. It should have said `no`. Provisioning worked fine, which is why nothing had flagged it.
- **Cause:** the `clusterroles` rule had the `bind` verb but no `resourceNames`, so Crossplane could bind any ClusterRole in the cluster.
- **Fix:** add `resourceNames: ["edit"]`. Afterwards `can-i bind` said `yes` for `clusterroles/edit` and `no` for both `cluster-admin` and a made-up role name, which shows the restriction is by name.
- **Lesson:** a permission that works is not evidence that it is narrow. Test the negative cases.

## The form and CI

### Disabling the template's workflow did not stop the generated repo's copy
- **Cause:** generated repos get their own copy of the workflow, so switching off the template's CI changes nothing for them.
- **Lesson:** the template's CI had to be understood, not just switched off.

### The first form draft would have failed several ways
Found by reading it before running it:
- jobs with no `needs:` ran in parallel, so validation gated nothing;
- it waited for a `:latest` image that CI never pushes (CI pushes only `sha-<short>` tags);
- text was compared as a number;
- inputs were pasted into the script text, which allows script injection. Inputs now reach scripts through `env:`.

### The first run failed on the push to the monorepo
- **Symptom:** a permissions error on the push.
- **Likely cause:** my understanding is that a `permissions:` block inside a workflow overrides the repository default. The template's workflow declares one and the form did not.
- **Fix:** `permissions: contents: write` on the job that pushes. The failing log was not saved, so treat the cause as likely rather than proven.

### A Docker build failed on a missing `go.sum` entry
- **Symptom:** the Docker build's `go vet` step failed on a missing `go.sum` entry for pgx, even though `go test` passed locally and `go.sum` was committed.
- **Cause:** the Dockerfile's `COPY` lines omitted `go.sum`. The base image also had to move to Go 1.25, because the pgx version needs it.
- **Fix:** `COPY go.mod go.sum ./` and the new base image. The template's `setup-go` step was also updated to 1.25.
- **Lesson:** a build can fail on a file that exists locally if the Dockerfile or `.dockerignore` does not bring it into the build.

### Timing: which timestamp to trust
- **Cause:** an Application's `operationState.startedAt` is overwritten by any later sync.
- **Fix:** I use the child Application's `creationTimestamp` as the zero point for platform time.
- **Related (cluster rebuilds):** the root Application's `operationState` says little about a rebuild. In two builds its sync spanned every child (385 s and 432 s), so the timing script named the root as the slowest sync; in the first build a later sync had overwritten it (477 s to 477 s). The README's per-Application figures leave the root out.

## Vault and External Secrets

### Vault's Kubernetes auth login returned 403 and logged nothing
- **Symptom:** `vault write auth/kubernetes/login` returned `403 permission denied`, and Vault logged nothing at the default level.
- **Cause:** found by reading `vault read auth/kubernetes/config`: `kubernetes_host` was `https://127.0.0.1:6443`, which from inside a pod points at the pod itself. It came from a command that an AI assistant gave me and I did not check.
- **Fix:** set it to `https://kubernetes.default.svc:443`.
- **Lesson:** read the config instead of theorizing. Along the way I also went down two wrong diagnoses (the issuer being `https`, a stale token), both suggested by the assistant.

### The first `PushSecret` failed with a 403
- **Symptom:** a 403 on a `PUT` to `secret/metadata/tenants/form-test/db`.
- **Cause:** the `eso` policy lacked `create` and `update` on `secret/metadata/tenants/*`. I had been told it covered the push without checking.
- **Fix:** add those capabilities on the metadata path rather than widening the policy.
- **Lesson:** read the denial message, which names the path, and check what a provider needs before saying a policy is enough.

## Networking and observability

### A request through Traefik returns 502
- **Symptom:** `http://<service>.localhost:8081` returns `502 Bad Gateway`.
- **Cause:** the service namespace has a `default-deny` NetworkPolicy, which blocks incoming traffic, including traffic from Traefik's pods in `kube-system`.
- **Fix:** the shared chart creates an `allow-traefik` NetworkPolicy that lets Traefik reach the service's pods on the container port. Once it synced, requests reached the service. If the 502 comes back, list the namespace's policies with `kubectl get networkpolicy -n <service-namespace>`. `default-deny` and `allow-traefik` should both be there. If `allow-traefik` is missing, check that the service's Argo CD Application has synced.

### A service has no row on Prometheus's Targets page
- **Symptom:** the service's `/metrics` endpoint works, but Prometheus's Targets page has no row for it at all. A row that says DOWN would be a different problem.
- **Cause:** the ServiceMonitor did not match the Service. In the chart's first version, the endpoint used `targetPort: http`, and Prometheus created no target.
- **Fix:** the endpoint uses `port: http`, which refers to the Service's port name. The chart's Service names its port `http`, so the two match, and the target appeared.
- **Telling the two cases apart:**
  - A missing row means the ServiceMonitor did not select the Service. Check the endpoint's `port` field, the label selector, and the `release: kube-prometheus-stack` label that Prometheus selects on.
  - A DOWN row means Prometheus found the target but could not scrape it. Check the `allow-prometheus` NetworkPolicy in the service namespace.

### A dashboard query returns nothing in Explore
- **Symptom:** a query copied from a dashboard panel, such as `rate(http_requests_total{namespace="$namespace"}[5m])`, returns no data in Grafana's Explore view.
- **Cause:** `$namespace` is a variable defined on the dashboard. Explore has no dashboard variables, so nothing fills it in.
- **Fix:** replace `$namespace` with the namespace name, for example `namespace="dev-env-3"`.
