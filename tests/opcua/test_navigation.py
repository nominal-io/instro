import warnings
from contextlib import suppress
from pathlib import Path
from unittest.mock import Mock

import pytest
from asyncua import ua
from asyncua.common.utils import ServiceError
from asyncua.server.server import Server
from asyncua.ua.object_ids import ObjectIds
from asyncua.ua.status_codes import StatusCodes
from typing_extensions import Callable

from instro.unstable.opcua import Node, OpcUaClient, OpcUaError
from instro.unstable.opcua.types import NodeId, RelativePath
from tests.opcua.conftest import AsyncRunner


def test_nested_navigation_retains_rooted_path(client: OpcUaClient):
    rig = client.get_node("/Objects/2:Rig")
    node = rig.get_child(RelativePath("/2:Temperature"))
    assert node.node_id == NodeId("ns=2;s=Temperature")
    assert node.value.value == 23.5
    assert node.browse_path.starting_node == NodeId("i=84")

    temperature = next(child for child in rig.children if child.node_id == node.node_id)
    assert temperature.browse_path.starting_node == NodeId("i=84")
    assert temperature.browse_path.relative_path == RelativePath("/Objects/2:Rig/2:Temperature")


def test_children_are_usable_nodes(client: OpcUaClient):
    children = client.objects.get_child("/2:Rig").children
    assert {child.node_id.identifier: child.value.value for child in children} == {
        "Temperature": 23.5,
        "Float": 1.25,
        "Boolean": True,
    }


def test_parallel_references_resolve_and_list_one_child(client: OpcUaClient, server: Server, run: AsyncRunner):
    rig = client.get_node("/Objects/2:Rig")
    parent = server.get_node("ns=2;s=Rig")
    temperature = server.get_node("ns=2;s=Temperature")
    try:
        run(parent.add_reference(temperature, ObjectIds.Organizes))
        assert rig.get_child("/2:Temperature").node_id == NodeId("ns=2;s=Temperature")
        assert [child.node_id.identifier for child in rig.children] == ["Temperature", "Float", "Boolean"]
    finally:
        with suppress(ua.uaerrors.BadNotFound):
            run(parent.delete_reference(temperature, ObjectIds.Organizes))


@pytest.mark.parametrize(
    ("advertised", "requested"),
    [
        (3, 3),
        (50, 5),
        (0, 5),
        (None, 5),
        pytest.param(ua.StatusCode(ua.UInt32(StatusCodes.BadUserAccessDenied)), 5, id="access-denied"),
        pytest.param(ua.StatusCode(ua.UInt32(StatusCodes.BadServiceUnsupported)), 5, id="unsupported-service"),
    ],
)
def test_children_reads_server_hint_before_bounded_browse(
    endpoint: str,
    tmp_path: Path,
    server: Server,
    monkeypatch: pytest.MonkeyPatch,
    advertised: int | ua.StatusCode | None,
    requested: int,
):
    view = server.iserver.view_service
    browse = view.browse
    attribute_service = server.iserver.attribute_service
    read = attribute_service.read
    capability_node = ua.NodeId(ua.Int32(ObjectIds.Server_ServerCapabilities_MaxArrayLength))
    requests: list[ua.ReadParameters | ua.BrowseParameters] = []

    def read_capability(params: ua.ReadParameters) -> list[ua.DataValue]:
        if not any(item.NodeId == capability_node for item in params.NodesToRead):
            return read(params)

        requests.append(params)

        if isinstance(advertised, ua.StatusCode):
            raise ServiceError(ua.UInt32(advertised.value))

        if advertised is None:
            return [ua.DataValue(None, ua.StatusCode(ua.UInt32(StatusCodes.BadNodeIdUnknown)))]

        return [ua.DataValue(ua.Variant(ua.UInt32(advertised), ua.VariantType.UInt32))]

    def record_browse(params: ua.BrowseParameters) -> list[ua.BrowseResult]:
        requests.append(params)
        return browse(params)

    with OpcUaClient.connect(endpoint, pki_dir=tmp_path, max_array_length=5) as client:
        rig = client.get_node("/Objects/2:Rig")
        monkeypatch.setattr(attribute_service, "read", read_capability)
        monkeypatch.setattr(view, "browse", record_browse)

        assert {child.node_id.identifier for child in rig.children} == {"Temperature", "Float", "Boolean"}

    read_request, browse_request = requests
    assert isinstance(read_request, ua.ReadParameters)
    assert [(item.NodeId, item.AttributeId) for item in read_request.NodesToRead] == [
        (capability_node, ua.AttributeIds.Value)
    ]
    assert isinstance(browse_request, ua.BrowseParameters)
    assert browse_request.RequestedMaxReferencesPerNode == requested


def test_batch_read_translates_paths_together_and_preserves_order(
    client: OpcUaClient, server: Server, monkeypatch: pytest.MonkeyPatch
):
    boolean = client.objects.get_child("/2:Rig/2:Boolean")
    view = server.iserver.view_service
    translate = Mock(wraps=view.translate_browsepaths_to_nodeids)
    monkeypatch.setattr(view, "translate_browsepaths_to_nodeids", translate)

    inputs: list[Node | RelativePath | str] = [
        RelativePath("/Objects/2:Rig/2:Float"),
        boolean,
        "/Objects/2:Rig/2:Temperature",
    ]
    results = client.read_nodes(inputs)

    assert [len(call.args[0]) for call in translate.call_args_list] == [2]
    assert [result.value for result in results] == [1.25, True, 23.5]


def test_empty_batch_read_returns_no_results(client: OpcUaClient):
    assert client.read_nodes([]) == []


def test_translate_returns_rooted_nodes_in_input_order(client: OpcUaClient):
    paths = (path for path in [RelativePath("/Objects/2:Rig/2:Float"), "/Objects/2:Rig/2:Temperature"])
    nodes = client._translate(paths)

    assert [node.node_id for node in nodes] == [NodeId("ns=2;s=Float"), NodeId("ns=2;s=Temperature")]
    assert [node.browse_path.starting_node for node in nodes] == [NodeId("i=84"), NodeId("i=84")]


def test_batch_read_rejects_missing_translation_result(
    client: OpcUaClient, server: Server, monkeypatch: pytest.MonkeyPatch
):
    view = server.iserver.view_service
    translate = view.translate_browsepaths_to_nodeids
    monkeypatch.setattr(view, "translate_browsepaths_to_nodeids", lambda paths: translate(paths)[:1])

    with pytest.raises(OpcUaError) as failure:
        client.read_nodes(["/Objects/2:Rig/2:Float", "/Objects/2:Rig/2:Temperature"])

    assert failure.value.status_code.code == StatusCodes.BadUnexpectedError


@pytest.mark.parametrize(
    ("attr", "error_type"),
    [(True, TypeError), (-1, OverflowError), (1 << 32, OverflowError), (0, ValueError)],
)
def test_attribute_rejects_invalid_integers(client_node: Node, attr: int, error_type: type[Exception]):
    with pytest.raises(error_type):
        client_node.get_attribute(attr)


@pytest.mark.parametrize("path", ["", "ns=2;s=Temperature"])
def test_child_requires_valid_browse_path(client_node: Node, path: str):
    with pytest.raises(ValueError):
        client_node.get_child(path)


@pytest.fixture
def set_remote_references(server: Server, monkeypatch: pytest.MonkeyPatch) -> Callable[[int], None]:
    targets = [("remote-a", "RemoteA", 1), ("remote-b", "RemoteB", 2)]

    references = [
        ua.ReferenceDescription(
            ReferenceTypeId=ua.NodeId(ua.Int32(ObjectIds.HierarchicalReferences)),
            IsForward=True,
            NodeId=ua.ExpandedNodeId(ua.String(identifier), ua.Int16(2), ServerIndex=ua.Int32(index)),
            BrowseName=ua.QualifiedName(name, 2),
            DisplayName=ua.LocalizedText("Remote child"),
            NodeClass=ua.NodeClass.Variable,
            TypeDefinition=ua.ExpandedNodeId(ua.Int32(ObjectIds.BaseDataVariableType)),
        )
        for identifier, name, index in targets
    ]

    def respond(status: int = StatusCodes.Good):
        def browse(_params: ua.BrowseParameters) -> list[ua.BrowseResult]:
            return [ua.BrowseResult(ua.StatusCode(ua.UInt32(status)), None, references)]

        monkeypatch.setattr(server.iserver.view_service, "browse", browse)

    return respond


def test_remote_children_emit_one_aggregated_warning(client_node: Node, set_remote_references: Callable[..., None]):
    set_remote_references()

    with pytest.warns(RuntimeWarning) as recorded:
        assert client_node.children == []

    assert len(recorded) == 1
    assert "remote-a" in str(recorded[0].message)
    assert "remote-b" in str(recorded[0].message)


def test_remote_child_warning_respects_error_filter(client_node: Node, set_remote_references: Callable[..., None]):
    set_remote_references()

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)

        with pytest.raises(RuntimeWarning):
            _ = client_node.children


def test_browse_failure_stays_primary_when_warning_raises(
    client_node: Node, set_remote_references: Callable[[int], None]
):
    set_remote_references(StatusCodes.BadTimeout)

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)

        with pytest.raises(OpcUaError) as failure:
            _ = client_node.children

    assert failure.value.status_code.code == StatusCodes.BadTimeout
    assert isinstance(failure.value.__context__, RuntimeWarning)
