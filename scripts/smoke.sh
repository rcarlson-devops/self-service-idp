#!/usr/bin/env bash
# Exit immediately if a command exits with a non-zero status, 
# if an unassigned variable is used, or if a piped command fails.
set -euo pipefail

echo "=========================================================="
echo " Running Infrastructure Smoke Test..."
echo "=========================================================="

# 1. Verify HashiCorp Vault Unseal State
echo "Checking Vault Health and Unseal State..."
VAULT_SEALED=$(kubectl exec -n vault vault-0 -- vault status -format=json 2>/dev/null | jq -r '.sealed' || echo "unknown")

if [ "$VAULT_SEALED" = "true" ]; then
    echo "CRITICAL ERROR: Vault is sealed! Orchestration blocked."
    exit 1
elif [ "$VAULT_SEALED" = "false" ]; then
    echo "Vault is unsealed and healthy."
else
    echo "CRITICAL ERROR: Unable to communicate with Vault pod vault-0."
    exit 1
fi

# 2. Verify ArgoCD Application Health
echo "Checking ArgoCD Application Health..."
applications=(
    "cloudnativepg-operator"
    "crossplane-functions"
    "crossplane-operator"
    "database-api"
    "external-secrets-operator"
    "grafana-dashboards"
    "hashicorp-vault"
    "kube-prometheus-stack"
    "kyverno"
    "kyverno-policies"
    "monitoring-ingress"
    "self-service-idp-root"
    "self-service-idp-tenants"
    "vault-backend"
)

for application in "${applications[@]}"; do
    if [[ "$(kubectl get application "$application" -n argocd -o jsonpath="{.status.health.status}")" != "Healthy" ]]; then
        echo "$application is not in a healthy state. Please investigate"
        exit 1
    fi
done
echo "All ArgoCD Applications Confirmed Healthy"

# 3. Verify Crossplane Function Health
echo "Verifying Crossplane Functions Status"
functions=(
    "auto-ready"
    "function-sequencer"
    "go-templating"
)

for function in "${functions[@]}"; do
    if [[ "$(kubectl get function $function -o jsonpath="{.status.conditions[0].type}")" != "Healthy" ]]; then
        echo "Crossplane function: $function is not in the Healthy state. Please investigate."
        exit 1
    fi
done
echo "All Crossplane Functions confirmed Healthy."

# 4. Verify Kyverno Deployment Health
echo "Verifying Kyverno Deployment Health"
deployments=(
    "kyverno-admission-controller"
    "kyverno-background-controller"
    "kyverno-cleanup-controller"
    "kyverno-reports-controller"
)

for deployment in "${deployments[@]}"; do
    if ! kubectl rollout status deployment/"${deployment}" -n kyverno > /dev/null 2>&1; then
        echo "$deployment has not completed successfully. Please investigate"
    fi
done

echo "All Kyverno Deployments have succesfully completed and are healthy."

echo "=========================================================="
echo " Baseline smoke verification checks completed successfully!"
echo "=========================================================="