import sys
from pathlib import Path

import pytest
from typing_extensions import TypeAlias

from instro.unstable.opcua import OpcUaClient, get_endpoints

Endpoint: TypeAlias = tuple[str, int]

ENDPOINTS: set[Endpoint] = {
    ("http://opcfoundation.org/UA/SecurityPolicy#None", 1),
    ("http://opcfoundation.org/UA/SecurityPolicy#Basic256Sha256", 2),
    ("http://opcfoundation.org/UA/SecurityPolicy#Basic256Sha256", 3),
}


def test_discovery_returns_advertised_security_modes(endpoint: str, tmp_path: Path):
    store = tmp_path / "certificates"
    endpoints = get_endpoints(endpoint, pki_dir=store)
    assert {(item.security_policy, int(item.security_mode)) for item in endpoints} == ENDPOINTS
    assert store.is_dir()


@pytest.mark.parametrize("operation", ["discovery", "connect"])
def test_default_certificate_store_uses_local_data_directory(
    endpoint: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path))

    if operation == "discovery":
        get_endpoints(endpoint)
    else:
        OpcUaClient.connect(endpoint).disconnect()

    assert not (cwd / "pki").exists()
    if sys.platform == "linux":
        assert (tmp_path / "data/instro/opcua").is_dir()
    elif sys.platform == "darwin":
        assert (tmp_path / "Library/Application Support/instro/opcua").is_dir()
