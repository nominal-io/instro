from datetime import timedelta
from typing import assert_type

from instro.unstable.opcua import Node, OpcUaClient, OpcUaConnectionConfig, OpcUaDevice, OpcUaDeviceConfig, Subscription
from instro.unstable.opcua.types import (
    AccessLevelExType,
    AttributePayload,
    AttributeValue,
    DataType,
    DateTime,
    EnumDefinition,
    EnumField,
    LocalizedText,
    NodeClass,
    NodeId,
    PermissionType,
    RolePermissionType,
    StatusCode,
    StructureDefinition,
    StructureField,
    Value,
    XmlElement,
)


def configured_device() -> None:
    config = OpcUaDeviceConfig(connection=OpcUaConnectionConfig(endpoint_url="opc.tcp://localhost"))
    with OpcUaDevice("line_1", config) as device:
        assert_type(device.client, OpcUaClient)


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
    assert_type(client._translate(["/Objects"]), list[Node])


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
    assert_type(enum_field.display_name.text, str | None)
    assert_type(enum_field.display_name.locale, str | None)


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


def attribute_writes(node: Node, field: EnumField) -> None:
    node.value = 42
    node.value = XmlElement("<mode>on</mode>")
    node.value = NodeId("i=84")
    node.value = field
    node.value = (field,)
    node.display_name = LocalizedText("Pump")
    node.set_attribute("Value", 42, dtype=DataType.INT16)
    node.set_attribute("DisplayName", LocalizedText("Pump"))
    node.set_attribute("Value", [1, 2], dtype=DataType.INT16)
    node.set_attribute("Value", (1, 2), dtype=DataType.UINT16)
    node.set_attribute("Value", [field], dtype=DataType.ENUM_FIELD)
    node.display_name = "Pump"  # type: ignore[assignment]
    node.set_attribute("Value", (1, "two"))  # type: ignore[arg-type]
    node.set_attribute("Value", object())  # type: ignore[arg-type]
    node.set_attribute("Value", 42, dtype=4)  # type: ignore[arg-type]
