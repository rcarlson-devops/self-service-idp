# Investigation 001: missing `db-ca-push` and the Composition revision

**Date:** 2026-10-21
**Status:** Mechanisms ruled out on the rebuilt cluster. Cause of the original failure: **Open** (not reproduced).
**Cluster:** `k3d-dev-cluster`. Crossplane CLI client v2.5.0, server v2.4.2 (a one-minor-version gap; read-only commands only).

## Question

During the feature-branch work, the database CA push resource (`db-ca-push`) was missing and the database TLS certificate did not work. Was the cause the branch workflow (the change never reached the cluster), a pinned composite resource (the change never rolled out), or a pipeline error?

## What was checked

| Check                                                    | Result                                                                                                                                                                                                   | Label                                      |
| -------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------ |
| Does every Application that tracks this repo use `main`? | Yes. Two Applications use `spec.sources` (plural), so the singular `spec.source` column was blank for them. Both list this repo at `main`. External Helm charts are pinned to chart versions on purpose. | Verified                                   |
| Are all Applications Synced and Healthy?                 | Yes (zero rows were not both).                                                                                                                                                                           | Verified                                   |
| How many CompositionRevisions exist?                     | One: `database-composition-633ea9d`, revision 1, age 2 days (the cold rebuild from `main`).                                                                                                              | Verified                                   |
| Where do the revision fields live?                       | `PostgresDatabase` (`rcarlsondevops.org/v1alpha1`) is cluster-scoped. The fields are `spec.crossplane.compositionRevisionRef`, `compositionRevisionSelector` and `compositionUpdatePolicy`.              | Verified                                   |
| Is the XR pinned?                                        | No. `compositionUpdatePolicy` is `Automatic`, and its `compositionRevisionRef` names `database-composition-633ea9d`, the only revision.                                                                  | Verified                                   |
| Does `db-ca-push` exist now?                             | Yes, as `PushSecret/db-ca-push` in `remediation-test-1-db`. Its trace row contains `True`, and a count of `False` or `Unknown` over the whole tree was 0.                                                | Verified (derived from two outputs)        |
| Are there Warning events on the XR?                      | 0. Kubernetes keeps events for about an hour by default, and the cluster has run for two days, so this says nothing about the original failure.                                                          | Verified (count); retention figure Assumed |

Six trace rows (`NetworkPolicy` x4, `RoleBinding`, `Namespace`) show `-` for Synced and Ready. These are native Kubernetes kinds with no Ready condition, and the `Namespace` status is only `{"phase":"Active"}`. They are not unhealthy.

## Conclusion

1. **Ruled out on the rebuilt cluster:** a branch that never reached the cluster (every Application tracks `main`) and a pinned XR (the policy is `Automatic` and only one revision exists). The pinned-XR mechanism could not have occurred here, because there is nothing older to pin to.
2. **Not established:** why `db-ca-push` was missing the first time. The original cluster no longer exists. A cold rebuild from `main` produced the resource and a working certificate with no manual fixes (**Observed**, one run). That suggests the failure came from the in-place, feature-branch situation and not from the Composition itself. It is a hypothesis.
3. **The original failure is recorded as not reproduced.** No cause is claimed.

## Open

- Whether a Composition edit on a running cluster rolls out to an existing XR under `Automatic`. Testing this needs a deliberate change on a throwaway resource. It is not done.
- `crossplane render` was not run. In CLI v2.5.0 the `trace` command moved to `crossplane resource trace`, and no top-level `render` command appeared in the help search. A local render test that CI can run belongs in P0.3 or P2.3, in `crossplane/api/database/tests/`.

## Commands run

`kubectl get applications -n argocd`, `kubectl get compositionrevisions`, `kubectl get postgresdatabases -o yaml`, `kubectl explain postgresdatabase.spec --recursive`, `crossplane resource trace postgresdatabase remediation-test-1`, `kubectl describe postgresdatabase remediation-test-1`.