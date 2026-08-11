"""VESC 6 motor controller driver (CAN bus via python-can, extended-ID simple command frames)."""

from __future__ import annotations

import enum
import logging
import struct
import sys
import threading
import time
from collections.abc import Callable
from typing import Any, cast

import can

from instro.unstable.motorcontroller.motorcontroller import MotorControllerDriverBase
from instro.unstable.motorcontroller.types import DriveState, MotorTelemetry

if sys.version_info >= (3, 11):
    from typing import NotRequired
else:
    from typing_extensions import NotRequired

logger = logging.getLogger(__name__)


class VESC6Telemetry(MotorTelemetry):
    """VESC 6 telemetry frame. Adds fields from the CAN broadcast status messages with no canonical mapping."""

    tachometer: NotRequired[float]
    amp_hours: NotRequired[float]
    amp_hours_charged: NotRequired[float]
    watt_hours: NotRequired[float]
    watt_hours_charged: NotRequired[float]
    adc1: NotRequired[float]
    adc2: NotRequired[float]
    adc3: NotRequired[float]
    ppm: NotRequired[float]


class _CanPacketId(enum.IntEnum):
    SET_DUTY = 0
    SET_CURRENT = 1
    SET_CURRENT_BRAKE = 2
    SET_RPM = 3
    SET_POS = 4
    STATUS_1 = 9
    SET_CURRENT_REL = 10
    SET_CURRENT_BRAKE_REL = 11
    STATUS_2 = 14
    STATUS_3 = 15
    STATUS_4 = 16
    PING = 17
    PONG = 18
    STATUS_5 = 27
    STATUS_6 = 58


def _parse_status_1(data: bytes) -> dict[str, float]:
    erpm, current, duty = struct.unpack(">ihh", data)
    return {"erpm": float(erpm), "motor_current": current / 10, "duty_cycle": duty / 1000}


def _parse_status_2(data: bytes) -> dict[str, float]:
    amp_hours, amp_hours_charged = struct.unpack(">ii", data)
    return {"amp_hours": amp_hours / 1e4, "amp_hours_charged": amp_hours_charged / 1e4}


def _parse_status_3(data: bytes) -> dict[str, float]:
    watt_hours, watt_hours_charged = struct.unpack(">ii", data)
    return {"watt_hours": watt_hours / 1e4, "watt_hours_charged": watt_hours_charged / 1e4}


def _parse_status_4(data: bytes) -> dict[str, float]:
    fet_temp, motor_temp, input_current, pid_position = struct.unpack(">hhhh", data)
    return {
        "fet_temperature": fet_temp / 10,
        "motor_temperature": motor_temp / 10,
        "input_current": input_current / 10,
        "position": pid_position / 50,
    }


def _parse_status_5(data: bytes) -> dict[str, float]:
    tachometer, input_voltage, _ = struct.unpack(">ihh", data)
    return {"tachometer": float(tachometer), "bus_voltage": input_voltage / 10}


def _parse_status_6(data: bytes) -> dict[str, float]:
    adc1, adc2, adc3, ppm = struct.unpack(">hhhh", data)
    return {"adc1": adc1 / 1000, "adc2": adc2 / 1000, "adc3": adc3 / 1000, "ppm": ppm / 1000}


_STATUS_PARSERS: dict[int, Callable[[bytes], dict[str, float]]] = {
    _CanPacketId.STATUS_1: _parse_status_1,
    _CanPacketId.STATUS_2: _parse_status_2,
    _CanPacketId.STATUS_3: _parse_status_3,
    _CanPacketId.STATUS_4: _parse_status_4,
    _CanPacketId.STATUS_5: _parse_status_5,
    _CanPacketId.STATUS_6: _parse_status_6,
}


class VESC6(MotorControllerDriverBase):
    """VESC 6 motor controller over CAN. Firmware stops the motor ~0.5 s after the last command; re-send setpoints to keep it running."""

    def __init__(
        self,
        channel: str | int,
        controller_id: int = 0,
        interface: str = "gs_usb",
        bitrate: int = 500_000,
        host_id: int = 254,
        pole_pairs: int = 1,
        bus_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """With the default pole_pairs=1, velocity RPM values are electrical RPM (ERPM); set the motor's pole-pair count for mechanical RPM."""
        if not 0 <= controller_id <= 255:
            raise ValueError(f"controller_id must be 0-255, got {controller_id}")
        if not 0 <= host_id <= 255:
            raise ValueError(f"host_id must be 0-255, got {host_id}")
        if pole_pairs < 1:
            raise ValueError(f"pole_pairs must be >= 1, got {pole_pairs}")
        self._channel = channel
        self._controller_id = controller_id
        self._interface = interface
        self._bitrate = bitrate
        self._host_id = host_id
        self._pole_pairs = pole_pairs
        self._bus_kwargs = bus_kwargs or {}
        self._bus: can.BusABC | None = None
        self._bus_lock = threading.Lock()
        self._state = DriveState.DISABLED
        self._pong_event = threading.Event()
        self._status_lock = threading.Lock()
        self._status_buffer: dict[str, float] = {}

    def open(self) -> None:
        self._bus = can.Bus(interface=self._interface, channel=self._channel, bitrate=self._bitrate, **self._bus_kwargs)

    def close(self) -> None:
        if self._bus is None:
            return
        try:
            self.stop()
        except can.CanError:
            logger.warning("VESC6 safe-stop on close failed", exc_info=True)
        self._bus.shutdown()
        self._bus = None
        self._state = DriveState.DISABLED

    def enable(self) -> None:
        """No-op: the VESC arms implicitly on the first setpoint command."""

    def disable(self) -> None:
        """Release the motor by commanding zero current; the motor coasts freely."""
        self._send(_CanPacketId.SET_CURRENT, struct.pack(">i", 0))
        self._state = DriveState.DISABLED

    def stop(self) -> None:
        """Release the motor by commanding zero current; the VESC cannot hold position."""
        self.disable()

    def get_drive_state(self) -> DriveState:
        """Synthesized from the last command sent; the VESC broadcast frames carry no drive state."""
        if self._bus is None:
            return DriveState.DISABLED
        return self._state

    def set_duty_cycle(self, duty: float) -> None:
        if not -1.0 <= duty <= 1.0:
            raise ValueError(f"duty must be within -1.0..1.0, got {duty}")
        self._send(_CanPacketId.SET_DUTY, struct.pack(">i", round(duty * 100_000)))
        self._state = DriveState.ENABLED

    def set_current(self, amps: float) -> None:
        """Command a motor current in amps (sign sets direction; device clamps to its configured limits)."""
        self._send(_CanPacketId.SET_CURRENT, struct.pack(">i", round(amps * 1000)))
        self._state = DriveState.ENABLED

    def set_brake_current(self, amps: float) -> None:
        if amps < 0:
            raise ValueError(f"brake current must be >= 0, got {amps}")
        self._send(_CanPacketId.SET_CURRENT_BRAKE, struct.pack(">i", round(amps * 1000)))
        self._state = DriveState.ENABLED

    def set_velocity(self, rpm: float) -> None:
        """Command a mechanical speed in RPM, converted to ERPM via pole_pairs."""
        self._send(_CanPacketId.SET_RPM, struct.pack(">i", round(rpm * self._pole_pairs)))
        self._state = DriveState.ENABLED

    def set_position(self, degrees: float) -> None:
        """Command a servo position in degrees, 0..360 (single-turn)."""
        if not 0.0 <= degrees <= 360.0:
            raise ValueError(f"position must be within 0..360 degrees, got {degrees}")
        self._send(_CanPacketId.SET_POS, struct.pack(">i", round(degrees * 1_000_000)))
        self._state = DriveState.ENABLED

    def set_relative_current(self, fraction: float) -> None:
        """Command motor current as a fraction (-1.0..1.0) of the configured maximum."""
        if not -1.0 <= fraction <= 1.0:
            raise ValueError(f"relative current must be within -1.0..1.0, got {fraction}")
        self._send(_CanPacketId.SET_CURRENT_REL, struct.pack(">i", round(fraction * 100_000)))
        self._state = DriveState.ENABLED

    def set_relative_brake_current(self, fraction: float) -> None:
        """Command braking current as a fraction (0..1.0) of the configured maximum."""
        if not 0.0 <= fraction <= 1.0:
            raise ValueError(f"relative brake current must be within 0..1.0, got {fraction}")
        self._send(_CanPacketId.SET_CURRENT_BRAKE_REL, struct.pack(">i", round(fraction * 100_000)))
        self._state = DriveState.ENABLED

    def get_telemetry(self) -> VESC6Telemetry:
        """Drain broadcast status frames and return the latest value of each field seen; empty if none arrived."""
        updates = self._drain_status_frames()
        erpm = updates.pop("erpm", None)
        if erpm is not None:
            updates["velocity"] = erpm / self._pole_pairs
        return cast(VESC6Telemetry, updates)

    def ping(self, timeout: float = 1.0) -> bool:
        """Ping the controller; True if a PONG arrives within timeout."""
        bus = self._require_bus()
        self._pong_event.clear()
        self._send(_CanPacketId.PING, bytes([self._host_id]))
        deadline = time.monotonic() + timeout
        while True:
            # The PONG may be received (and routed) by a concurrent get_telemetry drain,
            # so check the event rather than trusting this loop's own recv calls.
            if self._pong_event.is_set():
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            with self._bus_lock:
                message = bus.recv(timeout=min(0.05, remaining))
            self._route(message)

    def _require_bus(self) -> can.BusABC:
        if self._bus is None:
            raise RuntimeError("VESC6 is not open; call open() first")
        return self._bus

    def _send(self, packet_id: _CanPacketId, payload: bytes) -> None:
        bus = self._require_bus()
        message = can.Message(
            arbitration_id=(int(packet_id) << 8) | self._controller_id,
            data=payload,
            is_extended_id=True,
        )
        with self._bus_lock:
            bus.send(message)

    def _is_pong(self, message: can.Message) -> bool:
        return (
            message.is_extended_id
            and (message.arbitration_id >> 8) & 0xFF == _CanPacketId.PONG
            and message.arbitration_id & 0xFF == self._host_id
            and len(message.data) >= 1
            and message.data[0] == self._controller_id
        )

    def _route(self, message: can.Message | None) -> None:
        """Classify a received frame: PONGs set the ping event, status frames accumulate for get_telemetry."""
        if message is None:
            return
        if self._is_pong(message):
            self._pong_event.set()
            return
        updates = self._parse_status_frame(message)
        if updates:
            with self._status_lock:
                self._status_buffer.update(updates)

    def _drain_status_frames(self) -> dict[str, float]:
        bus = self._require_bus()
        while True:
            with self._bus_lock:
                message = bus.recv(timeout=0.0)
            if message is None:
                break
            self._route(message)
        with self._status_lock:
            updates = self._status_buffer
            self._status_buffer = {}
        return updates

    def _parse_status_frame(self, message: can.Message) -> dict[str, float]:
        if not message.is_extended_id or message.arbitration_id & 0xFF != self._controller_id:
            return {}
        parser = _STATUS_PARSERS.get((message.arbitration_id >> 8) & 0xFF)
        if parser is None or len(message.data) != 8:
            return {}
        return parser(bytes(message.data))
