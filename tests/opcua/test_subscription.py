import asyncio
import signal
from collections.abc import Coroutine, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from queue import Queue
from threading import Thread
from typing import Any, TypeVar, cast

import pytest
from asyncua import ua
from asyncua.common.callback import CallbackType
from asyncua.common.utils import ServiceError
from asyncua.server.server import Server

from instro.unstable.opcua import OpcUaClient, OpcUaError
from tests.opcua.server import populate, timestamp

T = TypeVar("T")


@dataclass
class Read:
    nodes: list[str]
    attributes: list[int]
    future: asyncio.Future[Any]
    loop: asyncio.AbstractEventLoop

    def reply(self, result: list[ua.DataValue] | Exception) -> None:
        if isinstance(result, Exception):
            self.loop.call_soon_threadsafe(self.future.set_exception, result)
        else:
            self.loop.call_soon_threadsafe(self.future.set_result, result)


class Peer:
    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.reads: Queue[Read] = Queue()
        self.control_reads = False
        self.overrides: dict[int, asyncio.Future[Any]] = {}
        self.acknowledgements: dict[int, asyncio.Event] = {}
        self.items: dict[str, tuple[int, int]] = {}
        self.subscription_request: Any = None
        self.item_request: Any = None
        self.server = self.call(self.start())

    def call(self, operation: Coroutine[Any, Any, T]) -> T:
        return asyncio.run_coroutine_threadsafe(operation, self.loop).result(8)

    async def start(self) -> Server:
        server = Server()
        await server.init()
        server.set_endpoint("opc.tcp://127.0.0.1:0/subscription-test/")
        server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
        await populate(server)
        service: Any = server.iserver.subscription_service
        create, publish = service.create_monitored_items, service.publish
        create_subscription = service.create_subscription

        async def subscription(params: Any, *args: Any, **kwargs: Any) -> Any:
            self.subscription_request = params
            return await create_subscription(params, *args, **kwargs)

        async def create_items(params: Any) -> Any:
            self.item_request = params
            native = service.subscriptions[params.SubscriptionId]
            enabled, native._publishing_enabled = native._publishing_enabled, False
            results = await create(params)
            self.items = {
                request.ItemToMonitor.NodeId.Identifier: (
                    result.MonitoredItemId,
                    request.RequestedParameters.ClientHandle,
                )
                for request, result in zip(params.ItemsToCreate, results, strict=True)
            }
            native._triggered_datachanges.clear()
            native._publishing_enabled = enabled
            return results

        def acknowledge(acks: Any) -> Any:
            # Native acknowledgement is queued under the same lock as callback delivery.
            for ack in acks:
                if event := self.acknowledgements.pop(ack.SequenceNumber, None):
                    event.set()
            return publish(acks)

        async def before_read(event: Any, _: Any) -> None:
            requests = event.request_params.NodesToRead
            monitored = all(request.NodeId.Identifier in self.items for request in requests)
            if self.control_reads and event.is_external and monitored:
                future = self.loop.create_future()
                nodes = [request.NodeId.Identifier for request in requests]
                attributes = [request.AttributeId for request in requests]
                self.overrides[id(event.request_params)] = future
                self.reads.put(Read(nodes, attributes, future, self.loop))
                await future

        async def after_read(event: Any, _: Any) -> None:
            if future := self.overrides.pop(id(event.request_params), None):
                event.response_params[:] = future.result()

        service.create_subscription = subscription
        service.create_monitored_items = create_items
        service.publish = acknowledge
        server.subscribe_server_callback(CallbackType.PreRead, before_read)
        server.subscribe_server_callback(CallbackType.PostRead, after_read)
        await server.start()
        return server

    @property
    def endpoint(self) -> str:
        native_server: Any = self.server.bserver
        return f"opc.tcp://127.0.0.1:{native_server.port}/subscription-test/"

    async def send(self, samples: list[tuple[str, ua.DataValue]]) -> asyncio.Event:
        subscription = next(iter(self.server.iserver.subscription_service.subscriptions.values()))
        sequence = subscription._notification_seq
        acknowledged = self.acknowledgements[sequence] = asyncio.Event()
        for name, value in samples:
            monitored_id, handle = self.items[name]
            notification = ua.MonitoredItemNotification(ClientHandle=handle, Value=value)
            await subscription.enqueue_datachange_event(monitored_id, notification, 0)
        await subscription.publish_results()
        return acknowledged

    async def emit(self, samples: list[tuple[str, ua.DataValue]]) -> None:
        acknowledged = await self.send(samples)
        await asyncio.wait_for(acknowledged.wait(), 5)

    async def release_reads(self) -> None:
        self.control_reads = False
        for future in self.overrides.values():
            if not future.done():
                future.set_exception(ServiceError(ua.StatusCodes.BadRequestCancelledByClient))


@pytest.fixture
def peer() -> Iterator[Peer]:
    peer = Peer()
    try:
        yield peer
    finally:
        peer.call(peer.server.stop())
        peer.loop.call_soon_threadsafe(peer.loop.stop)
        peer.thread.join(5)
        peer.loop.close()


@pytest.fixture
def subscriber(peer: Peer) -> Iterator[OpcUaClient]:
    with OpcUaClient.connect(peer.endpoint, request_timeout=5, session_retry_limit=0) as client:
        try:
            yield client
        finally:
            peer.call(peer.release_reads())


def data(
    value: float,
    source: int | None = None,
    server: int | None = None,
    fraction: int | None = None,
    server_fraction: int | None = None,
) -> ua.DataValue:
    return ua.DataValue(
        ua.Variant(value),
        SourceTimestamp=None if source is None else timestamp(source),
        ServerTimestamp=None if server is None else timestamp(server),
        SourcePicoseconds=None if fraction is None else ua.UInt16(fraction),
        ServerPicoseconds=None if server_fraction is None else ua.UInt16(server_fraction),
    )


def test_idle_consumer_retains_admitted_notifications_after_close(peer: Peer, subscriber: OpcUaClient) -> None:
    subscription = subscriber.subscribe(["/Objects/2:Rig/2:Temperature"], publishing_interval=0.01)
    peer.call(peer.emit([("Temperature", data(float(value))) for value in range(300)]))
    subscription.close()
    subscription.close()

    assert [sample.value for _, sample in subscription] == list(range(300))
    assert list(subscription) == []


def test_pending_poll_merge_uses_either_timestamp_and_fraction(peer: Peer, subscriber: OpcUaClient) -> None:
    names = ["Temperature", "Float", "Int64", "UInt64", "SourceTimestamped", "ServerTimestamped", "NoTimestamp"]
    nodes = [subscriber.get_node(f"/Objects/2:Rig/2:{name}") for name in names]
    peer.control_reads = True
    subscription = subscriber.subscribe(nodes, publishing_interval=0.01, poll_interval=1)
    read = peer.reads.get(timeout=5)
    polls = {
        "Temperature": data(1.0, source=1, server=9),
        "Float": data(2.0, source=9, server=1),
        "Int64": data(3.0, source=1, fraction=1),
        "UInt64": data(4.0, source=2, server=2),
        "SourceTimestamped": data(5.0),
        "ServerTimestamped": data(6.0, source=3, server=3),
        "NoTimestamp": data(7.0, server=1, server_fraction=9),
    }
    notifications = [
        ("Temperature", data(11.0, source=2, server=8)),
        ("Float", data(12.0, source=8, server=2)),
        ("Int64", data(13.0, source=1, fraction=2)),
        ("UInt64", data(14.0, source=1, server=1)),
        ("SourceTimestamped", data(15.0)),
        ("ServerTimestamped", data(16.0, source=3, server=3)),
        ("NoTimestamp", data(17.0, server=1, server_fraction=10)),
    ]
    acknowledged = peer.call(peer.send(notifications))
    read.reply([polls[name] for name in read.nodes])
    peer.call(asyncio.wait_for(acknowledged.wait(), 5))
    subscription.close()
    received: dict[str, list[Any]] = {name: [] for name in names}
    for node, sample in subscription:
        received[str(node.node_id.identifier)].append(sample.value)

    assert received == {
        "Temperature": [1.0, 11.0],
        "Float": [2.0, 12.0],
        "Int64": [3.0, 13.0],
        "UInt64": [14.0],
        "SourceTimestamped": [15.0],
        "ServerTimestamped": [16.0],
        "NoTimestamp": [7.0, 17.0],
    }


def test_interrupt_preserves_wait_and_admitted_read(peer: Peer, subscriber: OpcUaClient) -> None:
    peer.control_reads = True
    subscription = subscriber.subscribe(["/Objects/2:Rig/2:Temperature"], poll_interval=0.1)

    def interrupt() -> Read:
        read = peer.reads.get(timeout=5)
        signal.raise_signal(signal.SIGINT)
        return read

    with ThreadPoolExecutor(1) as workers:
        interrupted = workers.submit(interrupt)
        with pytest.raises(KeyboardInterrupt):
            next(subscription)
        read = interrupted.result(5)
    acknowledged = peer.call(peer.send([("Temperature", data(42.0))]))
    read.reply([data(41.0, source=1)])
    peer.call(asyncio.wait_for(acknowledged.wait(), 5))
    subscription.close()

    assert [sample.value for _, sample in subscription] == [42.0]


def test_interrupt_then_disconnect_drains_without_resuming_next(peer: Peer, subscriber: OpcUaClient) -> None:
    peer.control_reads = True
    subscription = subscriber.subscribe(["/Objects/2:Rig/2:Temperature"], poll_interval=0.1)

    def interrupt() -> Read:
        read = peer.reads.get(timeout=5)
        signal.raise_signal(signal.SIGINT)
        return read

    with ThreadPoolExecutor(1) as workers:
        interrupted = workers.submit(interrupt)
        with pytest.raises(KeyboardInterrupt):
            next(subscription)
        read = interrupted.result(5)
        disconnecting = workers.submit(subscriber.disconnect)
        read.reply([data(32.0)])
        disconnecting.result(5)
    subscription.close()

    assert [sample.value for _, sample in subscription] == [32.0]


def test_setup_rolls_back_and_reports_item_errors_in_request_order(peer: Peer, subscriber: OpcUaClient) -> None:
    requested = ["/Objects/2:Rig/2:Temperature", "/Objects/2:Rig", "/Objects/2:Rig/2:Mode"]
    with pytest.raises(ExceptionGroup) as failure:
        subscriber.subscribe(iter(requested))

    assert [cast(OpcUaError, error).status_code.name() for error in failure.value.exceptions] == [
        "BadAttributeIdInvalid",
        "BadAttributeIdInvalid",
    ]
    assert [cast(Any, error).node.node_id.identifier for error in failure.value.exceptions] == ["rig", "Mode"]
    assert not peer.server.iserver.subscription_service.subscriptions


def test_iterable_validation_precedes_resource_allocation(peer: Peer, subscriber: OpcUaClient) -> None:
    node = subscriber.get_node("/Objects/2:Rig/2:Temperature")
    with OpcUaClient.connect(peer.endpoint) as other:
        foreign = other.get_node("/Objects/2:Rig/2:Temperature")
        invalid: list[list[Any]] = [[], [node, node.browse_path.relative_path], [foreign]]
        for inputs in invalid:
            with pytest.raises(ValueError):
                subscriber.subscribe(iter(inputs))

    def broken() -> Iterator[str]:
        yield "/Objects/2:Rig/2:Temperature"
        raise LookupError("iteration failed")

    with pytest.raises(LookupError, match="iteration failed"):
        subscriber.subscribe(broken())
    assert not peer.server.iserver.subscription_service.subscriptions


@pytest.mark.parametrize("body_fails", [False, True])
def test_deletion_failure_is_reported_only_by_close(
    peer: Peer, subscriber: OpcUaClient, body_fails: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def reject_deletion() -> None:
        async def delete(_: Any) -> Any:
            raise ServiceError(ua.StatusCodes.BadInternalError)

        service: Any = peer.server.iserver.subscription_service
        monkeypatch.setattr(service, "delete_subscriptions", delete)

    subscription = subscriber.subscribe(["/Objects/2:Rig/2:Temperature"], publishing_interval=0.01)
    peer.call(peer.emit([("Temperature", data(52.0))]))
    peer.call(reject_deletion())
    body_error = LookupError("body failed")
    with pytest.raises(LookupError if body_fails else OpcUaError) as failure:
        with subscription:
            if body_fails:
                raise body_error
    cleanup_error = failure.value.__context__ if body_fails else failure.value

    assert isinstance(cleanup_error, OpcUaError)
    assert cleanup_error.status_code.name() == "BadInternalError"
    peer.call(peer.emit([("Temperature", data(53.0))]))
    assert [sample.value for _, sample in subscription] == [52.0]


def test_read_failure_preserves_quiet_membership(peer: Peer, subscriber: OpcUaClient) -> None:
    peer.control_reads = True
    subscription = subscriber.subscribe(
        ["/Objects/2:Rig/2:Temperature", "/Objects/2:Rig/2:Float"], publishing_interval=0.01, poll_interval=0.3
    )
    initial = peer.reads.get(timeout=5)
    acknowledged = peer.call(peer.send([("Temperature", data(10.0))]))
    initial.reply([data(0.0) for _ in initial.nodes])
    peer.call(asyncio.wait_for(acknowledged.wait(), 5))
    failed = peer.reads.get(timeout=5)
    assert failed.nodes == ["Float"]
    failed.reply(ServiceError(ua.StatusCodes.BadCommunicationError))
    retry = peer.reads.get(timeout=5)
    assert retry.nodes == ["Float"]
    retry.reply([data(20.0)])
    reset = peer.reads.get(timeout=5)
    assert set(reset.nodes) == {"Temperature", "Float"}
    reset.reply([data(30.0) for _ in reset.nodes])
    subscription.close()


def test_finalization_deletes_native_subscription(peer: Peer, subscriber: OpcUaClient) -> None:
    async def observe_deletion() -> asyncio.Event:
        deleted = asyncio.Event()
        service: Any = peer.server.iserver.subscription_service
        original = service.delete_subscriptions

        async def delete(ids: Any) -> Any:
            result = await original(ids)
            deleted.set()
            return result

        service.delete_subscriptions = delete
        return deleted

    deleted = peer.call(observe_deletion())
    subscription = subscriber.subscribe(["/Objects/2:Rig/2:Temperature"])
    del subscription
    peer.call(asyncio.wait_for(deleted.wait(), 5))


def test_native_terminal_error_follows_retained_samples_once(peer: Peer, subscriber: OpcUaClient) -> None:
    subscription = subscriber.subscribe(["/Objects/2:Rig/2:Temperature"], publishing_interval=0.01)
    peer.call(peer.emit([("Temperature", data(61.0))]))

    async def sever_connection() -> None:
        native_server: Any = peer.server.bserver
        listener = native_server._server
        listener.close()
        for transport in peer.server.iserver.asyncio_transports:
            transport.close()
        await listener.wait_closed()

    peer.call(sever_connection())
    assert next(subscription)[1].value == 61.0
    with pytest.raises(OpcUaError) as failure:
        next(subscription)
    assert failure.value.status_code.is_bad
    assert list(subscription) == []
    subscription.close()
    try:
        subscriber.disconnect()
    except OpcUaError:
        pass


@pytest.mark.parametrize(
    "setting, value, error",
    [
        ("poll_interval", 0, ValueError),
        ("poll_interval", -1, ValueError),
        ("sampling_interval", float("inf"), ValueError),
        ("sampling_interval", float("nan"), ValueError),
        ("priority", 256, ValueError),
        ("queue_size", -1, ValueError),
        ("priority", 1.5, TypeError),
        ("publishing_interval", "1", TypeError),
    ],
)
def test_invalid_settings_allocate_no_subscription(
    peer: Peer, subscriber: OpcUaClient, setting: str, value: Any, error: type[Exception]
) -> None:
    with pytest.raises(error):
        subscriber.subscribe(["/Objects/2:Rig/2:Temperature"], **{setting: value})
    assert not peer.server.iserver.subscription_service.subscriptions


def test_native_settings_and_selected_attribute_use_declared_units(peer: Peer, subscriber: OpcUaClient) -> None:
    from datetime import timedelta

    from instro.unstable.opcua.types import LocalizedText

    peer.control_reads = True
    subscription = subscriber.subscribe(
        ["/Objects/2:Rig/2:Temperature"],
        attribute="DISPLAY_NAME",
        publishing_interval=timedelta(milliseconds=125),
        lifetime_count=30,
        max_keep_alive_count=5,
        max_notifications_per_publish=7,
        priority=3,
        publishing_enabled=False,
        sampling_interval=-0.25,
        queue_size=4,
        discard_oldest=True,
        poll_interval=0.01,
    )
    request = peer.subscription_request
    assert (
        request.RequestedPublishingInterval,
        request.RequestedLifetimeCount,
        request.RequestedMaxKeepAliveCount,
        request.MaxNotificationsPerPublish,
        request.Priority,
        request.PublishingEnabled,
    ) == (125.0, 30, 5, 7, 3, False)
    item = peer.item_request.ItemsToCreate[0]
    assert (
        item.ItemToMonitor.AttributeId,
        item.RequestedParameters.SamplingInterval,
        item.RequestedParameters.QueueSize,
        item.RequestedParameters.DiscardOldest,
    ) == (ua.AttributeIds.DisplayName, -0.25, 4, True)
    read = peer.reads.get(timeout=5)
    read.reply([ua.DataValue(ua.Variant(ua.LocalizedText("ready", "en")))])
    subscription.close()
    _, sample = next(subscription)

    assert read.attributes == [ua.AttributeIds.DisplayName]
    assert sample.value == LocalizedText("ready", "en")


def test_bad_native_subscription_status_preserves_code_after_data(peer: Peer, subscriber: OpcUaClient) -> None:
    subscription = subscriber.subscribe(["/Objects/2:Rig/2:Temperature"], publishing_interval=0.01)
    peer.call(peer.emit([("Temperature", data(71.0))]))

    async def expire_subscription() -> None:
        native = next(iter(peer.server.iserver.subscription_service.subscriptions.values()))
        await native.monitored_item_srv.trigger_statuschange(ua.StatusCode(ua.UInt32(ua.StatusCodes.BadTimeout)))
        await native.publish_results()

    peer.call(expire_subscription())
    assert next(subscription)[1].value == 71.0
    with pytest.raises(OpcUaError) as failure:
        next(subscription)
    assert failure.value.status_code.name() == "BadTimeout"
    assert list(subscription) == []
    subscription.close()


def test_setup_service_failure_survives_failed_rollback(
    peer: Peer, subscriber: OpcUaClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts: list[str] = []

    async def reject_setup_and_cleanup() -> None:
        service: Any = peer.server.iserver.subscription_service

        async def create(_: Any) -> Any:
            attempts.append("create")
            raise ServiceError(ua.StatusCodes.BadTooManyMonitoredItems)

        async def delete(_: Any) -> Any:
            attempts.append("delete")
            raise ServiceError(ua.StatusCodes.BadInternalError)

        monkeypatch.setattr(service, "create_monitored_items", create)
        monkeypatch.setattr(service, "delete_subscriptions", delete)

    peer.call(reject_setup_and_cleanup())
    with pytest.raises(OpcUaError) as failure:
        subscriber.subscribe(["/Objects/2:Rig/2:Temperature"])

    assert failure.value.status_code.name() == "BadTooManyMonitoredItems"
    assert attempts == ["create", "delete"]
