from collections.abc import Iterable
from datetime import timedelta
from os import PathLike
from types import TracebackType
from typing import Never, Self, final

from .types import (
    AccessLevelExType,
    AccessLevelType,
    AccessRestrictionType,
    AttributeId,
    AttributePayload,
    AttributeValue,
    AttributeWriteMask,
    BrowsePath,
    Duration,
    EndpointDescription,
    EnumDefinition,
    EventNotifierType,
    LocalizedText,
    MessageSecurityMode,
    NodeClass,
    NodeId,
    QualifiedName,
    RelativePath,
    RolePermissionType,
    SecurityPolicy,
    StatusCode,
    StructureDefinition,
    Value,
)

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
    def get_node(self, path: RelativePath | str) -> Node: ...
    @property
    def objects(self) -> Node: ...
    @property
    def types(self) -> Node: ...
    @property
    def views(self) -> Node: ...
    def read_nodes(
        self,
        nodes: Iterable[Node | RelativePath | str],
        attr: AttributeId | int | str | None = None,
    ) -> list[AttributeValue[AttributePayload]]: ...
    def subscribe(
        self,
        nodes: Iterable[Node | RelativePath | str],
        *,
        attribute: AttributeId | int | str | None = None,
        publishing_interval: Duration | timedelta | int | float = ...,
        lifetime_count: int = ...,
        max_keep_alive_count: int = ...,
        max_notifications_per_publish: int = ...,
        priority: int = ...,
        publishing_enabled: bool = True,
        sampling_interval: float = 0.0,
        queue_size: int = ...,
        discard_oldest: bool = False,
        poll_interval: Duration | timedelta | int | float | None = None,
    ) -> Subscription: ...
    def __enter__(self) -> OpcUaClient: ...
    def __exit__(
        self,
        _kind: type[BaseException] | None,
        value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None: ...

@final
class Node:
    def __new__(cls, _unavailable: Never) -> Self: ...
    @property
    def node_id(self) -> NodeId: ...
    @property
    def browse_path(self) -> BrowsePath: ...
    def get_child(self, path: RelativePath | str) -> Node: ...
    @property
    def children(self) -> list[Node]: ...
    def get_attribute(self, attr: AttributeId | int | str) -> AttributeValue[AttributePayload]: ...
    @property
    def node_class(self) -> AttributeValue[NodeClass]: ...
    @property
    def browse_name(self) -> AttributeValue[QualifiedName]: ...
    @property
    def display_name(self) -> AttributeValue[LocalizedText]: ...
    @property
    def description(self) -> AttributeValue[LocalizedText]: ...
    @property
    def write_mask(self) -> AttributeValue[AttributeWriteMask]: ...
    @property
    def user_write_mask(self) -> AttributeValue[AttributeWriteMask]: ...
    @property
    def is_abstract(self) -> AttributeValue[bool]: ...
    @property
    def symmetric(self) -> AttributeValue[bool]: ...
    @property
    def inverse_name(self) -> AttributeValue[LocalizedText]: ...
    @property
    def contains_no_loops(self) -> AttributeValue[bool]: ...
    @property
    def event_notifier(self) -> AttributeValue[EventNotifierType]: ...
    @property
    def value(self) -> AttributeValue[Value]: ...
    @property
    def data_type(self) -> AttributeValue[NodeId]: ...
    @property
    def value_rank(self) -> AttributeValue[int]: ...
    @property
    def array_dimensions(self) -> AttributeValue[list[int]]: ...
    @property
    def access_level(self) -> AttributeValue[AccessLevelType]: ...
    @property
    def user_access_level(self) -> AttributeValue[AccessLevelType]: ...
    @property
    def minimum_sampling_interval(self) -> AttributeValue[timedelta]: ...
    @property
    def historizing(self) -> AttributeValue[bool]: ...
    @property
    def executable(self) -> AttributeValue[bool]: ...
    @property
    def user_executable(self) -> AttributeValue[bool]: ...
    @property
    def data_type_definition(self) -> AttributeValue[StructureDefinition | EnumDefinition]: ...
    @property
    def role_permissions(self) -> AttributeValue[list[RolePermissionType]]: ...
    @property
    def user_role_permissions(self) -> AttributeValue[list[RolePermissionType]]: ...
    @property
    def access_restrictions(self) -> AttributeValue[AccessRestrictionType]: ...
    @property
    def access_level_ex(self) -> AttributeValue[AccessLevelExType]: ...

@final
class Subscription:
    def __new__(cls, _unavailable: Never) -> Self: ...
    def __iter__(self) -> Subscription: ...
    def __next__(self) -> tuple[Node, AttributeValue[AttributePayload]]: ...
    def close(self) -> None: ...
    def __enter__(self) -> Subscription: ...
    def __exit__(
        self,
        _kind: type[BaseException] | None,
        value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None: ...

def get_endpoints(endpoint_url: str) -> list[EndpointDescription]: ...

__all__ = ["IdentityToken", "Node", "OpcUaClient", "OpcUaError", "Subscription", "get_endpoints"]
