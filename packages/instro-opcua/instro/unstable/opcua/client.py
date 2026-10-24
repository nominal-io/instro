from typing import Literal

SecurityMode = Literal["none", "sign", "sign_and_encrypt"]
SecurityPolicy = Literal["none", "basic256", "basic128_rsa15", "basic256_sha256", "aes128_sha256_rsa_oaep", "aes256_sha256_rsa_pss"]

class OpcUaClientBuilder:
    """Builder for the instro OPC UA client."""
    def __init__(self):
        self._security_mode: SecurityMode | None = None

class OpcUaClient:
    def __init__(self, security_mode: 'Literal["none", "sign"]'):
        self._security_mode = security_mode