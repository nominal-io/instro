"""Unit tests for the CAN transport (issues #395, #653): mocked python-can bus, plus python-can's virtual bus."""

import gc
import itertools
import time
import weakref
from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import can
import pytest

from instro.unstable.transports import CanConfig, CanFrame, CanTransport
from tests.unstable.can_fakes import ScriptedRecv, live_receive_threads, wait_until


def _frame(arbitration_id: int, data: bytes = b"\x00", extended: bool = True) -> can.Message:
    return can.Message(arbitration_id=arbitration_id, data=data, is_extended_id=extended)


@pytest.fixture
def prime_backend() -> Iterator[MagicMock]:
    with patch("instro.unstable.transports.can._prime_gs_usb_backend", autospec=True) as fn:
        yield fn


@pytest.fixture
def bus_cls(prime_backend: MagicMock) -> Iterator[MagicMock]:
    with patch("instro.unstable.transports.can.can.Bus", autospec=True) as cls:
        yield cls


@pytest.fixture
def rx() -> ScriptedRecv:
    return ScriptedRecv()


@pytest.fixture
def bus(bus_cls: MagicMock, rx: ScriptedRecv) -> MagicMock:
    instance = bus_cls.return_value
    instance.recv.side_effect = rx.recv
    return instance


@pytest.fixture
def transport(bus: MagicMock) -> Iterator[CanTransport]:
    transport = CanTransport(CanConfig(channel=0, interface="gs_usb"))
    yield transport
    transport.close()


@pytest.fixture(autouse=True)
def no_leaked_receive_threads() -> Iterator[None]:
    yield
    wait_until(lambda: not live_receive_threads())


def test_open_builds_bus_from_config_and_close_shuts_down(bus_cls: MagicMock, bus: MagicMock) -> None:
    transport = CanTransport(CanConfig(channel="COM4", interface="slcan", bitrate=250_000, bus_kwargs={"index": 1}))
    bus_cls.assert_not_called()

    transport.open()
    bus_cls.assert_called_once_with(interface="slcan", channel="COM4", bitrate=250_000, index=1)
    assert transport.is_open

    transport.close()
    bus.shutdown.assert_called_once_with()
    assert not transport.is_open


def test_shared_holders_open_once_and_teardown_on_last_close(
    transport: CanTransport, bus_cls: MagicMock, bus: MagicMock
) -> None:
    first, second = object(), object()

    assert transport.open(first) is True
    assert transport.open(second) is False
    bus_cls.assert_called_once()

    transport.close(first)
    bus.shutdown.assert_not_called()
    transport.close(second)
    bus.shutdown.assert_called_once_with()


def test_gs_usb_open_primes_the_libusb_backend(bus: MagicMock, prime_backend: MagicMock) -> None:
    gs_usb = CanTransport(CanConfig(channel=0, interface="gs_usb"))
    gs_usb.open()
    prime_backend.assert_called_once_with()
    gs_usb.close()

    prime_backend.reset_mock()
    slcan = CanTransport(CanConfig(channel="COM4", interface="slcan"))
    slcan.open()
    prime_backend.assert_not_called()
    slcan.close()


def test_gs_usb_close_releases_the_libusb_device(transport: CanTransport, bus: MagicMock) -> None:
    bus.gs_usb = MagicMock()
    transport.open()

    with patch("usb.util.dispose_resources", autospec=True) as dispose:
        transport.close()

    bus.shutdown.assert_called_once_with()
    dispose.assert_called_once_with(bus.gs_usb.gs_usb)


def test_gs_usb_open_failure_raises_a_teaching_error(bus_cls: MagicMock) -> None:
    bus_cls.side_effect = can.exceptions.CanInitializationError("Cannot find device 0. Devices found: 0")
    transport = CanTransport(CanConfig(channel=0, interface="gs_usb"))

    with pytest.raises(can.exceptions.CanInitializationError, match="candleLight") as excinfo:
        transport.open()
    assert isinstance(excinfo.value.__cause__, can.exceptions.CanInitializationError)


def test_send_before_open_raises(transport: CanTransport, bus: MagicMock) -> None:
    with pytest.raises(RuntimeError, match="not open"):
        transport.send(0x123, b"\x01")
    bus.send.assert_not_called()


def test_send_builds_wire_frame(transport: CanTransport, bus: MagicMock) -> None:
    transport.open()

    transport.send(0x966, b"\x01\x02", is_extended_id=True)

    frame = bus.send.call_args.args[0]
    assert frame.arbitration_id == 0x966
    assert bytes(frame.data) == b"\x01\x02"
    assert frame.is_extended_id


def test_received_frames_are_routed_to_all_matching_subscriptions(transport: CanTransport, rx: ScriptedRecv) -> None:
    transport.open()
    sub_a = transport.subscribe(lambda m: m.arbitration_id & 0xFF == 1)
    sub_b = transport.subscribe(lambda m: m.arbitration_id & 0xFF == 2)

    rx.feed(_frame(0x901), _frame(0x902), _frame(0x903))

    assert [f.arbitration_id for f in sub_a.drain()] == [0x901]
    # Draining sub_a never consumes sub_b's frame.
    assert [f.arbitration_id for f in sub_b.drain()] == [0x902]


def test_drain_hands_over_the_buffer(transport: CanTransport, rx: ScriptedRecv) -> None:
    transport.open()
    subscription = transport.subscribe(lambda m: True)
    rx.feed(_frame(0x901))

    assert len(subscription.drain()) == 1
    assert subscription.drain() == []


def test_full_subscription_drops_oldest_frames(transport: CanTransport, rx: ScriptedRecv) -> None:
    transport.open()
    subscription = transport.subscribe(lambda m: True, depth=2)
    rx.feed(_frame(1), _frame(2), _frame(3))

    assert [f.arbitration_id for f in subscription.drain()] == [2, 3]


def test_reopen_does_not_replay_frames_from_the_previous_session(transport: CanTransport, rx: ScriptedRecv) -> None:
    transport.open()
    keeper = transport.subscribe(lambda m: True)
    rx.feed(_frame(0x901))  # sits in keeper's buffer across the close

    transport.close()
    transport.open()

    assert keeper.drain() == []


def test_unsubscribed_subscription_no_longer_receives(transport: CanTransport, rx: ScriptedRecv) -> None:
    transport.open()
    dropped = transport.subscribe(lambda m: True)
    kept = transport.subscribe(lambda m: True)
    transport.unsubscribe(dropped)
    rx.feed(_frame(0x901))

    assert len(kept.drain()) == 1
    assert dropped.drain() == []


def test_drain_before_open_and_after_close_raises(transport: CanTransport) -> None:
    subscription = transport.subscribe(lambda m: True)
    with pytest.raises(RuntimeError, match="not open"):
        subscription.drain()
    transport.open()
    transport.close()
    with pytest.raises(RuntimeError, match="not open"):
        subscription.drain_frames()


def test_drain_frames_carries_the_message_and_host_arrival_time(transport: CanTransport, rx: ScriptedRecv) -> None:
    transport.open()
    subscription = transport.subscribe(lambda m: True)
    before = time.time_ns()
    rx.feed(_frame(0x901))
    after = time.time_ns()

    (frame,) = subscription.drain_frames()

    assert isinstance(frame, CanFrame)
    assert frame.message.arbitration_id == 0x901
    assert before <= frame.received_at_ns <= after


def test_receive_error_surfaces_on_every_drain_until_reopen(transport: CanTransport, rx: ScriptedRecv) -> None:
    transport.open()
    subscription = transport.subscribe(lambda m: True)
    thread = transport._rx_thread
    assert thread is not None

    rx.fail_with(can.CanOperationError("adapter unplugged"))
    thread.join(timeout=2.0)
    assert not thread.is_alive()

    for _ in range(2):
        with pytest.raises(can.CanOperationError, match="receive stopped") as excinfo:
            subscription.drain()
        assert "adapter unplugged" in str(excinfo.value.__cause__)

    transport.close()
    transport.open()
    rx.feed(_frame(0x901))
    assert len(subscription.drain()) == 1


def test_a_failing_filter_only_affects_its_own_subscription(transport: CanTransport, rx: ScriptedRecv) -> None:
    def broken(message: can.Message) -> bool:
        raise ValueError("bad filter")

    transport.open()
    bad = transport.subscribe(broken)
    good = transport.subscribe(lambda m: True)

    rx.feed(_frame(0x901), _frame(0x902))

    assert [f.arbitration_id for f in good.drain()] == [0x901, 0x902]
    with pytest.raises(ValueError, match="bad filter"):
        bad.drain()
    assert bad.drain() == []  # reported once, receiving carries on
    rx.feed(_frame(0x903))
    assert [f.arbitration_id for f in good.drain()] == [0x903]


def test_close_stops_the_receive_thread_before_shutting_the_bus_down(transport: CanTransport, bus: MagicMock) -> None:
    transport.open()
    thread = transport._rx_thread
    assert thread is not None and thread.is_alive()
    bus.shutdown.side_effect = lambda: alive_at_shutdown.append(thread.is_alive())
    alive_at_shutdown: list[bool] = []

    transport.close()

    assert alive_at_shutdown == [False]
    assert transport._rx_thread is None


def test_transport_dropped_without_close_is_collected_and_shuts_the_bus_down(bus: MagicMock) -> None:
    transport = CanTransport(CanConfig(channel="COM4", interface="slcan"))
    transport.open()
    thread = transport._rx_thread
    assert thread is not None
    ref = weakref.ref(transport)

    del transport
    gc.collect()

    assert ref() is None  # the receive thread does not keep the transport alive
    bus.shutdown.assert_called_once_with()
    thread.join(timeout=2.0)
    assert not thread.is_alive()


# --- python-can virtual bus: real receive semantics, no mocks -------------------------------------------

_channel_ids = itertools.count()


@pytest.fixture
def virtual_pair() -> Iterator[tuple[CanTransport, can.BusABC]]:
    channel = f"instro-test-{next(_channel_ids)}"
    transport = CanTransport(CanConfig(channel=channel, interface="virtual"))
    transport.open()
    peer = can.Bus(interface="virtual", channel=channel)
    try:
        yield transport, peer
    finally:
        peer.shutdown()
        transport.close()


def test_frames_are_stamped_when_they_arrive_not_when_drained(
    virtual_pair: tuple[CanTransport, can.BusABC],
) -> None:
    transport, peer = virtual_pair
    subscription = transport.subscribe(lambda m: True)

    sent_at = time.time_ns()
    peer.send(_frame(0x901))
    wait_until(lambda: len(subscription._frames) == 1)
    arrived_by = time.time_ns()
    time.sleep(0.2)  # a slow consumer
    drained_at = time.time_ns()
    (frame,) = subscription.drain_frames()

    assert sent_at <= frame.received_at_ns <= arrived_by
    assert drained_at - frame.received_at_ns >= 200_000_000


def test_frames_drained_together_keep_their_own_arrival_times(
    virtual_pair: tuple[CanTransport, can.BusABC],
) -> None:
    transport, peer = virtual_pair
    subscription = transport.subscribe(lambda m: True)

    peer.send(_frame(0x901))
    wait_until(lambda: len(subscription._frames) == 1)
    time.sleep(0.03)
    peer.send(_frame(0x902))
    wait_until(lambda: len(subscription._frames) == 2)
    first, second = subscription.drain_frames()

    assert [first.message.arbitration_id, second.message.arbitration_id] == [0x901, 0x902]
    assert second.received_at_ns - first.received_at_ns >= 25_000_000


def test_bus_is_serviced_between_drains_and_overflow_drops_the_oldest(
    virtual_pair: tuple[CanTransport, can.BusABC],
) -> None:
    transport, peer = virtual_pair
    subscription = transport.subscribe(lambda m: True, depth=512)

    for i in range(600):
        peer.send(_frame(i))
    wait_until(lambda: bool(subscription._frames) and subscription._frames[-1].message.arbitration_id == 599)

    ids = [m.arbitration_id for m in subscription.drain()]
    assert ids == list(range(88, 600))
