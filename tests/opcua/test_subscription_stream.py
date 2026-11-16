import asyncio
import signal
from asyncio.futures import Future
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from time import monotonic
from unittest.mock import AsyncMock, Mock

import pytest
from asyncua import ua
from asyncua.common.utils import ServiceError
from asyncua.server.internal_session import InternalSession
from asyncua.server.server import Server
from asyncua.server.subscription_service import SubscriptionService
from asyncua.server.uaprocessor import UaProcessor
from asyncua.ua.status_codes import StatusCodes
from typing_extensions import Any, Callable, Generator, Iterable, Protocol, TypeAlias, cast

from instro.unstable.opcua import Node, OpcUaClient, OpcUaError
from instro.unstable.opcua.types import LocalizedText
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


SendMessageParams: TypeAlias = tuple[UaProcessor, ua.IntegerId, ua.SequenceHeader, ua.ReadResponse, ua.MessageType]


class InterceptNextMessage(Protocol):
    def __call__(self, session_id: ua.NodeId | None = None) -> SendMessageParams: ...


class ForwardMessage(Protocol):
    def __call__(
        self,
        processor: UaProcessor,
        handle: ua.IntegerId,
        sequence: ua.SequenceHeader,
        response: ua.ReadResponse,
        msgtype: ua.MessageType,
    ) -> None: ...


Interceptor: TypeAlias = tuple[InterceptNextMessage, ForwardMessage]


@pytest.fixture
def interceptor(
    server: Server, run: AsyncRunner, monkeypatch: pytest.MonkeyPatch
) -> Generator[Interceptor, None, None]:
    waiting: Future[SendMessageParams] | None = None
    armed_session: ua.NodeId | None = None
    send = UaProcessor.send_response

    def intercept(
        processor: UaProcessor,
        handle: ua.IntegerId,
        sequence: ua.SequenceHeader,
        response: ua.ReadResponse,
        msgtype: ua.MessageType = cast(ua.MessageType, ua.MessageType.SecureMessage),
    ):
        pending = waiting
        session = processor.session
        matches_session = armed_session is None or (session is not None and session.session_id == armed_session)

        if (
            processor.iserver is server.iserver
            and isinstance(response, ua.ReadResponse)
            and pending is not None
            and not pending.done()
            and matches_session
        ):
            reply = (processor, handle, sequence, deepcopy(response), msgtype)
            pending.set_result(reply)
        else:
            send(processor, handle, sequence, response, msgtype)  # type: ignore

    async def intercept_msg(session_id: ua.NodeId | None = None) -> SendMessageParams:
        nonlocal waiting, armed_session
        armed_session = session_id
        waiting = asyncio.get_running_loop().create_future()
        return await waiting

    async def forward_msg(*args, **kwargs) -> None:
        nonlocal waiting
        waiting = None
        send(*args, **kwargs)

    monkeypatch.setattr(UaProcessor, "send_response", intercept)
    try:
        yield (
            lambda session_id=None: run(intercept_msg(session_id)),
            lambda *args, **kwargs: run(forward_msg(*args, **kwargs)),
        )

    finally:
        waiting = cast(
            Future[SendMessageParams] | None, waiting
        )  # pyright can't track that waiting might be substituted with a real value
        if waiting is not None and waiting.done():
            run(forward_msg(waiting.result()))


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


def test_ready_poll_tick_precedes_queued_notification(
    client: OpcUaClient,
    client_node: Node,
    notify: Notify,
    interceptor: Interceptor,
    run: AsyncRunner,
    monkeypatch: pytest.MonkeyPatch,
):
    receive, release = interceptor

    with client.subscribe(
        [client_node, "/Objects/2:Rig/2:Float"], publishing_interval=0.001, poll_interval=0.01
    ) as samples:
        assert {next(samples)[1].value for _ in range(2)} == {23.5, 1.25}

        reply = receive()
        reply[3].Results[0].Value = ua.Variant(31.0)
        reply[3].Results[0].SourceTimestamp = datetime.fromtimestamp(10, timezone.utc)

        notify(42.0, timestamp=ua.DateTime.fromtimestamp(1, timezone.utc))
        run(asyncio.sleep(0.025))

        held: list[SendMessageParams] = []
        send = UaProcessor.send_response

        def hold(
            processor: UaProcessor,
            handle: ua.IntegerId,
            sequence: ua.SequenceHeader,
            response: ua.ReadResponse,
            msgtype: ua.MessageType = cast(ua.MessageType, ua.MessageType.SecureMessage),
        ):
            if processor is reply[0] and isinstance(response, ua.ReadResponse):
                held.append((processor, handle, sequence, response, msgtype))
            else:
                send(processor, handle, sequence, response, msgtype)  # type: ignore

        async def unblock():
            patch.undo()
            for response in held:
                send(*response)  # type: ignore

        with monkeypatch.context() as patch, ThreadPoolExecutor() as worker:
            patch.setattr(UaProcessor, "send_response", hold)
            release(*reply)

            count = len(reply[3].Results) + 1
            values = worker.submit(lambda: [next(samples)[1].value for _ in range(count)])
            try:
                observed = values.result(timeout=2)
                assert observed[-1] == 42.0
                assert 31.0 in observed[:-1]
            finally:
                run(unblock())


def test_matching_notification_suppresses_incomparable_poll_but_keeps_other_node(
    client: OpcUaClient, client_node: Node, notify: Notify, interceptor: Interceptor
):
    receive, release = interceptor

    with client.subscribe(
        [client_node, "/Objects/2:Rig/2:Float"], publishing_interval=0.001, poll_interval=0.05
    ) as samples:
        for _ in range(2):
            next(samples)

        reply = receive()
        *_, read_resp, _ = reply

        for value in read_resp.Results:
            value.SourceTimestamp = value.ServerTimestamp = None

        notify(42.0)
        release(*reply)

        observed_node, value = next(samples)
        assert str(observed_node.node_id) == "ns=2;s=Temperature"
        assert value.value == 42.0

        samples.close()

        assert [value.value for node, value in samples if str(node.node_id) == "ns=2;s=Float"] == [1.25]


def test_failed_poll_retries_quiet_membership_then_success_resets_it(
    client: OpcUaClient,
    client_node: Node,
    service: SubscriptionService,
    server: Server,
    monkeypatch: pytest.MonkeyPatch,
    interceptor: Interceptor,
    notify: Notify,
    run: AsyncRunner,
):
    receive, release = interceptor
    read = Mock(wraps=server.iserver.attribute_service.read)
    monkeypatch.setattr(server.iserver.attribute_service, "read", read)

    with client.subscribe(
        [client_node, "/Objects/2:Rig/2:Float"],
        attribute="DisplayName",
        publishing_enabled=False,
        publishing_interval=1,
        poll_interval=0.02,
    ) as samples:

        async def prepare():
            native = next(iter(service.subscriptions.values()))
            native._triggered_datachanges.clear()
            native._publishing_enabled = True

        run(prepare())
        notify(ua.LocalizedText("reported-temp"))

        sample = next(samples)[1]
        assert isinstance(sample.value, LocalizedText)
        assert sample.value.text == "reported-temp"

        failed = receive()
        *_, read_resp, _ = failed
        read_resp.ResponseHeader.ServiceResult = ua.StatusCode(ua.UInt32(StatusCodes.BadTimeout))

        release(*failed)
        release(*receive())
        release(*receive())

        sample = next(samples)[1]
        assert isinstance(sample.value, LocalizedText)
        assert sample.value.text == "Float"

    assert [{item.NodeId.Identifier for item in call.args[0].NodesToRead} for call in read.call_args_list[:3]] == [
        {"Float"},
        {"Float"},
        {"Temperature", "Float"},
    ]

    assert all(item.AttributeId == 4 for call in read.call_args_list for item in call.args[0].NodesToRead)


def test_interrupt_does_not_abandon_poll_or_accepted_notification(
    client: OpcUaClient,
    client_node: Node,
    interceptor: Interceptor,
    notify: Notify,
    interrupt: Callable[[], None],
):
    receive, release = interceptor
    with client.subscribe([client_node], publishing_interval=0.001, poll_interval=0.01) as samples:
        next(samples)
        reply = receive()
        *_, read_resp, _ = reply
        read_resp.Results[0].Value = ua.Variant(41.0)
        read_resp.Results[0].SourceTimestamp = datetime.fromtimestamp(1, timezone.utc)

        with pytest.raises(KeyboardInterrupt):
            interrupt()
            next(samples)

        notify(42.0, timestamp=ua.DateTime.fromtimestamp(2, timezone.utc))

        release(*reply)

        samples.close()
        assert [value.value for _, value in samples] == [41.0, 42.0]


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
        service, "delete_subscriptions", AsyncMock(side_effect=ServiceError(ua.UInt32(StatusCodes.BadInternalError)))
    )

    with pytest.raises(OpcUaError) as raised:
        samples.close()

    assert raised.value.status_code.code == 0x80020000
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
    client: OpcUaClient, client_node: Node, notify: Notify
):
    samples = client.subscribe([client_node], publishing_interval=0.001)
    next(samples)

    notify(71.0)
    notify(None, status=ua.UInt32(StatusCodes.BadTimeout))

    assert next(samples)[1].value == 71.0

    with pytest.raises(OpcUaError) as raised:
        next(samples)

    assert raised.value.status_code.code == 0x800A0000
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
    def reject_reconnect(*args, **kwargs):
        raise ServiceError(ua.UInt32(StatusCodes.BadConnectionRejected))

    opts: Any = connection_options
    opts["session_retry_initial"] = 0.001
    opts["session_retry_max"] = 0.001

    client = OpcUaClient.connect(endpoint, **connection_options)

    try:
        samples = client.subscribe(["/Objects/2:Rig/2:Temperature"], publishing_interval=0.001)
        next(samples)
        notify(61.0)

        async def sever():
            session_id = next(iter(service.subscriptions.values())).session_id
            assert server.bserver is not None
            assert server.bserver.clients is not None

            peer = next(
                peer
                for peer in server.bserver.clients
                if peer.processor.session is not None and peer.processor.session.session_id == session_id  # type: ignore
            )

            assert isinstance(peer.transport, asyncio.Transport)
            peer.transport.abort()

        monkeypatch.setattr(InternalSession, "activate_session", reject_reconnect)
        run(sever())

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


@pytest.mark.parametrize("period,hold", [(0.1, None), (0.01, 0.035)], ids=["first-period", "skip-missed-periods"])
def test_poll_schedule(
    client: OpcUaClient,
    client_node: Node,
    service: SubscriptionService,
    server: Server,
    monkeypatch: pytest.MonkeyPatch,
    interceptor: Interceptor,
    run: AsyncRunner,
    period: float,
    hold: float | None,
):
    receive, release = interceptor
    admitted = []
    created = []
    read = server.iserver.attribute_service.read
    create = service.create_monitored_items

    def observe(parameters):
        admitted.append(monotonic())
        return read(parameters)

    async def ready(parameters):
        result = await create(parameters)
        created.append(monotonic())
        return result

    monkeypatch.setattr(server.iserver.attribute_service, "read", observe)
    monkeypatch.setattr(service, "create_monitored_items", ready)

    with client.subscribe([client_node], publishing_enabled=False, publishing_interval=1, poll_interval=period):
        reply = receive()
        if hold is None:
            delay = admitted[0] - created[0]
            release(*reply)
            assert delay >= 0.09

        else:
            run(asyncio.sleep(hold))
            release(*reply)
            release(*receive())
            started = monotonic()
            release(*receive())
            release(*receive())

            assert monotonic() - started >= 0.009
