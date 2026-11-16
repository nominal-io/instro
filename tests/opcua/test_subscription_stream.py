import asyncio
import signal
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from copy import deepcopy
from datetime import datetime, timezone
from time import monotonic
from unittest.mock import AsyncMock, Mock

import pytest
from asyncua import ua
from asyncua.common.utils import ServiceError
from asyncua.server.internal_session import InternalSession
from asyncua.server.uaprocessor import UaProcessor

from instro.unstable.opcua import OpcUaClient, OpcUaError


@pytest.fixture
def notify(service, monkeypatch, run):
    async def send(*values, name="Temperature", timestamp=None, status=None):
        native = next(iter(service.subscriptions.values()))
        sequence = native._notification_seq
        acknowledged = asyncio.Event()
        publish = service.publish

        def witness(acks):
            if any(ack.SubscriptionId == native.data.SubscriptionId and ack.SequenceNumber == sequence for ack in acks):
                acknowledged.set()
            return publish(acks)

        with monkeypatch.context() as patch:
            patch.setattr(service, "publish", witness)
            if status is not None:
                await native.enqueue_statuschange(ua.StatusCode(status))
            else:
                item = next(
                    item
                    for item in native.monitored_item_srv._monitored_items.values()
                    if item.read_value_id.NodeId == ua.NodeId(name, 2)
                )
                for value in values:
                    data = ua.DataValue(ua.Variant(value), SourceTimestamp=timestamp, ServerTimestamp=None)
                    await native.enqueue_datachange_event(
                        item.monitored_item_id,
                        ua.MonitoredItemNotification(ClientHandle=item.client_handle, Value=data),
                        0,
                    )
            await native.publish_results()
            if status is None:
                await asyncio.wait_for(acknowledged.wait(), 2)

    return lambda *args, **kwargs: run(send(*args, **kwargs))


@pytest.fixture
def interrupt(run):
    async def schedule():
        asyncio.get_running_loop().call_later(0.001, signal.raise_signal, signal.SIGINT)

    return lambda: run(schedule())


@pytest.fixture
def read_replies(server, run, monkeypatch):
    waiting = None
    send = UaProcessor.send_response

    def intercept(processor, handle, sequence, response, msgtype=ua.MessageType.SecureMessage):
        if (
            processor.iserver is server.iserver
            and isinstance(response, ua.ReadResponse)
            and waiting is not None
            and not waiting.done()
        ):
            reply = (processor, handle, sequence, deepcopy(response), msgtype)
            waiting.set_result(reply)
        else:
            send(processor, handle, sequence, response, msgtype)

    async def receive():
        nonlocal waiting
        waiting = asyncio.get_running_loop().create_future()
        return await waiting

    async def release(reply):
        nonlocal waiting
        waiting = None
        send(*reply)

    monkeypatch.setattr(UaProcessor, "send_response", intercept)
    yield lambda: run(receive()), lambda reply: run(release(reply))
    if waiting is not None and waiting.done():
        run(release(waiting.result()))


def test_idle_python_retains_every_accepted_notification(client, node, service, notify):
    samples = client.subscribe([node], publishing_interval=0.001)
    assert next(samples)[1].value == 23.5
    notify(*(float(value) for value in range(80)))
    samples.close()
    samples.close()
    assert [value.value for _, value in samples] == list(range(80))
    assert service.subscriptions == {}
    assert list(samples) == []


def test_ready_poll_tick_precedes_queued_notification(client, node, service, notify, read_replies, run):
    receive, release = read_replies
    with client.subscribe([node], publishing_interval=0.001, poll_interval=0.01) as samples:
        assert next(samples)[1].value == 23.5
        reply = receive()
        reply[3].Results[0].Value = ua.Variant(31.0)
        reply[3].Results[0].SourceTimestamp = datetime.fromtimestamp(10, timezone.utc)
        notify(42.0, timestamp=datetime.fromtimestamp(1, timezone.utc))
        run(asyncio.sleep(0.025))
        release(reply)
        assert next(samples)[1].value == 31.0
        samples.close()
        assert 42.0 in [value.value for _, value in samples]


def test_matching_notification_suppresses_incomparable_poll_but_keeps_other_node(
    client, node, service, notify, read_replies
):
    receive, release = read_replies
    with client.subscribe([node, "/Objects/2:Rig/2:Float"], publishing_interval=0.001, poll_interval=0.05) as samples:
        for _ in range(2):
            next(samples)
        reply = receive()
        for value in reply[3].Results:
            value.SourceTimestamp = value.ServerTimestamp = None
        notify(42.0)
        release(reply)
        observed_node, value = next(samples)
        assert str(observed_node.node_id) == "ns=2;s=Temperature"
        assert value.value == 42.0
        samples.close()
        assert [value.value for node, value in samples if str(node.node_id) == "ns=2;s=Float"] == [1.25]


def test_failed_poll_retries_quiet_membership_then_success_resets_it(
    client, node, service, server, monkeypatch, read_replies, notify, run
):
    receive, release = read_replies
    read = Mock(wraps=server.iserver.attribute_service.read)
    monkeypatch.setattr(server.iserver.attribute_service, "read", read)
    with client.subscribe(
        [node, "/Objects/2:Rig/2:Float"],
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
        assert next(samples)[1].value.text == "reported-temp"
        failed = receive()
        failed[3].ResponseHeader.ServiceResult = ua.StatusCode(ua.StatusCodes.BadTimeout)
        release(failed)
        release(receive())
        release(receive())
        assert next(samples)[1].value.text == "Float"
    assert [{item.NodeId.Identifier for item in call.args[0].NodesToRead} for call in read.call_args_list[:3]] == [
        {"Float"},
        {"Float"},
        {"Temperature", "Float"},
    ]
    assert all(item.AttributeId == 4 for call in read.call_args_list for item in call.args[0].NodesToRead)


def test_interrupt_does_not_abandon_poll_or_accepted_notification(
    client, node, service, read_replies, notify, interrupt
):
    receive, release = read_replies
    with client.subscribe([node], publishing_interval=0.001, poll_interval=0.01) as samples:
        next(samples)
        reply = receive()
        reply[3].Results[0].Value = ua.Variant(41.0)
        reply[3].Results[0].SourceTimestamp = datetime.fromtimestamp(1, timezone.utc)

        interrupt()
        with pytest.raises(KeyboardInterrupt):
            next(samples)
        notify(42.0, timestamp=datetime.fromtimestamp(2, timezone.utc))
        release(reply)
        samples.close()
        assert [value.value for _, value in samples] == [41.0, 42.0]


@pytest.mark.parametrize("action", ["close", "interrupt_then_disconnect"])
def test_shutdown_finishes_admitted_read_and_retains_it(
    endpoint, connection_options, service, read_replies, interrupt, action
):
    receive, release = read_replies
    with OpcUaClient.connect(endpoint, **connection_options) as client:
        samples = client.subscribe(["/Objects/2:Rig/2:Temperature"], publishing_interval=0.001, poll_interval=0.01)
        next(samples)
        reply = receive()
        reply[3].Results[0].Value = ua.Variant(32.0)
        if action == "interrupt_then_disconnect":
            interrupt()
            with pytest.raises(KeyboardInterrupt):
                next(samples)
        with ThreadPoolExecutor(max_workers=1) as pool:
            stopping = pool.submit(samples.close if action == "close" else client.disconnect)
            release(reply)
            stopping.result(timeout=2)
        samples.close()
        assert [value.value for _, value in samples] == [32.0]
        if action == "close":
            assert service.subscriptions == {}


def test_failed_deletion_seals_notification_admission(client, node, service, monkeypatch, notify):
    samples = client.subscribe([node], publishing_interval=0.001)
    next(samples)
    notify(52.0)

    monkeypatch.setattr(
        service, "delete_subscriptions", AsyncMock(side_effect=ServiceError(ua.StatusCodes.BadInternalError))
    )
    with pytest.raises(OpcUaError) as raised:
        samples.close()
    assert raised.value.status_code.code == 0x80020000
    assert len(service.subscriptions) == 1
    notify(53.0)
    assert [value.value for _, value in samples] == [52.0]
    samples.close()
    assert list(samples) == []


def test_context_body_error_keeps_cleanup_error_as_context(client, node, service, monkeypatch):
    failure = LookupError("body")

    monkeypatch.setattr(
        service, "delete_subscriptions", AsyncMock(return_value=[ua.StatusCode(ua.StatusCodes.BadInternalError)])
    )
    with pytest.raises(LookupError) as raised:
        with client.subscribe([node], publishing_interval=0.001):
            raise failure
    assert raised.value is failure
    assert isinstance(failure.__context__, OpcUaError)
    assert failure.__context__.status_code.code == 0x80020000


def test_terminal_subscription_error_follows_retained_samples_once(client, node, service, notify):
    samples = client.subscribe([node], publishing_interval=0.001)
    next(samples)
    notify(71.0)
    notify(None, status=ua.StatusCodes.BadTimeout)
    assert next(samples)[1].value == 71.0
    with pytest.raises(OpcUaError) as raised:
        next(samples)
    assert raised.value.status_code.code == 0x800A0000
    assert list(samples) == []
    samples.close()


def test_last_subscription_owner_does_not_wait_for_native_deletion(client, node, service, monkeypatch, run):
    progress = asyncio.Queue()
    released = asyncio.Queue()
    delete = service.delete_subscriptions

    async def hold(ids):
        await progress.put("started")
        await released.get()
        result = await delete(ids)
        await progress.put("finished")
        return result

    monkeypatch.setattr(service, "delete_subscriptions", hold)
    samples = client.subscribe([node], publishing_interval=0.001)
    del samples
    try:
        run(progress.get())
    finally:
        run(released.put(None))
    run(progress.get())
    assert service.subscriptions == {}


def test_transport_failure_preserves_samples_before_terminal_error(
    endpoint, connection_options, server, service, notify, run, monkeypatch
):
    def reject_reconnect(*args, **kwargs):
        raise ServiceError(ua.StatusCodes.BadConnectionRejected)

    client = OpcUaClient.connect(
        endpoint, **(connection_options | {"session_retry_initial": 0.001, "session_retry_max": 0.001})
    )
    try:
        samples = client.subscribe(["/Objects/2:Rig/2:Temperature"], publishing_interval=0.001)
        next(samples)
        notify(61.0)

        async def sever():
            session_id = next(iter(service.subscriptions.values())).session_id
            peer = next(
                peer
                for peer in server.bserver.clients
                if peer.processor.session is not None and peer.processor.session.session_id == session_id
            )
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
        with suppress(OpcUaError):
            client.disconnect()


@pytest.mark.parametrize("period,hold", [(0.1, None), (0.01, 0.035)], ids=["first-period", "skip-missed-periods"])
def test_poll_schedule(client, node, service, server, monkeypatch, read_replies, run, period, hold):
    receive, release = read_replies
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
    with client.subscribe([node], publishing_enabled=False, publishing_interval=1, poll_interval=period):
        reply = receive()
        if hold is None:
            delay = admitted[0] - created[0]
            release(reply)
            assert delay >= 0.09
        else:
            run(asyncio.sleep(hold))
            release(reply)
            release(receive())
            started = monotonic()
            release(receive())
            release(receive())
            assert monotonic() - started >= 0.009
