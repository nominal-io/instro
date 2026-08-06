"""Unit tests for the VESC 6 motor controller driver (issue #362), mocked python-can bus."""

import struct
from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import can
import pytest

from instro.lib.publishers import Publisher
from instro.lib.types import Command, Measurement
from instro.unstable.motorcontroller.drivers import VESC6

_CONTROLLER_ID = 42
_HOST_ID = 254


def _status_frame(packet_id: int, payload: bytes, controller_id: int = _CONTROLLER_ID) -> can.Message:
    return can.Message(arbitration_id=(packet_id << 8) | controller_id, data=payload, is_extended_id=True)


@pytest.fixture
def bus_cls() -> Iterator[MagicMock]:
    with patch("instro.unstable.motorcontroller.drivers.vesc_6.can.Bus", autospec=True) as cls:
        yield cls


@pytest.fixture
def bus(bus_cls: MagicMock) -> MagicMock:
    instance = bus_cls.return_value
    instance.recv.return_value = None
    return instance


@pytest.fixture
def vesc(bus: MagicMock) -> Iterator[VESC6]:
    motor = VESC6(channel="COM4", controller_id=_CONTROLLER_ID, interface="slcan", name="m")
    motor.open()
    yield motor
    motor.close()


def _sent_frames(bus: MagicMock) -> list[can.Message]:
    return [c.args[0] for c in bus.send.call_args_list]


def test_open_builds_bus_and_close_shuts_down(bus_cls: MagicMock, bus: MagicMock) -> None:
    motor = VESC6(channel="COM4", controller_id=_CONTROLLER_ID, interface="slcan", bitrate=250_000, name="m")
    bus_cls.assert_not_called()

    motor.open()
    bus_cls.assert_called_once_with(interface="slcan", channel="COM4", bitrate=250_000)

    motor.close()
    bus.shutdown.assert_called_once_with()


def test_bus_kwargs_forwarded(bus_cls: MagicMock, bus: MagicMock) -> None:
    motor = VESC6(channel=0, interface="gs_usb", bus_kwargs={"index": 0}, name="m")
    motor.open()
    bus_cls.assert_called_once_with(interface="gs_usb", channel=0, bitrate=500_000, index=0)


def test_send_before_open_raises(bus_cls: MagicMock) -> None:
    motor = VESC6(channel="COM4", name="m")
    with pytest.raises(RuntimeError, match="not open"):
        motor.set_duty(0.1)


@pytest.mark.parametrize("controller_id", [-1, 256])
def test_init_rejects_out_of_range_controller_id(bus_cls: MagicMock, controller_id: int) -> None:
    with pytest.raises(ValueError, match="controller_id"):
        VESC6(channel="COM4", controller_id=controller_id, name="m")


@pytest.mark.parametrize(
    ("call", "packet_id", "raw"),
    [
        (lambda m: m.set_duty(0.5), 0, 50_000),
        (lambda m: m.set_current(-7.5), 1, -7_500),
        (lambda m: m.set_brake_current(3.0), 2, 3_000),
        (lambda m: m.set_rpm(12_000), 3, 12_000),
        (lambda m: m.set_position(180.0), 4, 180_000_000),
        (lambda m: m.set_relative_current(-0.25), 10, -25_000),
        (lambda m: m.set_relative_brake_current(0.4), 11, 40_000),
        (lambda m: m.stop_motor(), 1, 0),
    ],
)
def test_commands_send_expected_wire_frames(vesc: VESC6, bus: MagicMock, call, packet_id: int, raw: int) -> None:
    call(vesc)

    frame = _sent_frames(bus)[-1]
    assert frame.is_extended_id
    assert frame.arbitration_id == (packet_id << 8) | _CONTROLLER_ID
    assert bytes(frame.data) == struct.pack(">i", raw)


@pytest.mark.parametrize(
    "call",
    [
        lambda m: m.set_duty(1.5),
        lambda m: m.set_brake_current(-1.0),
        lambda m: m.set_position(361.0),
        lambda m: m.set_relative_current(-1.1),
        lambda m: m.set_relative_brake_current(-0.1),
    ],
)
def test_commands_reject_out_of_range_values(vesc: VESC6, bus: MagicMock, call) -> None:
    with pytest.raises(ValueError):
        call(vesc)
    bus.send.assert_not_called()


def test_get_telemetry_parses_status_frames(vesc: VESC6, bus: MagicMock) -> None:
    bus.recv.side_effect = [
        _status_frame(9, struct.pack(">ihh", 12_345, 155, 500)),
        _status_frame(16, struct.pack(">hhhh", 421, 380, 92, 9_000)),
        _status_frame(27, struct.pack(">ihh", 6_000, 244, 0)),
        None,
    ]

    telemetry = vesc.get_telemetry()

    assert telemetry is not None
    data = {k: v[0] for k, v in telemetry.channel_data.items()}
    assert data["m.erpm"] == pytest.approx(12_345)
    assert data["m.motor_current"] == pytest.approx(15.5)
    assert data["m.duty"] == pytest.approx(0.5)
    assert data["m.fet_temperature"] == pytest.approx(42.1)
    assert data["m.motor_temperature"] == pytest.approx(38.0)
    assert data["m.input_current"] == pytest.approx(9.2)
    assert data["m.pid_position"] == pytest.approx(180.0)
    assert data["m.tachometer"] == pytest.approx(6_000)
    assert data["m.input_voltage"] == pytest.approx(24.4)


def test_get_telemetry_ignores_other_senders_and_unknown_packets(vesc: VESC6, bus: MagicMock) -> None:
    bus.recv.side_effect = [
        _status_frame(9, struct.pack(">ihh", 999, 10, 10), controller_id=7),
        _status_frame(5, b"\x00" * 8),  # FILL_RX_BUFFER (fragmented COMM_* protocol)
        None,
    ]

    assert vesc.get_telemetry() is None


def test_ping_detects_pong(vesc: VESC6, bus: MagicMock) -> None:
    bus.recv.side_effect = [
        can.Message(arbitration_id=(18 << 8) | _HOST_ID, data=bytes([_CONTROLLER_ID, 0]), is_extended_id=True),
    ]

    result = vesc.ping(timeout=0.2)

    ping_frame = _sent_frames(bus)[-1]
    assert ping_frame.arbitration_id == (17 << 8) | _CONTROLLER_ID
    assert bytes(ping_frame.data) == bytes([_HOST_ID])
    assert result.latest == pytest.approx(1.0)


def test_ping_times_out_without_pong(vesc: VESC6, bus: MagicMock) -> None:
    result = vesc.ping(timeout=0.05)

    assert result.latest == pytest.approx(0.0)


def test_commands_and_telemetry_publish(bus_cls: MagicMock, bus: MagicMock) -> None:
    publisher = MagicMock(spec=Publisher)
    motor = VESC6(channel="COM4", controller_id=_CONTROLLER_ID, name="m", publishers=[publisher])
    motor.open()

    motor.set_duty(0.1)
    bus.recv.side_effect = [_status_frame(9, struct.pack(">ihh", 100, 10, 10)), None]
    motor.get_telemetry()
    motor.close()

    published = [c.args[0] for c in publisher.publish.call_args_list]
    assert any(isinstance(p, Command) for p in published)
    assert any(isinstance(p, Measurement) for p in published)
