"""OPC UA client support."""

from enum import Enum

from instro.unstable._opcua import OpcUaClient, OpcUaError, OpcUaNode


class OpcUaSecurityMode(str, Enum):
    NONE = "none"
    SIGN = "sign"
    SIGN_AND_ENCRYPT = "sign_and_encrypt"


class OpcUaSecurityPolicy(str, Enum):
    NONE = "none"
    BASIC128_RSA15 = "basic128_rsa15"
    BASIC256 = "basic256"
    BASIC256_SHA256 = "basic256_sha256"
    AES128_SHA256_RSA_OAEP = "aes128_sha256_rsa_oaep"
    AES256_SHA256_RSA_PSS = "aes256_sha256_rsa_pss"


__all__ = [
    "OpcUaClient",
    "OpcUaError",
    "OpcUaNode",
    "OpcUaSecurityMode",
    "OpcUaSecurityPolicy",
]
