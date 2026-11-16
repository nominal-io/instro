import pytest

from instro.unstable.opcua.types import SecurityPolicy


@pytest.mark.parametrize(
    ("spelling", "canonical", "uri_suffix"),
    [
        ("None", "None", "None"),
        ("Basic128Rsa15", "Basic128Rsa15", "Basic128Rsa15"),
        ("basic256", "Basic256", "Basic256"),
        ("bAsIc256ShA256", "Basic256Sha256", "Basic256Sha256"),
        ("http://opcfoundation.org/UA/SecurityPolicy#Basic256Sha256", "Basic256Sha256", "Basic256Sha256"),
        ("Aes128-Sha256-RsaOaep", "Aes128-Sha256-RsaOaep", "Aes128_Sha256_RsaOaep"),
        ("Aes128Sha256RsaOaep", "Aes128-Sha256-RsaOaep", "Aes128_Sha256_RsaOaep"),
        ("Aes256Sha256RsaPss", "Aes256-Sha256-RsaPss", "Aes256_Sha256_RsaPss"),
    ],
)
def test_security_policy_normalizes_public_spellings(spelling, canonical, uri_suffix):
    policy = SecurityPolicy(spelling)
    assert policy.name == canonical
    assert policy.uri == f"http://opcfoundation.org/UA/SecurityPolicy#{uri_suffix}"


def test_security_policy_rejects_unknown_name():
    with pytest.raises(ValueError):
        SecurityPolicy("Basic512")
