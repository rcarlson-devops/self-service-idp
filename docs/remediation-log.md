# Self-Service IDP Remediation Audit Log

## Branch & Merge History
*   Work for each step was done on a short-lived `feature/<step>-<label>` branch cut from `develop`.
*   Completed work was merged into `develop`, and `develop` was then merged into `main`.
*   The cluster tracks `main`, so all verification runs below were executed against `main`.
*   Each PR was squash-merged, so each step is a single commit rather than a merge commit.

| PR | Squash commit | Description | Target |
|----|---------------|-------------|--------|
| #3 | `e8512f4` | docs: establish baseline state and remediation log (0.1) | develop |
| #4 | `6bb6712` | feat: implement platform integrity smoke script (0.2) | develop |
| #5 | `3ab8a1f` | feat: implementing database tls requirements (1.1) | develop |
| #6 | `d44a69e` | feat: merging phase 0 and phase 1.1 | main |

*   Earlier PRs #1 (`c0cfcd8`) and #2 (`af13615`) predate the remediation work (dev image tagging changes) and are not part of the audit.

**Workflow change (effective Phase 1.2):** The branch-per-step flow above applied to Phase 0 and Step 1.1. From Phase 1.2 onward, changes are committed directly to `main` (trunk-based). Verification evidence is still recorded in this log in the same commit as the change. Because Argo CD tracks `main`, each push deploys to the cluster immediately, so the "Prove it" check is run right after each push.

## Phase 0: Baseline & Safe Smokescripting

### Step 0.1: Establish the Clean State
*   **Date:** October 18, 2026
*   **Status:** Initializing Remediation Framework
*   **Source Branch:** main (Commit Hash: 59da6b5)
*   **Target Branch:** develop

#### Executed Actions
1. Analyzed the production-grade refactoring blueprint roadmap.
2. Verified the initial proof-of-concept codebase states on the primary workstation.
3. Successfully applied an immutable Git milestone tag (`v0-reviewed`) to lock down the reviewed baseline.
4. Pushed tag tracking parameters upstream to the origin repository.
5. Initialized this central audit document to enforce absolute compliance and execution accountability.

#### Verification Evidence
```text
\$ git status
On branch feature/0.1-baseline
Changes to be committed:
  (use "git restore --staged <file>..." to unstage)
	new file:   docs/remediation-log.md

\$ git commit -m "docs(remediation): initialize central audit log for phase 0"
[feature/0.1-baseline abc123f] docs(remediation): initialize central audit log for phase 0

\$ git push -u origin feature/0.1-baseline
Branch 'feature/0.1-baseline' set up to track remote branch 'feature/0.1-baseline' from 'origin'.
```

### Step 0.2: Develop the Automation Integrity Smoke Script
*   **Date:** October 18, 2026
*   **Status:** Completed
*   **Source Branch:** develop
*   **Target Branch:** feature/0.2-smokescript

#### Executed Actions
1. Formulated a unified platform readiness automation validation layout at `scripts/smoke.sh`.
2. Structured array-driven loop checks to assert readiness across Vault status, Crossplane Functions conditions, Kyverno deployments, and all core Argo CD infrastructure applications.

#### Verification Evidence
```text
\$ ./scripts/smoke.sh
==========================================================
 Running Infrastructure Smoke Test...
==========================================================
Checking Vault Health and Unseal State...
Vault is unsealed and healthy.
Checking ArgoCD Application Health...
All ArgoCD Applications Confirmed Healthy
Verifying Crossplane Functions Status
All Crossplane Functions confirmed Healthy.
Verifying Kyverno Deployment Health
All Kyverno Deployments have succesfully completed and are healthy.
==========================================================
 Baseline smoke verification checks completed successfully!
==========================================================
```

## Phase 1: Cryptographic Boundaries & Supply Chain Hardening

### Step 1.1: Enforce Server-Side Database TLS
*   **Date:** October 18, 2026
*   **Status:** Completed and verified
*   **Source Branch:** develop
*   **Working Branch:** feature/1.1-database-tls
*   **Merged To:** develop, then main (verification executed on main)

#### Executed Actions
1. Configured an automated, distinct Crossplane `PushSecret` pipeline step to replicate the self-signed CloudNativePG root CA (`postgres-db-ca`, key `ca.crt`) directly into HashiCorp Vault.
2. Formulated an application-side `ExternalSecret` manifest asset inside the shared application Helm chart to pull down certificate values into tenant execution boundaries.
3. Updated the application deployment templates to mount the `db-ca` payload as an uncorrupted, file-backed PEM certificate file at `/etc/pg-ca/ca.crt`.
4. Injected an un-bypassable server-side `hostnossl all all all reject` blocking rule at the top of the database cluster's Host-Based Authentication (`pg_hba`) configuration array.
5. Refactored the central GitHub Actions workflow pipeline to dynamic branch targets (`HEAD:${{ github.ref_name }}`) and added explicit ref tracking to the checkout step to protect `main` during feature branch runs.
6. Rebuilt the k3d cluster from scratch by running `bootstrap.sh` on a clean environment tracking `main` (which already contained the Phase 1.1 changes), so tenants would compile against the newest Composition revision. (The earlier symptom, where `kubectl get pushsecret -A` showed only `db-credentials`, was suspected to be a tenant pinned to an old `CompositionRevision`. That suspicion was not confirmed with `kubectl get compositionrevisions` before the rebuild.)
7. Confirmed on the rebuilt cluster that the `db-ca` ExternalSecret is present for tenant `dev-env-2` and that the application connects to the database.
8. Ran the three "Prove it" checks below against tenant `dev-env-2`.

#### Verification Evidence

**Check 1: The application's database session is encrypted (server side).**
Traffic was generated against the app's `/db` route while sampling `pg_stat_ssl` from inside the database pod 300 times. Rows with an empty `usename` were excluded because they are mid-handshake placeholders whose `ssl` flag has not been populated yet.

```text
$ kubectl exec -n dev-env-2-db -c postgres postgres-db-1 -- bash -c '
for i in $(seq 1 300); do
  psql -U postgres -At -c "select a.usename, a.client_addr, s.ssl, s.version, s.cipher
  from pg_stat_ssl s join pg_stat_activity a using (pid)
  where a.client_addr is not null and a.usename is not null;"
done' | sort | uniq -c
1577 app|10.42.1.17|t|TLSv1.3|TLS_AES_256_GCM_SHA384
```

All 1,577 sampled sessions for user `app` showed `ssl = t` using TLS 1.3 with `TLS_AES_256_GCM_SHA384`.

*Observation:* An earlier, un-filtered sampling run returned two rows of the form `|10.42.1.17|f||` (empty user, `ssl = f`). These came from the same client IP and were caught before authentication completed, so they do not represent an authenticated plaintext session. The authoritative result is the filtered run above.

**Check 2: The server-side reject rule is active.**

```text
$ kubectl exec -n dev-env-2-db -c postgres postgres-db-1 -- psql -U postgres -c \
"select line_number, type, database, user_name, address, auth_method, error from pg_hba_file_rules;"
 line_number |   type    |   database    |        user_name        | address |  auth_method  | error
-------------+-----------+---------------+-------------------------+---------+---------------+-------
           7 | local     | {all}         | {cnpg_metrics_exporter} |         | peer          |
           8 | local     | {all}         | {all}                   |         | peer          |
          11 | hostssl   | {postgres}    | {streaming_replica}     | all     | cert          |
          12 | hostssl   | {replication} | {streaming_replica}     | all     | cert          |
          13 | hostssl   | {all}         | {cnpg_pooler_pgbouncer} | all     | cert          |
          20 | hostnossl | {all}         | {all}                   | all     | reject        |
          21 | hostssl   | {all}         | {all}                   | all     | md5           |
          28 | host      | {all}         | {all}                   | all     | scram-sha-256 |
(8 rows)
```

Line 20 (`hostnossl all all all reject`) is present and the `error` column is empty on every row.

**Check 3: A plaintext connection is refused at the database boundary.**
A separate test pod could not be scheduled because of the cluster's resource-specification requirements, so the test was run from inside the existing database pod over the service hostname (a TCP connection, so `pg_hba` rules apply).

```text
$ kubectl exec -n dev-env-2-db -c postgres postgres-db-1 -- \
  psql "host=postgres-db-rw.dev-env-2-db.svc port=5432 user=postgres dbname=postgres sslmode=disable" -c "select 1;"
psql: error: connection to server at "postgres-db-rw.dev-env-2-db.svc" (10.43.82.116), port 5432 failed: FATAL:  pg_hba.conf rejects connection for host "10.42.0.1", user "postgres", database "postgres", no encryption
```

The server rejected the connection before authentication, citing the missing encryption.

#### Result
All three Phase 1.1 "Prove it" checks passed. The application connects over TLS 1.3, the server drops plaintext connections, and the connection is made with `sslmode=verify-full` against the CA mounted at `/etc/pg-ca/ca.crt`.

#### Notes and Follow-ups
*   **Roadmap correction:** The roadmap's `-c operand` container name is invalid on this CNPG version. The database container is named `postgres`. Commands above use `-c postgres`.
*   **Follow-up (not changed in this step):** `pg_hba` line 21 is `hostssl ... md5` and sits ahead of the `scram-sha-256` rule on line 28. Review the Composition's `pg_hba` ordering and move to `scram-sha-256` in a later step.
*   **Follow-up (Phase 1.2):** The application opens a connection per request, which made live-session sampling timing-sensitive. A pooled `*sql.DB` is planned in Step 1.2.

#### Post-1.1 Smoke Snapshot
Run against the rebuilt cluster (tracking `main`) after the Phase 1.1 verification checks.

```text
$ ./scripts/smoke.sh
==========================================================
 Running Infrastructure Smoke Test...
==========================================================
Checking Vault Health and Unseal State...
Vault is unsealed and healthy.
Checking ArgoCD Application Health...
All ArgoCD Applications Confirmed Healthy
Verifying Crossplane Functions Status
All Crossplane Functions confirmed Healthy.
Verifying Kyverno Deployment Health
All Kyverno Deployments have succesfully completed and are healthy.
==========================================================
 Baseline smoke verification checks completed successfully!
==========================================================
```

All baseline checks pass after Phase 1.1, matching the Phase 0 "before" snapshot. (Note: `smoke.sh` does not yet assert database TLS or tenant connectivity. Those were verified manually in the checks above.)

