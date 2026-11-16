import warnings

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


def test_batch_read_preserves_mixed_input_order(client: OpcUaClient):
    boolean = client.objects.get_child("/2:Rig/2:Boolean")
    inputs: list[Node | RelativePath | str] = [
        boolean,
        RelativePath("/Objects/2:Rig/2:Float"),
        "/Objects/2:Rig/2:Temperature",
    ]
    results = client.read_nodes(inputs)
    assert [result.value for result in results] == [True, 1.25, 23.5]


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
