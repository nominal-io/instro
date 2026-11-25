import asyncio
import signal
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from time import monotonic
from unittest.mock import AsyncMock

import pytest
from asyncua import ua
from asyncua.common.utils import ServiceError
from asyncua.server.server import Server
from asyncua.server.subscription_service import SubscriptionService
from asyncua.server.uaprocessor import UaProcessor
from asyncua.ua.status_codes import StatusCodes
from asyncua.ua.ua_binary import struct_from_binary
from typing_extensions import Any, Callable, ContextManager, Iterable, Protocol, TypeAlias

from instro.unstable.opcua import Node, OpcUaClient, OpcUaError, Subscription
from tests.opcua.conftest import AsyncRunner, DefaultConnectionOptions


class Notify(Protocol):
    def __call__(
        self,
        *values: Any,
        node: ua.NodeId = ua.NodeId(ua.String("Temperature"), ua.Int16(2)),
        timestamp: ua.DateTime | None = None,
        status: int | None = None,
    ) -> None: ...


@pytest.fixture
def notify(service: SubscriptionService, monkeypatch: pytest.MonkeyPatch, run: AsyncRunner) -> Notify:
    async def send(
        *values: Any,
        node: ua.NodeId = ua.NodeId(ua.String("Temperature"), ua.Int16(2)),
        timestamp: ua.DateTime | None = None,
        status: int | None = None,
    ):
        native = next(iter(service.subscriptions.values()))
        if not native._publishing_enabled:
            native._triggered_datachanges.clear()
            await native.set_publishing_mode(True)
        sequence = native._notification_seq
        acknowledged = asyncio.Event()

        publish = service.publish

        def witness(acks: Iterable[ua.SubscriptionAcknowledgement]):
            if any(ack.SubscriptionId == native.data.SubscriptionId and ack.SequenceNumber == sequence for ack in acks):
                acknowledged.set()

            return publish(acks)

        with monkeypatch.context() as patch:
            patch.setattr(service, "publish", witness)

            if status is not None:
                await native.enqueue_statuschange(ua.StatusCode(ua.UInt32(status)))

            else:
                item = next(
                    item
                    for item in native.monitored_item_srv._monitored_items.values()
                    if item.read_value_id and item.read_value_id.NodeId == node
                )

                for value in values:
                    data = ua.DataValue(ua.Variant(value), SourceTimestamp=timestamp, ServerTimestamp=None)

                    assert item.monitored_item_id is not None

                    await native.enqueue_datachange_event(
                        item.monitored_item_id,
                        ua.MonitoredItemNotification(item.client_handle, data),
                        0,
                    )

            await native.publish_results()

            if status is None:
                await asyncio.wait_for(acknowledged.wait(), 2)

    return lambda *args, **kwargs: run(send(*args, **kwargs))


Interrupt: TypeAlias = Callable[[], None]


@pytest.fixture
def interrupt(run: AsyncRunner) -> Interrupt:
    async def schedule():
        asyncio.get_running_loop().call_later(0.001, signal.raise_signal, signal.SIGINT)

    return lambda: run(schedule())


@dataclass
class Poll:
    nodes: list[str]
    response: ua.ReadResponse
    received_at: float


class Polling(Protocol):
    def __call__(
        self, nodes: list[str], period: float, attribute: ua.AttributeIds = ua.AttributeIds.Value
    ) -> ContextManager[tuple[Subscription, Callable[[], Poll], Callable[[], None]]]: ...


@pytest.fixture
def polling(client: OpcUaClient, server: Server, run: AsyncRunner, monkeypatch: pytest.MonkeyPatch) -> Polling:
    @contextmanager
    def subscribe(nodes: list[str], period: float, attribute: ua.AttributeIds = ua.AttributeIds.Value):
        targets = {ua.NodeId(ua.String(name), ua.Int16(2)) for name in nodes}
        requests = asyncio.Queue[Poll]()
        pending: Callable[[], None] | None = None
        process = UaProcessor._process_message
        read_request = ua.NodeId(ua.Int32(ua.ObjectIds.ReadRequest_Encoding_DefaultBinary))

        async def intercept(processor, typeid, header, sequence, body):
            nonlocal pending
            if processor.iserver is server.iserver and typeid == read_request:
                params = struct_from_binary(ua.ReadParameters, body.copy())
                items = params.NodesToRead
                if items and all(item.NodeId in targets and item.AttributeId == attribute for item in items):
                    received_at = monotonic()
                    response = ua.ReadResponse()
                    response.Results = deepcopy(server.iserver.attribute_service.read(params))
                    assert pending is None
                    pending = partial(processor.send_response, header.RequestHandle, sequence, response)
                    requests.put_nowait(Poll([str(item.NodeId.Identifier) for item in items], response, received_at))
                    return True
            return await process(processor, typeid, header, sequence, body)

        async def release():
            nonlocal pending
            if pending is not None:
                pending()
                pending = None

        with monkeypatch.context() as patch:
            patch.setattr(UaProcessor, "_process_message", intercept)
            with client.subscribe(
                [f"/Objects/2:Rig/2:{name}" for name in nodes],
                attribute=int(attribute),
                publishing_enabled=False,
                publishing_interval=0.01,
                poll_interval=period,
            ) as samples:
                try:
                    yield samples, lambda: run(requests.get()), lambda: run(release())
                finally:
                    patch.undo()
                    run(release())

    return subscribe


def test_idle_python_retains_every_accepted_notification(
    client: OpcUaClient, client_node: Node, service: SubscriptionService, notify: Notify
):
    samples = client.subscribe([client_node], publishing_interval=0.001)
    assert next(samples)[1].value == 23.5

    notify(*(float(value) for value in range(80)))
    samples.close()
    samples.close()

    assert [value.value for _, value in samples] == list(range(80))
    assert service.subscriptions == {}
    assert list(samples) == []


def test_ready_poll_tick_precedes_queued_notification(polling: Polling, notify: Notify, run: AsyncRunner):
    period = 0.01
    with polling(["Temperature"], period) as (samples, receive, release):
        poll = receive()
        value = poll.response.Results[0]
        value.Value = ua.Variant(31.0)
        value.SourceTimestamp = datetime.fromtimestamp(10, timezone.utc)

        notify(42.0, timestamp=ua.DateTime.fromtimestamp(1, timezone.utc))
        run(asyncio.sleep(2 * period))
        release()
        receive()

    assert [value.value for _, value in samples][:2] == [31.0, 42.0]


def test_matching_notification_suppresses_incomparable_poll_but_keeps_other_node(polling: Polling, notify: Notify):
    with polling(["Temperature", "Float"], 0.01) as (samples, receive, release):
        poll = receive()
        for name, result in zip(poll.nodes, poll.response.Results, strict=True):
            result.SourceTimestamp = result.ServerTimestamp = None
            result.Value = ua.Variant({"Temperature": 31.0, "Float": 32.0}[name])

        notify(42.0)

        # A repeat close returns once admission is sealed. The owner still waits for this read.
        with ThreadPoolExecutor(2) as workers:
            closes = [workers.submit(samples.close) for _ in range(2)]
            try:
                done, _ = wait(closes, timeout=2, return_when=FIRST_COMPLETED)
                assert done
            finally:
                release()
            for close in closes:
                close.result()

    assert [(node.node_id.identifier, value.value) for node, value in samples] == [
        ("Temperature", 42.0),
        ("Float", 32.0),
    ]


@pytest.mark.parametrize("short_read", [False, True], ids=["service-error", "short-response"])
def test_unsuccessful_poll_still_polls_newly_quiet_nodes_next_interval(
    polling: Polling, notify: Notify, short_read: bool
):
    with polling(["Temperature", "Float"], 0.02, ua.AttributeIds.DisplayName) as (_, receive, release):
        first = receive()
        assert set(first.nodes) == {"Temperature", "Float"}
        notify(ua.LocalizedText("reported-temp"))
        release()

        failed = receive()
        assert failed.nodes == ["Float"]
        if short_read:
            failed.response.Results.clear()
        else:
            failed.response.ResponseHeader.ServiceResult = ua.StatusCode(ua.UInt32(StatusCodes.BadTimeout))
        release()

        assert set(receive().nodes) == {"Temperature", "Float"}


def test_interrupt_does_not_abandon_poll_or_accepted_notification(
    polling: Polling, notify: Notify, interrupt: Interrupt
):
    with polling(["Temperature"], 0.01) as (samples, receive, release):
        result = receive().response.Results[0]
        result.Value = ua.Variant(41.0)
        result.SourceTimestamp = datetime.fromtimestamp(1, timezone.utc)

        with pytest.raises(KeyboardInterrupt):
            interrupt()
            next(samples)

        notify(42.0, timestamp=ua.DateTime.fromtimestamp(2, timezone.utc))
        release()
        receive()

    assert [value.value for _, value in samples][:2] == [41.0, 42.0]


def test_failed_deletion_seals_notification_admission(
    client: OpcUaClient,
    client_node: Node,
    service: SubscriptionService,
    monkeypatch: pytest.MonkeyPatch,
    notify: Notify,
):
    samples = client.subscribe([client_node], publishing_interval=0.001)

    next(samples)
    notify(52.0)

    monkeypatch.setattr(
        service,
        "delete_subscriptions",
        AsyncMock(side_effect=ServiceError(ua.UInt32(StatusCodes.BadSubscriptionIdInvalid))),
    )

    with pytest.raises(OpcUaError) as raised:
        samples.close()

    assert raised.value.status_code.code == StatusCodes.BadSubscriptionIdInvalid
    assert len(service.subscriptions) == 1

    notify(53.0)
    assert [value.value for _, value in samples] == [52.0]

    samples.close()
    assert list(samples) == []


def test_context_body_error_keeps_cleanup_error_as_context(
    client: OpcUaClient, client_node: Node, service: SubscriptionService, monkeypatch: pytest.MonkeyPatch
):
    failure = LookupError("body")

    monkeypatch.setattr(
        service,
        "delete_subscriptions",
        AsyncMock(return_value=[ua.StatusCode(ua.UInt32(StatusCodes.BadInternalError))]),
    )

    with pytest.raises(LookupError) as raised:
        with client.subscribe([client_node], publishing_interval=0.001):
            raise failure
    assert raised.value is failure
    assert isinstance(failure.__context__, OpcUaError)
    assert failure.__context__.status_code.code == 0x80020000


def test_terminal_subscription_error_follows_retained_samples_once(
    client: OpcUaClient,
    client_node: Node,
    notify: Notify,
    service: SubscriptionService,
    monkeypatch: pytest.MonkeyPatch,
):
    samples = client.subscribe([client_node], publishing_interval=0.001)
    next(samples)

    notify(71.0)
    monkeypatch.setattr(
        service,
        "delete_subscriptions",
        AsyncMock(return_value=[ua.StatusCode(ua.UInt32(StatusCodes.BadSubscriptionIdInvalid))]),
    )
    notify(None, status=ua.UInt32(StatusCodes.BadTimeout))

    assert next(samples)[1].value == 71.0

    with pytest.raises(OpcUaError) as raised:
        next(samples)

    assert raised.value.status_code.code == StatusCodes.BadTimeout
    assert list(samples) == []

    samples.close()


def test_last_subscription_owner_does_not_wait_for_native_deletion(
    client: OpcUaClient,
    client_node: Node,
    service: SubscriptionService,
    monkeypatch: pytest.MonkeyPatch,
    run: AsyncRunner,
):
    progress = asyncio.Queue[str]()
    released = asyncio.Queue[None]()
    delete = service.delete_subscriptions

    async def hold(ids):
        await progress.put("started")
        await released.get()

        result = await delete(ids)

        await progress.put("finished")

        return result

    monkeypatch.setattr(service, "delete_subscriptions", hold)
    samples = client.subscribe([client_node], publishing_interval=0.001)

    del samples

    try:
        run(progress.get())

    finally:
        run(released.put(None))

    run(progress.get())
    assert service.subscriptions == {}


def test_transport_failure_preserves_samples_before_terminal_error(
    endpoint: str,
    connection_options: DefaultConnectionOptions,
    server: Server,
    service: SubscriptionService,
    notify: Notify,
    run: AsyncRunner,
    monkeypatch: pytest.MonkeyPatch,
):
    process = UaProcessor._process_message
    session_requests = {
        ua.NodeId(ua.Int32(ua.ObjectIds.ActivateSessionRequest_Encoding_DefaultBinary)),
        ua.NodeId(ua.Int32(ua.ObjectIds.CreateSessionRequest_Encoding_DefaultBinary)),
    }

    async def reject_reconnect(processor, typeid, header, sequence, body):
        if processor.iserver is server.iserver and typeid in session_requests:
            raise ServiceError(ua.UInt32(StatusCodes.BadConnectionRejected))
        return await process(processor, typeid, header, sequence, body)

    opts: Any = connection_options
    opts["session_retry_initial"] = 0.001
    opts["session_retry_max"] = 0.001

    client = OpcUaClient.connect(endpoint, **connection_options)
    severed_session = None

    try:
        samples = client.subscribe(["/Objects/2:Rig/2:Temperature"], publishing_interval=0.001)
        next(samples)
        notify(61.0)

        async def sever():
            session_id = next(iter(service.subscriptions.values())).session_id
            assert server.bserver is not None

            peer = next(
                peer
                for peer in server.bserver.clients
                if peer.processor.session is not None and peer.processor.session.session_id == session_id  # type: ignore
            )

            assert isinstance(peer.transport, asyncio.Transport)
            peer.transport.abort()
            return peer.processor.session

        monkeypatch.setattr(UaProcessor, "_process_message", reject_reconnect)
        severed_session = run(sever())

        assert next(samples)[1].value == 61.0

        with pytest.raises(OpcUaError) as raised:
            next(samples)

        assert raised.value.status_code.is_bad
        assert list(samples) == []

        samples.close()

    finally:
        try:
            client.disconnect()
        except OpcUaError as error:
            if error.status_code.code != StatusCodes.BadConnectionRejected:
                raise
        finally:
            if severed_session is not None:
                run(severed_session.close_session())


@pytest.mark.parametrize("period,hold", [(0.1, None), (0.01, 0.035)], ids=["first-period", "skip-missed-periods"])
def test_poll_schedule(
    polling: Polling,
    service: SubscriptionService,
    monkeypatch: pytest.MonkeyPatch,
    run: AsyncRunner,
    period: float,
    hold: float | None,
):
    created = []
    create = service.create_monitored_items

    async def ready(parameters):
        result = await create(parameters)
        created.append(monotonic())
        return result

    monkeypatch.setattr(service, "create_monitored_items", ready)

    with polling(["Temperature"], period) as (_, receive, release):
        first = receive()
        if hold is None:
            assert first.received_at - created[0] >= 0.09
        else:
            run(asyncio.sleep(hold))
            started = monotonic()
            for _ in range(3):
                release()
                following = receive()
            assert following.received_at - started >= 0.009
