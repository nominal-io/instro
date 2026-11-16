import asyncio
import signal

import pytest
from asyncua import ua
from asyncua.server.user_managers import User, UserRole

from instro.unstable.opcua import IdentityToken, OpcUaClient, OpcUaError, get_endpoints
from instro.unstable.opcua.types import NodeId


def test_client_context_disconnects_network_but_retains_local_node_metadata(endpoint, connection_options):
    with OpcUaClient.connect(endpoint, **connection_options) as client:
        node = client.get_node("/Objects/2:Rig/2:Temperature")
        assert node.value.value == 23.5

    assert node.node_id == NodeId("ns=2;s=Temperature")
    assert node.browse_path.starting_node == NodeId("i=84")
    with pytest.raises(OpcUaError) as failure:
        _ = node.value

    assert failure.value.status_code.code == 0x800D0000


def test_node_retains_client_after_original_reference_is_deleted(endpoint, connection_options):
    client = OpcUaClient.connect(endpoint, **connection_options)
    try:
        node = client.get_node("/Objects/2:Rig/2:Temperature")
    finally:
        del client

    try:
        assert node.value.value == 23.5
    finally:
        del node


def test_discovery_returns_advertised_security_modes(endpoint):
    endpoints = get_endpoints(endpoint)
    assert {(item.security_policy, int(item.security_mode)) for item in endpoints} == {
        ("http://opcfoundation.org/UA/SecurityPolicy#None", 1),
        ("http://opcfoundation.org/UA/SecurityPolicy#Basic256Sha256", 2),
        ("http://opcfoundation.org/UA/SecurityPolicy#Basic256Sha256", 3),
    }


@pytest.mark.parametrize(
    ("mode", "wire_mode"),
    [("Sign", ua.MessageSecurityMode.Sign), ("SignAndEncrypt", ua.MessageSecurityMode.SignAndEncrypt)],
)
def test_username_authentication_reads_over_selected_secure_mode(
    endpoint, connection_options, server, monkeypatch, mode, wire_mode
):
    def operator_only(iserver, username=None, password=None, certificate=None):
        if (username, password) == ("operator", "secret"):
            return User(role=UserRole.User, name="operator")
        return None

    endpoints = [item for item in server.iserver.endpoints if item.SecurityMode == wire_mode]
    monkeypatch.setattr(server.iserver, "endpoints", endpoints)
    monkeypatch.setattr(server.iserver.user_manager, "get_user", operator_only)
    options = connection_options | {
        "identity": IdentityToken.user_name("operator", "secret"),
        "security_policy": "Basic256Sha256",
        "security_mode": mode,
    }

    with OpcUaClient.connect(endpoint, **options) as client:
        assert client.get_node("/Objects/2:Rig/2:Temperature").value.value == 23.5


@pytest.mark.parametrize("operation", ["discovery", "connect"])
def test_interrupted_handshake_closes_transport(run, connection_options, operation):
    closed = asyncio.Event()

    async def blackhole(reader, writer):
        try:
            if await reader.read(1):
                signal.raise_signal(signal.SIGINT)
            await reader.read()
        finally:
            writer.close()
            await writer.wait_closed()
            closed.set()

    listener = run(asyncio.start_server(blackhole, "127.0.0.1", 0))
    port = listener.sockets[0].getsockname()[1]
    url = f"opc.tcp://127.0.0.1:{port}"
    try:
        with pytest.raises(KeyboardInterrupt):
            if operation == "discovery":
                get_endpoints(url)
            else:
                with OpcUaClient.connect(url, **connection_options):
                    pytest.fail("blackhole accepted a session")

        run(asyncio.wait_for(closed.wait(), timeout=2))
    finally:

        async def close_listener():
            listener.close()
            await listener.wait_closed()

        run(close_listener())
