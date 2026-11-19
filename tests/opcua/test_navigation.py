import warnings
from unittest.mock import Mock

import pytest
from asyncua import ua
from asyncua.server.server import Server
from asyncua.ua.object_ids import ObjectIds
from asyncua.ua.status_codes import StatusCodes
from typing_extensions import Callable

from instro.unstable.opcua import Node, OpcUaClient, OpcUaError
from instro.unstable.opcua.types import NodeId, RelativePath


def test_nested_navigation_retains_rooted_path(client: OpcUaClient):
    rig = client.get_node("/Objects/2:Rig")
    node = rig.get_child(RelativePath("/2:Temperature"))
    assert node.node_id == NodeId("ns=2;s=Temperature")
    assert node.value.value == 23.5
    assert node.browse_path.starting_node == NodeId("i=84")
    for child in node.children:
        assert child.browse_path.starting_node == NodeId("i=84")

        assert (qname := child.browse_name.value) is not None

        assert [
            (part.target_name.namespace_index, part.target_name.name)
            for part in child.browse_path.relative_path.elements
        ] == [
            (0, "Objects"),
            (2, "Rig"),
            (2, "Temperature"),
            (qname.namespace_index, qname.name),
        ]


def test_children_are_usable_nodes(client: OpcUaClient):
    children = client.objects.get_child("/2:Rig").children
    assert {child.node_id.identifier: child.value.value for child in children} == {
        "Temperature": 23.5,
        "Float": 1.25,
        "Boolean": True,
    }


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
    nodes = client.translate(paths)

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
    [(True, TypeError), (-1, ValueError), (1 << 32, ValueError)],
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
