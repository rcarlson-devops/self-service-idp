#!/usr/bin/env python3
import time
import subprocess
import sys
import requests

VAULT_URL = "http://vault.localhost:8081"

def wait_for_vault_api():
    """Ensure Vault API is active and listening to health probes."""
    print("Checking Vault API connection metrics...")
    for _ in range(45):
        try:
            response = requests.get(f"{VAULT_URL}/v1/sys/health")
            # 200 = initialized/unsealed, 501 = uninitialized, 503 = sealed
            if response.status_code in [200, 429, 501, 503]:
                return True
        except requests.exceptions.ConnectionError:
            pass
        time.sleep(2)
    raise TimeoutError("Vault endpoint failed to become reachable within timeframe.")

def initialize_vault():
    """Equivalent to: vault operator init"""
    print("Initializing Vault operator...")
    init_payload = {
        "secret_shares": 3,
        "secret_threshold": 3
    }
    response = requests.put(f"{VAULT_URL}/v1/sys/init", json=init_payload)
    response.raise_for_status()
    return response.json()

def store_keys_in_pass(init_data):
    """Securely channels critical tokens directly into 'pass' via stdin streams."""
    print("Saving initialization tokens into local 'pass' store...")
    secrets_mapping = {
        "infrastructure/vault/root_token": init_data["root_token"],
        "infrastructure/vault/unseal_key_1": init_data["keys"][0],
        "infrastructure/vault/unseal_key_2": init_data["keys"][1],
        "infrastructure/vault/unseal_key_3": init_data["keys"][2],
    }
    
    for path, value in secrets_mapping.items():
        try:
            proc = subprocess.Popen(
                ["pass", "insert", "-m", "-f", path],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True
            )
            stdout, stderr = proc.communicate(input=value)
            if proc.returncode != 0:
                print(f"Failed to store [{path}]: {stderr.strip()}", file=sys.stderr)
                sys.exit(proc.returncode)
            print(f"Saved to tree: {path}")
        except FileNotFoundError:
            print("Dependency error: 'pass' binary or active GPG runtime missing.", file=sys.stderr)
            sys.exit(1)

def unseal_vault(unseal_keys):
    """Equivalent to: running 'vault operator unseal' 3 distinct times"""
    print("Unsealing Vault with 3 mandatory key shares...")
    for key in unseal_keys[:3]:
        response = requests.put(f"{VAULT_URL}/v1/sys/unseal", json={"key": key})
        response.raise_for_status()

def configure_vault_platform(root_token):
    """Authenticates using X-Vault-Token and configures all requested engines, auth methods, and roles."""
    print("Authenticating and configuring backend dependencies...")
    
    # AUTHENTICATION STEP: Headers are used for token authentication on all subsequent requests
    headers = {
        "X-Vault-Token": root_token,
        "Content-Type": "application/json"
    }

    # 1. Enable KV-v2 Engine: vault secrets enable -path=secret kv-v2
    print("Enabling KV-v2 Secrets Engine at path 'secret'...")
    requests.post(
        f"{VAULT_URL}/v1/sys/mounts/secret",
        headers=headers,
        json={"type": "kv", "options": {"version": "2"}}
    )

    # 2. Enable Kubernetes Auth: vault auth enable kubernetes
    print("Enabling Kubernetes Authentication Method...")
    requests.post(
        f"{VAULT_URL}/v1/sys/auth/kubernetes",
        headers=headers,
        json={"type": "kubernetes"}
    )

    # 3. Configure Kubernetes Auth: vault write auth/kubernetes/config ...
    print("Configuring Kubernetes engine cluster host target...")
    k8s_config_payload = {
        "kubernetes_host": "https://kubernetes.default.svc:443"
    }
    requests.post(
        f"{VAULT_URL}/v1/auth/kubernetes/config",
        headers=headers,
        json=k8s_config_payload
    ).raise_for_status()

    # 4. Write ACL Policy: vault policy write eso - <<EOF ...
    print("Creating 'eso' Access Control Policy...")
    eso_policy_hcl = """
    path "secret/data/tenants/*" {
      capabilities = ["create", "read", "update", "delete"]
    }
    path "secret/metadata/tenants/*" {
      capabilities = ["create", "read", "update", "list", "delete"]
    }
    """
    requests.put(
        f"{VAULT_URL}/v1/sys/policies/acl/eso",
        headers=headers,
        json={"policy": eso_policy_hcl}
    ).raise_for_status()

    # 5. Map Role to Service Account: vault write auth/kubernetes/role/eso ...
    print("Mapping External Secrets ServiceAccount bindings to 'eso' role...")
    role_payload = {
        "bound_service_account_names": ["external-secrets"],
        "bound_service_account_namespaces": ["external-secrets"],
        "policies": ["eso"],
        "ttl": "1h"
    }
    requests.post(
        f"{VAULT_URL}/v1/auth/kubernetes/role/eso",
        headers=headers,
        json=role_payload
    ).raise_for_status()

if __name__ == "__main__":
    try:
        wait_for_vault_api()
        vault_data = initialize_vault()
        store_keys_in_pass(vault_data)
        unseal_vault(vault_data["keys"])
        
        # Pass the extracted root token directly into the configuration runner
        configure_vault_platform(vault_data["root_token"])
        
        print("Vault post-deploy bootstrap cycle completed successfully.")
    except Exception as e:
        print(f"Script breakdown during execution phase: {e}", file=sys.stderr)
        sys.exit(1)