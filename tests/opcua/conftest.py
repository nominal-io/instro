from collections.abc import Iterator
from contextlib import suppress
from multiprocessing import get_context

import pytest

from instro.unstable.opcua import OpcUaClient
from tests.opcua.server import OpcUaTestServer, run_server


@pytest.fixture(scope="session")
def opcua_server() -> Iterator[OpcUaTestServer]:
    context = get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=run_server, args=(child,), daemon=True)
    process.start()
    child.close()

    try:
        if not parent.poll(15):
            pytest.fail("OPC UA test server did not start within 15 seconds")

        yield OpcUaTestServer(parent.recv(), parent)

    finally:
        if process.is_alive():
            with suppress(BrokenPipeError, EOFError):
                parent.send(None)

        process.join(timeout=5)

        if process.is_alive():
            process.terminate()
            process.join(timeout=5)

        parent.close()


@pytest.fixture(scope="session")
def endpoint(opcua_server: OpcUaTestServer) -> str:
    return opcua_server.endpoint


@pytest.fixture(scope="session")
def client(endpoint: str) -> Iterator[OpcUaClient]:
    with OpcUaClient.connect(
        endpoint,
        security_mode="none",
        security_policy="none",
        request_timeout=2.0,
    ) as client:
        yield client
