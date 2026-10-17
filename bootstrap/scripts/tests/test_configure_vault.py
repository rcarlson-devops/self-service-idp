from types import SimpleNamespace

import pytest
import requests

from src import configure_vault as cv

VAULT_URL = "http://vault.localhost:8081"
TOKEN = "dummy-root-token"
INIT_DATA = {"keys": ["key1", "key2", "key3"], "root_token": "s.root"}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)


class FakePass:
    """Stands in for subprocess.run(["pass", ...]) and records every call."""

    def __init__(self, monkeypatch, fail_on=None, missing=False, store=None):
        self.calls = []  # (args, stdin)
        self.fail_on = fail_on  # substring of args that makes the call fail
        self.missing = missing
        self.store = store or {}
        monkeypatch.setattr(cv.subprocess, "run", self._run)

    def _run(self, cmd, input=None, capture_output=None, text=None):
        if self.missing:
            raise FileNotFoundError("pass")
        args = cmd[1:]
        self.calls.append((args, input))
        if self.fail_on and self.fail_on in " ".join(args):
            return SimpleNamespace(returncode=1, stdout="", stderr="gpg: boom")
        if args[0] == "insert":
            self.store[args[-1]] = input
        if args[0] == "show":
            if args[1] not in self.store:
                return SimpleNamespace(returncode=1, stdout="", stderr="not in the password store")
            return SimpleNamespace(returncode=0, stdout=self.store[args[1]] + "\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")


# ---- Vault API helpers ---------------------------------------------------

def test_wait_for_vault_api_success(requests_mock):
    requests_mock.get(f"{VAULT_URL}/v1/sys/health", status_code=501)
    assert cv.wait_for_vault_api() is True


def test_wait_for_vault_api_retries_after_connection_error(requests_mock):
    requests_mock.get(
        f"{VAULT_URL}/v1/sys/health",
        [{"exc": requests.exceptions.ConnectionError}, {"status_code": 503}],
    )
    assert cv.wait_for_vault_api() is True


def test_wait_for_vault_api_timeout(requests_mock):
    requests_mock.get(f"{VAULT_URL}/v1/sys/health", exc=requests.exceptions.ConnectionError)
    with pytest.raises(TimeoutError, match="failed to become reachable"):
        cv.wait_for_vault_api()


def test_wait_for_vault_api_keeps_waiting_on_404(requests_mock):
    """A 404 means the Ingress or port-forward is up but Vault is not behind it yet."""
    requests_mock.get(f"{VAULT_URL}/v1/sys/health", status_code=404)
    with pytest.raises(TimeoutError):
        cv.wait_for_vault_api()


def test_vault_request_error_has_status_and_vault_message(requests_mock):
    requests_mock.put(f"{VAULT_URL}/v1/sys/init", status_code=400, json={"errors": ["already initialized"]})
    with pytest.raises(cv.VaultError, match="400.*already initialized"):
        cv.vault_request("PUT", "/v1/sys/init", json={"secret": "do-not-print"})


def test_vault_request_error_does_not_leak_request_body(requests_mock):
    requests_mock.put(f"{VAULT_URL}/v1/sys/unseal", status_code=400, json={"errors": ["bad key"]})
    with pytest.raises(cv.VaultError) as exc:
        cv.vault_request("PUT", "/v1/sys/unseal", json={"key": "SECRETKEY"})
    assert "SECRETKEY" not in str(exc.value)


def test_vault_request_sends_token_header(requests_mock):
    m = requests_mock.get(f"{VAULT_URL}/v1/sys/mounts", json={})
    cv.vault_request("GET", "/v1/sys/mounts", TOKEN)
    assert m.last_request.headers["X-Vault-Token"] == TOKEN


def test_vault_status(requests_mock):
    requests_mock.get(f"{VAULT_URL}/v1/sys/seal-status", json={"initialized": True, "sealed": True})
    assert cv.vault_status() == {"initialized": True, "sealed": True}


def test_initialize_vault_sends_3_of_3_and_returns_payload(requests_mock):
    m = requests_mock.put(f"{VAULT_URL}/v1/sys/init", json=INIT_DATA)
    assert cv.initialize_vault() == INIT_DATA
    assert m.last_request.json() == {"secret_shares": 3, "secret_threshold": 3}


def test_initialize_vault_failure_raises(requests_mock):
    requests_mock.put(f"{VAULT_URL}/v1/sys/init", status_code=400, json={"errors": ["already initialized"]})
    with pytest.raises(cv.VaultError, match="already initialized"):
        cv.initialize_vault()


def test_unseal_vault_stops_once_unsealed(requests_mock):
    m = requests_mock.put(
        f"{VAULT_URL}/v1/sys/unseal",
        [{"json": {"sealed": True}}, {"json": {"sealed": True}}, {"json": {"sealed": False}}],
    )
    cv.unseal_vault(["k1", "k2", "k3", "k4"])
    assert m.call_count == 3


def test_unseal_vault_sends_at_most_three_keys(requests_mock):
    m = requests_mock.put(f"{VAULT_URL}/v1/sys/unseal", json={"sealed": True})
    with pytest.raises(cv.VaultError, match="still sealed"):
        cv.unseal_vault(["k1", "k2", "k3", "k4"])
    assert m.call_count == 3


# ---- pass ----------------------------------------------------------------

def test_check_pass_ready_writes_then_removes_probe(monkeypatch):
    fake = FakePass(monkeypatch)
    cv.check_pass_ready()
    assert fake.calls[0][0] == ["insert", "-m", "-f", cv.PASS_PROBE]
    assert fake.calls[1][0] == ["rm", "-f", cv.PASS_PROBE]


def test_check_pass_ready_missing_binary(monkeypatch):
    FakePass(monkeypatch, missing=True)
    with pytest.raises(cv.PassError, match="not installed"):
        cv.check_pass_ready()


def test_check_pass_ready_write_failure(monkeypatch):
    FakePass(monkeypatch, fail_on="insert")
    with pytest.raises(cv.PassError, match="cannot write.*gpg: boom"):
        cv.check_pass_ready()


def test_store_keys_in_pass_writes_all_four_entries(monkeypatch):
    fake = FakePass(monkeypatch)
    cv.store_keys_in_pass(INIT_DATA)
    assert fake.store == {
        "infrastructure/vault/root_token": "s.root",
        "infrastructure/vault/unseal_key_1": "key1",
        "infrastructure/vault/unseal_key_2": "key2",
        "infrastructure/vault/unseal_key_3": "key3",
    }


def test_store_keys_in_pass_failure_names_the_path(monkeypatch):
    FakePass(monkeypatch, fail_on="unseal_key_2")
    with pytest.raises(cv.PassError, match="unseal_key_2"):
        cv.store_keys_in_pass(INIT_DATA)


def test_load_from_pass_round_trip(monkeypatch):
    FakePass(monkeypatch)
    cv.store_keys_in_pass(INIT_DATA)
    assert cv.load_from_pass() == ("s.root", ["key1", "key2", "key3"])


def test_load_from_pass_missing_entry(monkeypatch):
    FakePass(monkeypatch)
    with pytest.raises(cv.PassError, match="root_token"):
        cv.load_from_pass()


# ---- configuration -------------------------------------------------------

KV2 = {"data": {"secret/": {"type": "kv", "options": {"version": "2"}}}}
K8S_AUTH = {"data": {"kubernetes/": {"type": "kubernetes"}}}


def test_ensure_kv_v2_mount_creates_when_missing(requests_mock):
    requests_mock.get(f"{VAULT_URL}/v1/sys/mounts", json={"data": {"sys/": {"type": "system"}}})
    m = requests_mock.post(f"{VAULT_URL}/v1/sys/mounts/secret", status_code=204)
    cv.ensure_kv_v2_mount(TOKEN)
    assert m.last_request.json() == {"type": "kv", "options": {"version": "2"}}


def test_ensure_kv_v2_mount_skips_when_present(requests_mock):
    requests_mock.get(f"{VAULT_URL}/v1/sys/mounts", json=KV2)
    m = requests_mock.post(f"{VAULT_URL}/v1/sys/mounts/secret", status_code=204)
    cv.ensure_kv_v2_mount(TOKEN)
    assert m.called is False


def test_ensure_kv_v2_mount_refuses_kv_v1(requests_mock):
    requests_mock.get(f"{VAULT_URL}/v1/sys/mounts", json={"data": {"secret/": {"type": "kv", "options": {"version": "1"}}}})
    with pytest.raises(cv.VaultError, match="not KV v2"):
        cv.ensure_kv_v2_mount(TOKEN)


def test_ensure_kv_v2_mount_failure_is_not_hidden(requests_mock):
    requests_mock.get(f"{VAULT_URL}/v1/sys/mounts", json={"data": {}})
    requests_mock.post(f"{VAULT_URL}/v1/sys/mounts/secret", status_code=403, json={"errors": ["permission denied"]})
    with pytest.raises(cv.VaultError, match="403.*permission denied"):
        cv.ensure_kv_v2_mount(TOKEN)


def test_ensure_kubernetes_auth_creates_when_missing(requests_mock):
    requests_mock.get(f"{VAULT_URL}/v1/sys/auth", json={"data": {"token/": {"type": "token"}}})
    m = requests_mock.post(f"{VAULT_URL}/v1/sys/auth/kubernetes", status_code=204)
    cv.ensure_kubernetes_auth(TOKEN)
    assert m.called is True


def test_ensure_kubernetes_auth_skips_when_present(requests_mock):
    requests_mock.get(f"{VAULT_URL}/v1/sys/auth", json=K8S_AUTH)
    m = requests_mock.post(f"{VAULT_URL}/v1/sys/auth/kubernetes", status_code=204)
    cv.ensure_kubernetes_auth(TOKEN)
    assert m.called is False


def mock_configure_endpoints(requests_mock, mounts, auth):
    requests_mock.get(f"{VAULT_URL}/v1/sys/mounts", json=mounts)
    requests_mock.get(f"{VAULT_URL}/v1/sys/auth", json=auth)
    return {
        "kv": requests_mock.post(f"{VAULT_URL}/v1/sys/mounts/secret", status_code=204),
        "auth": requests_mock.post(f"{VAULT_URL}/v1/sys/auth/kubernetes", status_code=204),
        "config": requests_mock.post(f"{VAULT_URL}/v1/auth/kubernetes/config", status_code=204),
        "policy": requests_mock.put(f"{VAULT_URL}/v1/sys/policies/acl/eso", status_code=204),
        "role": requests_mock.post(f"{VAULT_URL}/v1/auth/kubernetes/role/eso", status_code=204),
    }


def test_configure_vault_platform_fresh_vault_hits_every_endpoint(requests_mock):
    m = mock_configure_endpoints(requests_mock, {"data": {}}, {"data": {}})
    cv.configure_vault_platform(TOKEN)
    assert all(call.called for call in m.values())
    assert m["role"].last_request.json()["bound_service_account_names"] == ["external-secrets"]
    assert "secret/data/tenants/*" in m["policy"].last_request.json()["policy"]
    assert m["policy"].last_request.headers["X-Vault-Token"] == TOKEN


def test_configure_vault_platform_rerun_does_not_recreate_mounts(requests_mock):
    m = mock_configure_endpoints(requests_mock, KV2, K8S_AUTH)
    cv.configure_vault_platform(TOKEN)
    assert m["kv"].called is False
    assert m["auth"].called is False
    assert m["config"].called and m["policy"].called and m["role"].called


def test_configure_vault_platform_config_failure_is_not_hidden(requests_mock):
    m = mock_configure_endpoints(requests_mock, KV2, K8S_AUTH)
    requests_mock.post(f"{VAULT_URL}/v1/auth/kubernetes/config", status_code=500, json={"errors": ["boom"]})
    with pytest.raises(cv.VaultError, match="500"):
        cv.configure_vault_platform(TOKEN)
    assert m["policy"].called is False


# ---- main() flow ---------------------------------------------------------

@pytest.fixture
def flow(monkeypatch):
    """Replace every step of main() with a recorder so only the ordering is tested."""
    calls = []
    state = {"status": {"initialized": False, "sealed": True}}

    monkeypatch.setattr(cv, "wait_for_vault_api", lambda: calls.append("wait"))
    monkeypatch.setattr(cv, "vault_status", lambda: state["status"])
    monkeypatch.setattr(cv, "check_pass_ready", lambda: calls.append("check_pass"))
    monkeypatch.setattr(cv, "initialize_vault", lambda: calls.append("init") or INIT_DATA)
    monkeypatch.setattr(cv, "store_keys_in_pass", lambda d: calls.append("store"))
    monkeypatch.setattr(cv, "load_from_pass", lambda: calls.append("load") or ("s.root", ["key1", "key2", "key3"]))
    monkeypatch.setattr(cv, "unseal_vault", lambda k: calls.append("unseal"))
    monkeypatch.setattr(cv, "configure_vault_platform", lambda t: calls.append(f"configure:{t}"))
    return SimpleNamespace(calls=calls, state=state, monkeypatch=monkeypatch)


def test_main_fresh_vault_checks_pass_before_init_and_stores_before_unseal(flow):
    cv.main()
    assert flow.calls == ["wait", "check_pass", "init", "store", "unseal", "configure:s.root"]


def test_main_pass_not_usable_never_initialises_vault(flow):
    def broken():
        raise cv.PassError("cannot write")
    flow.monkeypatch.setattr(cv, "check_pass_ready", broken)
    with pytest.raises(cv.PassError):
        cv.main()
    assert "init" not in flow.calls


def test_main_store_failure_after_init_says_keys_were_not_saved(flow):
    def broken(_):
        raise cv.PassError("failed to store x")
    flow.monkeypatch.setattr(cv, "store_keys_in_pass", broken)
    with pytest.raises(cv.PassError, match="NOT saved"):
        cv.main()
    assert "unseal" not in flow.calls


def test_main_initialised_and_sealed_loads_from_pass_and_unseals(flow):
    flow.state["status"] = {"initialized": True, "sealed": True}
    cv.main()
    assert flow.calls == ["wait", "load", "unseal", "configure:s.root"]


def test_main_initialised_and_unsealed_skips_init_and_unseal(flow):
    flow.state["status"] = {"initialized": True, "sealed": False}
    cv.main()
    assert flow.calls == ["wait", "load", "configure:s.root"]
