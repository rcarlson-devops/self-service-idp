import pytest
import requests
import requests_mock
import sys

from src.configure_vault import wait_for_vault_api, initialize_vault, unseal_vault, configure_vault_platform

VAULT_URL = "http://vault.localhost:8081"

def test_wait_for_vault_api_success(requests_mock):
    """Test that the wait loop exits cleanly when Vault returns 501 (uninitialized)."""
    # Arrange: Tell the mock engine to return a 501 status when hitting the health endpoint
    requests_mock.get(f"{VAULT_URL}/v1/sys/health", status_code=501)
    
    # Act & Assert: Call the function; it should complete without throwing an exception
    assert wait_for_vault_api() is True

def test_wait_for_vault_api_timeout(requests_mock, monkeypatch):
    """Test that the wait loop raises a TimeoutError if the API is continually broken."""
    # Arrange: Mock the endpoint to raise a connection error every time
    requests_mock.get(f"{VAULT_URL}/v1/sys/health", exc=requests.exceptions.ConnectionError)
    
    # Speed up the test by forcing time.sleep() to do nothing instead of waiting 2 seconds
    monkeypatch.setattr("time.sleep", lambda x: None)
    
    # Act & Assert: Verify that a TimeoutError is raised exactly as expected
    with pytest.raises(TimeoutError, match="Vault endpoint failed to become reachable"):
        wait_for_vault_api()

def test_initialize_vault_success(requests_mock):
    """Test that initialize_vault parses and returns the expected dictionary structure."""
    # Arrange: Create a mock response mimicking Vault's actual initialization JSON payload
    mock_response = {
        "keys": ["key1", "key2", "key3"],
        "root_token": "s.mockRootToken12345"
    }
    requests_mock.put(f"{VAULT_URL}/v1/sys/init", json=mock_response, status_code=200)
    
    # Act
    result = initialize_vault()
    
    # Assert
    assert result["root_token"] == "s.mockRootToken12345"
    assert len(result["keys"]) == 3
    assert result["keys"][0] == "key1"

def test_unseal_vault_sends_three_keys(requests_mock):
    """Test that unseal_vault loops exactly 3 times to send all required keys."""
    # Arrange: Catch PUT calls to /sys/unseal and return 200
    mock_put = requests_mock.put(f"{VAULT_URL}/v1/sys/unseal", json={"sealed": False}, status_code=200)
    dummy_keys = ["key1", "key2", "key3", "key4"]
    
    # Act
    unseal_vault(dummy_keys)
    
    # Assert: Verify requests_mock captured exactly 3 API calls (since threshold is 3)
    assert mock_put.call_count == 3

def test_configure_vault_platform_endpoints(requests_mock):
    """Test that all 5 required post-deployment configuration endpoints are hit."""
    # Arrange: Mock all the distinct paths we expect the script to call
    m_kv = requests_mock.post(f"{VAULT_URL}/v1/sys/mounts/secret", status_code=200)
    m_auth = requests_mock.post(f"{VAULT_URL}/v1/sys/auth/kubernetes", status_code=200)
    m_k8s_cfg = requests_mock.post(f"{VAULT_URL}/v1/auth/kubernetes/config", status_code=200)
    m_policy = requests_mock.put(f"{VAULT_URL}/v1/sys/policies/acl/eso", status_code=200)
    m_role = requests_mock.post(f"{VAULT_URL}/v1/auth/kubernetes/role/eso", status_code=200)
    
    # Act
    configure_vault_platform("dummy-root-token")
    
    # Assert: Confirm that every configuration function was successfully triggered
    assert m_kv.called is True
    assert m_auth.called is True
    assert m_k8s_cfg.called is True
    assert m_policy.called is True
    assert m_role.called is True