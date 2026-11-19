from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from asyncua import ua
from asyncua.server.server import Server
from asyncua.ua.attribute_ids import AttributeIds
from asyncua.ua.status_codes import StatusCodes

from instro.unstable.opcua import Node, OpcUaError
from instro.unstable.opcua.types import (
    AttributeId,
    DataType,
    ExpandedNodeId,
    LocalizedText,
    NodeId,
    QualifiedName,
    StatusCode,
    XmlElement,
)
from tests.opcua.conftest import ServerNode

TEMPERATURE = ua.NodeId(ua.String("Temperature"), ua.Int16(2))


@pytest.fixture
def requests(server: Server, monkeypatch: pytest.MonkeyPatch) -> tuple[Mock, AsyncMock]:
    service = server.iserver.attribute_service
    read = Mock(wraps=service.read)
    write = AsyncMock(return_value=[ua.StatusCode()])
    monkeypatch.setattr(service, "read", read)
    monkeypatch.setattr(service, "write", write)
    return read, write


def target_reads(read: Mock) -> list[int]:
    return [
        item.AttributeId
        for call in read.call_args_list
        for item in call.args[0].NodesToRead
        if item.NodeId == TEMPERATURE
    ]


def single_write(write: AsyncMock, attribute: AttributeIds) -> ua.Variant:
    write.assert_awaited_once()
    assert write.await_args is not None
    [request] = write.await_args.args[0].NodesToWrite
    assert request.NodeId == TEMPERATURE
    assert request.AttributeId == attribute
    assert request.Value.Value is not None
    return request.Value.Value


@pytest.mark.parametrize(
    ("value", "dtype", "expected"),
    [
        (-42, DataType.INT16, ua.Variant(-42, ua.VariantType.Int16)),
        ([-1, 2], DataType.SBYTE, ua.Variant([-1, 2], ua.VariantType.SByte)),
        ([], DataType.UINT16, ua.Variant([], ua.VariantType.UInt16)),
        (
            -1_234_000,
            DataType.DATE_TIME,
            ua.Variant(datetime(1969, 12, 31, 23, 59, 59, 998766, timezone.utc), ua.VariantType.DateTime),
        ),
        (
            UUID("12345678-1234-5678-9abc-def012345678"),
            DataType.GUID,
            ua.Variant(UUID("12345678-1234-5678-9abc-def012345678"), ua.VariantType.Guid),
        ),
        (
            XmlElement("<x/>"),
            DataType.XML_ELEMENT,
            ua.Variant(ua.XmlElement(ua.String("<x/>")), ua.VariantType.XmlElement),
        ),
        (
            NodeId("ns=2;s=target"),
            DataType.NODE_ID,
            ua.Variant(ua.NodeId(ua.String("target"), ua.Int16(2)), ua.VariantType.NodeId),
        ),
        (
            StatusCode(StatusCodes.BadTimeout),
            DataType.STATUS_CODE,
            ua.Variant(ua.StatusCode(ua.UInt32(StatusCodes.BadTimeout)), ua.VariantType.StatusCode),
        ),
        (
            [QualifiedName(2, "target")],
            DataType.QUALIFIED_NAME,
            ua.Variant([ua.QualifiedName("target", 2)], ua.VariantType.QualifiedName),
        ),
    ],
)
def test_explicit_value_write_sends_one_write_without_a_read(
    client_node: Node,
    requests: tuple[Mock, AsyncMock],
    value: object,
    dtype: DataType,
    expected: ua.Variant,
):
    read, write = requests

    client_node.set_attribute("value", value, dtype=dtype)

    assert target_reads(read) == []
    assert single_write(write, AttributeIds.Value) == expected


def test_expanded_node_id_write_preserves_namespace_uri_and_server_index(
    client_node: Node, server_node: ServerNode, requests: tuple[Mock, AsyncMock]
):
    read, write = requests
    remote = ua.ExpandedNodeId(ua.Int32(42), NamespaceUri=ua.String("urn:example"), ServerIndex=ua.Int32(3))
    server_node.set_attr(AttributeIds.Value, ua.DataValue(ua.Variant(remote, ua.VariantType.ExpandedNodeId)))
    value = client_node.value.value
    assert isinstance(value, ExpandedNodeId)
    read.reset_mock()

    client_node.set_attribute(AttributeId.VALUE, value, dtype=DataType.EXPANDED_NODE_ID)

    assert target_reads(read) == []
    variant = single_write(write, AttributeIds.Value)
    assert variant.VariantType == ua.VariantType.ExpandedNodeId
    assert isinstance(sent := variant.Value, ua.ExpandedNodeId)
    assert (sent.Identifier, sent.NamespaceUri, sent.ServerIndex) == (42, "urn:example", 3)


def test_xml_array_round_trips_as_xml_elements(
    client_node: Node, server_node: ServerNode, requests: tuple[Mock, AsyncMock]
):
    read, write = requests
    wire = ua.Variant([ua.XmlElement(ua.String("<mode>on</mode>"))], ua.VariantType.XmlElement)
    server_node.set_attr(AttributeIds.Value, ua.DataValue(wire))

    values = client_node.value.value
    assert isinstance(values, list)
    [element] = values
    assert isinstance(element, XmlElement)
    assert str(element) == "<mode>on</mode>"
    read.reset_mock()

    client_node.set_attribute(AttributeId.VALUE, values, dtype=DataType.XML_ELEMENT)

    assert target_reads(read) == []
    assert single_write(write, AttributeIds.Value) == wire


def test_xml_write_requires_an_xml_element(client_node: Node, requests: tuple[Mock, AsyncMock]):
    read, write = requests

    with pytest.raises(TypeError):
        client_node.set_attribute(AttributeId.VALUE, "<x/>", dtype=DataType.XML_ELEMENT)

    assert target_reads(read) == []
    write.assert_not_awaited()


def test_value_assignment_uses_the_declared_wire_type(
    client_node: Node, server_node: ServerNode, requests: tuple[Mock, AsyncMock]
):
    read, write = requests
    server_node.set_attr(AttributeIds.DataType, ua.DataValue(ua.Variant(ua.NodeId(ua.Int32(5)), ua.VariantType.NodeId)))

    client_node.value = 42

    assert target_reads(read) == [AttributeIds.DataType]
    assert single_write(write, AttributeIds.Value) == ua.Variant(42, ua.VariantType.UInt16)


@pytest.mark.parametrize("explicit", [False, True])
def test_display_name_writes_localized_text_without_a_read(
    client_node: Node, requests: tuple[Mock, AsyncMock], explicit: bool
):
    read, write = requests
    name = LocalizedText("Pump", "en-US")

    if explicit:
        client_node.set_attribute(AttributeId.DISPLAY_NAME, name, dtype=DataType.LOCALIZED_TEXT)
    else:
        client_node.display_name = name

    assert target_reads(read) == []
    expected = ua.Variant(ua.LocalizedText("Pump", "en-US"), ua.VariantType.LocalizedText)
    assert single_write(write, AttributeIds.DisplayName) == expected


def test_conflicting_display_name_type_sends_no_request(client_node: Node, requests: tuple[Mock, AsyncMock]):
    read, write = requests

    with pytest.raises(ValueError, match="LOCALIZED_TEXT"):
        client_node.set_attribute(AttributeId.DISPLAY_NAME, LocalizedText("Pump"), dtype=DataType.STRING)

    assert target_reads(read) == []
    write.assert_not_awaited()


@pytest.mark.parametrize(
    ("value", "dtype"),
    [(65_536, DataType.UINT16), (1, DataType.DATE_TIME), (10**24, DataType.DATE_TIME)],
)
def test_unrepresentable_value_sends_no_write(
    client_node: Node, requests: tuple[Mock, AsyncMock], value: int, dtype: DataType
):
    read, write = requests

    with pytest.raises(ValueError):
        client_node.set_attribute(AttributeId.VALUE, value, dtype=dtype)

    assert target_reads(read) == []
    write.assert_not_awaited()


def test_server_write_rejection_preserves_status(client_node: Node, requests: tuple[Mock, AsyncMock]):
    read, write = requests
    write.return_value = [ua.StatusCode(ua.UInt32(StatusCodes.BadNotWritable))]

    with pytest.raises(OpcUaError) as failure:
        client_node.set_attribute(13, 42, dtype=DataType.INT16)

    assert failure.value.status_code.code == StatusCodes.BadNotWritable
    assert target_reads(read) == []
    write.assert_awaited_once()
