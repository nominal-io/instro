from instro.unstable.opcua import get_endpoints


def test_discovery_returns_advertised_security_modes(endpoint):
    endpoints = get_endpoints(endpoint)
    assert {(item.security_policy, int(item.security_mode)) for item in endpoints} == {
        ("http://opcfoundation.org/UA/SecurityPolicy#None", 1),
        ("http://opcfoundation.org/UA/SecurityPolicy#Basic256Sha256", 2),
        ("http://opcfoundation.org/UA/SecurityPolicy#Basic256Sha256", 3),
    }
