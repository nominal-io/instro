"""Unit tests for the CAN transport (issue #395), mocked python-can bus."""

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import can
import pytest

from instro.unstable.transports import CanConfig, CanDriver


def _frame(arbitration_id: int, data: bytes = b"\x00", extended: bool = True) -> can.Message:
    return can.Message(arbitration_id=arbitration_id, data=data, is_extended_id=extended)


@pytest.fixture
def bus_cls() -> Iterator[MagicMock]:
    with patch("instro.unstable.transports.can.can.Bus", autospec=True) as cls:
        yield cls


@pytest.fixture
def bus(bus_cls: MagicMock) -> MagicMock:
    instance = bus_cls.return_value
    instance.recv.return_value = None
    return instance


@pytest.fixture
def transport(bus: MagicMock) -> CanDriver:
    return CanDriver(CanConfig(channel=0, interface="gs_usb"))


def test_open_builds_bus_from_config_and_close_shuts_down(bus_cls: MagicMock, bus: MagicMock) -> None:
    transport = CanDriver(CanConfig(channel="COM4", interface="slcan", bitrate=250_000, bus_kwargs={"index": 1}))
    bus_cls.assert_not_called()

    transport.open()
    bus_cls.assert_called_once_with(interface="slcan", channel="COM4", bitrate=250_000, index=1)
    assert transport.is_open

    transport.close()
    bus.shutdown.assert_called_once_with()
    assert not transport.is_open


def test_shared_holders_open_once_and_teardown_on_last_close(
    transport: CanDriver, bus_cls: MagicMock, bus: MagicMock
) -> None:
    first, second = object(), object()

    assert transport.open(first) is True
    assert transport.open(second) is False
    bus_cls.assert_called_once()

    transport.close(first)
    bus.shutdown.assert_not_called()
    transport.close(second)
    bus.shutdown.assert_called_once_with()


def test_send_before_open_raises(transport: CanDriver, bus: MagicMock) -> None:
    with pytest.raises(RuntimeError, match="not open"):
        transport.send(0x123, b"\x01")
    bus.send.assert_not_called()


def test_send_builds_wire_frame(transport: CanDriver, bus: MagicMock) -> None:
    transport.open()

    transport.send(0x966, b"\x01\x02", is_extended_id=True)

    frame = bus.send.call_args.args[0]
    assert frame.arbitration_id == 0x966
    assert bytes(frame.data) == b"\x01\x02"
    assert frame.is_extended_id


def test_drain_routes_frames_to_all_matching_subscriptions(transport: CanDriver, bus: MagicMock) -> None:
    transport.open()
    sub_a = transport.subscribe(lambda m: m.arbitration_id & 0xFF == 1)
    sub_b = transport.subscribe(lambda m: m.arbitration_id & 0xFF == 2)
    bus.recv.side_effect = [_frame(0x901), _frame(0x902), _frame(0x903), None, None]

    frames_a = sub_a.drain()

    assert [f.arbitration_id for f in frames_a] == [0x901]
    # sub_b's frame was routed during sub_a's drain, not consumed; the bus has nothing further.
    assert [f.arbitration_id for f in sub_b.drain()] == [0x902]


def test_drain_hands_over_the_buffer(transport: CanDriver, bus: MagicMock) -> None:
    transport.open()
    subscription = transport.subscribe(lambda m: True)
    bus.recv.side_effect = [_frame(0x901), None, None]

    assert len(subscription.drain()) == 1
    assert subscription.drain() == []


def test_full_subscription_drops_oldest_frames(transport: CanDriver, bus: MagicMock) -> None:
    transport.open()
    subscription = transport.subscribe(lambda m: True, depth=2)
    bus.recv.side_effect = [_frame(1), _frame(2), _frame(3), None]

    assert [f.arbitration_id for f in subscription.drain()] == [2, 3]


def test_reopen_does_not_replay_frames_from_the_previous_session(transport: CanDriver, bus: MagicMock) -> None:
    transport.open()
    keeper = transport.subscribe(lambda m: True)
    router = transport.subscribe(lambda m: True)
    bus.recv.side_effect = [_frame(0x901), None, None]
    router.drain()  # routes the frame into keeper's buffer, where it sits across the close

    transport.close()
    transport.open()

    assert keeper.drain() == []


def test_unsubscribed_subscription_no_longer_receives(transport: CanDriver, bus: MagicMock) -> None:
    transport.open()
    dropped = transport.subscribe(lambda m: True)
    kept = transport.subscribe(lambda m: True)
    transport.unsubscribe(dropped)
    bus.recv.side_effect = [_frame(0x901), None, None]

    assert len(kept.drain()) == 1
    assert dropped.drain() == []
