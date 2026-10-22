#!/usr/bin/env bash
# Bootstrap the platform from nothing: cluster -> Argo CD -> root app -> Vault.
# Everything Argo CD deploys comes from Git. Vault is then initialised and
# configured by bootstrap/scripts/src/configure_vault.py, which saves the
# unseal keys and root token to 'pass'.
#
# Usage (from the repo root):
#   ./bootstrap/bootstrap.sh
#
# Safe to re-run: each step is skipped or re-applied idempotently.
set -euo pipefail

CLUSTER_NAME="dev-cluster"
VAULT_LOCAL_PORT=18200
VAULT_WAIT_SECONDS="${VAULT_WAIT_SECONDS:-1800}"

cd "$(dirname "$0")/.."

for bin in k3d kubectl docker python3 pass; do
  command -v "$bin" >/dev/null || { echo "missing required tool: $bin" >&2; exit 1; }
done

# Fail now, not after the cluster is built: the Vault keys go into 'pass'.
if ! pass ls >/dev/null 2>&1; then
  echo "'pass' is not initialised. Run: pass init <your-gpg-id>" >&2
  exit 1
fi

# Fail now: step 5 creates a virtual environment, and on Debian/Ubuntu the venv
# module is a separate package. Find out before the cluster is built.
venv_test="$(mktemp -d)"
if ! python3 -m venv "$venv_test/v" >/dev/null 2>&1; then
  rm -rf "$venv_test"
  echo "python3 cannot create a virtual environment. On Debian/Ubuntu: sudo apt install python3-venv" >&2
  exit 1
fi
rm -rf "$venv_test"

# Fail now: step 5 reaches Vault through a port-forward on this local port.
if (exec 3<>"/dev/tcp/127.0.0.1/${VAULT_LOCAL_PORT}") 2>/dev/null; then
  echo "local port ${VAULT_LOCAL_PORT} is already in use. Free it and re-run." >&2
  exit 1
fi

# Rebuilds only work if Argo CD can read the repo without a credential.
if grep -rq 'git@github.com' argocd/ 2>/dev/null; then
  echo "WARNING: argocd/ manifests use an SSH repoURL. Argo CD will not be able to" >&2
  echo "         sync on a fresh cluster without a registered deploy key. Switch to" >&2
  echo "         https://github.com/rcarlson-platform/self-service-idp.git (public repo)." >&2
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

echo "==> 4/5 root application (hands control to Git)"
kubectl apply -f argocd/root.yaml

echo "==> 5/5 Vault initialisation and configuration"
# Not 'kubectl rollout status': the Vault chart's StatefulSet does not use the
# RollingUpdate strategy, so that command errors out. Wait for the pod instead.
waited=0
until kubectl get pod vault-0 -n vault &>/dev/null; do
  if [ "$waited" -ge "$VAULT_WAIT_SECONDS" ]; then
    echo "vault-0 was not created within ${VAULT_WAIT_SECONDS}s. Check: kubectl -n argocd get applications" >&2
    exit 1
  fi
  echo "  Waiting for Argo CD to create the vault-0 pod..."
  sleep 15
  waited=$((waited + 15))
done

kubectl wait --for=jsonpath='{.status.phase}'=Running pod/vault-0 -n vault --timeout=300s

# Reach Vault through a port-forward to the pod, so this step does not depend on
# the Vault Ingress, which another Argo CD app creates later.
PF_LOG="$(mktemp)"
kubectl -n vault port-forward pod/vault-0 "${VAULT_LOCAL_PORT}:8200" >"$PF_LOG" 2>&1 &
PF_PID=$!
trap 'kill "$PF_PID" 2>/dev/null || true' EXIT
sleep 2
if ! kill -0 "$PF_PID" 2>/dev/null; then
  echo "port-forward to vault-0 failed:" >&2
  cat "$PF_LOG" >&2
  exit 1
fi
export VAULT_URL="http://127.0.0.1:${VAULT_LOCAL_PORT}"

echo "  Preparing Python virtual environment..."
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -q -r bootstrap/scripts/requirements.txt

.venv/bin/python bootstrap/scripts/src/configure_vault.py

cat <<MSG

Done. Argo CD UI:  https://localhost:8080  (self-signed cert; try http:// if https fails)
Admin password:    kubectl -n argocd get secret argocd-initial-admin-secret \\
                     -o jsonpath='{.data.password}' | base64 -d; echo
Watch sync:        kubectl -n argocd get applications -w
Vault:             keys and root token are in 'pass' (infrastructure/vault/). After a
                   Vault pod restart, re-run this script to unseal it.
Check later:       kubectl get clustersecretstore vault-backend
                   (Ready once Argo CD has synced wave 1)
MSG
