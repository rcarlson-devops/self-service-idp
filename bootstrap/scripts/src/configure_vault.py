#!/usr/bin/env python3
"""Initialise, unseal and configure Vault for the platform.

Safe to re-run:
  * Vault not initialised -> check 'pass' is writable, initialise, save the
    keys and root token to 'pass', unseal, configure.
  * Vault initialised     -> read the keys and root token back from 'pass',
    unseal if it is sealed, configure.

Every Vault call is checked. A failure stops the script with the HTTP status
and Vault's error message. Request bodies are never printed.
"""
import os
import subprocess
import sys
import textwrap
import time

import requests

VAULT_URL = os.environ.get("VAULT_URL", "http://vault.localhost:8081")
REQUEST_TIMEOUT = 10  # seconds, per request
WAIT_ATTEMPTS = 45
WAIT_INTERVAL = 2  # seconds

SECRET_SHARES = 3
SECRET_THRESHOLD = 3

PASS_ROOT_TOKEN = "infrastructure/vault/root_token"
PASS_UNSEAL_KEYS = [f"infrastructure/vault/unseal_key_{i}" for i in range(1, SECRET_SHARES + 1)]
PASS_PROBE = "infrastructure/vault/preflight_probe"

ESO_POLICY = textwrap.dedent("""\
    path "secret/data/tenants/*" {
      capabilities = ["create", "read", "update", "delete"]
    }
    path "secret/metadata/tenants/*" {
      capabilities = ["create", "read", "update", "list", "delete"]
    }
    """)

ESO_ROLE = {
    "bound_service_account_names": ["external-secrets"],
    "bound_service_account_namespaces": ["external-secrets"],
    "policies": ["eso"],
    "ttl": "1h",
}


class VaultError(Exception):
    """A Vault API call failed."""


class PassError(Exception):
    """The 'pass' password store could not be used."""


# --------------------------------------------------------------------------
# Vault API helpers
# --------------------------------------------------------------------------

def _error_text(response):
    try:
        errors = response.json().get("errors")
        if errors:
            return "; ".join(str(e) for e in errors)
    except ValueError:
        pass
    return response.text[:200]


def vault_request(method, path, token=None, **kwargs):
    """Call the Vault API and raise VaultError on any non-2xx response."""
    headers = {"X-Vault-Token": token} if token else {}
    response = requests.request(
        method, f"{VAULT_URL}{path}", headers=headers, timeout=REQUEST_TIMEOUT, **kwargs
    )
    if not response.ok:
        raise VaultError(f"{method} {path} returned {response.status_code}: {_error_text(response)}")
    return response


def wait_for_vault_api():
    """Wait until Vault's API answers. Sealed and uninitialised both count as up."""
    print("Waiting for the Vault API...")
    for _ in range(WAIT_ATTEMPTS):
        try:
            response = requests.request(
                "GET", f"{VAULT_URL}/v1/sys/health", timeout=REQUEST_TIMEOUT
            )
            # 200 = unsealed, 429 = standby, 501 = uninitialised, 503 = sealed
            if response.status_code in (200, 429, 501, 503):
                return True
        except requests.exceptions.RequestException:
            pass
        time.sleep(WAIT_INTERVAL)
    raise TimeoutError(f"Vault endpoint failed to become reachable at {VAULT_URL}.")


def vault_status():
    """Return {'initialized': bool, 'sealed': bool, ...} from /sys/seal-status."""
    return vault_request("GET", "/v1/sys/seal-status").json()


def initialize_vault():
    """Equivalent to: vault operator init"""
    print("Initializing Vault...")
    response = vault_request(
        "PUT",
        "/v1/sys/init",
        json={"secret_shares": SECRET_SHARES, "secret_threshold": SECRET_THRESHOLD},
    )
    return response.json()


def unseal_vault(keys):
    """Equivalent to: vault operator unseal, once per key until Vault reports unsealed."""
    print("Unsealing Vault...")
    for key in keys[:SECRET_THRESHOLD]:
        response = vault_request("PUT", "/v1/sys/unseal", json={"key": key})
        if not response.json().get("sealed", True):
            return
    raise VaultError("Vault is still sealed after submitting all key shares.")


# --------------------------------------------------------------------------
# pass helpers
# --------------------------------------------------------------------------

def _run_pass(args, stdin=None):
    try:
        return subprocess.run(["pass", *args], input=stdin, capture_output=True, text=True)
    except FileNotFoundError:
        raise PassError("the 'pass' binary is not installed or not on PATH.")


def check_pass_ready():
    """Prove 'pass' can write before Vault is initialised.

    Initialising Vault makes the unseal keys and root token exist only in the
    init response. If saving them failed afterwards, they would be lost, so do
    a throwaway write first. This tests writing only; reading needs GPG to
    decrypt and is not tested here.
    """
    print("Checking that 'pass' is usable...")
    result = _run_pass(["insert", "-m", "-f", PASS_PROBE], stdin="probe")
    if result.returncode != 0:
        raise PassError(f"cannot write to the password store: {result.stderr.strip()}")
    _run_pass(["rm", "-f", PASS_PROBE])  # best effort; a leftover probe entry is harmless


def store_keys_in_pass(init_data):
    """Save the root token and unseal keys to 'pass'."""
    print("Saving the root token and unseal keys to 'pass'...")
    secrets = {PASS_ROOT_TOKEN: init_data["root_token"]}
    for path, key in zip(PASS_UNSEAL_KEYS, init_data["keys"]):
        secrets[path] = key

    for path, value in secrets.items():
        result = _run_pass(["insert", "-m", "-f", path], stdin=value)
        if result.returncode != 0:
            raise PassError(f"failed to store {path}: {result.stderr.strip()}")
        print(f"  saved {path}")


def load_from_pass():
    """Return (root_token, keys) previously saved by store_keys_in_pass."""
    print("Vault is already initialised. Reading its credentials from 'pass'...")

    def read(path):
        result = _run_pass(["show", path])
        if result.returncode != 0:
            raise PassError(f"failed to read {path}: {result.stderr.strip()}")
        return result.stdout.strip()

    return read(PASS_ROOT_TOKEN), [read(path) for path in PASS_UNSEAL_KEYS]


# --------------------------------------------------------------------------
# Vault configuration
# --------------------------------------------------------------------------

def ensure_kv_v2_mount(token):
    """Equivalent to: vault secrets enable -path=secret kv-v2"""
    print("Ensuring KV v2 at 'secret/'...")
    body = vault_request("GET", "/v1/sys/mounts", token).json()
    existing = body.get("data", body).get("secret/")
    if existing is None:
        vault_request(
            "POST", "/v1/sys/mounts/secret", token,
            json={"type": "kv", "options": {"version": "2"}},
        )
        return
    version = (existing.get("options") or {}).get("version")
    if existing.get("type") != "kv" or str(version) != "2":
        raise VaultError(
            f"'secret/' is already mounted as type={existing.get('type')} version={version}, "
            "not KV v2. Not changing it."
        )


def ensure_kubernetes_auth(token):
    """Equivalent to: vault auth enable kubernetes"""
    print("Ensuring the Kubernetes auth method...")
    body = vault_request("GET", "/v1/sys/auth", token).json()
    existing = body.get("data", body).get("kubernetes/")
    if existing is None:
        vault_request("POST", "/v1/sys/auth/kubernetes", token, json={"type": "kubernetes"})
        return
    if existing.get("type") != "kubernetes":
        raise VaultError(
            f"'kubernetes/' is already enabled as type={existing.get('type')}. Not changing it."
        )


def configure_vault_platform(token):
    """Mounts, auth method, policy and role. Every step is safe to repeat."""
    print("Configuring Vault...")
    ensure_kv_v2_mount(token)
    ensure_kubernetes_auth(token)

    print("Writing the Kubernetes auth config...")
    vault_request(
        "POST", "/v1/auth/kubernetes/config", token,
        json={"kubernetes_host": "https://kubernetes.default.svc:443"},
    )

    print("Writing the 'eso' policy...")
    vault_request("PUT", "/v1/sys/policies/acl/eso", token, json={"policy": ESO_POLICY})

    print("Writing the 'eso' role...")
    vault_request("POST", "/v1/auth/kubernetes/role/eso", token, json=ESO_ROLE)


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def main():
    wait_for_vault_api()
    status = vault_status()

    if not status["initialized"]:
        check_pass_ready()
        init_data = initialize_vault()
        try:
            store_keys_in_pass(init_data)
        except PassError as err:
            raise PassError(
                f"{err}\nVault is initialised but its keys were NOT saved, and the init "
                "response is gone. Delete Vault's data (recreate the cluster) and run again."
            )
        root_token, keys, sealed = init_data["root_token"], init_data["keys"], True
    else:
        root_token, keys = load_from_pass()
        sealed = status["sealed"]

    if sealed:
        unseal_vault(keys)
    else:
        print("Vault is already unsealed.")

    configure_vault_platform(root_token)
    print("Vault bootstrap completed successfully.")


if __name__ == "__main__":
    try:
        main()
    except Exception as err:
        print(f"Vault bootstrap failed: {err}", file=sys.stderr)
        sys.exit(1)
