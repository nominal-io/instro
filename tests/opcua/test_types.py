from uuid import UUID

import pytest

from instro.unstable.opcua import OpcUaClient
from instro.unstable.opcua.types import (
    AttributeWriteMask,
    NodeId,
    RelativePath,
    StructureType,
)


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Boolean", True),
        ("SByte", -100),
        ("Byte", 250),
        ("Int16", -32000),
        ("UInt16", 64000),
        ("Int32", -2_000_000_000),
        ("UInt32", 4_000_000_000),
        ("Int64", -9_000_000_000_000_000_000),
        ("UInt64", 18_000_000_000_000_000_000),
        ("Float", 1.25),
        ("Double", -9.5),
        ("String", "pressure µ"),
        ("Guid", UUID("12345678-1234-5678-9abc-def012345678")),
        ("DateTime", 5_125_000_000),
    ],
)
def test_scalar_values(client: OpcUaClient, name: str, expected: object) -> None:
    value = client.get_node(f"/Objects/2:Rig/2:{name}").value.value
    assert type(value) is type(expected)
    assert value == expected


def test_node_id_value(client: OpcUaClient) -> None:
    value = client.get_node("/Objects/2:Rig/2:NodeId").value.value
    assert value.namespace_index == 4
    assert value.identifier == "remote-pump"
    assert {value: "pump"}[NodeId.from_string("ns=4;s=remote-pump")] == "pump"


def test_expanded_node_id_value(client: OpcUaClient) -> None:
    value = client.get_node("/Objects/2:Rig/2:ExpandedNodeId").value.value
    assert value.node_id.identifier == 42
    assert value.namespace_uri == "urn:external"
    assert value.server_index == 7
    local = client.get_node("/Objects/2:Rig/2:LocalExpandedNodeId").value.value
    assert local.namespace_uri is None
    assert local.server_index == 0


def test_qualified_name_value(client: OpcUaClient) -> None:
    value = client.get_node("/Objects/2:Rig/2:QualifiedName").value.value
    assert (value.namespace_index, value.name) == (4, "Pressure")
    assert str(value) == "4:Pressure"


@pytest.mark.parametrize(
    "name, code, status_name, classification",
    [
        ("GoodStatus", 0, "Good", (True, False, False)),
        ("UncertainStatus", 0x40000000, "Uncertain", (False, True, False)),
        ("BadStatus", 0x80000000, "Bad", (False, False, True)),
    ],
)
def test_status_code_values(
    client: OpcUaClient, name: str, code: int, status_name: str, classification: tuple[bool, bool, bool]
) -> None:
    value = client.get_node(f"/Objects/2:Rig/2:{name}").value.value
    assert value.code == code
    assert value.name() == status_name
    assert (value.is_good, value.is_uncertain, value.is_bad) == classification


@pytest.mark.parametrize("name, message", [("Array", "non-scalar"), ("Bytes", "unknown scalar value type")])
def test_unsupported_values(client: OpcUaClient, name: str, message: str) -> None:
    value = client.get_node(f"/Objects/2:Rig/2:{name}").value
    with pytest.raises(ValueError, match=message):
        _ = value.value


def test_bad_data_status_is_not_returned_as_a_value(client: OpcUaClient) -> None:
    with pytest.raises(ValueError, match="BadNotReadable"):
        _ = client.get_node("/Objects/2:Rig/2:BadData").value


def test_timestamps_cross_the_python_binding(client: OpcUaClient) -> None:
    value = client.get_node("/Objects/2:Rig/2:Timed").value
    assert value.source_timestamp == 1_250_000_000
    assert value.server_timestamp == 2_750_000_000


def test_absent_value_and_timestamps(client: OpcUaClient) -> None:
    value = client.get_node("/Objects/2:Rig/2:NoValue").value
    assert value.value is None
    assert value.source_timestamp is None
    assert value.server_timestamp is None


def test_relative_path_elements() -> None:
    path = RelativePath.parse("/2:Rig<#!HasComponent>3:Pump&/Status")
    first, second = path.elements
    assert first.reference_type_id.identifier == 33
    assert first.include_subtypes is True
    assert first.is_inverse is False
    assert second.reference_type_id.identifier == 47
    assert second.include_subtypes is False
    assert second.is_inverse is True
    assert (second.target_name.namespace_index, second.target_name.name) == (3, "Pump/Status")


def test_relative_path_value_semantics() -> None:
    path = RelativePath.parse("/Objects/2:Rig")
    equivalent = RelativePath.parse("/0:Objects/2:Rig")
    assert {path: "pump"}[equivalent] == "pump"
    assert path != RelativePath.parse("/2:Rig/3:Valve")


def test_attribute_mask_operations() -> None:
    mask = AttributeWriteMask.DISPLAY_NAME | AttributeWriteMask.DESCRIPTION
    assert mask.is_display_name_set is True
    assert mask.is_description_set is True
    assert mask.is_data_type_set is False
    assert mask & AttributeWriteMask.DISPLAY_NAME == AttributeWriteMask.DISPLAY_NAME
    assert mask & AttributeWriteMask.DATA_TYPE == AttributeWriteMask.NONE


def test_enum_definition(client: OpcUaClient) -> None:
    definition = client.get_node("/Objects/2:Rig/2:Mode").data_type_definition
    fields = definition.fields
    assert [(field.name, field.value, field.description.locale, field.description.text) for field in fields] == [
        ("Off", -7, "en-US", "Disabled"),
        ("On", 42, "en-US", "Enabled"),
    ]


def test_structure_definition(client: OpcUaClient) -> None:
    definition = client.get_node("/Objects/2:Rig/2:Reading").data_type_definition
    assert definition.base_data_type.identifier == 22
    assert (definition.default_encoding_id.namespace_index, definition.default_encoding_id.identifier) == (2, 7001)
    assert definition.structure_type == StructureType.STRUCTURE_WITH_OPTIONAL_FIELDS
    samples, label = definition.fields
    assert (samples.name, samples.description.locale, samples.description.text) == (
        "Samples",
        "en-US",
        "Voltage samples",
    )
    assert samples.data_type.identifier == 11
    assert samples.array_dimensions == [3]
    assert samples.value_rank == 1
    assert samples.is_optional is True
    assert samples.max_string_length == 0
    assert (label.name, label.description.locale, label.description.text) == ("Label", None, "Channel label")
    assert label.data_type.identifier == 12
    assert label.array_dimensions is None
    assert label.value_rank == -1
    assert label.is_optional is False
    assert label.max_string_length == 32
