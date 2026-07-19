from typing import Literal, TypeAlias

SecurityMode: TypeAlias = Literal["none", "sign", "sign_and_encrypt"]
SecurityPolicy: TypeAlias = Literal[
    "none",
    "basic128_rsa15",
    "basic256",
    "basic256_sha256",
    "aes128_sha256_rsa_oaep",
    "aes256_sha256_rsa_pss",
]
class OpcUaError(Exception): ...


class OpcUaNode:
    @property
    def node_id(self) -> str: ...
    @property
    def browse_name(self) -> str: ...
    @property
    def display_name(self) -> str: ...
    @property
    def node_class(self) -> str: ...
    @property
    def browse_path(self) -> str: ...
    @property
    def children(self) -> list["OpcUaNode"]: ...
    def __repr__(self) -> str: ...


class OpcUaClient:
    @classmethod
    def connect(
        cls,
        endpoint_url: str,
        *,
        security_mode: SecurityMode | None = None,
        security_policy: SecurityPolicy | None = None,
        username: str | None = None,
        password: str | None = None,
        user_certificate: bytes | None = None,
        user_token_policy_id: str | None = None,
        certificate: bytes | None = None,
        private_key: bytes | None = None,
        private_key_password: str | None = None,
        generate_self_signed_pki: bool | None = None,
        trust_server_certificates: bool | None = None,
        timeout: float | None = None,
        secure_channel_lifetime: float | None = None,
        requested_session_timeout: float | None = None,
        connectivity_check_interval: float | None = None,
    ) -> "OpcUaClient": ...
    @property
    def endpoint_url(self) -> str: ...
    @property
    def closed(self) -> bool: ...
    def get_node(self, query: str) -> OpcUaNode: ...
    def resolve_browse_path(self, browse_path: str) -> str: ...
    def close(self) -> None: ...
    def __enter__(self) -> "OpcUaClient": ...
    def __exit__(
        self,
        exc_type: object | None,
        exc: BaseException | None,
        tb: object | None,
    ) -> bool: ...
    def __repr__(self) -> str: ...
