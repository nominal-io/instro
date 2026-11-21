import asyncio
from pathlib import Path
from threading import Thread
from typing import TypedDict

import pytest
from asyncua import ua
from asyncua.server.address_space import AttributeValue, NodeData
from asyncua.server.server import Server
from asyncua.server.subscription_service import SubscriptionService
from asyncua.ua.attribute_ids import AttributeIds
from pytest import FixtureRequest, MonkeyPatch, TempPathFactory
from typing_extensions import Any, Coroutine, Generator, Protocol, TypeVar

from instro.unstable.opcua import Node, OpcUaClient

from .server import CERT_DIR, start_server

T = TypeVar("T")


class AsyncRunner(Protocol):
    def __call__(self, coroutine: Coroutine[Any, Any, T]) -> T: ...


@pytest.fixture(scope="session")
def run() -> Generator[AsyncRunner, None, None]:
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


@pytest.fixture(scope="function")
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


@pytest.fixture(scope="session")
def test_nodes(server_node: NodeData, client_node: Node) -> tuple[NodeData, Node]:
    return server_node, client_node


@pytest.fixture
def client_node(client: OpcUaClient) -> Node:
    return client.get_node("/Objects/2:Rig/2:Temperature")


class ServerNode(Protocol):
    """MonkeyPath-ing adapter for setting test-case-scoped attribute values on the test server node."""

    def set_attr(self, attribute_id: AttributeIds, value: ua.DataValue) -> None: ...


@pytest.fixture
def server_node(server: Server, monkeypatch: MonkeyPatch) -> ServerNode:
    class _ServerNode(ServerNode):
        def __init__(self, server_node: NodeData):
            self.server_node = server_node

        def set_attr(self, attribute_id: AttributeIds, value: ua.DataValue) -> None:
            monkeypatch.setitem(self.server_node.attributes, attribute_id, AttributeValue(value))

    node_id = ua.NodeId(ua.String("Temperature"), ua.Int16(2))

    return _ServerNode(server.iserver.aspace[node_id])


@pytest.fixture
def service(server: Server, run: AsyncRunner) -> Generator[SubscriptionService, None, None]:
    service = server.iserver.subscription_service
    yield service
    run(service.delete_subscriptions(list(service.subscriptions)))
