from instro.unstable.opcua import IdentityToken, Node, OpcUaClient
from instro.unstable.opcua.types import AttributeValue, NodeClass, NodeId, RolePermissionType


def unsupported_inputs(client: OpcUaClient, node: Node) -> None:
    OpcUaClient.connect("opc.tcp://localhost:4840", username="operator")
    OpcUaClient.connect("opc.tcp://localhost:4840", session_timeout="60000")
    client.get_node(NodeId("i=85"))
    client.subscribe(["/Objects"], poll_frequency=1.0)
    classification: NodeClass | None = node.value.value


def unavailable_constructors() -> None:
    IdentityToken()
    Node()
    AttributeValue[int]()
    RolePermissionType()
