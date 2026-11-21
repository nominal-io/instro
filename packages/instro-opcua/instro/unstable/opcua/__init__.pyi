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
class OpcUaClient:
    def __new__(cls, _unavailable: Never) -> Self: ...
    @staticmethod
    def connect(endpoint_url: str) -> OpcUaClient: ...
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

def get_endpoints(endpoint_url: str) -> list[EndpointDescription]: ...

__all__ = ["OpcUaError", "OpcUaClient", "get_endpoints"]
