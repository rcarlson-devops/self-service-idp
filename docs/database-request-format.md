# Requesting a Postgres database

You ask for a database by submitting one small `PostgresDatabase` manifest. The platform creates a namespace for it, runs a Postgres cluster inside it, and generates the credentials and connection endpoints. You do not file a ticket or touch the cluster's infrastructure.

Typical time from submit to usable database on the local cluster: about 45 seconds with images cached, about 50 seconds on a cold node.

## The request

```yaml
apiVersion: rcarlsondevops.org/v1alpha1
kind: PostgresDatabase
metadata:
  name: example-postgresdatabase
spec:
  team: example-team
  instances: 2
  size: small
```

Submit it with `kubectl apply -f <file>`. (Submitting through Git and the Backstage portal comes in later phases; the manifest stays the same.)

## Fields

| Field | Required | Allowed values | Notes |
|---|---|---|---|
| `metadata.name` | Yes | Lowercase letters, digits, and `-` | Names the database **and** its namespace (see below). Keep it short: the namespace is `<name>-db`, and Kubernetes names max out at 63 characters. |
| `spec.team` | Yes | Lowercase letters, digits, and `-`; must start and end with a letter or digit; max 63 characters | The owning team. Stamped as the `team` label on everything the request creates. |
| `spec.instances` | Yes | Integer from 1 to 3 | Number of Postgres pods, counting the primary. `3` means 1 primary and 2 replicas. There is no default, so you must set it. |
| `spec.size` | No | `small`, `medium`, `large` | Storage per instance. Defaults to `small`. |

A request that breaks these rules is rejected immediately, before anything is created. The message names the field and the rule it broke. For example:

```
spec.size: Unsupported value: "huge": supported values: "small", "medium", "large"
spec.instances: Invalid value: 500: spec.instances in body should be less than or equal to 3
spec.team: Invalid value: "Payments Team": spec.team in body should match '^[a-z0-9]([-a-z0-9]*[a-z0-9])?$'
spec.team: Required value
```

A request with no `spec` at all is rejected too (`spec: Required value`).

### `size` is per instance

`size` sets the storage of **each** Postgres pod, not the total. Every instance keeps its own full copy of the data on its own volume.

| `size` | Storage per instance |
|---|---|
| `small` | 1Gi |
| `medium` | 2Gi |
| `large` | 3Gi |

Total storage is `size` multiplied by `instances`. For example, `small` with `instances: 3` uses 3Gi, and the largest possible request (`large` with `instances: 3`) uses 9Gi.

`size` controls storage only. CPU and memory are not set by it.

Pick `size` carefully: resizing a volume after creation is not supported on the current storage class.

## What you get

For the example request above, once `READY` shows `True`:

| What | Name | Where |
|---|---|---|
| Namespace | `example-postgresdatabase-db` | Cluster-wide |
| Postgres cluster | `postgres-db` | In that namespace. The name is the same in every namespace. |
| Credentials | Secret `postgres-db-app` | In that namespace |
| Primary (reads and writes) | Service `postgres-db-rw` | In that namespace |
| Replicas only (reads) | Service `postgres-db-ro` | In that namespace |
| Any instance (reads) | Service `postgres-db-r` | In that namespace |
| Storage | One volume per instance | In that namespace |

Connect applications to the `-rw` Service for anything that writes, and read the credentials from the `-app` Secret. From another namespace, the primary is reachable at `postgres-db-rw.<namespace>.svc`.

The Cluster name is fixed, so these Secret and Service names are the same for every database you request. Only the namespace changes.

### Checking status

```
kubectl get postgresdatabase
```

`READY=True` means the database pods are up and the database is usable. If it stays `False`, look at the events:

```
kubectl describe postgresdatabase <name>
```

## Deleting

Deleting the request removes the namespace and everything in it, **including the database and its data**. The storage class reclaims volumes on delete, so there is no recovery step. Back up anything you need first.

## Not yet implemented

- **Team access (RBAC):** the namespace is created, but per-team roles are not yet part of the request.
- **Guardrails:** policy checks (no root containers, required labels) arrive in Phase 4.
- **Secrets management:** credentials live in the generated Secret; integration with an external secrets store arrives in Phase 4.
- **Observability:** metrics and dashboards arrive in Phase 5.
- **Backups and resizing:** not configured or supported.
- **Self-service through the portal:** requesting a database from a form (Backstage) arrives in Phase 3.
