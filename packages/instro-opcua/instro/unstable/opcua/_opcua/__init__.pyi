import datetime
import typing
from collections.abc import Sequence
from types import TracebackType
from typing import final

from .types import (
    AccessLevelExType,
    AccessLevelType,
    AccessRestrictionType,
    AttributeId,
    AttributeWriteMask,
    BrowsePath,
    DataValue,
    EndpointDescription,
    EnumDefinition,
    EventNotifierType,
    LocalizedText,
    MessageSecurityMode,
    NodeClass,
    NodeId,
    QualifiedName,
    RelativePath,
    StructureDefinition,
)

@final
class Node:
    def __repr__(self, /) -> str: ...
    @property
    def access_level(self, /) -> AccessLevelType | None: ...
    @property
    def access_level_ex(self, /) -> AccessLevelExType | None: ...
    @property
    def access_restrictions(self, /) -> AccessRestrictionType | None: ...
    @property
    def browse_name(self, /) -> QualifiedName: ...
    @property
    def browse_path(self, /) -> BrowsePath: ...
    @property
    def children(self, /) -> list[Node]: ...
    @property
    def contains_no_loops(self, /) -> bool | None: ...
    @property
    def data_type(self, /) -> NodeId | None: ...
    @property
    def data_type_definition(self, /) -> StructureDefinition | EnumDefinition | None: ...
    @property
    def description(self, /) -> LocalizedText | None: ...
    @property
    def display_name(self, /) -> LocalizedText: ...
    @property
    def event_notifier(self, /) -> EventNotifierType | None: ...
    @property
    def executable(self, /) -> bool | None: ...
    def get_attribute(self, /, attr: AttributeId | int | str) -> DataValue: ...
    def get_child(self, /, path: RelativePath | str) -> Node: ...
    @property
    def historizing(self, /) -> bool | None: ...
    @property
    def inverse_name(self, /) -> LocalizedText | None: ...
    @property
    def is_abstract(self, /) -> bool | None: ...
    @property
    def minimum_sampling_interval(self, /) -> datetime.timedelta | None: ...
    @property
    def node_class(self, /) -> NodeClass: ...
    @property
    def node_id(self, /) -> NodeId: ...
    @property
    def symmetric(self, /) -> bool | None: ...
    @property
    def user_access_level(self, /) -> AccessLevelType | None: ...
    @property
    def user_executable(self, /) -> bool | None: ...
    @property
    def user_write_mask(self, /) -> AttributeWriteMask | None: ...
    @property
    def value(self, /) -> DataValue | None: ...
    @property
    def write_mask(self, /) -> AttributeWriteMask | None: ...

@final
class OpcUaClient:
    def __enter__(self, /) -> OpcUaClient: ...
    def __exit__(
        self,
        /,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> bool: ...
    @staticmethod
    def connect(
        endpoint_url: str,
        *,
        username: str | None = None,
        password: str | None = None,
        cert: bytes | None = None,
        private_key: bytes | None = None,
        ignore_server_cert: bool = False,
        use_generated_cert: bool = False,
        security_mode: typing.Literal["none", "sign", "signandencrypt"] | MessageSecurityMode | None = None,
        security_policy: typing.Literal[
            "none", "basic128rsa15", "basic256", "basic256sha256", "aes128sha256rsaoaep", "aes256sha256rsapss"
        ]
        | None = None,
        timeout: float | datetime.timedelta | None = None,
    ) -> OpcUaClient: ...
    def disconnect(self, /) -> None: ...
    def get_node(self, /, path: RelativePath | str) -> Node: ...
    @property
    def objects(self, /) -> Node: ...
    def subscribe(
        self,
        /,
        on_data: typing.Callable[[list[tuple[Node, DataValue]]], None],
        nodes: Sequence[Node | RelativePath | str],
        freq_hz: float,
        bg_poll_freq_hz: float | None = None,
        lifetime_count: int | None = None,
        max_keep_alive_count: int | None = None,
        max_notifications_per_publish: int | None = None,
        priority: int | None = None,
        publish_freq_hz: float | None = None,
        queue_size: int | None = None,
        discard_oldest: bool = True,
    ) -> Session: ...
    @property
    def types(self, /) -> Node: ...
    @property
    def views(self, /) -> Node: ...

@final
class Session:
    def __enter__(self, /) -> Session: ...
    def __exit__(
        self,
        /,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> bool: ...

def get_endpoints(endpoint_url: str) -> list[EndpointDescription]:
    """Helper function to discover endpoints for a given endpoint URL."""
