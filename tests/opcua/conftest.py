from collections.abc import Iterator
from contextlib import contextmanager, suppress
from multiprocessing import get_context

import pytest

from instro.unstable.opcua import OpcUaClient
from tests.opcua.server import OpcUaTestServer, run_server


@contextmanager
def start_server(secure: bool = False) -> Iterator[OpcUaTestServer]:
    context = get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=run_server, args=(child, secure), daemon=True)
    process.start()
    child.close()
    try:
        if not parent.poll(15):
            pytest.fail("OPC UA test server did not start within 15 seconds")
        server = OpcUaTestServer(parent.recv(), parent)
        yield server
    finally:
        if process.is_alive():
            with suppress(BrokenPipeError, EOFError):
                parent.send(None)
        process.join(timeout=5)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        parent.close()


@pytest.fixture(scope="module")
def opcua_server() -> Iterator[OpcUaTestServer]:
    with start_server() as server:
        yield server


@pytest.fixture(scope="module")
def secure_server() -> Iterator[OpcUaTestServer]:
    with start_server(secure=True) as server:
        yield server


@pytest.fixture
def endpoint(opcua_server: OpcUaTestServer) -> str:
    return opcua_server.endpoint


@pytest.fixture
def client(endpoint: str) -> Iterator[OpcUaClient]:
    with OpcUaClient.connect(endpoint, security_mode="none", security_policy="none", timeout=2.0) as client:
        yield client
