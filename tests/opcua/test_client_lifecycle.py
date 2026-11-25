import asyncio
import signal
import sys
from asyncio.streams import StreamReader, StreamWriter
from pathlib import Path

import pytest
from typing_extensions import TypeAlias

from instro.unstable.opcua import OpcUaClient, get_endpoints
from tests.opcua.conftest import AsyncRunner, DefaultConnectionOptions

Endpoint: TypeAlias = tuple[str, int]

ENDPOINTS: set[Endpoint] = {
    ("http://opcfoundation.org/UA/SecurityPolicy#None", 1),
    ("http://opcfoundation.org/UA/SecurityPolicy#Basic256Sha256", 2),
    ("http://opcfoundation.org/UA/SecurityPolicy#Basic256Sha256", 3),
}


def test_discovery_returns_advertised_security_modes(endpoint: str, tmp_path: Path):
    store = tmp_path / "certificates"
    endpoints = get_endpoints(endpoint, pki_dir=store)
    assert {(item.security_policy, int(item.security_mode)) for item in endpoints} == ENDPOINTS
    assert store.is_dir()


@pytest.mark.parametrize("operation", ["discovery", "connect"])
def test_default_certificate_store_uses_local_data_directory(
    endpoint: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path))

    if operation == "discovery":
        get_endpoints(endpoint)
    else:
        OpcUaClient.connect(endpoint).disconnect()

    assert not (cwd / "pki").exists()
    if sys.platform == "linux":
        assert (tmp_path / "data/instro/opcua").is_dir()
    elif sys.platform == "darwin":
        assert (tmp_path / "Library/Application Support/instro/opcua").is_dir()


@pytest.mark.parametrize("operation", ["discovery", "connect"])
def test_interrupted_handshake_closes_transport(
    run: AsyncRunner, connection_options: DefaultConnectionOptions, operation: str
):
    closed = asyncio.Event()

    async def blackhole(reader: StreamReader, writer: StreamWriter):
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
