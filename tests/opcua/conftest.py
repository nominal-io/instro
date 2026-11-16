import asyncio
from threading import Thread

import pytest
from asyncua import ua
from asyncua.server.address_space import AttributeValue

from instro.unstable.opcua import OpcUaClient

from .server import CERT_DIR, start_server


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
def client(endpoint, tmp_path_factory):
    with OpcUaClient.connect(endpoint, pki_dir=tmp_path_factory.mktemp("opcua"), session_retry_limit=0) as connected:
        yield connected


@pytest.fixture(scope="session")
def node(client):
    return client.get_node("/Objects/2:Rig/2:Temperature")


@pytest.fixture
def attribute_data(server, monkeypatch):
    attributes = server.iserver.aspace[ua.NodeId("Temperature", 2)].attributes
    return lambda attribute, value: monkeypatch.setitem(attributes, attribute, AttributeValue(value))


@pytest.fixture
def service(server, run):
    service = server.iserver.subscription_service
    yield service
    run(service.delete_subscriptions(list(service.subscriptions)))
