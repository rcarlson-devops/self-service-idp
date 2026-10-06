# One-time setup

This page covers everything you do **once** before the platform works. The GitHub-side steps (1 to 3) survive a cluster rebuild. Step 4 is what you re-run whenever you rebuild the cluster.

Order matters: do the GitHub setup first, then bootstrap the cluster.

## Prerequisites

- Docker, `k3d` and `kubectl` installed and working.
- A GitHub account that owns two repositories: this one (`self-service-idp`) and the template repo (`go-app-template`).
- The GitHub CLI (`gh`) and `yq` (the mikefarah/Go version, v4) if you want to run the checks in this guide locally. Two unrelated tools are both called `yq`; check with `yq --version` that yours says `mikefarah/yq`.

## 1. Create the template repo

The form creates every new service from a template repository, so it must exist first.

1. Create a **public** repository named `go-app-template` under your account.
2. Push the template contents (the `app/` folder, `environments/dev/values-dev.yaml`, `.github/workflows/build-and-push.yml`, a README and a `.gitignore`).
3. In the repo, open **Settings > General** and tick **Template repository**.
4. Disable the build workflow **on the template repo itself** (Actions tab, select the workflow, choose Disable). This stops the template publishing its own image.
   - Disabling it here does **not** stop the copy inside each generated repo, which is what you want: generated repos build their own images.
5. Confirm it is pushed: run `git fetch && git status -sb` inside the clone. You want `## main...origin/main` with no `ahead` or `behind` marker.

## 2. Create the access token (PAT)

The workflow needs a token that can create repositories from the template. The built-in `GITHUB_TOKEN` cannot do this (it is scoped to the repo the workflow runs in), so you create a personal access token.

1. Open **GitHub > Settings > Developer settings > Personal access tokens > Fine-grained tokens** and choose **Generate new token**.
2. Set:
   - **Resource owner:** your account.
   - **Expiration:** the shortest that suits you. TODO: record the expiry you chose.
   - **Repository access:** TODO: record the scope you chose (for example, all repositories or selected repositories).
   - **Permissions:** TODO: record the exact permissions you granted. GitHub's documentation for the "create a repository using a template" endpoint lists the permission combinations it accepts; recheck it when you create the token.
3. Copy the token **once**, straight into your password manager. Never paste it into a file, a chat, a commit or a shell startup file.

Why this is a trade-off: a token that can create repositories is broad. It is tied to your account, it expires, and it is stored only as an Actions secret. A GitHub App is the at-scale replacement (see the main README's "what's next" section).

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
2. Argo CD then installs everything else from Git. Wait until every Application is Synced and Healthy:
   ```
   kubectl get applications -n argocd
   ```
   You should see `cloudnativepg-operator`, `crossplane-functions`, `crossplane-operator`, `database-api`, `self-service-idp-root` and `self-service-idp-tenants`, plus one Application per service you have created.
3. The Argo CD UI is at `http://127.0.0.1:8080`. The first admin password is generated at install time and is not in Git:
   ```
   kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d
   ```

Because services and databases are defined in Git (`argocd/tenants/`), a rebuild brings them back automatically. Nothing from the cluster itself needs saving.

## Where things live

| What | Where |
|---|---|
| Platform Applications (pointers only) | `argocd/apps/` |
| Generated services and database requests | `argocd/tenants/<service>/` |
| Blueprints the form fills in | `self-service/templates/` (never synced by Argo) |
| Shared Helm chart | `charts/app/` |
| Secret for repo creation | Actions secret `REPO_CREATOR_PAT` |

## Cleaning up a test service

Removing a service is a Git change, and Argo CD prunes automatically, so **deleting a service's folder under `argocd/tenants/` deletes its Deployment and, if it has one, its database and the data in it.**

1. Delete `argocd/tenants/<service>/` (keep `argocd/tenants/.gitkeep`; Argo errors on an empty or missing folder), commit and push.
2. Wait for the next Argo poll (about 3 minutes), then confirm it is gone: `kubectl get applications -n argocd` and `kubectl get postgresdatabase`.
3. Delete the service's GitHub repository.
4. Delete its container package separately. A package is not removed with its repository (profile > Packages > the package > Package settings > Danger Zone).

## Known limits

- Argo CD polls Git about every 3 minutes, so a new service can take up to that long to appear. A webhook would remove the wait, but a local k3d cluster is not reachable from GitHub.
- Generated service repositories are public.
- The repository-creation token is broad, as described in step 2.
