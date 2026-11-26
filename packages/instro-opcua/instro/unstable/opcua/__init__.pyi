from datetime import timedelta
from os import PathLike
from types import TracebackType
from typing import Never, Self, final

from .types import Duration, EndpointDescription, MessageSecurityMode, SecurityPolicy, StatusCode

class OpcUaError(Exception):
    status_code: StatusCode

@final
class IdentityToken:
    def __new__(cls, _unavailable: Never) -> Self: ...
    @staticmethod
    def anonymous() -> IdentityToken: ...
    @staticmethod
    def user_name(username: str, password: str) -> IdentityToken: ...
    @staticmethod
    def x509(certificate_path: str | PathLike[str], private_key_path: str | PathLike[str]) -> IdentityToken: ...

@final
class OpcUaClient:
    def __new__(cls, _unavailable: Never) -> Self: ...
    @staticmethod
    def connect(
        endpoint_url: str,
        *,
        identity: IdentityToken | None = None,
        security_policy: SecurityPolicy | str = ...,
        security_mode: MessageSecurityMode | str = ...,
        application_name: str = ...,
        application_uri: str = ...,
        product_uri: str = "",
        create_sample_keypair: bool = False,
        certificate_path: str | PathLike[str] | None = None,
        private_key_path: str | PathLike[str] | None = None,
        trust_server_certs: bool = False,
        verify_server_certs: bool = True,
        pki_dir: str | PathLike[str] | None = None,
        preferred_locales: list[str] = ...,
        channel_lifetime: int = ...,
        session_retry_limit: int = ...,
        session_retry_initial: Duration | timedelta | int | float = ...,
        session_retry_max: Duration | timedelta | int | float = ...,
        keep_alive_interval: Duration | timedelta | int | float = ...,
        max_array_length: int = ...,
        max_byte_string_length: int = ...,
        max_chunk_count: int = ...,
        max_chunk_size: int = ...,
        max_incoming_chunk_size: int = ...,
        max_message_size: int = ...,
        max_string_length: int = ...,
        max_failed_keep_alive_count: int = ...,
        request_timeout: Duration | timedelta | int | float = ...,
        publish_timeout: Duration | timedelta | int | float = ...,
        min_publish_interval: Duration | timedelta | int | float = ...,
        ignore_clock_skew: bool = False,
        recreate_monitored_items_chunk: int = ...,
        recreate_subscriptions: bool = True,
        session_name: str = "Rust OPC UA Client",
        session_timeout: int = ...,
        session_nonce_length: int = ...,
    ) -> OpcUaClient: ...
    def disconnect(self) -> None: ...
    def enable_reconnects(self) -> None: ...
    def disable_reconnects(self) -> None: ...
    def __enter__(self) -> OpcUaClient: ...
    def __exit__(
        self,
        _kind: type[BaseException] | None,
        value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None: ...

def get_endpoints(endpoint_url: str, *, pki_dir: str | PathLike[str] | None = None) -> list[EndpointDescription]: ...

__all__ = ["OpcUaError", "OpcUaClient", "get_endpoints", "IdentityToken"]
