from datetime import timedelta

import pytest
from typing_extensions import Any

from instro.unstable.opcua import OpcUaClient
from instro.unstable.opcua.types import Duration, SecurityPolicy
from tests.opcua.conftest import DefaultConnectionOptions


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
def test_security_policy_normalizes_public_spellings(spelling: str, canonical: str, uri_suffix: str):
    policy = SecurityPolicy(spelling)
    assert policy.name == canonical
    assert policy.uri == f"http://opcfoundation.org/UA/SecurityPolicy#{uri_suffix}"


def test_security_policy_rejects_unknown_name():
    with pytest.raises(ValueError):
        SecurityPolicy("Basic512")


@pytest.mark.parametrize(
    ("invalid_opt", "error"),
    [
        ({"identity": "anonymous"}, TypeError),
        ({"security_policy": 1}, TypeError),
        ({"security_policy": "unknown"}, ValueError),
        ({"security_mode": 1}, TypeError),
        ({"security_mode": "unknown"}, ValueError),
        ({"request_timeout": True}, TypeError),
        ({"request_timeout": "2"}, TypeError),
        ({"request_timeout": -1}, ValueError),
        ({"request_timeout": float("nan")}, ValueError),
        ({"request_timeout": float("inf")}, ValueError),
        ({"request_timeout": timedelta(seconds=-1)}, ValueError),
        ({"request_timeout": Duration(2**64 - 1, 999_999_999)}, ValueError),
        ({"session_retry_limit": -2}, ValueError),
        ({"session_retry_limit": True}, TypeError),
        ({"session_timeout": 2**32}, OverflowError),
        ({"max_chunk_count": -1}, OverflowError),
        ({"max_chunk_size": 1.5}, TypeError),
        ({"keep_alive_interval": 0}, ValueError),
        ({"session_retry_initial": Duration(2**63), "session_retry_limit": 1}, ValueError),
        ({"certificate_path": None}, ValueError),
        ({"private_key_path": None}, ValueError),
        ({"unknown_option": 1}, TypeError),
    ],
)
def test_invalid_connection_arguments_fail_before_network(
    invalid_opt: dict[str, Any], error: type[Exception], connection_options: DefaultConnectionOptions
):
    options: dict[str, Any] = connection_options | invalid_opt

    with pytest.raises(error):
        OpcUaClient.connect("opc.tcp://127.0.0.1:1", **options)
