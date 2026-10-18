# Requesting a Postgres database

You ask for a database by submitting one small `PostgresDatabase` manifest. The platform creates a namespace for it, runs a Postgres cluster inside it, and generates the credentials and connection endpoints. You do not file a ticket or touch the cluster's infrastructure.

Time from submit to usable database on the local cluster: 76 seconds for the first service on a freshly built cluster and 32 seconds for the second, measured from the request's creation to its Ready condition (two self-service form runs on 2026-10-17, on the same cluster). I have not established why the first took longer; a first-time image pull is a candidate that I did not check.

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

There are two ways to submit it:

- **Through the self-service form** (the normal path). The form creates the service's repository and commits this manifest, filled in for you, to `argocd/tenants/<service>/database.yaml`; Argo CD applies it. The form sets `metadata.name` to the service name and `spec.team` to the team name you entered, so the namespace is `<service>-db` and the group that gets `edit` is the team.
- **By hand:** `kubectl apply -f <file>`, or commit finished YAML under `argocd/tenants/`. The manifest is the same either way.

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
| Team access | RoleBinding `team-edit` | In that namespace. Grants the group named by `spec.team` the `edit` role there. |
| Postgres cluster | `postgres-db` | In that namespace. The name is the same in every namespace. |
| Credentials | Secret `postgres-db-app` | In that namespace |
| Primary (reads and writes) | Service `postgres-db-rw` | In that namespace |
| Replicas only (reads) | Service `postgres-db-ro` | In that namespace |
| Any instance (reads) | Service `postgres-db-r` | In that namespace |
| Storage | One volume per instance | In that namespace |

Connect applications to the `-rw` Service for anything that writes, and read the credentials from the `-app` Secret. From another namespace, use the full name `postgres-db-rw.<namespace>.svc.cluster.local`: the `host` value inside the `-app` Secret is the short name `postgres-db-rw`, which does not resolve across namespaces. The database name and user are both `app`.

A pod cannot read a Secret from another namespace, so a service does not read `postgres-db-app` directly. In the platform, the shared chart expects a Secret named `db-credentials` in the service's own namespace, with the keys `username`, `password` and `dbname`. For every service the platform copies the password there through Vault and External Secrets: the database Composition creates a `PushSecret` that writes it to Vault, and the shared chart creates an `ExternalSecret` that builds `db-credentials` in the service's namespace. The service's pod cannot start until that Secret exists; in the two runs on 2026-10-17 it appeared 5 s and 10 s after the database became Ready. See `docs/setup.md`, step 7.

The Cluster name is fixed, so these Secret and Service names are the same for every database you request. Only the namespace changes.

### Who can access it

Members of the group named in `spec.team` get the built-in `edit` role in the database's namespace, and nowhere else. They can work with the workloads, Services, and Secrets there (including reading the credentials Secret), but they cannot change role bindings or access other namespaces. Group membership comes from your identity provider; this request only says which group gets access.

### Checking status

```
kubectl get postgresdatabase
```

`READY=True` means the database pods are up and the database is usable. If it stays `False`, look at the events:

```
kubectl describe postgresdatabase <name>
```

## Deleting

Deleting the request removes the namespace and everything in it, **including the database and its data**. Through the platform, removing a service's folder from `argocd/tenants/` does the same, because Argo CD prunes automatically. The storage class reclaims volumes on delete, so there is no recovery step. Back up anything you need first.

## Not yet implemented

- **Finer-grained access:** every request grants the team group the same `edit` role. Read-only or admin roles, and per-person access, are not configurable yet.
- **Guardrails:** each service and its database namespace have network policies, the service namespace has a quota, and Kyverno enforces image signatures. Not built: egress policy and a quota on the database namespace.
- **Database-specific metrics:** I have not set up Postgres metrics such as connections or replication lag.
- **Backups and resizing:** not configured or supported.
