# One-time setup

This page covers everything you do **once** before the platform works. The GitHub-side steps (1 to 3) survive a cluster rebuild. Steps 4 to 6 are what you re-run whenever you rebuild the cluster. Step 7 describes the manifests that move the database password from CloudNativePG through Vault to the app. They are kept in Git, in `vault-and-eso/`, but Argo CD does **not** sync that folder, so you apply them by hand after a rebuild.

Order matters: do the GitHub setup first, then bootstrap the cluster, then initialize Vault, then configure it for External Secrets, then apply the credentials manifests.

## Prerequisites

- Docker, `k3d` and `kubectl` installed and working.
- A GitHub account that owns two repositories: this one (`self-service-idp`) and the template repo (`go-app-template`).
- The GitHub CLI (`gh`) and `yq` (the mikefarah/Go version, v4) if you want to run the checks in this guide locally. Two unrelated tools are both called `yq`; check with `yq --version` that yours says `mikefarah/yq`.

## 1. Create the template repo

The form creates every new service from a template repository, so it must exist first.

1. Create a **public** repository named `go-app-template` under your account.
2. Push the template contents: the `app/` folder (Go source and tests, `go.mod`, `go.sum`, `Dockerfile`, a README), `environments/dev/values-dev.yaml`, `.github/workflows/build-and-push.yml`, a README and a `.gitignore`. Both `go.mod` and `go.sum` must be committed: the Docker build copies them, and the build fails without `go.sum`.
3. In the repo, open **Settings > General** and tick **Template repository**.
4. Disable the build workflow **on the template repo itself** (Actions tab, select the workflow, choose Disable). This stops the template publishing its own image.
   - Disabling it here does **not** stop the copy inside each generated repo, which is what you want: generated repos build their own images.
5. Confirm it is pushed: run `git fetch && git status -sb` inside the clone. You want `## main...origin/main` with no `ahead` or `behind` marker.

## 2. Create the access token (PAT)

The workflow needs a token that can create repositories from the template. The built-in `GITHUB_TOKEN` cannot do this (it is scoped to the repo the workflow runs in), so you create a personal access token.

1. Open **GitHub > Settings > Developer settings > Personal access tokens > Fine-grained tokens** and choose **Generate new token**.
2. Set:
   - **Resource owner:** your account.
   - **Expiration:** no expiry date for now (my choice). In real use, set the shortest expiry that suits you.
   - **Repository access:** all repositories I own. This is broad; see the trade-off below.
   - **Permissions:** no user permissions. Repository permissions: read access to code and metadata, and read and write access to administration.
3. Copy the token **once**, straight into your password manager. Never paste it into a file, a chat, a commit or a shell startup file.

Why this is a trade-off: a token that can create repositories is broad. It is tied to your account, here it never expires, and it is stored only as an Actions secret. A GitHub App is the at-scale replacement (see the main README's "what's next" section).

## 3. Store the token as an Actions secret

1. In the monorepo, open **Settings > Secrets and variables > Actions**.
2. Choose **New repository secret**.
3. Name it `REPO_CREATOR_PAT` (the form workflow reads this exact name) and paste the token.

Also make sure the form workflow can write to the repo: it must declare `permissions: contents: write`, because it commits the new service's files to `argocd/tenants/`.

### Testing the token from your own terminal (optional)

`gh` reads the token from the `GH_TOKEN` environment variable. Set it for one terminal session only, reading the value from your password manager:

```
export GH_TOKEN=...      # paste from the password manager, in this session only
gh api --method POST -H "Accept: application/vnd.github+json" \
  /repos/<your-account>/go-app-template/generate \
  -f name=rest-test -F private=false
unset GH_TOKEN
```

Notes:

- Use `-F` (not `-f`) for `private` so it is sent as a boolean.
- Do not add `export GH_TOKEN=...` to `~/.zshrc` or any other startup file.
- Delete the test repo afterwards, and its package (see "Cleaning up" below).

## 4. Bootstrap the cluster

This is the part you repeat after every rebuild.

1. From the repo root, run `bootstrap/bootstrap.sh`. It creates the k3d cluster if it is missing, installs Argo CD, waits for Argo's workloads, and applies `argocd/root.yaml`.
2. Argo CD then installs everything else from Git. Wait until every Application **except Vault** is Synced and Healthy. Vault stays not Healthy until you initialize and unseal it in step 5:
   ```
   kubectl get applications -n argocd
   ```
   You should see `cloudnativepg-operator`, `crossplane-functions`, `crossplane-operator`, `database-api`, `external-secrets-operator`, `hashicorp-vault`, `self-service-idp-root` and `self-service-idp-tenants`, plus one Application per service you have created.
   - The Vault Application is `argocd/apps/00-operators/hashicorp-vault.yaml` (Helm chart 0.34.1). It deploys into the `vault` namespace.
   - The External Secrets Application is `external-secrets-operator` in `argocd/apps/00-operators/external-secrets.yaml` (Helm chart 2.12.0). It deploys into the `external-secrets` namespace.
   - The Vault Application is named `hashicorp-vault`, and its values set `global.tlsDisable: true`, so Vault serves plain HTTP inside the cluster.
3. The Argo CD UI is at `https://localhost:8080` (self-signed certificate; the bootstrap script suggests trying `http://` if https fails). The first admin password is generated at install time and is not in Git:
   ```
   kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d
   ```

Because services and databases are defined in Git (`argocd/tenants/`), a rebuild brings them back automatically. The exceptions are Vault (its keys, its configuration and its contents belong to one cluster build; see steps 5 and 6) and the per-service credentials manifests in `vault-and-eso/` (step 7).

The `vault-backend` store (`argocd/apps/30-workloads/vault-backend.yaml`) is applied by the root Application together with the External Secrets Application, and it needs the External Secrets CRDs. It has not been tested on a fresh rebuild since it was added. If the store is missing or the root Application reports a sync error about an unknown kind after a rebuild, sync the root Application again once External Secrets is Healthy.

## 5. Initialize and unseal Vault

Vault runs in **standalone mode**. This is a deliberate choice: it is much closer to a production deployment than dev mode, which initializes and unseals itself. In standalone mode a new Vault starts **uninitialized and sealed**, and its pod is not Ready until you initialize and unseal it by hand. Argo CD cannot do this for you, and the keys it produces must never be committed to Git.

The Vault pod is `vault-0` in the namespace `vault`. If you ever need to confirm that:

```
kubectl get pods -A | grep vault
```

1. **Check the state.** A new Vault reports `Initialized false` and `Sealed true`. The command may exit with a non-zero code while Vault is sealed; that is expected.
   ```
   kubectl exec -n vault vault-0 -- vault status
   ```
2. **Initialize, once.** This prints 5 unseal keys and an initial root token, one time only. By default the unseal key is split into 5 shares and any 3 of them unseal Vault.
   ```
   kubectl exec -n vault vault-0 -- vault operator init
   ```
   Copy all 5 keys and the root token into `pass` (the Linux password store) immediately. Do not redirect the output into a file in this repo, paste it into a chat, or commit it. Your terminal scrollback also holds it, so clear it afterwards.
3. **Unseal, three times, with three different keys.** Run this three times. It prompts for a key without echoing it, which keeps the key out of your shell history; do not pass the key as a command-line argument.
   ```
   kubectl exec -it -n vault vault-0 -- vault operator unseal
   ```
4. **Verify.** `vault status` should now report `Initialized true` and `Sealed false`, and the Vault Application in Argo CD should turn Healthy:
   ```
   kubectl exec -n vault vault-0 -- vault status
   kubectl get applications -n argocd
   ```

Things to know:

- **Init is once per Vault, not once per restart.** Running it on an already initialized Vault is refused. If the Vault pod restarts it comes back sealed (the normal behavior without auto-unseal), and you repeat only step 3.
- **A cluster rebuild means a new init.** After `k3d cluster delete` and a fresh bootstrap, the old keys are useless and Vault's old contents are gone. Repeat steps 1 to 4 and store the new keys. Anything that lived only in Vault has to be put back; the plan is that database passwords are repopulated from the CloudNativePG Secrets, which stay the source of truth.
- **The root token is for one-time setup only.** Use it to configure Vault (step 6), then stop using it for day-to-day work.

Why a sealed Vault matters: External Secrets cannot read from a sealed Vault, so a sealed Vault stops new services from receiving their credentials and stops refreshes until someone unseals it. Secrets that were already synced keep working, and a restarted service pod still starts, because it needs the Secret and not Vault. This was tested once; the results are under "Known limits" at the end of this page.

## 6. Configure Vault for External Secrets

A freshly initialized Vault holds nothing and trusts nobody. This step teaches it three things: where secrets live (a KV engine), how External Secrets proves who it is (Kubernetes auth), and what that identity may touch (a policy and a role). It is also the only step where you use the root token, so do it in one sitting.

**This configuration lives inside Vault, not in Git.** A cluster rebuild loses it, so you repeat this whole step after every rebuild. (Putting it in a script is a possible later improvement; a script must never contain the root token or the unseal keys.)

### 6a. Open a shell in the Vault pod and log in with the root token

Run the Vault commands from inside the pod. `vault login` prompts for the token without echoing it, so it stays out of your shell history. Read the root token from `pass` and paste it at the prompt (do not put it on the command line):

```
kubectl exec -it -n vault vault-0 -- sh
vault login
```

If `vault token lookup` shows anything other than the root token, an older token is in the way: run `unset VAULT_TOKEN`, `rm -f ~/.vault-token`, then `vault login` again.

**Optional: use the Vault CLI on your laptop instead of the pod.** Forward the port and give each command its own token, read from `pass`, so nothing is written to `~/.vault-token`. Do not run `vault login` locally, because it saves the token to that file.

```
kubectl port-forward -n vault svc/vault 8200:8200      # leave running in one terminal
export VAULT_ADDR=http://127.0.0.1:8200
VAULT_TOKEN=$(pass show <entry>) vault <command>        # one command, one token
```

The commands in 6b to 6e work the same way. This page still shows the pod form because it needs nothing installed locally.

### 6b. Enable the KV secrets engine (version 2)

Secrets go under the mount `secret/` (singular). KV version 2 stores the data at `secret/data/...` and its metadata at `secret/metadata/...`, which matters for the policy in 6d.

```
vault secrets enable -path=secret kv-v2
```

### 6c. Enable Kubernetes auth and point it at the cluster API

```
vault auth enable kubernetes
vault write auth/kubernetes/config kubernetes_host="https://kubernetes.default.svc:443"
```

**Do not build `kubernetes_host` from `127.0.0.1` or from the pod's environment variables.** Inside a pod, `127.0.0.1` is the pod itself, so Vault would ask itself to validate the token instead of the Kubernetes API, and every login fails with `403 permission denied`. Use the in-cluster address above. Vault checks tokens using its own pod's service account and CA certificate, so nothing else needs configuring here.

### 6d. Write the policy

The policy `eso` lets the identity write and read secrets under `secret/tenants/` and nothing else:

```
vault policy write eso - <<EOF
path "secret/data/tenants/*" {
  capabilities = ["create", "read", "update"]
}
path "secret/metadata/tenants/*" {
  capabilities = ["create", "read", "update", "list"]
}
EOF
```

The path scheme is `secret/tenants/<service>/db`. One policy covers both writing a secret into Vault and reading it back.

The metadata path needs `create` and `update`, not just `read` and `list`: when a `PushSecret` writes a secret, External Secrets also writes to `secret/metadata/...`, and with read-only metadata access the push fails with `403 permission denied` on a `PUT` to that path. The policy deliberately has no `delete`.

### 6e. Create the role

The role says which Kubernetes identity may log in and what it gets. It binds the service account `external-secrets` in the namespace `external-secrets` (the service account of the External Secrets controller) to the policy `eso`, for one hour per token:

```
vault write auth/kubernetes/role/eso \
  bound_service_account_names=external-secrets \
  bound_service_account_namespaces=external-secrets \
  policies=eso \
  ttl=1h
```

Leave the pod shell with `exit` when you are done.

### 6f. Prove it works, and prove the limit holds

Test from your laptop with a one-shot command. It asks Kubernetes for a short-lived token for the External Secrets service account, logs in as that identity **inside the pod**, and runs the checks. The Vault token it receives never leaves the pod, so there is nothing to copy or leak:

```
kubectl exec -n vault vault-0 -- env -u VAULT_TOKEN sh -c '
  export VAULT_TOKEN=$(vault write -field=token auth/kubernetes/login role=eso jwt="$1")
  vault kv put secret/tenants/test/db password=x
  vault kv get secret/tenants/test/db
  vault kv put secret/other/x a=b
' sh "$(kubectl create token external-secrets -n external-secrets)"
```

You want the login to work, the `put` and `get` on `secret/tenants/test/db` to succeed, and the `put` on `secret/other/x` to be refused with `403 permission denied`. The refusal is the point: it shows the identity cannot write outside `secret/tenants/`.

Then delete the test secret with the root token. The `eso` policy cannot delete, which is deliberate. Open a root shell as in 6a and run (note `secret`, not `secrets`):

```
vault kv metadata delete secret/tenants/test/db
```

### 6g. Point External Secrets at Vault

External Secrets reads from Vault through a `ClusterSecretStore` named `vault-backend`. It uses Kubernetes auth, so **it holds no token and no secret at all**:

```yaml
apiVersion: external-secrets.io/v1
kind: ClusterSecretStore
metadata:
  name: vault-backend
spec:
  provider:
    vault:
      server: http://vault.vault.svc.cluster.local:8200
      path: secret
      version: v2
      auth:
        kubernetes:
          mountPath: kubernetes
          role: eso
          serviceAccountRef:
            name: external-secrets
            namespace: external-secrets
```

- `server` is Vault's in-cluster Service address. Confirm the Service name with `kubectl get svc -n vault`. Never use `127.0.0.1` here either: inside the External Secrets pod that address is the pod itself.
- This manifest is kept in the monorepo at `argocd/apps/30-workloads/`, so Argo applies it. The External Secrets CRDs must already exist (they come from the External Secrets Application), so the store has to sync after them.

Check that the store is accepted. It should report Valid and Ready, and Vault must be unsealed:

```
kubectl get clustersecretstore vault-backend
kubectl describe clustersecretstore vault-backend
```

### Rules for this step

- **Never put a Vault token in a Kubernetes Secret or a file.** An early attempt stored the root token in a Secret (`vault-token`) so the store could log in. That defeats the purpose of the root token being for one-time setup, because anyone who can read that Secret owns Vault. It was deleted, along with the local file that held the token, and replaced by Kubernetes auth. If a root token ever lands in a file, a Secret or a chat, revoke it (`vault token revoke`) and generate a new one.
- **Never paste a token into a chat.** Accessors and key names are safe to share; the token itself is not. A token that has been pasted somewhere should be revoked, not left to expire.
- **When a Kubernetes-auth login fails, read the config instead of guessing.** Vault logs nothing about a failed login at its default log level, so a `403` tells you very little. Run `vault read auth/kubernetes/config` and `vault read auth/kubernetes/role/eso` and compare them with 6c and 6e. A wrong `kubernetes_host` was the cause the one time this failed.
- **Test from a one-shot command (6f), not from an interactive shell.** A login typed by hand into an interactive `sh` inside the pod failed once for a reason that was never found, while the one-shot form worked every time.

## 7. Sync the database password into the app

The app reads its database credentials from a Kubernetes Secret named `db-credentials` in its own namespace, with the keys `username`, `password` and `dbname` (the chart's `secretKeyRef` entries point at exactly that name). Nobody types these values. CloudNativePG generates the password and keeps it in a Secret named `postgres-db-app` in the database namespace (`<service>-db`). Two External Secrets objects carry it to the app, with Vault in between:

```
postgres-db-app            (namespace <service>-db, made by CloudNativePG)
   |  PushSecret
   v
Vault: secret/tenants/<service>/db
   |  ExternalSecret
   v
db-credentials             (namespace <service>, read by the app)
```

CloudNativePG stays the source of truth. If Vault is wiped, for example by a cluster rebuild, the `PushSecret` writes the password back.

Before you start: step 6 is done, Vault is unsealed and `kubectl get clustersecretstore vault-backend` shows Valid.

### 7a. The PushSecret (database namespace)

A `PushSecret` reads a Secret from **its own namespace**, so it lives in `<service>-db`, next to `postgres-db-app`. Example for the service `form-test`, with one `match` per key:

```yaml
apiVersion: external-secrets.io/v1alpha1
kind: PushSecret
metadata:
  name: db-credentials
  namespace: form-test-db
spec:
  secretStoreRefs:
    - name: vault-backend
      kind: ClusterSecretStore
  selector:
    secret:
      name: postgres-db-app
  data:
    - match:
        secretKey: password
        remoteRef:
          remoteKey: tenants/form-test/db
          property: password
    - match:
        secretKey: username
        remoteRef:
          remoteKey: tenants/form-test/db
          property: username
    - match:
        secretKey: dbname
        remoteRef:
          remoteKey: tenants/form-test/db
          property: dbname
```

- `kind: ClusterSecretStore` is required because the store reference defaults to a namespaced `SecretStore`.
- `remoteKey` is the path **under the mount** (`tenants/form-test/db`, not `secret/tenants/...`), because the store sets `path: secret`.
- Each `match` is a separate write, so the first push creates three versions of the Vault secret within milliseconds. That is expected.
- `apiVersion` is whatever your cluster serves; check with `kubectl get crd pushsecrets.external-secrets.io -o jsonpath='{.spec.versions[*].name}'`.
- The push needs the `eso` policy from 6d, including `create` and `update` on the **metadata** path. Without them it fails with `403 permission denied` on a `PUT` to `secret/metadata/tenants/...`.

Check it: `kubectl get pushsecrets -n <service>-db` should show `Synced`. If it is `Errored`, `kubectl describe pushsecret db-credentials -n <service>-db` names the failing path or field.

### 7b. Check what landed in Vault

Read field names and non-secret values only, never print the password. With the root token (see the rules in step 6):

```
vault kv metadata get secret/tenants/<service>/db
vault kv get -field=dbname secret/tenants/<service>/db
```

You should see the three keys merged into one secret, with the database name as the `dbname` value.

### 7c. The ExternalSecret (app namespace)

An `ExternalSecret` creates a Secret in **its own namespace**, so this one lives in `<service>`. This is the shape; your manifest in `vault-and-eso/` is the source of truth:

```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: db-credentials
  namespace: form-test
spec:
  refreshInterval: 15s
  secretStoreRef:
    name: vault-backend
    kind: ClusterSecretStore
  target:
    name: db-credentials
    creationPolicy: Owner
  data:
    - secretKey: username
      remoteRef:
        key: tenants/form-test/db
        property: username
    - secretKey: password
      remoteRef:
        key: tenants/form-test/db
        property: password
    - secretKey: dbname
      remoteRef:
        key: tenants/form-test/db
        property: dbname
```

- `target.name` must be `db-credentials` with exactly the keys `username`, `password` and `dbname`. Without `target.name`, the Secret takes the `ExternalSecret`'s own name.
- `refreshInterval` is `15s` in the committed manifest. A short interval made the 5g test quick to read. The `PushSecret` is separate and refreshes hourly by default.
- If a hand-made Secret of the same name exists, delete it first.
- Check: `kubectl get externalsecret -n <service>` shows ready, and `kubectl get secret db-credentials -n <service>` exists.

### 7d. Prove the app uses it

A pod reads its environment variables from the Secret when it **starts**. A pod that was already running keeps whatever it had, so restart it before you test:

```
kubectl rollout restart deploy/<service> -n <service>
kubectl port-forward -n <service> deploy/<service> 9000:8080
curl -s localhost:9000/db
```

`{"configured":true,"connected":true,...}` from the restarted pod proves the synced Secret works. If the Secret is missing the pod sits in `CreateContainerConfigError`, which is also how you spot a broken sync.

### 7e. Where these manifests live

For now the `PushSecret` and `ExternalSecret` are kept in `vault-and-eso/` in the monorepo. That is a holding place, not a production design: each new service needs its own pair, so in production they would be generated per service instead of written by hand. How to automate that is an open design question.

The files are `vault-and-eso/push-secret-template.yaml` and `vault-and-eso/external-secret-template.yaml`. Despite the name, they are not templates yet: both are written for the service `form-test`, with its name hard-coded. For another service, copy them and change the namespaces and the `tenants/<service>/db` path.

Argo CD does **not** sync `vault-and-eso/` (confirmed: no Application points at it). After a cluster rebuild, apply the pieces in this order: the store is Valid (steps 5 and 6 done), then the `PushSecret` (it repopulates Vault from the CloudNativePG Secret; the `<service>-db` namespace and `postgres-db-app` must already exist), then the `ExternalSecret`:

```
kubectl apply -f vault-and-eso/push-secret-template.yaml
kubectl apply -f vault-and-eso/external-secret-template.yaml
```

Until the credentials Secret exists, a service pod cannot start. I expect it to sit in `CreateContainerConfigError` (not yet observed for a freshly generated service).

## Where things live

| What | Where |
|---|---|
| Platform Applications (pointers only) | `argocd/apps/` |
| Vault Application (`hashicorp-vault`, chart 0.34.1, namespace `vault`) | `argocd/apps/00-operators/hashicorp-vault.yaml` |
| External Secrets Application (`external-secrets-operator`, chart 2.12.0, namespace `external-secrets`) | `argocd/apps/00-operators/external-secrets.yaml` |
| Generated services and database requests | `argocd/tenants/<service>/` |
| Blueprints the form fills in | `self-service/templates/` (never synced by Argo) |
| Shared Helm chart | `charts/app/` |
| Secret for repo creation | Actions secret `REPO_CREATOR_PAT` |
| Vault unseal keys and initial root token | `pass` only, never in Git |
| Vault configuration (KV engine, Kubernetes auth, policy `eso`, role `eso`) | Inside Vault only; redo step 6 after a rebuild |
| Tenant secrets in Vault | `secret/tenants/<service>/db` |
| ClusterSecretStore `vault-backend` | `argocd/apps/30-workloads/` |
| `PushSecret` and `ExternalSecret` per service (interim home; applied by hand, not synced by Argo) | `vault-and-eso/` |
| Per-service progress and design notes | `docs/progress-notes/` |

## Cleaning up a test service

Removing a service is a Git change, and Argo CD prunes automatically, so **deleting a service's folder under `argocd/tenants/` deletes its Deployment and, if it has one, its database and the data in it.**

1. Delete `argocd/tenants/<service>/` (keep `argocd/tenants/.gitkeep`; Argo errors on an empty or missing folder), commit and push.
2. Wait for the next Argo poll, then confirm it is gone: `kubectl get applications -n argocd` and `kubectl get postgresdatabase`.
3. Delete the service's GitHub repository.
4. Delete its container package separately. A package is not removed with its repository (profile > Packages > the package > Package settings > Danger Zone).
5. If the service has a secret in Vault, delete it by hand with the root token, since deleting the folder does not touch Vault: `vault kv metadata delete secret/tenants/<service>/db`. Also remove the service's `PushSecret` and `ExternalSecret` manifests from `vault-and-eso/`.

## Known limits

- Argo CD polls Git every 3 minutes, so a new service can take up to that long to appear. A webhook would remove the wait, but a local k3d cluster is not reachable from GitHub.
- Generated service repositories are public.
- The repository-creation token is broad, as described in step 2.
- Vault is a local approximation of a production deployment. It runs in standalone mode as a single replica, so there is no fault tolerance, and it talks plain HTTP inside the cluster (this version has no TLS).
- Vault is unsealed by hand. Production would use auto-unseal backed by a cloud KMS or HSM; that is not available on a local, free-tier setup, and a script that holds the unseal keys would defeat the point. After every Vault pod restart or cluster rebuild a person has to unseal it, so the rebuild is not fully hands-off.
- The unseal keys and root token are held by one person in `pass`. In production the key shares would be split between several people. `pass` encrypts with a GPG key, so back up that key safely: losing it means losing the Vault keys and root token.
- Vault's configuration is not in Git, so every rebuild means repeating step 6 by hand.
- All tenants share one External Secrets identity (the role `eso`). That identity can read every tenant's path under `secret/tenants/`, so a service is not limited to reading only its own secret. A per-tenant role and store would fix this, but something would have to create them for each new service; that is left for when the credentials path is automated.
- **Sealed Vault, tested once.** Deleting the Vault pod left Vault sealed with its data intact (it is on a persistent volume). While it was sealed: the already-synced `db-credentials` Secret stayed in place; the running service kept its database connection; a restarted service pod still came up; new `ExternalSecret`s could not sync (`503`); and the `vault-backend` store reported errors, though its status lagged the seal at first. After the unseal, syncing resumed with no action, in about 4 minutes 19 seconds in that one run. So a sealed Vault blocks new services and credential changes, not running ones. Not tested: what a `PushSecret` does while Vault is sealed.
- The `PushSecret` and `ExternalSecret` are written by hand per service and kept in `vault-and-eso/`. That is an interim holding place, not a production pattern: in production they would be generated for each new service. Automating it is open.
- A pod reads its credentials Secret when it starts, so a changed password reaches a running pod only after a restart (believed from standard Kubernetes behavior; not tested here). Nothing restarts pods automatically yet. Whether CloudNativePG ever rotates the password by itself has not been checked.
- The reserved-name list in the form (`validate` job) is missing `hashicorp-vault`, `external-secrets-operator`, `kube-public` and `kube-node-lease`.
