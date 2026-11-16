from datetime import timedelta
from typing import assert_type

from instro.unstable.opcua import Node, OpcUaClient, Subscription
from instro.unstable.opcua.types import (
    AccessLevelExType,
    AttributePayload,
    AttributeValue,
    DateTime,
    EnumDefinition,
    EnumField,
    NodeClass,
    NodeId,
    PermissionType,
    RolePermissionType,
    StatusCode,
    StructureDefinition,
    StructureField,
    Value,
)


def semantic_results(node: Node, client: OpcUaClient) -> None:
    assert_type(node.value, AttributeValue[Value])
    assert_type(node.value.value, Value | None)
    assert_type(node.node_class.value, NodeClass | None)
    assert_type(node.access_level_ex.value, AccessLevelExType | None)
    assert_type(node.minimum_sampling_interval.value, timedelta | None)
    assert_type(node.array_dimensions.value, list[int] | None)
    assert_type(node.role_permissions.value, list[RolePermissionType] | None)
    assert_type(node.data_type_definition.value, StructureDefinition | EnumDefinition | None)
    assert_type(node.value.status, StatusCode)
    assert_type(node.value.source_timestamp, DateTime | None)
    assert_type(node.value.server_timestamp, DateTime | None)
    assert_type(node.value.source_unix_timestamp, int | None)
    assert_type(node.value.server_unix_timestamp, int | None)
    assert_type(node.value.source_picoseconds, int | None)
    assert_type(node.value.server_picoseconds, int | None)
    assert_type(client.read_nodes([node]), list[AttributeValue[AttributePayload]])
    assert_type(client.translate(["/Objects"]), list[Node])


def structure_fields(
    role: RolePermissionType,
    structure: StructureDefinition,
    field: StructureField,
    enumeration: EnumDefinition,
    enum_field: EnumField,
) -> None:
    assert_type(role.role_id, NodeId)
    assert_type(role.permissions, PermissionType)
    assert_type(structure.fields, list[StructureField])
    assert_type(field.array_dimensions, list[int] | None)
    assert_type(field.is_optional, bool)
    assert_type(enumeration.fields, list[EnumField])
    assert_type(enum_field.value, int)


def rejected_calls(node: Node, client: OpcUaClient) -> None:
    node.get_child(NodeId("i=84"))  # type: ignore[arg-type]
    node.get_attribute(1.5)  # type: ignore[arg-type]
    client.get_node(84)  # type: ignore[arg-type]
    client.read_nodes([object()])  # type: ignore[list-item]
    client.subscribe([node], publishing_interval="fast")  # type: ignore[arg-type]
    OpcUaClient.connect("opc.tcp://localhost", security_mode=3)  # type: ignore[arg-type]
    OpcUaClient.connect("opc.tcp://localhost", request_timeout="slow")  # type: ignore[arg-type]


def subscription_results(subscription: Subscription) -> None:
    assert_type(next(subscription), tuple[Node, AttributeValue[AttributePayload]])
