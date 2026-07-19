import pytest

from instro.unstable.opcua import (
    OpcUaClient,
    OpcUaNode,
    OpcUaSecurityMode,
    OpcUaSecurityPolicy,
)


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"username": "user"}, "can't combine basic or certificate identity options"),
        ({"password": "secret"}, "can't combine basic or certificate identity options"),
        (
            {
                "username": "user",
                "password": "secret",
                "user_certificate": b"certificate",
            },
            "user_certificate cannot be combined with username or password",
        ),
    ],
)
def test_invalid_identity_is_rejected_before_network_io(
    options: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        OpcUaClient.connect("opc.tcp://127.0.0.1:1", **options)


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"certificate": b"certificate"}, "certificate and private_key"),
        ({"private_key": b"private-key"}, "certificate and private_key"),
        (
            {"private_key_password": "secret"},
            "private_key_password requires certificate and private_key",
        ),
        (
            {
                "certificate": b"certificate",
                "private_key": b"private-key",
                "generate_self_signed_pki": True,
            },
            "generated PKI cannot be combined with provided PKI",
        ),
    ],
)
def test_invalid_pki_is_rejected_before_network_io(
    options: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        OpcUaClient.connect("opc.tcp://127.0.0.1:1", **options)


@pytest.mark.parametrize(
    "option",
    [
        "timeout",
        "secure_channel_lifetime",
        "requested_session_timeout",
        "connectivity_check_interval",
    ],
)
@pytest.mark.parametrize("value", [-1.0, float("nan"), float("inf")])
def test_invalid_durations_are_rejected_before_network_io(
    option: str,
    value: float,
) -> None:
    with pytest.raises(ValueError, match="must be finite and non-negative"):
        OpcUaClient.connect("opc.tcp://127.0.0.1:1", **{option: value})


def test_security_enums_match_binding_values() -> None:
    assert OpcUaSecurityMode.SIGN_AND_ENCRYPT == "sign_and_encrypt"
    assert OpcUaSecurityPolicy.AES256_SHA256_RSA_PSS == "aes256_sha256_rsa_pss"


def test_browse_path_resolution_is_exposed() -> None:
    assert callable(OpcUaClient.resolve_browse_path)
    assert callable(OpcUaClient.get_node)
    assert OpcUaNode.__name__ == "OpcUaNode"


@pytest.mark.parametrize(
    ("option", "value", "message"),
    [
        ("security_mode", "encrypt", "unsupported security_mode"),
        ("security_policy", "modern", "unsupported security_policy"),
    ],
)
def test_invalid_security_values_are_rejected_before_network_io(
    option: str,
    value: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        OpcUaClient.connect("opc.tcp://127.0.0.1:1", **{option: value})
