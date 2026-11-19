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
from typing_extensions import Any, Callable, Generator, TypeAlias

from instro.unstable.opcua import OpcUaClient, OpcUaError
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
                    client.translate([path])

            assert not released.is_set()
            assert handled_on == [main_thread]
        finally:
            finish()


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


def test_disconnect_waits_through_a_signal(
    endpoint: str,
    connection_options: DefaultConnectionOptions,
    stalled_reply: StalledReply,
):
    arm, released, _, main_thread, finish = stalled_reply
    observed = []
    signal.signal(signal.SIGINT, lambda _signum, _frame: observed.append((get_ident(), released.is_set())))

    connection_options["request_timeout"] = 10
    with OpcUaClient.connect(endpoint, **connection_options) as client:
        arm("close_session", 0.2)

        try:
            client.disconnect()
            assert released.is_set()
            assert observed == [(main_thread, True)]
        finally:
            finish()


def test_disconnect_reports_a_rejected_close_session_after_shutdown(
    endpoint: str,
    connection_options: DefaultConnectionOptions,
    server: Server,
    monkeypatch: pytest.MonkeyPatch,
):
    close_session = InternalSession.close_session

    async def reject(session: InternalSession, delete_subs: bool) -> None:
        if session.iserver is server.iserver and session.external and delete_subs:
            raise ServiceError(ua.UInt32(ua.StatusCodes.BadUserAccessDenied))
        await close_session(session, delete_subs)

    with OpcUaClient.connect(endpoint, **connection_options) as client:
        monkeypatch.setattr(InternalSession, "close_session", reject)
        with pytest.raises(OpcUaError) as rejected:
            client.disconnect()

        assert rejected.value.status_code.code == ua.StatusCodes.BadUserAccessDenied
        client.disconnect()
