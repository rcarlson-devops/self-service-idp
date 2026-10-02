# Phase 2 progress notes: self-service infrastructure

Written 2026-10-02, reconstructed from the working session (no notes were kept while building, so details like exact commands and screenshots are not included).

## Result

A developer submits one small `PostgresDatabase` manifest. Crossplane then creates, with no manual steps:

- a namespace (`<name>-db`),
- a CloudNativePG (CNPG) `Cluster` named `postgres-db` inside it,
- a RoleBinding giving the team's group the built-in `edit` role in that namespace only.

Exit criterion met: a request results in a fully provisioned namespace, database, and access with no manual `kubectl` against the infrastructure.

### Metrics

| Measurement | Result | Conditions |
|---|---|---|
| Bootstrap from nothing (`bootstrap.sh`, cluster created from scratch) | ~2 min 30 s | Includes all Argo CD Applications synced and Crossplane ready. No manual steps. |
| First request on a freshly built cluster | ~1 min 45 s | Images not yet cached. Image pulls are the likely cause of the gap; I did not check the pull events. |
| Request on a warm cluster, final Composition | ~47 s | Namespace, 2-instance cluster, RoleBinding, `READY=True`. |
| Earlier warm runs (before RoleBinding) | 52 s, 46 s, 43 s | The 52 s run stopped the clock at XR `Ready`. The other two waited for XR `Ready` and pods running. |

Headline: about 45 to 50 seconds from `kubectl apply` to a usable database on a warm local cluster.

## What I built

- **`PostgresDatabase` API (the XRD).** Three inputs: `spec.team`, `spec.instances`, `spec.size`. The XR's `metadata.name` names the database. Validation is enforced by the schema: `team` must be a valid lowercase DNS-style label (max 63 characters), `instances` is required and must be 1 to 3, `size` is `small`, `medium`, or `large` (default `small`).
- **The Composition.** A Pipeline-mode Composition using `function-go-templating`, `function-auto-ready`, and `function-sequencer`. It emits the Namespace, the CNPG Cluster, and the RoleBinding.
- **Permissions for Crossplane.** One aggregated ClusterRole (labeled `rbac.crossplane.io/aggregate-to-crossplane: "true"`) granting what Crossplane needs for Namespaces, CNPG Clusters, and RoleBindings, plus a narrow `bind` permission on the one ClusterRole it hands out.
- **Developer docs.** `docs/database-request-format.md` describes the request, allowed values, what gets created, and how `size` works.

## Design decisions

| Decision | Reason |
|---|---|
| No Crossplane provider in Phase 2 | The Composition emits the Namespace, Cluster, and RoleBinding directly as Kubernetes objects, so no cloud or database provider is needed on a local cluster. |
| `function-go-templating` | I already knew Helm-style templates, and the XRD is the contract, so the implementation can be swapped later. |
| Small API (three inputs) | Fewer inputs mean fewer ways to get a request wrong, and caps are enforced at admission by the schema. |
| T-shirt `size`, mapped in the Composition | The mapping (1Gi / 2Gi / 3Gi) can change without breaking the contract. `size` sets storage only, not CPU or memory. |
| `size` is per instance | CNPG gives each instance its own volume, so total storage is `size` times `instances`. Worst case (`large`, 3 instances) is 9Gi. |
| Namespace per database, named `<name>-db` | The suffix avoids collisions with `kube-system`, `argocd`, and similar. |
| Fixed Cluster name (`postgres-db`) in every namespace | The generated Secret and Service names (`-app`, `-rw`, `-ro`, `-r`) derive from the Cluster name, so templates and docs can hard-code them. |
| `Group` subject for the RoleBinding | In a real cluster, an identity provider puts people into groups. Binding a group avoids editing the Composition when people join or leave. |
| `bind` on one named ClusterRole, not `escalate` and not copying its permissions | Keeps Crossplane from becoming a near-admin just to hand out namespace access. |

## Problems hit

Each problem is written as symptom, cause, and how it was found or fixed. These are the interview stories.

### 1. Crossplane could not create the Namespace
- **Symptom:** `cannot compose resources` with a `forbidden` error for the `crossplane` ServiceAccount.
- **Cause:** Crossplane v2 needs explicit permission to compose any kind beyond its defaults.
- **Fix:** an aggregated ClusterRole. The label `rbac.crossplane.io/aggregate-to-crossplane` folds it into Crossplane's own role, so no separate binding is needed.

### 2. A rule that was accepted but granted nothing
- **Symptom:** Namespace creation still forbidden after adding the role.
- **Cause:** I had listed `namespaces` under the `postgresql.cnpg.io` API group. A rule applies every resource to every listed group, and Namespaces live in the core group (`""`). Also, subresources such as `clusters/status` are separate resource strings from `clusters`.
- **Fix:** one rule per API group, with `clusters/status` included. (The final role has no `namespaces/status` entry, even though a Crossplane warning once asked for it, and provisioning works without it.)
- **Lesson:** Kubernetes accepts rules that match nothing without any warning. The error messages list exactly what Crossplane asks for, so copy from them. Also, the Cluster error was masking the Namespace error, since Crossplane reports one failure per reconcile.

### 3. Events can be stale
- **Symptom:** Warnings that looked current but were minutes old.
- **Lesson:** Events are history. Check the XR's current `status.conditions`, the `SYNCED` and `READY` columns, and whether the composed objects exist before changing anything.

### 4. `spec.size: field not declared in schema`
- **Symptom:** The API server rejected the composed Cluster.
- **Cause:** My template put `size` directly under `spec`. CNPG's storage size lives under `spec.storage.size`.
- **Why render missed it:** `crossplane render` never talks to a real API server, so it can't check a resource against its CRD.
- **Fix:** move the field.

### 5. Readiness and sequencing
- **Symptom:** `namespaces ... not found` when the Cluster was applied, then, after adding `function-sequencer`, the log said "Delaying creation ... because namespace is not fully ready".
- **Cause:** In a function pipeline nothing is ready until something says so. The Namespace never became ready, so the sequencer waited forever.
- **Fix:** add `function-auto-ready` before the sequencer. It treats a Namespace as ready once it exists.
- **Caveat I did not test:** whether the sequencer is strictly needed, or whether Crossplane's own retries would have settled the ordering. The earlier "Namespace never created" symptom was really the RBAC problem in #2, so the ordering theory was never isolated.

### 6. `crossplane render` hides resources behind the sequencer
- **Symptom:** Render output showed only the first resource.
- **Cause:** With nothing observed, the Namespace never counts as ready, so the sequencer holds everything else back.
- **Takeaway:** render is good for checking template output (names, labels, storage) and bad for anything that depends on a live cluster: RBAC, readiness, webhooks. A passing render has now been wrong twice (#4 and this one).

### 7. The RoleBinding, four separate failures
1. **No namespace on the object.** The XR is cluster-scoped, so a namespaced composed resource needs an explicit `metadata.namespace`. Errors: "empty namespace may not be set when a resource name is provided".
2. **Missing verbs.** The Crossplane warning listed `update` and `delete` as missing on `rolebindings`.
3. **Privilege escalation prevention.** Kubernetes refused to create a binding to `edit` because Crossplane didn't hold all of `edit`'s permissions. The error printed the entire role's contents. Fix: the `bind` verb on `clusterroles`, restricted with `resourceNames` to the one role. Without `resourceNames`, Crossplane could bind `cluster-admin`. (My first version left `resourceNames` out; see #9.)
4. **Readiness.** A RoleBinding has no status, so `function-auto-ready` (which checks for a `Ready` condition on kinds it doesn't know) never marked it ready. Fix: the `gotemplating.fn.crossplane.io/ready: "True"` annotation on that resource only. It's accurate there, because an existing RoleBinding is as ready as it can be, and would be wrong on the database. I confirmed the annotation was needed, after finding a source claiming otherwise; the package docs and the live cluster both disagreed with it.

### 8. Subject kind: `User` instead of `Group`
- **Symptom:** The RoleBinding existed, but `kubectl auth can-i get pods --as=anyone --as-group=example-team` said `no`.
- **Cause:** a `User` subject only matches an identity with that exact user name. `--as-group` presents group membership.
- **Fix:** `kind: Group` with `apiGroup: rbac.authorization.k8s.io`.

### 9. The `bind` rule was too wide, and a test caught it
- **Symptom:** while closing out the phase, `kubectl auth can-i bind clusterroles/cluster-admin --as=system:serviceaccount:crossplane-system:crossplane` said `yes`. It should have said `no`.
- **Cause:** the `clusterroles` rule had the `bind` verb but no `resourceNames`, so Crossplane could bind any ClusterRole in the cluster. Provisioning worked fine, which is why nothing had flagged it. A wildcard I suspected on the CNPG `clusters` rule was a red herring: it only covers that API group and resource.
- **Fix:** add `resourceNames: ["edit"]` to that rule.
- **Verified after the fix:** `can-i bind` says `yes` for `clusterroles/edit` and `no` for both `clusterroles/cluster-admin` and a made-up role name. The made-up name matters, because it shows the restriction is by name and not a special case for `cluster-admin`.
- **Lesson:** a permission that works is not evidence that it's narrow. Test the negative cases for any security claim before stating it.

## Validation tests

I ran seven server-side dry-run tests against the XRD. All were rejected with clear messages: invalid `size`, missing `team`, `instances: 0`, `instances: 500`, a `team` with a space, a `team` with a leading hyphen, and a request with no `spec` at all. The no-`spec` case is rejected even without a top-level `required: [spec]` in the schema; I recorded the behavior without knowing the reason. The old `xpsdatabases` XRD and CRD were confirmed gone after the rename.

## Reproducibility

I deleted the cluster and ran `bootstrap.sh`. The cluster came up with every Application synced and Crossplane ready, with zero manual steps. A request applied to the rebuilt cluster worked on the first try. (The script's private-registry branch, which reads `GHCR_USER` and `GHCR_TOKEN`, is skipped because the package is public, so it wasn't exercised.)

## Still to verify

- [x] `kubectl auth can-i bind clusterroles/cluster-admin --as=system:serviceaccount:crossplane-system:crossplane` returns `no` (after the `resourceNames` fix in #9; it said `yes` before)
- [x] `kubectl auth can-i bind clusterroles/made-up-name ...` returns `no`, and `bind clusterroles/edit` returns `yes`
- [x] `kubectl auth can-i get pods -n kube-system --as=anyone --as-group=example-team` returns `no`
- [x] RoleBinding sequencing: decided to leave it chained behind the database (it waits for both the namespace and the database), and not to split it into its own rule.
- [ ] After the `resourceNames` fix, delete and re-apply the example XR and confirm the RoleBinding is still created and the XR reaches `READY=True`, and that the `--as-group` check in the team namespace still says `yes`.

## What I'd do at scale

- Narrow the Namespace permission from `*` to the verbs Crossplane actually uses; as written, Crossplane could delete any namespace, including `kube-system`.
- Per-team quotas (`ResourceQuota` or Kyverno) so aggregate storage and instance counts are limited per team.
- Conditional synchronous replication (only when `instances > 1`); CNPG rejects it at one instance.
- Let `size` also control CPU and memory.
- Backups, and a resize path (the current storage class doesn't allow volume expansion).
- Read-only and admin role options, not one `edit` role for every team.
- Enforce the name-length limit on `metadata.name` (the namespace suffix reduces it from 63).

## Interview talking points from this phase

- **Abstraction layers:** the XRD as contract and the Composition as implementation; small API, t-shirt sizes, caps in the schema.
- **Security in a self-service platform:** Crossplane's own permissions kept narrow (named `bind`, not `escalate`), and team access scoped to one namespace through a group binding.
- **Debugging a control plane:** reading current state instead of stale events, knowing what a local render can and can't prove, and checking claims against the real system.
