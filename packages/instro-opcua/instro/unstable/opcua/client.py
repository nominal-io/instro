from typing import Literal

SecurityMode = Literal["none", "sign", "sign_and_encrypt"]
SecurityPolicy = Literal[
    "none", "basic256", "basic128rsa15", "basic256sha256", "aes128_sha256rsaoaep", "aes256sha256rsapss"
]


class OpcUaClientBuilder:
    """Builder for the instro OPC UA client."""

    def __init__(self):
        self._security_mode: SecurityMode | None = None


class OpcUaClient:
    def __init__(self, security_mode: 'Literal["none", "sign"]'):
        self._security_mode = security_mode
