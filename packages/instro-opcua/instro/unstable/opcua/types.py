import sys
from typing import TYPE_CHECKING

from ._opcua import types

if TYPE_CHECKING:
    AccessLevelExType = types.AccessLevelExType
    AccessLevelType = types.AccessLevelType
    AccessRestrictionType = types.AccessRestrictionType
    ApplicationType = types.ApplicationType
    AttributeId = types.AttributeId
    AttributeWriteMask = types.AttributeWriteMask
    BrowsePath = types.BrowsePath
    DataValue = types.DataValue
    EndpointDescription = types.EndpointDescription
    EnumDefinition = types.EnumDefinition
    EnumField = types.EnumField
    EventNotifierType = types.EventNotifierType
    ExpandedNodeId = types.ExpandedNodeId
    LocalizedText = types.LocalizedText
    MessageSecurityMode = types.MessageSecurityMode
    NodeClass = types.NodeClass
    NodeId = types.NodeId
    QualifiedName = types.QualifiedName
    RelativePath = types.RelativePath
    RelativePathElement = types.RelativePathElement
    StatusCode = types.StatusCode
    StructureDefinition = types.StructureDefinition
    StructureField = types.StructureField
    StructureType = types.StructureType

    __all__ = [
        "AccessLevelExType",
        "AccessLevelType",
        "AccessRestrictionType",
        "ApplicationType",
        "AttributeId",
        "AttributeWriteMask",
        "BrowsePath",
        "DataValue",
        "EndpointDescription",
        "EnumDefinition",
        "EnumField",
        "EventNotifierType",
        "ExpandedNodeId",
        "LocalizedText",
        "MessageSecurityMode",
        "NodeClass",
        "NodeId",
        "QualifiedName",
        "RelativePath",
        "RelativePathElement",
        "StatusCode",
        "StructureDefinition",
        "StructureField",
        "StructureType",
    ]

else:
    # patch in the types module so that direct imports from the types module work
    # i.e. so `from instro.unstable.opcua.types import AccessLevelType` works
    sys.modules[__name__] = types

