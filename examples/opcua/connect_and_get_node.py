"""Connect to an OPC UA server and resolve one node."""

from instro.unstable.opcua import OpcUaClient

ENDPOINT_URL = "opc.tcp://127.0.0.1:4840"
BROWSE_PATH = "/Objects/Server/ServerStatus/CurrentTime"


with OpcUaClient.connect(ENDPOINT_URL) as client:
    node = client.get_node(BROWSE_PATH)

    print(f"connected: {client.endpoint_url}")
    print(f"browse path: {node.browse_path}")
    print(f"node ID: {node.node_id}")
    print(f"display name: {node.display_name}")
