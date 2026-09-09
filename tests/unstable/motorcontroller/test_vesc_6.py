"""Unit tests for the VESC 6 motor controller driver (issue #362), mocked python-can bus."""

import struct
from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import can
import pytest

from instro.unstable.motorcontroller.drivers import VESC6
from instro.unstable.transports import CanConfig, CanTransport

_CONTROLLER_ID = 42


def _status_frame(packet_id: int, payload: bytes, controller_id: int = _CONTROLLER_ID) -> can.Message:
    return can.Message(arbitration_id=(packet_id << 8) | controller_id, data=payload, is_extended_id=True)


@pytest.fixture
def bus_cls() -> Iterator[MagicMock]:
    with (
        patch("instro.unstable.transports.can._prime_gs_usb_backend", autospec=True),
        patch("instro.unstable.transports.can.can.Bus", autospec=True) as cls,
    ):
        yield cls


@pytest.fixture
def bus(bus_cls: MagicMock) -> MagicMock:
    instance = bus_cls.return_value
    instance.recv.return_value = None
    return instance


@pytest.fixture
def vesc(bus: MagicMock) -> Iterator[VESC6]:
    driver = VESC6(channel="COM4", pole_pairs=1, controller_id=_CONTROLLER_ID, interface="slcan")
    driver.open()
    yield driver
    driver.close()


def _sent_frames(bus: MagicMock) -> list[can.Message]:
    return [c.args[0] for c in bus.send.call_args_list]


def test_open_builds_bus_and_close_safe_stops_and_shuts_down(bus_cls: MagicMock, bus: MagicMock) -> None:
    driver = VESC6(channel="COM4", pole_pairs=1, controller_id=_CONTROLLER_ID, interface="slcan", bitrate=250_000)
    bus_cls.assert_not_called()

    driver.open()
    bus_cls.assert_called_once_with(interface="slcan", channel="COM4", bitrate=250_000)

    driver.close()
    stop_frame = _sent_frames(bus)[-1]
    assert stop_frame.arbitration_id == (1 << 8) | _CONTROLLER_ID
    assert bytes(stop_frame.data) == struct.pack(">i", 0)
    bus.shutdown.assert_called_once_with()

    driver.close()
    bus.shutdown.assert_called_once_with()


def test_open_twice_raises_instead_of_leaking_the_first_bus(vesc: VESC6, bus_cls: MagicMock) -> None:
    with pytest.raises(RuntimeError, match="already open"):
        vesc.open()
    bus_cls.assert_called_once()


def test_close_shuts_down_bus_even_when_safe_stop_fails(bus_cls: MagicMock, bus: MagicMock) -> None:
    driver = VESC6(channel="COM4", pole_pairs=1, controller_id=_CONTROLLER_ID, interface="slcan")
    driver.open()
    bus.send.side_effect = OSError("USB device unplugged")

    driver.close()

    bus.shutdown.assert_called_once_with()


def test_bus_kwargs_forwarded(bus_cls: MagicMock, bus: MagicMock) -> None:
    driver = VESC6(channel=0, pole_pairs=1, interface="gs_usb", bus_kwargs={"index": 0})
    driver.open()
    bus_cls.assert_called_once_with(interface="gs_usb", channel=0, bitrate=500_000, index=0)


def test_send_before_open_raises(bus_cls: MagicMock) -> None:
    driver = VESC6(channel="COM4", pole_pairs=1)
    with pytest.raises(RuntimeError, match="not open"):
        driver.set_duty_cycle(0.1)


@pytest.mark.parametrize("controller_id", [-1, 256])
def test_init_rejects_out_of_range_controller_id(bus_cls: MagicMock, controller_id: int) -> None:
    with pytest.raises(ValueError, match="controller_id"):
        VESC6(channel="COM4", pole_pairs=1, controller_id=controller_id)


def test_init_rejects_invalid_pole_pairs(bus_cls: MagicMock) -> None:
    with pytest.raises(ValueError, match="pole_pairs"):
        VESC6(channel="COM4", pole_pairs=0)


@pytest.mark.parametrize(
    ("call", "packet_id", "raw"),
    [
        (lambda m: m.set_duty_cycle(0.5), 0, 50_000),
        (lambda m: m.set_current(-7.5), 1, -7_500),
        (lambda m: m.set_brake_current(3.0), 2, 3_000),
        (lambda m: m.set_velocity(12_000), 3, 12_000),
        (lambda m: m.set_position(180.0), 4, 180_000_000),
        (lambda m: m.stop(), 1, 0),
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
        lambda m: m.set_duty_cycle(1.5),
        lambda m: m.set_brake_current(-1.0),
        lambda m: m.set_position(361.0),
        lambda m: m.set_current(3e6),  # scaled x1000 overflows int32
        lambda m: m.set_velocity(3e9),  # scaled by pole_pairs overflows int32
    ],
)
def test_commands_reject_out_of_range_values(vesc: VESC6, bus: MagicMock, call) -> None:
    with pytest.raises(ValueError):
        call(vesc)
    bus.send.assert_not_called()


def test_pole_pairs_convert_rpm_and_telemetry_velocity(bus: MagicMock) -> None:
    driver = VESC6(channel="COM4", pole_pairs=5, controller_id=_CONTROLLER_ID, interface="slcan")
    driver.open()

    driver.set_velocity(1_000)
    frame = _sent_frames(bus)[-1]
    assert frame.arbitration_id == (3 << 8) | _CONTROLLER_ID
    assert bytes(frame.data) == struct.pack(">i", 5_000)

    bus.recv.side_effect = [_status_frame(9, struct.pack(">ihh", 12_345, 0, 0)), None]
    assert driver.get_telemetry()["velocity"] == pytest.approx(2_469.0)


def test_get_telemetry_parses_status_frames(vesc: VESC6, bus: MagicMock) -> None:
    bus.recv.side_effect = [
        _status_frame(9, struct.pack(">ihh", 12_345, 155, 500)),
        _status_frame(16, struct.pack(">hhhh", 421, 380, 92, 9_000)),
        _status_frame(27, struct.pack(">ihh", 6_000, 244, 0)),
        None,
    ]

    telemetry = vesc.get_telemetry()

    assert telemetry == {
        "velocity": pytest.approx(12_345),  # pole_pairs=1: RPM == ERPM
        "motor_current": pytest.approx(15.5),
        "duty_cycle": pytest.approx(0.5),
        "fet_temperature": pytest.approx(42.1),
        "motor_temperature": pytest.approx(38.0),
        "input_current": pytest.approx(9.2),
        "position": pytest.approx(180.0),
        "bus_voltage": pytest.approx(24.4),
    }


def test_get_telemetry_ignores_other_senders_and_unknown_packets(vesc: VESC6, bus: MagicMock) -> None:
    bus.recv.side_effect = [
        _status_frame(9, struct.pack(">ihh", 999, 10, 10), controller_id=7),
        _status_frame(5, b"\x00" * 8),  # FILL_RX_BUFFER (fragmented COMM_* protocol)
        None,
    ]

    assert vesc.get_telemetry() == {}


def test_two_drivers_share_one_transport(bus_cls: MagicMock, bus: MagicMock) -> None:
    transport = CanTransport(CanConfig(channel=0, interface="gs_usb"))
    left = VESC6(channel=transport, pole_pairs=1, controller_id=1)
    right = VESC6(channel=transport, pole_pairs=1, controller_id=2)

    left.open()
    right.open()
    bus_cls.assert_called_once()

    bus.recv.side_effect = [
        _status_frame(9, struct.pack(">ihh", 100, 0, 0), controller_id=1),
        _status_frame(9, struct.pack(">ihh", 200, 0, 0), controller_id=2),
        None,
        None,
    ]
    assert left.get_telemetry()["velocity"] == pytest.approx(100)
    # left's drain routed right's frame instead of consuming it.
    assert right.get_telemetry()["velocity"] == pytest.approx(200)

    left.close()
    bus.shutdown.assert_not_called()
    right.close()
    bus.shutdown.assert_called_once_with()
