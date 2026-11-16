import warnings

import pytest
from asyncua import ua

from instro.unstable.opcua import OpcUaError
from instro.unstable.opcua.types import NodeId, RelativePath


def test_nested_navigation_retains_rooted_path(client):
    rig = client.get_node("/Objects/2:Rig")
    temperature = rig.get_child(RelativePath("/2:Temperature"))
    assert temperature.node_id == NodeId("ns=2;s=Temperature")
    assert temperature.value.value == 23.5
    assert temperature.browse_path.starting_node == NodeId("i=84")
    assert [
        (part.target_name.namespace_index, part.target_name.name)
        for part in temperature.browse_path.relative_path.elements
    ] == [
        (0, "Objects"),
        (2, "Rig"),
        (2, "Temperature"),
    ]


def test_children_are_usable_nodes(client):
    children = client.objects.get_child("/2:Rig").children
    assert {child.node_id.identifier: child.value.value for child in children} == {
        "Temperature": 23.5,
        "Float": 1.25,
        "Boolean": True,
    }


def test_batch_read_preserves_mixed_input_order(client):
    boolean = client.objects.get_child("/2:Rig/2:Boolean")
    inputs = [boolean, RelativePath("/Objects/2:Rig/2:Float"), "/Objects/2:Rig/2:Temperature"]
    results = client.read_nodes(inputs)
    assert [result.value for result in results] == [True, 1.25, 23.5]


@pytest.mark.parametrize("path", ["", "ns=2;s=Temperature"])
def test_child_requires_a_nonempty_browse_path(node, path):
    with pytest.raises(ValueError):
        node.get_child(path)


@pytest.fixture
def remote_references(server, monkeypatch):
    targets = [("remote-a", "RemoteA", 1), ("remote-b", "RemoteB", 2)]
    references = [
        ua.ReferenceDescription(
            ReferenceTypeId=ua.NodeId(35),
            IsForward=True,
            NodeId=ua.ExpandedNodeId(identifier, 2, ServerIndex=index),
            BrowseName=ua.QualifiedName(name, 2),
            DisplayName=ua.LocalizedText("Remote child"),
            NodeClass=ua.NodeClass.Variable,
            TypeDefinition=ua.ExpandedNodeId(63),
        )
        for identifier, name, index in targets
    ]

    def respond(status=ua.StatusCodes.Good):
        def browse(params):
            return [ua.BrowseResult(StatusCode=ua.StatusCode(status), ContinuationPoint=None, References=references)]

        monkeypatch.setattr(server.iserver.view_service, "browse", browse)

    return respond


def test_remote_children_emit_one_aggregated_warning(node, remote_references):
    remote_references()
    with pytest.warns(RuntimeWarning) as recorded:
        assert node.children == []

    assert len(recorded) == 1
    assert "remote-a" in str(recorded[0].message)
    assert "remote-b" in str(recorded[0].message)


def test_remote_child_warning_respects_error_filter(node, remote_references):
    remote_references()
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        with pytest.raises(RuntimeWarning):
            _ = node.children


def test_browse_failure_stays_primary_when_warning_raises(node, remote_references):
    remote_references(ua.StatusCodes.BadTimeout)
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        with pytest.raises(OpcUaError) as failure:
            _ = node.children

    assert failure.value.status_code.code == 0x800A0000
    assert isinstance(failure.value.__context__, RuntimeWarning)
