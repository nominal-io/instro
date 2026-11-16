from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from asyncua import ua

from instro.unstable.opcua import OpcUaError
from instro.unstable.opcua.types import (
    AccessLevelExType,
    AttributeId,
    EnumDefinition,
    EnumField,
    NodeClass,
    NodeId,
    RolePermissionType,
    StructureDefinition,
    StructureField,
)


def sample(value=None, *, status=None, source=None, server=None, source_fraction=None, server_fraction=None):
    result = ua.DataValue(
        Value=value,
        StatusCode=None if status is None else ua.StatusCode(status),
        SourceTimestamp=source,
        ServerTimestamp=server,
        SourcePicoseconds=source_fraction,
        ServerPicoseconds=server_fraction,
    )
    result.Value = value
    return result


@pytest.mark.parametrize(
    ("kind", "wire", "python_type", "expected"),
    [
        (ua.VariantType.Boolean, True, bool, True),
        (ua.VariantType.SByte, -101, int, -101),
        (ua.VariantType.Byte, 251, int, 251),
        (ua.VariantType.Int16, -32001, int, -32001),
        (ua.VariantType.UInt16, 65001, int, 65001),
        (ua.VariantType.Int32, -2000000001, int, -2000000001),
        (ua.VariantType.UInt32, 4000000001, int, 4000000001),
        (ua.VariantType.Int64, -9000000000000000001, int, -9000000000000000001),
        (ua.VariantType.UInt64, 18000000000000000001, int, 18000000000000000001),
        (ua.VariantType.Float, 1.25, float, 1.25),
        (ua.VariantType.Double, -2.125, float, -2.125),
        (ua.VariantType.String, "café", str, "café"),
        (ua.VariantType.XmlElement, ua.XmlElement("<mode>on</mode>"), str, "<mode>on</mode>"),
        (ua.VariantType.ByteString, b"\x00\xff\x80", bytes, b"\x00\xff\x80"),
        (
            ua.VariantType.Guid,
            UUID("12345678-9abc-def0-8123-456789abcdef"),
            UUID,
            UUID("12345678-9abc-def0-8123-456789abcdef"),
        ),
        (ua.VariantType.DateTime, datetime(1970, 1, 1, 0, 0, 5, 125000, tzinfo=timezone.utc), int, 5125000000),
    ],
)
def test_wire_scalars_have_python_type_and_value(node, attribute_data, kind, wire, python_type, expected):
    attribute_data(ua.AttributeIds.Value, sample(ua.Variant(wire, kind)))
    actual = node.value.value
    assert type(actual) is python_type
    assert actual == expected


def test_array_payload_preserves_order_and_allocation(node, attribute_data):
    attribute_data(ua.AttributeIds.Value, sample(ua.Variant([7, -2, 41], ua.VariantType.Int32)))
    result = node.value
    assert result.value == [7, -2, 41]
    assert result.value is result.value


@pytest.fixture
def structure_values():
    role = ua.RolePermissionType(RoleId=ua.NodeId("operator", 2), Permissions=ua.PermissionType(33))
    field = ua.StructureField(Name="samples", DataType=ua.NodeId(7), ValueRank=1, ArrayDimensions=[3], IsOptional=True)
    structure = ua.StructureDefinition(StructureType=ua.StructureType.StructureWithOptionalFields, Fields=[field])
    off = ua.EnumField(Value=-7, Name="Off")

    on = ua.EnumField(Value=42, Name="On")
    return role, structure, field, ua.EnumDefinition(Fields=[off, on]), off


def test_supported_structures_decode_nondefault_fields(node, attribute_data, structure_values):
    attribute_data(ua.AttributeIds.Value, sample(ua.Variant(list(structure_values), ua.VariantType.ExtensionObject)))
    values = node.value.value
    expected_types = [RolePermissionType, StructureDefinition, StructureField, EnumDefinition, EnumField]
    assert [type(value) for value in values] == expected_types

    role, structure, field, enumeration, enum_field = values
    assert role.role_id == NodeId("ns=2;s=operator")
    assert int(role.permissions) == 33
    assert int(structure.structure_type) == 1
    assert [(item.name, item.is_optional) for item in structure.fields] == [("samples", True)]
    assert field.array_dimensions == [3]
    assert [(item.name, item.value) for item in enumeration.fields] == [("Off", -7), ("On", 42)]
    assert (enum_field.name, enum_field.value) == ("Off", -7)


@pytest.mark.parametrize("definition_index", [1, 3])
def test_data_type_definition_selects_supported_definition(node, attribute_data, structure_values, definition_index):
    wire = ua.Variant(structure_values[definition_index], ua.VariantType.ExtensionObject)
    attribute_data(ua.AttributeIds.DataTypeDefinition, sample(wire))
    definition = node.data_type_definition.value
    if definition_index == 1:
        assert type(definition) is StructureDefinition
        assert definition.fields[0].name == "samples"
        assert definition.fields[0].is_optional is True
    else:
        assert type(definition) is EnumDefinition
        assert [(field.name, field.value) for field in definition.fields] == [("Off", -7), ("On", 42)]


def test_named_getters_apply_attribute_semantics(node, attribute_data):
    attribute_data(ua.AttributeIds.NodeClass, sample(ua.Variant(2, ua.VariantType.Int32)))
    node_class = node.node_class.value
    assert type(node_class) is NodeClass
    assert node_class == NodeClass.VARIABLE

    attribute_data(ua.AttributeIds.AccessLevelEx, sample(ua.Variant(513, ua.VariantType.UInt32)))
    access = node.access_level_ex.value
    assert type(access) is AccessLevelExType
    assert int(access) == 513

    attribute_data(ua.AttributeIds.MinimumSamplingInterval, sample(ua.Variant(250.5, ua.VariantType.Double)))
    assert node.minimum_sampling_interval.value == timedelta(microseconds=250500)


@pytest.mark.parametrize("selector", [AttributeId.NODE_CLASS, 2, "NodeClass", "node_class"])
def test_attribute_selectors_use_semantic_conversion(node, selector):
    value = node.get_attribute(selector)
    assert value.attribute_id == AttributeId.NODE_CLASS
    assert value.value == NodeClass.VARIABLE


@pytest.mark.parametrize("payload", [None, ua.Variant(None, ua.VariantType.Null)])
def test_absent_and_explicit_null_payloads_default_to_none_and_good(node, attribute_data, payload):
    attribute_data(ua.AttributeIds.Value, sample(payload))
    result = node.value
    assert result.value is None
    assert result.status.code == 0


@pytest.mark.parametrize("status", [0x800A0000, 0x40000000])
def test_bad_or_uncertain_quality_retains_payload(node, attribute_data, status):
    attribute_data(ua.AttributeIds.Value, sample(ua.Variant(17, ua.VariantType.Int32), status=status))
    result = node.value
    assert result.status.code == status
    assert result.value == 17


def test_source_and_server_timestamps_remain_distinct(node, attribute_data):
    source = datetime(1970, 1, 1, 0, 0, 5, 125000, tzinfo=timezone.utc)
    server = datetime(1970, 1, 1, 0, 0, 9, 250000, tzinfo=timezone.utc)
    wire = sample(ua.Variant(4, ua.VariantType.Int32), source=source, server=server)

    attribute_data(ua.AttributeIds.Value, wire)
    result = node.value
    assert result.source_timestamp.unix_timestamp_nanos() == 5125000000
    assert result.server_timestamp.unix_timestamp_nanos() == 9250000000
    assert result.source_unix_timestamp == 5125000000
    assert result.server_unix_timestamp == 9250000000


@pytest.mark.parametrize(
    ("present", "fraction", "expected"), [("source", 1234, 5000000012), ("server", 5678, 5000000056)]
)
def test_single_clock_fraction_does_not_fill_absent_clock(node, attribute_data, present, fraction, expected):
    timestamp = datetime(1970, 1, 1, 0, 0, 5, tzinfo=timezone.utc)
    wire = sample(ua.Variant(4, ua.VariantType.Int32), **{present: timestamp, f"{present}_fraction": fraction})
    attribute_data(ua.AttributeIds.Value, wire)
    result = node.value
    assert getattr(result, f"{present}_unix_timestamp") == expected
    assert getattr(result, f"{present}_picoseconds") == fraction
    absent = "server" if present == "source" else "source"
    assert getattr(result, f"{absent}_timestamp") is None
    assert getattr(result, f"{absent}_unix_timestamp") is None
    assert getattr(result, f"{absent}_picoseconds") is None


def test_incompatible_fixed_payload_defers_error_and_keeps_metadata(node, attribute_data):
    timestamp = datetime(1970, 1, 1, 0, 0, 5, tzinfo=timezone.utc)
    wire = sample(ua.Variant(1.25, ua.VariantType.Double), status=0x40000000, source=timestamp, source_fraction=1234)
    attribute_data(ua.AttributeIds.DisplayName, wire)
    result = node.display_name
    assert result.attribute_id == AttributeId.DISPLAY_NAME
    assert result.status.code == 0x40000000
    assert result.source_unix_timestamp == 5000000012
    with pytest.raises(OpcUaError) as failure:
        _ = result.value

    assert failure.value.status_code.code == 0x80740000


def test_unsupported_generic_payload_defers_not_supported(node, attribute_data):
    nested = sample(ua.Variant(7, ua.VariantType.Int32))
    attribute_data(ua.AttributeIds.Value, sample(ua.Variant(nested, ua.VariantType.DataValue)))
    result = node.value
    with pytest.raises(OpcUaError) as failure:
        _ = result.value

    assert failure.value.status_code.code == 0x803D0000
