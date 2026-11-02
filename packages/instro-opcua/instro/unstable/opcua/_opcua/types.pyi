import datetime
import uuid
from typing import Any, Final, final

@final
class AttributeId:
    """Attributes that are available to read from a [`Node`]."""

    ACCESSLEVEL: Final[AttributeId]
    ACCESSLEVELEX: Final[AttributeId]
    ACCESSRESTRICTIONS: Final[AttributeId]
    ARRAYDIMENSIONS: Final[AttributeId]
    BROWSENAME: Final[AttributeId]
    CONTAINSNOLOOPS: Final[AttributeId]
    DATATYPE: Final[AttributeId]
    DATATYPEDEFINITION: Final[AttributeId]
    DESCRIPTION: Final[AttributeId]
    DISPLAYNAME: Final[AttributeId]
    EVENTNOTIFIER: Final[AttributeId]
    EXECUTABLE: Final[AttributeId]
    HISTORIZING: Final[AttributeId]
    INVERSENAME: Final[AttributeId]
    ISABSTRACT: Final[AttributeId]
    MINIMUMSAMPLINGINTERVAL: Final[AttributeId]
    NODECLASS: Final[AttributeId]
    NODEID: Final[AttributeId]
    ROLEPERMISSIONS: Final[AttributeId]
    SYMMETRIC: Final[AttributeId]
    USERACCESSLEVEL: Final[AttributeId]
    USEREXECUTABLE: Final[AttributeId]
    USERROLEPERMISSIONS: Final[AttributeId]
    USERWRITEMASK: Final[AttributeId]
    VALUE: Final[AttributeId]
    VALUERANK: Final[AttributeId]
    WRITEMASK: Final[AttributeId]

@final
class BrowsePath:
    def __repr__(self, /) -> str: ...
    @property
    def relative_path(self, /) -> Any: ...
    @property
    def starting_node(self, /) -> NodeId: ...

@final
class DataValue:
    @property
    def server_timestamp(self, /) -> datetime.datetime | None: ...
    @property
    def source_timestamp(self, /) -> datetime.datetime | None: ...
    def value(
        self, /
    ) -> (
        bool | int | float | str | uuid.UUID | NodeId | ExpandedNodeId | StatusCode | QualifiedName | datetime.datetime
    ): ...

@final
class ExpandedNodeId:
    def __str__(self, /) -> str: ...
    def namespace_uri(self, /) -> str | None: ...
    def node_id(self, /) -> NodeId: ...
    def server_index(self, /) -> int: ...

@final
class MessageSecurityMode:
    """Message security mode enum."""

    NONE: Final[MessageSecurityMode]
    """No signing or encryption."""
    SIGN: Final[MessageSecurityMode]
    """Message signing only, no encryption."""
    SIGNANDENCRYPT: Final[MessageSecurityMode]
    """Message signing and encryption."""

@final
class NodeId:
    def __str__(self, /) -> str: ...
    @staticmethod
    def from_string(id_str: str) -> NodeId: ...
    def identifier(self, /) -> int | str | bytes | uuid.UUID | None: ...
    def namespace_index(self, /) -> int: ...

@final
class QualifiedName:
    def __str__(self, /) -> str: ...
    def name(self, /) -> str: ...
    def namespace_index(self, /) -> int: ...

@final
class StatusCode:
    def __str__(self, /) -> str: ...
    def code(self, /) -> int: ...
    def is_bad(self, /) -> bool: ...
    def is_good(self, /) -> bool: ...
    def is_uncertain(self, /) -> bool: ...
    def name(self, /) -> str: ...

@final
class RelativePathElement:
    @property
    def target_name(self, /) -> QualifiedName: ...
    @property
    def reference_type_id(self, /) -> NodeId: ...
    @property
    def is_inverse(self, /) -> bool: ...
    @property
    def include_subtypes(self, /) -> bool: ...

@final
class RelativePath:
    def __repr__(self, /) -> str: ...
    @staticmethod
    def parse(path_str: str) -> RelativePath: ...
    @property
    def elements(self, /) -> list[RelativePathElement]: ...
