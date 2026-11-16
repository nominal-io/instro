import asyncio
from pathlib import Path
from threading import Thread

import pytest
from asyncua.server.server import Server
from typing_extensions import Any, Coroutine, Generator, Protocol, TypeVar

from instro.unstable.opcua import OpcUaClient

from .server import CERT_DIR, start_server

T = TypeVar("T")


class _Run(Protocol):
    def __call__(self, coroutine: Coroutine[Any, Any, T]) -> T: ...


@pytest.fixture(scope="session")
def run() -> Generator[_Run, None, None]:
    with asyncio.Runner() as runtime:
        loop = runtime.get_loop()
        thread = Thread(target=loop.run_forever)
        thread.start()

        def execute(coroutine: Coroutine[Any, Any, T]) -> T:
            return asyncio.run_coroutine_threadsafe(coroutine, loop).result(timeout=5)

        yield execute
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


@pytest.fixture
def connection_options(tmp_path, request):
    return dict(
        pki_dir=tmp_path,
        request_timeout=2,
        session_retry_limit=0,
        session_name=request.node.name,
        certificate_path=CERT_DIR / "client-cert.pem",
        private_key_path=CERT_DIR / "client-key.pem",
        trust_server_certs=True,
        verify_server_certs=False,
    )


@pytest.fixture(scope="session")
def client(endpoint: str, tmp_path_factory) -> Generator[OpcUaClient, None, None]:
    with OpcUaClient.connect(endpoint, pki_dir=tmp_path_factory.mktemp("opcua"), session_retry_limit=0) as connected:
        yield connected
