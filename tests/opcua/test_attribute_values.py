from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from asyncua import ua
from asyncua.ua.attribute_ids import AttributeIds
from asyncua.ua.status_codes import StatusCodes
from typing_extensions import Any, cast

from instro.unstable.opcua import Node, OpcUaError
from instro.unstable.opcua.types import (
    AccessLevelExType,
    AttributeId,
    EnumDefinition,
    EnumField,
    NodeClass,
    NodeId,
    PermissionType,
    RolePermissionType,
    StructureDefinition,
    StructureField,
)
from tests.opcua.conftest import ServerNode


def sample(
    value: Any | None = None,
    value_type: ua.VariantType | None = None,
    *,
    status: int | None = None,
    source: ua.DateTime | None = None,
    server: ua.DateTime | None = None,
    source_picos: int | None = None, # 10-pico intervals
    server_picos: int | None = None, # 10-pico intervals
) -> ua.DataValue:
    result = ua.DataValue(
        Value=ua.Variant(value) if value_type is None else ua.Variant(value, value_type),
        StatusCode=ua.StatusCode(ua.UInt32(status)) if status else None,
        SourceTimestamp=source,
        ServerTimestamp=server,
        SourcePicoseconds=ua.UInt16(source_picos) if source_picos is not None else None,
        ServerPicoseconds=ua.UInt16(server_picos) if server_picos is not None else None,
    )

    # ctor of `ua.DataValue` sets `Value` to `Null` if `value` is `None`
    #
    if value is None:
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
        (ua.VariantType.XmlElement, ua.XmlElement(cast(ua.String, "<mode>on</mode>")), str, "<mode>on</mode>"),
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
def test_wire_scalars_have_python_type_and_value(
    server_node: ServerNode, client_node: Node, kind: ua.VariantType, wire: ua.Variant, python_type: type, expected: Any
):
    server_node.set_attr(AttributeIds.Value, sample(wire, kind))

    dtype = ua.NodeId(ua.Int32(kind))
    server_node.set_attr(AttributeIds.DataType, sample(dtype))

    actual = client_node.value.value
    assert type(actual) is python_type
    assert actual == expected
    assert str(client_node.data_type.value), dtype.to_string()


def test_array_payload_preserves_order_and_allocation(server_node: ServerNode, client_node: Node):
    values = [7, -2, 41]
    server_node.set_attr(AttributeIds.Value, sample(values, ua.VariantType.Int32))

    result = client_node.value
    assert result.value == values

    # looks odd, but the `AttributeValue.value` field should return the same object
    # to prevent overhead of converting to a new python object per-access
    access_1 = result.value
    access_2 = result.value
    assert access_1 is access_2


def test_role_permission_decodes_role_id_and_permission_bits(server_node: ServerNode, client_node: Node):
    wire = ua.RolePermissionType(
        RoleId=ua.NodeId.from_string("ns=2;s=operator"),
        Permissions=ua.PermissionType.ReadRolePermissions | ua.PermissionType.WriteAttribute,
    )

    server_node.set_attr(AttributeIds.Value, sample(wire, ua.VariantType.ExtensionObject))

    role = client_node.value.value
    assert type(role) is RolePermissionType
    assert role.role_id == NodeId("ns=2;s=operator")
    assert role.permissions == PermissionType.READ_ROLE_PERMISSIONS | PermissionType.WRITE_ATTRIBUTE


def test_structure_field_decodes_array_dimensions_and_optionality(server_node: ServerNode, client_node: Node):
    wire = ua.StructureField(
        Name="samples",
        DataType=ua.NodeId(ua.Int32(7)),
        ValueRank=1,
        ArrayDimensions=[3],
        IsOptional=True,
    )

    server_node.set_attr(AttributeIds.Value, sample(wire, ua.VariantType.ExtensionObject))

    field = client_node.value.value
    assert type(field) is StructureField
    assert field.array_dimensions == [3]
    assert field.is_optional is True


def test_structure_definition_decodes_optional_field(server_node: ServerNode, client_node: Node):
    field = ua.StructureField(
        Name="samples",
        DataType=ua.NodeId(ua.Int32(7)),
        ValueRank=1,
        ArrayDimensions=[3],
        IsOptional=True,
    )

    wire = ua.StructureDefinition(StructureType=ua.StructureType.StructureWithOptionalFields, Fields=[field])
    server_node.set_attr(AttributeIds.Value, sample(wire, ua.VariantType.ExtensionObject))

    structure = client_node.value.value
    assert type(structure) is StructureDefinition
    assert int(structure.structure_type) == 1
    assert len(structure.fields) == 1
    assert structure.fields[0].name == "samples"
    assert structure.fields[0].is_optional is True


def test_enum_field_decodes_name_and_value(server_node: ServerNode, client_node: Node):
    server_node.set_attr(AttributeIds.Value, sample(ua.EnumField(Value=-7, Name="Off"), ua.VariantType.ExtensionObject))

    field = client_node.value.value
    assert type(field) is EnumField
    assert field.name == "Off"
    assert field.value == -7


def test_enum_definition_decodes_fields_in_order(server_node: ServerNode, client_node: Node):
    wire = ua.EnumDefinition(
        Fields=[
            ua.EnumField(Value=-7, Name="Off"),
            ua.EnumField(Value=42, Name="On"),
        ]
    )

    server_node.set_attr(AttributeIds.Value, sample(wire, ua.VariantType.ExtensionObject))

    enumeration = client_node.value.value
    assert type(enumeration) is EnumDefinition
    assert len(enumeration.fields) == 2
    assert enumeration.fields[0].name == "Off"
    assert enumeration.fields[0].value == -7
    assert enumeration.fields[1].name == "On"
    assert enumeration.fields[1].value == 42


def test_data_type_definition_decodes_structure(server_node: ServerNode, client_node: Node):
    field = ua.StructureField(
        Name="samples",
        DataType=ua.NodeId(ua.Int32(7)),
        ValueRank=1,
        ArrayDimensions=[3],
        IsOptional=True,
    )

    body = ua.StructureDefinition(StructureType=ua.StructureType.StructureWithOptionalFields, Fields=[field])
    server_node.set_attr(AttributeIds.DataTypeDefinition, sample(body, ua.VariantType.ExtensionObject))

    definition = client_node.data_type_definition.value
    assert type(definition) is StructureDefinition
    assert definition.fields[0].name == "samples"
    assert definition.fields[0].is_optional is True


def test_data_type_definition_decodes_enumeration(server_node: ServerNode, client_node: Node):
    body = ua.EnumDefinition(
        Fields=[
            ua.EnumField(Value=-7, Name="Off"),
            ua.EnumField(Value=42, Name="On"),
        ]
    )

    server_node.set_attr(AttributeIds.DataTypeDefinition, sample(body, ua.VariantType.ExtensionObject))

    definition = client_node.data_type_definition.value
    assert type(definition) is EnumDefinition
    assert [(field.name, field.value) for field in definition.fields] == [("Off", -7), ("On", 42)]


def test_named_getters_apply_attribute_semantics(server_node: ServerNode, client_node: Node):
    server_node.set_attr(AttributeIds.NodeClass, sample(ua.Int32(2)))
    node_class = client_node.node_class.value
    assert type(node_class) is NodeClass
    assert node_class == NodeClass.VARIABLE

    server_node.set_attr(AttributeIds.AccessLevelEx, sample(ua.UInt32(513)))
    access = client_node.access_level_ex.value
    assert type(access) is AccessLevelExType
    assert int(access) == 513

    server_node.set_attr(AttributeIds.MinimumSamplingInterval, sample(ua.Double(250.5)))
    sampling_interval = client_node.minimum_sampling_interval.value
    assert sampling_interval == timedelta(microseconds=250500)


@pytest.mark.parametrize("selector", [AttributeId.NODE_CLASS, 2, "NodeClass", "node_class"])
def test_attribute_selectors_use_semantic_conversion(client_node: Node, selector: AttributeId | int | str):
    value = client_node.get_attribute(selector)
    assert value.attribute_id == AttributeId.NODE_CLASS
    assert value.value == NodeClass.VARIABLE


@pytest.mark.parametrize("payload", [None, ua.Variant(None, ua.VariantType.Null)])
def test_absent_and_explicit_null_payloads_default_to_none_and_good(server_node: ServerNode, client_node: Node, payload: ua.Variant | None):
    server_node.set_attr(AttributeIds.Value, sample(payload))

    result = client_node.value

    assert result.value is None
    assert result.status.code == StatusCodes.Good


@pytest.mark.parametrize("status", [StatusCodes.BadTimeout, StatusCodes.Uncertain])
def test_bad_or_uncertain_quality_retains_payload(client_node: Node, server_node: ServerNode, status: int):
    server_node.set_attr(AttributeIds.Value, sample(ua.UInt32(17), status=status))

    result = client_node.value
    assert result.status.code == status
    assert result.value == 17


def test_source_and_server_timestamps_remain_distinct(server_node: ServerNode, client_node: Node):
    source = ua.DateTime(1970, 1, 1, 0, 0, 5, 125000, tzinfo=timezone.utc)
    server = ua.DateTime(1970, 1, 1, 0, 0, 9, 250000, tzinfo=timezone.utc)

    server_node.set_attr(AttributeIds.Value, sample(ua.UInt32(4), source=source, server=server))

    result = client_node.value
    assert (src := result.source_timestamp) is not None and src.unix_timestamp_nanos() == 5125000000
    assert (srv := result.server_timestamp) is not None and srv.unix_timestamp_nanos() == 9250000000
    assert result.source_unix_timestamp == 5125000000
    assert result.server_unix_timestamp == 9250000000


@pytest.mark.parametrize(
    ("stamp_kind", "fraction", "expected"), [("source", 1234, 5000000012), ("server", 5678, 5000000056)]
)
def test_single_clock_fraction_does_not_fill_absent_clock(
    client_node: Node, server_node: ServerNode, stamp_kind: str, fraction: int, expected: int
):
    timestamp = ua.DateTime(1970, 1, 1, 0, 0, 5, tzinfo=timezone.utc)

    wire = sample(
        ua.Variant(4, ua.VariantType.Int32), **{stamp_kind: timestamp, f"{stamp_kind}_picos": fraction} # type: ignore[arg-type]
    )

    server_node.set_attr(AttributeIds.Value, wire)

    result = client_node.value

    assert getattr(result, f"{stamp_kind}_unix_timestamp") == expected
    assert getattr(result, f"{stamp_kind}_picoseconds") == fraction

    absent = "server" if stamp_kind == "source" else "source"

    assert getattr(result, f"{absent}_timestamp") is None
    assert getattr(result, f"{absent}_unix_timestamp") is None
    assert getattr(result, f"{absent}_picoseconds") is None


def test_incompatible_fixed_payload_defers_error_and_keeps_metadata(server_node: ServerNode, client_node: Node):
    timestamp = ua.DateTime(1970, 1, 1, 0, 0, 5, tzinfo=timezone.utc)

    wire = sample(
        ua.Variant(1.25, ua.VariantType.Double), status=StatusCodes.Uncertain, source=timestamp, source_picos=1234
    )

    server_node.set_attr(AttributeIds.DisplayName, wire)

    result = client_node.display_name

    assert result.attribute_id == AttributeId.DISPLAY_NAME
    assert result.status.code == StatusCodes.Uncertain
    assert result.source_unix_timestamp == 5000000012

    with pytest.raises(OpcUaError) as failure:
        _ = result.value

    assert failure.value.status_code.code == StatusCodes.BadTypeMismatch


def test_unsupported_generic_payload_defers_not_supported(server_node: ServerNode, client_node: Node):
    nested = sample(7, ua.VariantType.Int32)
    server_node.set_attr(AttributeIds.Value, sample(ua.Variant(nested, ua.VariantType.DataValue)))

    result = client_node.value

    with pytest.raises(OpcUaError) as failure:
        _ = result.value

    assert failure.value.status_code.code == StatusCodes.BadNotSupported
