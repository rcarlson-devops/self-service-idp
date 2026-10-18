# One-time setup

This page covers everything you do **once** before the platform works. The GitHub-side steps (1 to 3) survive a cluster rebuild. Step 4 is what you re-run whenever you rebuild the cluster, and it also sets Vault up: steps 5 and 6 describe what the bootstrap does there and give the manual commands. Step 7 describes how the database password moves from CloudNativePG through Vault to the app. Nothing in step 7 is applied by hand: the database Composition and the shared chart create those objects for every service.

Order matters: do the GitHub setup first, then bootstrap the cluster, which also initializes Vault and configures it for External Secrets. After that, every service brings its own credentials objects.

## Prerequisites

- Docker, `k3d` and `kubectl` installed and working.
- A GitHub account that owns two repositories: this one (`self-service-idp`) and the template repo (`go-app-template`).
- The GitHub CLI (`gh`) and `yq` (the mikefarah/Go version, v4) if you want to run the checks in this guide locally. Two unrelated tools are both called `yq`; check with `yq --version` that yours says `mikefarah/yq`.
- `pass` (the Linux password store) set up with a GPG key. The bootstrap keeps Vault's root token and unseal keys there, and it runs `pass ls` first, so it stops early if `pass` does not work. Back up the GPG key (see Known limits).
- Python 3 with the `venv` module, for the Vault setup script (`bootstrap/scripts/src/configure_vault.py`). On Debian and Ubuntu that module is the separate `python3-venv` package. `bootstrap.sh` creates a virtual environment in `.venv` at the repo root and installs `bootstrap/scripts/requirements.txt` into it with `pip`, so the machine needs access to PyPI. Before it builds the cluster, the script tests that a virtual environment can be created, and stops with a message if not.
- Local port 18200 free. The Vault setup reaches `vault-0` through `kubectl port-forward` on that port, not through Traefik. The script checks that the port is free before it builds the cluster.
- On WSL2, enough memory. Memory in use peaked at 7.2 to 7.8 GiB during a build. My laptop has an Intel Core i7-1260P and 16 GB of RAM, and my `.wslconfig` sets `memory=10GB` and `swap=8GB`. Before I raised the limit the machine froze during a build; I believe it ran out of memory but did not confirm it. Close VS Code windows attached to WSL before `wsl --shutdown`, or they keep `vmmemWSL` alive.

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

The `decommission` workflow needs a second token to delete the service's image package: a classic personal access token with only the `delete:packages` scope (GitHub adds `read:packages` with it), stored as its own Actions secret. A fine-grained token could not delete packages. The secret's name is in the `decommission` workflow.

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

1. From the repo root, run `bootstrap/bootstrap.sh`. It creates the k3d cluster if it is missing, installs Argo CD, waits for Argo's workloads and applies `argocd/root.yaml`. It then sets Vault up (steps 5 and 6): before it builds anything it checks `pass ls`, that Python can create a virtual environment and that port 18200 is free, and later it waits for the `vault-0` pod to exist (up to `VAULT_WAIT_SECONDS`, default 1800, after which it stops with an error) and be Running, and reaches Vault through a port-forward on local port 18200. It is safe to re-run. Do not edit `bootstrap.sh` while it runs: an edit during one run produced `ubectl: command not found`.
   - It waits for the pod to be Running, not Ready, because a standalone Vault is not Ready until it is unsealed.
2. Argo CD then installs everything else from Git, in three sync waves. Wait until every Application is Synced and Healthy. On a cold build that took 8 to 10 minutes (480 s, 506 s and 598 s in the three builds I timed), almost all of it in wave 0:
   ```
   kubectl get applications -n argocd
   ```
   You should see 14 Applications, plus one per service you have created:
   - Wave 0: `cloudnativepg-operator`, `crossplane-operator`, `external-secrets-operator`, `hashicorp-vault`, `kube-prometheus-stack` and `kyverno`.
   - Wave 1: `crossplane-functions`, `database-api`, `grafana-dashboards`, `kyverno-policies`, `monitoring-ingress` and `vault-backend`.
   - Wave 2: `self-service-idp-tenants`. The root, `self-service-idp-root`, is the fourteenth.

   Notes on the Vault and External Secrets Applications:
   - The Vault Application is `argocd/apps/operators/hashicorp-vault.yaml` (Helm chart 0.34.1). It deploys into the `vault` namespace.
   - The External Secrets Application is `external-secrets-operator` in `argocd/apps/operators/external-secrets.yaml` (Helm chart 2.12.0). It deploys into the `external-secrets` namespace.
   - The Vault Application is named `hashicorp-vault`, and its values set `global.tlsDisable: true`, so Vault serves plain HTTP inside the cluster.
3. The Argo CD UI is at `https://localhost:8080` (self-signed certificate; the bootstrap script suggests trying `http://` if https fails). The first admin password is generated at install time and is not in Git:
   ```
   kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d
   ```
4. Grafana is at `http://grafana.localhost:8081`. The admin password is in a Secret in the `monitoring` namespace (`kubectl get secret -n monitoring | grep grafana` finds it), but on my cluster that password did not log in, and I did not find out why. If yours is refused too, reset the admin password with Grafana's CLI inside the pod:
   ```
   read -rs GRAFANA_PW        # type the new password; it stays out of shell history
   kubectl exec -n monitoring deploy/kube-prometheus-stack-grafana -c grafana -- \
     grafana cli admin reset-admin-password "$GRAFANA_PW"
   unset GRAFANA_PW
   ```
   Then log in as `admin` with the new password. The reset is stored on Grafana's 1Gi volume, which a cluster rebuild discards, so I expect to repeat it after a rebuild (not tested). Keep the new password in your password manager: it is not in Vault or Git.

Because services and databases are defined in Git (`argocd/tenants/`), a rebuild is meant to bring them back automatically. The three rebuilds I timed started with no services in `argocd/tenants/`, so I have not retested that since Vault's setup moved into the bootstrap. Vault is the exception: its keys, its configuration and its contents belong to one cluster build, so a rebuild gives a new Vault, which `bootstrap.sh` initializes and configures again (steps 5 and 6). Once that is done, the credentials objects of any returning services sync again (step 7).

The `vault-backend` store (`argocd/apps/platform/vault-backend.yaml`) needs the External Secrets CRDs and webhook, so it is in sync wave 1, after External Secrets (wave 0) is Healthy. It synced without a retry in each of the three rebuilds I timed. After a rebuild, check it with `kubectl get clustersecretstore vault-backend`, which should report Valid and Ready (see 6g).

## 5. Initialize and unseal Vault

Vault runs in **standalone mode**. This is a deliberate choice: it is much closer to a production deployment than dev mode, which initializes and unseals itself. In standalone mode a new Vault starts **uninitialized and sealed**, and its pod is not Ready until it is initialized and unsealed. Argo CD cannot do this, and the keys it produces must never be committed to Git, so `bootstrap.sh` does it, through `bootstrap/scripts/src/configure_vault.py`, and the keys go to `pass`.

The script reads Vault's `seal-status` and then does one of two things:

- **Not initialized (a new cluster).** It first writes and removes a probe entry in `pass`, so it fails before it initializes Vault if `pass` cannot store anything. It then initializes Vault with three key shares and a threshold of three, so all three keys are needed to unseal it and losing any one `pass` entry means losing access to that Vault. It saves the root token and the three unseal keys in `pass` (`infrastructure/vault/root_token` and `infrastructure/vault/unseal_key_1` to `unseal_key_3`), overwriting any entries with those names (a rebuild's new keys replace the old ones), unseals Vault and configures it (step 6).
- **Already initialized (a re-run, or a Vault pod that restarted).** It reads the keys from `pass`, which asks for your GPG passphrase, unseals Vault if it is sealed, and applies the configuration again.

Every Vault call is checked, and mounts are checked before they are created, so a re-run is safe. I ran this on a real cluster: the first run initialized Vault, and a re-run read the keys from `pass`, unsealed Vault and re-applied the configuration. Afterwards all 14 Applications were Synced and Healthy.

### Doing it by hand

If you cannot use the script, these are the manual commands. If you initialize by hand and later want `bootstrap.sh` to work, store the root token and three unseal keys under the same `pass` entries. The manual `vault operator init` below makes 5 shares with a threshold of 3, so store any three of the five as `unseal_key_1` to `unseal_key_3`; the script submits those three.

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

- **Init is once per Vault, not once per restart.** Running it on an already initialized Vault is refused. If the Vault pod restarts it comes back sealed (the normal behavior without auto-unseal), and you either re-run `bootstrap.sh` or repeat only step 3.
- **A cluster rebuild means a new init.** After `k3d cluster delete` and a fresh bootstrap, the old keys are useless and Vault's old contents are gone. The bootstrap initializes the new Vault and stores the new keys in `pass`; by hand, repeat steps 1 to 4 and store them yourself. Anything that lived only in Vault has to be put back; the plan is that database passwords are repopulated from the CloudNativePG Secrets, which stay the source of truth.
- **The root token is for Vault's setup, not for day-to-day work.** `bootstrap.sh` reads it from `pass` on every run to apply the configuration (step 6), so it is not revoked after setup. Nothing else uses it: External Secrets logs in with Kubernetes auth. See "Vault trust model" below.

Why a sealed Vault matters: External Secrets cannot read from a sealed Vault, so a sealed Vault stops new services from receiving their credentials and stops refreshes until someone unseals it. Secrets that were already synced keep working, and a restarted service pod still starts, because it needs the Secret and not Vault. This was tested once; the results are under "Known limits" at the end of this page.

## 6. Configure Vault for External Secrets

A freshly initialized Vault holds nothing and trusts nobody. This step teaches it three things: where secrets live (a KV engine), how External Secrets proves who it is (Kubernetes auth), and what that identity may touch (a policy and a role). It is also where the root token is used. If you do it by hand, do it in one sitting.

**This configuration lives inside Vault, not in Git.** A cluster rebuild loses it, so `bootstrap.sh` applies it again on every run, as the last part of its Vault setup (step 5). The script contains no secrets: the root token and the unseal keys come from `pass`. The commands in 6a to 6g show what the configuration consists of, and are the manual route.

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

The policy `eso` lets the identity write, read and delete secrets under `secret/tenants/` and nothing else:

```
vault policy write eso - <<EOF
path "secret/data/tenants/*" {
  capabilities = ["create", "read", "update", "delete"]
}
path "secret/metadata/tenants/*" {
  capabilities = ["create", "read", "update", "list", "delete"]
}
EOF
```

The path scheme is `secret/tenants/<service>/db`. One policy covers both writing a secret into Vault and reading it back.

The metadata path needs `create` and `update`, not just `read` and `list`: when a `PushSecret` writes a secret, External Secrets also writes to `secret/metadata/...`, and with read-only metadata access the push fails with `403 permission denied` on a `PUT` to that path. `delete` is there because the `PushSecret` the Composition creates uses `deletionPolicy: Delete`: when a service is removed, External Secrets deletes that service's entry from Vault, and Vault refuses unless the policy allows it. In a test, deleting the `PushSecret` removed the entry through the `metadata` path (the entry's `created_time` changed), so keep `delete` on both paths. Without it, the entry is not removed and the `PushSecret` reports an error.

**Cluster set up before 2026-10-13:** `vault policy write` replaces the whole policy, so run `vault policy read eso` first, then write the full text above. The role from 6e needs no change.

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

Then delete the test secret with the root token. Open a root shell as in 6a and run (note `secret`, not `secrets`):

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
- This manifest is kept in the monorepo at `argocd/apps/platform/`, so Argo applies it. The External Secrets CRDs and webhook must already exist (they come from the External Secrets Application), so the `vault-backend` Application is in sync wave 1, after External Secrets (wave 0) is Healthy.

Check that the store is accepted. It should report Valid and Ready, and Vault must be unsealed:

```
kubectl get clustersecretstore vault-backend
kubectl describe clustersecretstore vault-backend
```

Do not rely on Argo CD for this check. After I deleted `vault-0`, `vault-backend` and the `ClusterSecretStore` stayed Healthy in Argo CD while Vault was sealed. I believe, from a third-party source I did not verify, that Argo CD has no built-in health check for this kind, so `kubectl get clustersecretstore vault-backend` is the check.

### Rules for this step

- **Never put a Vault token in a Kubernetes Secret or a file.** An early attempt stored the root token in a Secret (`vault-token`) so the store could log in. That defeats the purpose of keeping the root token out of the cluster, because anyone who can read that Secret owns Vault. It was deleted, along with the local file that held the token, and replaced by Kubernetes auth. If a root token ever lands in a file, a Secret or a chat, revoke it (`vault token revoke`) and generate a new one.
- **Never paste a token into a chat.** Accessors and key names are safe to share; the token itself is not. A token that has been pasted somewhere should be revoked, not left to expire.
- **When a Kubernetes-auth login fails, read the config instead of guessing.** Vault logs nothing about a failed login at its default log level, so a `403` tells you very little. Run `vault read auth/kubernetes/config` and `vault read auth/kubernetes/role/eso` and compare them with 6c and 6e. A wrong `kubernetes_host` was the cause the one time this failed.
- **Test from a one-shot command (6f), not from an interactive shell.** A login typed by hand into an interactive `sh` inside the pod failed once for a reason that was never found, while the one-shot form worked every time.

## 7. Sync the database password into the app

The app reads its database credentials from a Kubernetes Secret named `db-credentials` in its own namespace, with the keys `username`, `password` and `dbname` (the chart's `secretKeyRef` entries point at exactly that name). Nobody types these values. CloudNativePG generates the password and keeps it in a Secret named `postgres-db-app` in the database namespace (`<service>-db`). Two External Secrets objects carry it to the app, with Vault in between. The `PushSecret` is created by the database Composition and the `ExternalSecret` by the shared chart, so a new service gets both without anyone applying them:

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

For this to work, step 6 must be done, Vault unsealed and `kubectl get clustersecretstore vault-backend` Valid.

### 7a. The PushSecret (database namespace)

A `PushSecret` reads a Secret from **its own namespace**, so it lives in `<service>-db`, next to `postgres-db-app`. The Composition creates it for each database request. This is the shape, for a service named `form-test`, with one `match` per key; the Composition is the source of truth:

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
  deletionPolicy: Delete
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
- `deletionPolicy: Delete` means removing the `PushSecret` also removes the Vault entry. That needs `delete` in the `eso` policy from 6d.
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

An `ExternalSecret` creates a Secret in **its own namespace**, so this one lives in `<service>`. The shared chart creates it (`charts/app/templates/external-secret.yaml`). This is the shape; the chart is the source of truth:

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
- If a hand-made Secret named `db-credentials` already exists in the namespace, delete it first.
- Check: `kubectl get externalsecret -n <service>` shows ready, and `kubectl get secret db-credentials -n <service>` exists.

### 7d. Prove the app uses it

A pod reads its environment variables from the Secret when it **starts**. A pod that was already running keeps whatever it had, so restart it before you test:

```
kubectl rollout restart deploy/<service> -n <service>
kubectl port-forward -n <service> deploy/<service> 9000:8080
curl -s localhost:9000/db
```

`{"configured":true,"connected":true,...}` from the restarted pod proves the synced Secret works. If the Secret is missing the pod sits in `CreateContainerConfigError`, which is also how you spot a broken sync.

### 7e. After a rebuild

Services and databases come back from Git, and with them the `PushSecret` (from the Composition) and the `ExternalSecret` (from the chart). With Vault initialized, configured and unsealed (steps 5 and 6), the `PushSecret` writes the password from the CloudNativePG Secret back into Vault, and the `ExternalSecret` then creates `db-credentials`. No order is enforced between these pieces. Until `db-credentials` exists, a service pod cannot start: in the two services I deployed on 2026-10-17 the pod reported `secret "db-credentials" not found` until the `ExternalSecret` created it, 5 s and 10 s after the database became Ready, and the pod was Ready 11 s and 14 s after that, with no action from me.

If a `PushSecret` still shows `Errored` after Vault is ready, `kubectl describe pushsecret db-credentials -n <service>-db` names the cause.

## Vault trust model

This section says what is stored where, who can use it, and how production would differ. The last part describes things I did not build.

What exists today:

- Vault runs as one replica in standalone mode, with plain HTTP inside the cluster. Its data is on a persistent volume in the cluster and is lost when the cluster is rebuilt.
- `bootstrap.sh` initializes Vault with three key shares and a threshold of three. It saves all three unseal keys and the root token in `pass`, encrypted to one GPG key. Anyone who can decrypt that store can unseal Vault and act as root. All three shares sit in the same place, so splitting the key gives no extra protection here.
- The bootstrap reads the root token from `pass` on every run to apply the configuration, so I do not revoke it after setup.
- External Secrets does not use the root token. It logs in with Kubernetes auth as the `external-secrets` service account and gets the `eso` policy, which covers `secret/tenants/*` and nothing else. I tested that a write outside that path is refused (6f).
- No Vault token is stored in a Kubernetes Secret or in Git. The script does not print tokens or keys, only the names of the `pass` entries.
- The script does not enable an audit device, so Vault keeps no audit log.

I think this is acceptable for a disposable local cluster whose Vault holds the passwords of test databases, and CloudNativePG stays the source of truth for those. That is my judgment, not the result of a threat model.

Not checked: whether the `vault-backend` store limits which namespaces may use it. If it does not, I expect, but did not test, that an `ExternalSecret` in any namespace could ask for any path the `eso` policy covers. That is the shared-role limit listed under "Known limits".

What production would do differently (none of this is built):

- Auto-unseal through a cloud KMS or HSM, so no person and no file holds the unseal key.
- Revoke the initial root token after setup, and create a new one with `vault operator generate-root` only when it is needed.
- Human access through named identities (for example OIDC) with their own policies, instead of a shared root token.
- An audit device, with the log shipped somewhere Vault's operators cannot edit it.
- TLS between External Secrets and Vault, a multi-replica Raft cluster with snapshots, and one role and policy per service.

## Where things live

| What | Where |
|---|---|
| Platform Applications (pointers only) | `argocd/apps/` |
| Vault Application (`hashicorp-vault`, chart 0.34.1, namespace `vault`) | `argocd/apps/operators/hashicorp-vault.yaml` |
| External Secrets Application (`external-secrets-operator`, chart 2.12.0, namespace `external-secrets`) | `argocd/apps/operators/external-secrets.yaml` |
| Generated services and database requests | `argocd/tenants/<service>/` |
| Blueprints the form fills in | `self-service/templates/` (never synced by Argo) |
| Shared Helm chart | `charts/app/` |
| Secret for repo creation | Actions secret `REPO_CREATOR_PAT` |
| Vault unseal keys and initial root token | `pass` only (`infrastructure/vault/`), never in Git |
| Vault configuration (KV engine, Kubernetes auth, policy `eso`, role `eso`) | Inside Vault only; `bootstrap.sh` applies it again on every run |
| Tenant secrets in Vault | `secret/tenants/<service>/db` |
| ClusterSecretStore `vault-backend` | `argocd/apps/platform/` |
| `PushSecret` per service (created for each database request) | the `PostgresDatabase` Composition in `crossplane/` |
| `ExternalSecret` per service | `charts/app/templates/external-secret.yaml` |
| Problems met and how they were solved | `docs/troubleshooting.md` |

## Cleaning up a test service

Removing a service is a Git change, and Argo CD prunes automatically, so **deleting a service's folder under `argocd/tenants/` deletes its Deployment and, if it has one, its database and the data in it.**

1. Delete `argocd/tenants/<service>/` (keep `argocd/tenants/.gitkeep` and the `tenants-sentinel` ConfigMap; Argo CD will not prune an Application down to nothing, so the sentinel is the manifest that always remains), commit and push.
2. Wait for the next Argo poll, then confirm it is gone: `kubectl get applications -n argocd` and `kubectl get postgresdatabase`.
3. Delete the service's GitHub repository.
4. Delete its container package separately. A package is not removed with its repository (profile > Packages > the package > Package settings > Danger Zone).
5. The service's `PushSecret` uses `deletionPolicy: Delete`, so removing the service also removes its Vault entry. Check that it is gone: `vault kv metadata get secret/tenants/<service>/db` should find nothing. If the entry is still there (for example Vault was sealed during the removal), delete it by hand with the root token: `vault kv metadata delete secret/tenants/<service>/db`.

## Known limits

- Argo CD polls Git every 3 minutes by default, so a new service can take about that long to appear (I measured 133 s and 196 s). A webhook would remove the wait, but a local k3d cluster is not reachable from GitHub.
- Generated service repositories are public.
- The repository-creation token is broad, as described in step 2.
- Vault is a local approximation of a production deployment. It runs in standalone mode as a single replica, so there is no fault tolerance, and it talks plain HTTP inside the cluster (this version has no TLS).
- Vault is not auto-unsealed. Production would use auto-unseal backed by a cloud KMS or HSM; that is not available on a local, free-tier setup, and a script that carried the unseal keys itself would defeat the point. `bootstrap.sh` reads them from `pass` at run time, which asks for the GPG passphrase, so a person with the GPG key still has to be there. After every Vault pod restart, someone has to re-run `bootstrap.sh` or unseal by hand.
- The unseal keys and root token are held by one person in `pass`. All three shares are needed to unseal Vault (three shares, threshold three) and they sit in the same store, so the split adds no protection here. In production the key shares would be split between several people. See "Vault trust model". `pass` encrypts with a GPG key, so back up that key safely: losing it means losing the Vault keys and root token.
- Vault's configuration is not in Git and Argo CD does not apply it; `bootstrap.sh` applies it on every run instead.
- All tenants share one External Secrets identity (the role `eso`). That identity can read, write and delete every tenant's path under `secret/tenants/`, so a service is not limited to its own secret. A per-tenant role and store would fix this, but something would have to create them for each new service; that is left for when the credentials path is automated.
- **Sealed Vault, tested once.** Deleting the Vault pod left Vault sealed with its data intact (it is on a persistent volume). While it was sealed: the already-synced `db-credentials` Secret stayed in place; the running service kept its database connection; a restarted service pod still came up; new `ExternalSecret`s could not sync (`503`); and the `vault-backend` store reported errors, though its status lagged the seal at first. After the unseal, syncing resumed with no action. So a sealed Vault blocks new services and credential changes, not running ones. Not tested: what a `PushSecret` does while Vault is sealed.
- Removing a service while Vault is sealed has not been tested.
- A pod reads its credentials Secret when it starts, so a changed password reaches a running pod only after a restart (believed from standard Kubernetes behavior; not tested here). Nothing restarts pods automatically yet. Whether CloudNativePG ever rotates the password by itself has not been checked.
- The reserved-name list in the form (`validate` job) is missing `hashicorp-vault`, `external-secrets-operator`, `kube-public` and `kube-node-lease`.
