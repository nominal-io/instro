import asyncio
from threading import Thread

import pytest

from instro.unstable.opcua import OpcUaClient

from .server import start_server


@pytest.fixture(scope="session")
def run():
    with asyncio.Runner() as runtime:
        loop = runtime.get_loop()
        thread = Thread(target=loop.run_forever)
        thread.start()

        def execute(coroutine):
            return asyncio.run_coroutine_threadsafe(coroutine, loop).result(timeout=5)

        yield execute
        loop.call_soon_threadsafe(loop.stop)
        thread.join()


@pytest.fixture(scope="session")
def server(run):
    peer = run(start_server())
    yield peer
    run(peer.stop())
    peer.iserver.aspace.clear()


@pytest.fixture(scope="session")
def endpoint(server):
    return f"opc.tcp://127.0.0.1:{server.bserver.port}/instro-tests/"


@pytest.fixture(scope="session")
def client(endpoint):
    with OpcUaClient.connect(endpoint) as connected:
        yield connected
