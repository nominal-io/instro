"""Bounded OPC UA primitives and read-only attribute results."""

from datetime import timedelta
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
]

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
