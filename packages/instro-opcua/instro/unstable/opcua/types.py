"""Bounded OPC UA primitives and read-only attribute results."""

from datetime import timedelta
from enum import IntEnum
from typing import TypeAlias
from uuid import UUID

from instro.unstable._opcua import (
    AccessLevelExType,
    AccessLevelType,
    AccessRestrictionType,
    ApplicationDescription,
    ApplicationType,
    AttributeId,
    AttributeValue,
    AttributeWriteMask,
    BrowsePath,
    DateTime,
    Duration,
    EndpointDescription,
    EnumDefinition,
    EnumField,
    EventNotifierType,
    ExpandedNodeId,
    LocalizedText,
    MessageSecurityMode,
    NodeClass,
    NodeId,
    PermissionType,
    QualifiedName,
    RelativePath,
    RelativePathElement,
    RolePermissionType,
    SecurityPolicy,
    StatusCode,
    StructureDefinition,
    StructureField,
    StructureType,
    UserTokenPolicy,
    UserTokenType,
    XmlElement,
)

__all__ = [
    "AccessLevelExType",
    "AccessLevelType",
    "AccessRestrictionType",
    "ApplicationDescription",
    "ApplicationType",
    "AttributeId",
    "AttributePayload",
    "AttributeValue",
    "AttributeWriteMask",
    "BrowsePath",
    "DataType",
    "DateTime",
    "Duration",
    "EndpointDescription",
    "EnumDefinition",
    "EnumField",
    "EventNotifierType",
    "ExpandedNodeId",
    "LocalizedText",
    "MessageSecurityMode",
    "NodeClass",
    "NodeId",
    "PermissionType",
    "QualifiedName",
    "RelativePath",
    "RelativePathElement",
    "RolePermissionType",
    "SecurityPolicy",
    "StatusCode",
    "StructureDefinition",
    "StructureField",
    "StructureType",
    "UserTokenPolicy",
    "UserTokenType",
    "Value",
    "ValueScalar",
    "XmlElement",
]


class DataType(IntEnum):
    """Namespace-zero OPC UA DataType identifiers supported for writes."""

    BOOLEAN = 1
    SBYTE = 2
    BYTE = 3
    INT16 = 4
    UINT16 = 5
    INT32 = 6
    UINT32 = 7
    INT64 = 8
    UINT64 = 9
    FLOAT = 10
    DOUBLE = 11
    STRING = 12
    DATE_TIME = 13
    GUID = 14
    BYTE_STRING = 15
    XML_ELEMENT = 16
    NODE_ID = 17
    EXPANDED_NODE_ID = 18
    STATUS_CODE = 19
    QUALIFIED_NAME = 20
    LOCALIZED_TEXT = 21


ValueScalar: TypeAlias = (
    bool
    | int
    | float
    | str
    | bytes
    | UUID
    | NodeId
    | ExpandedNodeId
    | StatusCode
    | QualifiedName
    | LocalizedText
    | XmlElement
    | RolePermissionType
    | StructureDefinition
    | StructureField
    | EnumDefinition
    | EnumField
)
Value: TypeAlias = (
    ValueScalar
    | list[bool]
    | list[int]
    | list[float]
    | list[str]
    | list[bytes]
    | list[UUID]
    | list[NodeId]
    | list[ExpandedNodeId]
    | list[StatusCode]
    | list[QualifiedName]
    | list[LocalizedText]
    | list[XmlElement]
    | list[RolePermissionType]
    | list[StructureDefinition]
    | list[StructureField]
    | list[EnumDefinition]
    | list[EnumField]
    | list[RolePermissionType | StructureDefinition | StructureField | EnumDefinition | EnumField]
)
AttributePayload: TypeAlias = (
    Value
    | NodeClass
    | AttributeWriteMask
    | EventNotifierType
    | AccessLevelType
    | AccessLevelExType
    | AccessRestrictionType
    | timedelta
)
