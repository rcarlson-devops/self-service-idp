#!/usr/bin/env bash
# A smoke test should run every check and report all failures,
# not stop at the first failure.
set -uo pipefail

echo "=========================================================="
echo " Running Infrastructure Smoke Test..."
echo "=========================================================="

tracker() {
    local display_name="$1"
    local function_name="$2"

    if ! "$function_name"; then
        FAILURES=$((FAILURES + 1))
        echo "$display_name FAIL"
    else
        PASSED=$((PASSED + 1))
        echo "$display_name PASS"
    fi
}

# Verify HashiCorp Vault Unseal State
# shellcheck disable=SC2317
# functions invoked indirectly by tracker()
vault_check() {
    local VAULT_SEALED
 
    VAULT_SEALED=$(kubectl exec -n vault vault-0 -- vault status -format=json 2>/dev/null | jq -r '.sealed' || echo "unknown")

    if [ "$VAULT_SEALED" = "true" ]; then
        echo "CRITICAL ERROR: Vault is sealed! Orchestration blocked."
        return 1
    elif [ "$VAULT_SEALED" = "false" ]; then
        echo "Vault is unsealed and healthy."
        return 0
    else
        echo "CRITICAL ERROR: Unable to communicate with Vault pod vault-0."
        return 1
    fi
}

# Verify ArgoCD Application Health
# shellcheck disable=SC2317
# functions invoked indirectly by tracker()
argo_app_check() {
    local applications=(
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

    local application

    for application in "${applications[@]}"; do
        if [[ "$(kubectl get application "$application" -n argocd -o jsonpath="{.status.health.status}")" != "Healthy" ]]; then
            echo "$application is not in a healthy state. Please investigate"
            return 1
        else
            if [[ "$(kubectl get application "$application" -n argocd -o jsonpath="{.status.sync.status}")" != "Synced" ]]; then
                echo "$application is not synced. Please investigate"
                return 1
            fi
        fi
    done

    return 0
}

# Verify Crossplane Function Health
# shellcheck disable=SC2317
# functions invoked indirectly by tracker()
crossplane_func_check() {
    local functions=(
        "auto-ready"
        "function-sequencer"
        "go-templating"
    )

    local crossplane_function

    for crossplane_function in "${functions[@]}"; do
        if [[ "$(kubectl get function "$crossplane_function" -o jsonpath="{.status.conditions[0].type}")" != "Healthy" ]]; then
            echo "Crossplane function: $crossplane_function is not in the Healthy state. Please investigate."
            return 1
        fi
    done

    return 0
}

# Verify Kyverno Deployment Health
# shellcheck disable=SC2317
# functions invoked indirectly by tracker()
kyverno_check() {
    local deployments=(
        "kyverno-admission-controller"
        "kyverno-background-controller"
        "kyverno-cleanup-controller"
        "kyverno-reports-controller"
    )

    local deployment

    for deployment in "${deployments[@]}"; do
        if ! kubectl rollout status deployment/"${deployment}" -n kyverno > /dev/null 2>&1; then
            echo "$deployment has not completed successfully. Please investigate"
            return 1
        fi
    done

    return 0
}

PASSED=0
FAILURES=0

tracker "vault" "vault_check"
tracker "argocd" "argo_app_check"
tracker "crossplane" "crossplane_func_check"
tracker "kyverno" "kyverno_check"

echo "PASSING: $PASSED; FAILING: $FAILURES"

if [ "$FAILURES" -ge 1 ]; then
    exit 1
else
    echo "=========================================================="
    echo " Baseline smoke verification checks completed successfully!"
    echo "=========================================================="
    exit 0
fi
