import asyncio
from pathlib import Path
from threading import Thread
from typing import TypedDict

import pytest
from asyncua.server.server import Server
from pytest import FixtureRequest, TempPathFactory
from typing_extensions import Any, Coroutine, Generator, Protocol, TypeVar

from instro.unstable.opcua import OpcUaClient

from .server import CERT_DIR, start_server

T = TypeVar("T")


class AsyncRunner(Protocol):
    def __call__(self, coroutine: Coroutine[Any, Any, T]) -> T: ...


@pytest.fixture(scope="session")
def run() -> Generator[AsyncRunner, None, None]:
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
def server(run: AsyncRunner) -> Generator[Server, None, None]:
    peer = run(start_server())
    yield peer
    run(peer.stop())
    peer.iserver.aspace.clear()


@pytest.fixture(scope="session")
def endpoint(server: Server) -> str:
    assert server.bserver is not None

    return f"opc.tcp://127.0.0.1:{server.bserver.port}/instro-tests/"


class DefaultConnectionOptions(TypedDict):
    pki_dir: Path
    request_timeout: float
    session_retry_limit: int
    session_name: str
    certificate_path: Path
    private_key_path: Path
    trust_server_certs: bool
    verify_server_certs: bool


@pytest.fixture
def connection_options(tmp_path: Path, request: FixtureRequest) -> DefaultConnectionOptions:
    return {
        "pki_dir": tmp_path,
        "request_timeout": 2,
        "session_retry_limit": 0,
        "session_name": request.node.name,
        "certificate_path": CERT_DIR / "client-cert.pem",
        "private_key_path": CERT_DIR / "client-key.pem",
        "trust_server_certs": True,
        "verify_server_certs": False,
    }


@pytest.fixture(scope="session")
def client(endpoint: str, tmp_path_factory: TempPathFactory) -> Generator[OpcUaClient, None, None]:
    with OpcUaClient.connect(endpoint, pki_dir=tmp_path_factory.mktemp("opcua"), session_retry_limit=0) as connected:
        yield connected
