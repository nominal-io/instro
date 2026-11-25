import asyncio
import signal
from threading import get_ident

import pytest
from asyncua import ua
from asyncua.common.utils import ServiceError
from asyncua.server.internal_session import InternalSession
from asyncua.server.server import Server
from asyncua.server.uaprocessor import UaProcessor
from asyncua.ua.ua_binary import struct_from_binary
from typing_extensions import Any, Awaitable, Callable, Generator, TypeAlias

from instro.unstable.opcua import Node, OpcUaClient, OpcUaError
from tests.opcua.conftest import AsyncRunner, DefaultConnectionOptions


class SignalObserved(Exception):
    pass


StalledReply: TypeAlias = tuple[Callable[[str, float], None], asyncio.Event, list[int], int, Callable[[], None]]


@pytest.fixture
def stalled_reply(
    server: Server, run: AsyncRunner, monkeypatch: pytest.MonkeyPatch
) -> Generator[StalledReply, None, None]:
    released = asyncio.Event()
    handled_on = []
    main_thread = get_ident()
    timer = None
    previous_handler = signal.getsignal(signal.SIGINT)

    def interrupt(_signum: Any, _frame: Any):
        handled_on.append(get_ident())
        raise SignalObserved

    signal.signal(signal.SIGINT, interrupt)

    def arm(method, release_after: float = 5):
        original = getattr(InternalSession, method)

        # inject an interrupt signal while the client is waiting for the expected service reply
        async def intercept(session: InternalSession, params: Any):
            nonlocal timer
            result = await original(session, params)

            is_subject = session.iserver is server.iserver and session.external and timer is None

            if method == "read":
                # The initial keepalive can arrive while the fixture is armed.
                assert isinstance(params, ua.ReadParameters)

                test_node = ua.NodeId(ua.String("Temperature"), ua.Int16(2))

                is_subject = is_subject and any(
                    isinstance(nid := item.NodeId, ua.NodeId) and nid == test_node for item in params.NodesToRead
                )

            if is_subject:
                timer = asyncio.get_running_loop().call_later(release_after, released.set)
                signal.raise_signal(signal.SIGINT)
                await released.wait()

            return result

        monkeypatch.setattr(InternalSession, method, intercept)

    async def finish():
        if timer is not None:
            timer.cancel()
        released.set()

    try:
        yield (
            arm,
            released,
            handled_on,
            main_thread,
            lambda: run(finish()),
        )

    finally:
        try:
            run(finish())
        finally:
            signal.signal(signal.SIGINT, previous_handler)


@pytest.mark.parametrize("operation", ["read", "batch", "resolve", "translate"])
def test_interruption_returns_before_held_service_reply(
    endpoint: str,
    connection_options: DefaultConnectionOptions,
    stalled_reply: StalledReply,
    operation: str,
):
    arm, released, handled_on, main_thread, finish = stalled_reply
    methods = {
        "read": "read",
        "batch": "read",
        "resolve": "translate_browsepaths_to_nodeids",
        "translate": "translate_browsepaths_to_nodeids",
    }

    connection_options["request_timeout"] = 10
    with OpcUaClient.connect(endpoint, **connection_options) as client:
        path = "/Objects/2:Rig/2:Temperature"
        node = client.get_node(path)
        arm(methods[operation])

        try:
            with pytest.raises(SignalObserved):
                if operation == "read":
                    _ = node.value
                elif operation == "batch":
                    client.read_nodes([node])
                elif operation == "resolve":
                    client.get_node(path)
                else:
                    client._translate([path])

            assert not released.is_set()
            assert handled_on == [main_thread]
        finally:
            finish()


def test_disconnect_from_signal_handler_rejects_reentrant_wait(
    client: OpcUaClient, client_node: Node, stalled_reply: StalledReply
):
    arm, released, _, _, finish = stalled_reply
    signal.signal(signal.SIGINT, lambda _signum, _frame: client.disconnect())
    arm("read", 5)

    with pytest.raises(RuntimeError, match="inside another blocking OPC UA wait"):
        _ = client_node.value
    assert not released.is_set()

    finish()
    assert client_node.value.value == 23.5


def test_interrupted_browse_finishes_the_held_page_and_releases_on_error(
    endpoint: str,
    connection_options: DefaultConnectionOptions,
    server: Server,
    run: AsyncRunner,
    monkeypatch: pytest.MonkeyPatch,
    stalled_reply: StalledReply,
):
    arm, released, handled_on, main_thread, finish = stalled_reply
    point = ua.ByteString(b"held-browse-page")
    view = server.iserver.view_service
    browse = view.browse
    process = UaProcessor._process_message
    requests = []
    released_point = asyncio.Event()

    def first_page(params: ua.BrowseParameters) -> list[ua.BrowseResult]:
        results = browse(params)
        results[0].ContinuationPoint = point
        return results

    async def browse_next(processor, typeid, header, sequence, body):
        if typeid != ua.NodeId(ua.Int32(ua.ObjectIds.BrowseNextRequest_Encoding_DefaultBinary)):
            return await process(processor, typeid, header, sequence, body)

        params = struct_from_binary(ua.BrowseNextParameters, body)
        requests.append((params.ReleaseContinuationPoints, params.ContinuationPoints))
        response = ua.BrowseNextResponse()

        if params.ReleaseContinuationPoints:
            released_point.set()
        else:
            status = ua.StatusCode(ua.UInt32(ua.StatusCodes.BadTimeout))
            response.Parameters.Results = [ua.BrowseResult(StatusCode=status)]

        processor.send_response(header.RequestHandle, sequence, response)
        return True

    monkeypatch.setattr(view, "browse", first_page)
    monkeypatch.setattr(UaProcessor, "_process_message", browse_next)
    connection_options["request_timeout"] = 10

    with OpcUaClient.connect(endpoint, **connection_options) as client:
        arm("browse", 5)

        try:
            with pytest.raises(SignalObserved):
                _ = client.objects.children

            assert not released.is_set()
            assert handled_on == [main_thread]
        finally:
            finish()

        run(asyncio.wait_for(released_point.wait(), 2))
        assert requests == [(False, [point]), (True, [point])]


CloseHook: TypeAlias = Callable[[int, Callable[[], Awaitable[None]]], None]


@pytest.fixture
def closing_client(
    endpoint: str, connection_options: DefaultConnectionOptions, server: Server, monkeypatch: pytest.MonkeyPatch
) -> Generator[tuple[OpcUaClient, CloseHook], None, None]:
    assert server.bserver is not None
    existing = set(server.bserver.clients)
    connection_options["request_timeout"] = 10
    with OpcUaClient.connect(endpoint, **connection_options) as client:
        (processor,) = (
            peer.processor
            for peer in server.bserver.clients
            if peer not in existing and peer.processor is not None and peer.processor.session is not None
        )
        process = processor._process_message

        def on_request(encoding_id, hook):
            request = ua.NodeId(ua.Int32(encoding_id))

            async def intercept(typeid, header, sequence, body):
                if typeid == request:
                    await hook()
                return await process(typeid, header, sequence, body)

            monkeypatch.setattr(processor, "_process_message", intercept)

        yield client, on_request


@pytest.mark.parametrize("operation", ["disconnect", "disconnect-with-subscription", "close-subscription"])
def test_explicit_cleanup_waits_through_a_signal(
    closing_client: tuple[OpcUaClient, CloseHook], server: Server, operation: str
):
    client, on_request = closing_client
    released = asyncio.Event()
    observed = []

    async def stall():
        signal.raise_signal(signal.SIGINT)
        await asyncio.sleep(0.2)
        released.set()

    close = client.disconnect
    request = ua.ObjectIds.CloseSessionRequest_Encoding_DefaultBinary
    subscriptions = server.iserver.subscription_service.subscriptions
    target = None
    if operation != "disconnect":
        existing = set(subscriptions)
        samples = client.subscribe(["/Objects/2:Rig/2:Temperature"])
        next(samples)
        (target,) = subscriptions.keys() - existing
        if operation == "close-subscription":
            close = samples.close
            request = ua.ObjectIds.DeleteSubscriptionsRequest_Encoding_DefaultBinary

    on_request(request, stall)
    previous = signal.signal(signal.SIGINT, lambda *_: observed.append((get_ident(), released.is_set())))
    try:
        close()
    finally:
        signal.signal(signal.SIGINT, previous)

    assert observed == [(get_ident(), True)]
    if target is not None:
        assert target not in subscriptions


def test_disconnect_reports_a_rejected_close_session_after_shutdown(closing_client: tuple[OpcUaClient, CloseHook]):
    client, on_request = closing_client

    async def reject():
        raise ServiceError(ua.UInt32(ua.StatusCodes.BadUserAccessDenied))

    on_request(ua.ObjectIds.CloseSessionRequest_Encoding_DefaultBinary, reject)
    with pytest.raises(OpcUaError) as rejected:
        client.disconnect()

    assert rejected.value.status_code.code == ua.StatusCodes.BadUserAccessDenied
    client.disconnect()


def test_interrupted_subscription_setup_finishes_and_rolls_back(
    endpoint: str,
    connection_options: DefaultConnectionOptions,
    server: Server,
    run: AsyncRunner,
    monkeypatch: pytest.MonkeyPatch,
    stalled_reply: StalledReply,
):
    arm, released, handled_on, main_thread, finish = stalled_reply
    deleted = asyncio.Event()
    service = server.iserver.subscription_service
    delete = service.delete_subscriptions
    existing = set(service.subscriptions)
    target = None

    async def observe_deletion(ids):
        result = await delete(ids)
        if target in ids:
            deleted.set()
        return result

    monkeypatch.setattr(service, "delete_subscriptions", observe_deletion)
    connection_options["request_timeout"] = 10
    with OpcUaClient.connect(endpoint, **connection_options) as client:
        node = client.get_node("/Objects/2:Rig/2:Temperature")
        arm("create_monitored_items", 5)

        try:
            with pytest.raises(SignalObserved):
                client.subscribe([node])

            assert not released.is_set()
            assert handled_on == [main_thread]
            (target,) = service.subscriptions.keys() - existing
        finally:
            finish()

        run(asyncio.wait_for(deleted.wait(), 2))
        assert target not in service.subscriptions
