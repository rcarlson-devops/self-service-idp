#!/usr/bin/env bash
# Bootstrap the platform from nothing: cluster -> Argo CD -> root app.
# Everything after the final step is deployed by Argo CD from Git.
#
# Usage (from the repo root):
#   export GHCR_USER=<github-username>
#   export GHCR_TOKEN=<PAT with read:packages>   # needed while the hello-world image is private
#   ./bootstrap/bootstrap.sh
#
# Safe to re-run: each step is skipped or re-applied idempotently.
set -euo pipefail

CLUSTER_NAME="dev-cluster"
APP_NAMESPACE="hello-world-dev"

cd "$(dirname "$0")/.."

for bin in k3d kubectl docker; do
  command -v "$bin" >/dev/null || { echo "missing required tool: $bin" >&2; exit 1; }
done

# Rebuilds only work if Argo CD can read the repo without a credential.
if grep -q 'git@github.com' argocd/root.yaml argocd/apps/*.yaml 2>/dev/null; then
  echo "WARNING: argocd/ manifests use an SSH repoURL. Argo CD will not be able to" >&2
  echo "         sync on a fresh cluster without a registered deploy key. Switch to" >&2
  echo "         https://github.com/rcarlson-devops/self-service-idp.git (public repo)." >&2
fi

echo "==> 1/5 k3d cluster"
if k3d cluster list "$CLUSTER_NAME" >/dev/null 2>&1; then
  echo "    cluster '$CLUSTER_NAME' already exists, skipping create"
else
  k3d cluster create --config bootstrap/k3d-config.yaml
fi
kubectl config use-context "k3d-${CLUSTER_NAME}" >/dev/null
kubectl wait --for=condition=Ready nodes --all --timeout=120s

echo "==> 2/5 Argo CD (pinned via bootstrap/argocd/kustomization.yaml)"
kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -n argocd -k bootstrap/argocd --server-side --force-conflicts

echo "==> 3/5 waiting for Argo CD to become ready"
kubectl -n argocd rollout status deploy/argocd-server --timeout=300s
kubectl -n argocd rollout status deploy/argocd-repo-server --timeout=300s
kubectl -n argocd rollout status deploy/argocd-applicationset-controller --timeout=300s
kubectl -n argocd rollout status statefulset/argocd-application-controller --timeout=300s

echo "==> 4/5 image pull secret for private GHCR package"
# TEMPORARY manual step: secrets must not live in Git. Replace with External
# Secrets in Phase 4 and delete this block.
if [[ -n "${GHCR_USER:-}" && -n "${GHCR_TOKEN:-}" ]]; then
  kubectl create namespace "$APP_NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -
  kubectl -n "$APP_NAMESPACE" create secret docker-registry ghcr-cred \
    --docker-server=ghcr.io \
    --docker-username="$GHCR_USER" \
    --docker-password="$GHCR_TOKEN" \
    --dry-run=client -o yaml | kubectl apply -f -
else
  echo "    GHCR_USER / GHCR_TOKEN not set: skipping. The hello-world pod will sit in"
  echo "    ImagePullBackOff until the 'ghcr-cred' secret exists in $APP_NAMESPACE."
fi

echo "==> 5/5 root application (hands control to Git)"
kubectl apply -f argocd/root.yaml

cat <<MSG

Done. Argo CD UI:  https://localhost:8080  (self-signed cert; try http:// if https fails)
Admin password:    kubectl -n argocd get secret argocd-initial-admin-secret \\
                     -o jsonpath='{.data.password}' | base64 -d; echo
Watch sync:        kubectl -n argocd get applications -w
MSG
