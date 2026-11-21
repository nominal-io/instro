from typing_extensions import TypeAlias

from instro.unstable.opcua import get_endpoints

Endpoint: TypeAlias = tuple[str, int]

ENDPOINTS: set[Endpoint] = {
    ("http://opcfoundation.org/UA/SecurityPolicy#None", 1),
    ("http://opcfoundation.org/UA/SecurityPolicy#Basic256Sha256", 2),
    ("http://opcfoundation.org/UA/SecurityPolicy#Basic256Sha256", 3),
}


def test_discovery_returns_advertised_security_modes(endpoint: str):
    endpoints = get_endpoints(endpoint)
    assert {(item.security_policy, int(item.security_mode)) for item in endpoints} == ENDPOINTS
