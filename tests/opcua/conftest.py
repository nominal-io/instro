import asyncio
from threading import Thread

import pytest
from asyncua.server.server import Server
from typing_extensions import Any, Coroutine, Generator, Protocol, TypeVar

from instro.unstable.opcua import OpcUaClient

from .server import start_server

T = TypeVar("T")


class _Run(Protocol):
    def __call__(self, coroutine: Coroutine[Any, Any, T]) -> T: ...


@pytest.fixture(scope="session")
def run() -> Generator[_Run, None, None]:
    loop = asyncio.new_event_loop()

    def drive_loop() -> None:
        asyncio.set_event_loop(loop)
        try:
            loop.run_forever()
        finally:
            loop.close()

    thread = Thread(target=drive_loop)
    thread.start()

    def execute(coroutine: Coroutine[Any, Any, T]) -> T:
        future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        try:
            return future.result(timeout=5)
        finally:
            future.cancel()

    try:
        yield execute
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join()


@pytest.fixture(scope="session")
def server(run: _Run) -> Generator[Server, None, None]:
    peer = run(start_server())
    yield peer
    run(peer.stop())
    peer.iserver.aspace.clear()


@pytest.fixture(scope="session")
def endpoint(server: Server) -> str:
    assert server.bserver is not None

    return f"opc.tcp://127.0.0.1:{server.bserver.port}/instro-tests/"


@pytest.fixture(scope="session")
def client(endpoint: str) -> Generator[OpcUaClient, None, None]:
    with OpcUaClient.connect(endpoint) as connected:
        yield connected
