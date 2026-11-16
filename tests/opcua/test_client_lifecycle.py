from typing_extensions import TypeAlias

import asyncio
import signal

import pytest

from instro.unstable.opcua import OpcUaClient, get_endpoints

Endpoint: TypeAlias = tuple[str, int]

ENDPOINTS: set[Endpoint] = {
    ("http://opcfoundation.org/UA/SecurityPolicy#None", 1),
    ("http://opcfoundation.org/UA/SecurityPolicy#Basic256Sha256", 2),
    ("http://opcfoundation.org/UA/SecurityPolicy#Basic256Sha256", 3),
}


def test_discovery_returns_advertised_security_modes(endpoint: str):
    endpoints = get_endpoints(endpoint)
    assert {(item.security_policy, int(item.security_mode)) for item in endpoints} == ENDPOINTS


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
