"""VESC 6 motor controller driver (CAN bus via python-can, extended-ID simple command frames)."""

from __future__ import annotations

import enum
import logging
import struct
import threading
import time
from collections.abc import Callable
from typing import Any

import can

from instro.lib.instrument import Instrument, publish_command, publish_measurement
from instro.lib.publishers import Publisher
from instro.lib.types import Command, Measurement

logger = logging.getLogger(__name__)


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
    return {"erpm": float(erpm), "motor_current": current / 10, "duty": duty / 1000}


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
        "pid_position": pid_position / 50,
    }


def _parse_status_5(data: bytes) -> dict[str, float]:
    tachometer, input_voltage, _ = struct.unpack(">ihh", data)
    return {"tachometer": float(tachometer), "input_voltage": input_voltage / 10}


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


class VESC6(Instrument):
    """VESC 6 motor controller over CAN. Firmware stops the motor ~0.5 s after the last command; re-send setpoints to keep it running."""

    def __init__(
        self,
        channel: str | int,
        controller_id: int = 0,
        interface: str = "gs_usb",
        bitrate: int = 500_000,
        host_id: int = 254,
        bus_kwargs: dict[str, Any] | None = None,
        name: str = "vesc",
        publishers: list[Publisher] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(name=name, publishers=publishers, **kwargs)
        if not 0 <= controller_id <= 255:
            raise ValueError(f"controller_id must be 0-255, got {controller_id}")
        if not 0 <= host_id <= 255:
            raise ValueError(f"host_id must be 0-255, got {host_id}")
        self._channel = channel
        self._controller_id = controller_id
        self._interface = interface
        self._bitrate = bitrate
        self._host_id = host_id
        self._bus_kwargs = bus_kwargs or {}
        self._bus: can.BusABC | None = None
        self._bus_lock = threading.Lock()
        self._define_background_daemon()

    def open(self) -> None:
        self._bus = can.Bus(interface=self._interface, channel=self._channel, bitrate=self._bitrate, **self._bus_kwargs)

    def close(self) -> None:
        super().close()
        if self._bus is not None:
            self._bus.shutdown()
            self._bus = None

    def _require_bus(self) -> can.BusABC:
        if self._bus is None:
            raise RuntimeError(f"VESC6 '{self.name}' is not open; call open() first")
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

    @publish_command
    def set_duty(self, duty: float) -> Command:
        """Command a duty cycle in -1.0..1.0."""
        if not -1.0 <= duty <= 1.0:
            raise ValueError(f"duty must be within -1.0..1.0, got {duty}")
        self._send(_CanPacketId.SET_DUTY, struct.pack(">i", round(duty * 100_000)))
        return self._package_command("duty.cmd", duty, time.time_ns())

    @publish_command
    def set_current(self, amps: float) -> Command:
        """Command a motor current in amps (sign sets direction; device clamps to its configured limits)."""
        self._send(_CanPacketId.SET_CURRENT, struct.pack(">i", round(amps * 1000)))
        return self._package_command("current.cmd", amps, time.time_ns())

    @publish_command
    def set_brake_current(self, amps: float) -> Command:
        """Command a braking current in amps (>= 0)."""
        if amps < 0:
            raise ValueError(f"brake current must be >= 0, got {amps}")
        self._send(_CanPacketId.SET_CURRENT_BRAKE, struct.pack(">i", round(amps * 1000)))
        return self._package_command("brake_current.cmd", amps, time.time_ns())

    @publish_command
    def set_rpm(self, erpm: int) -> Command:
        """Command a speed in electrical RPM (mechanical RPM x motor pole pairs)."""
        self._send(_CanPacketId.SET_RPM, struct.pack(">i", int(erpm)))
        return self._package_command("erpm.cmd", float(erpm), time.time_ns())

    @publish_command
    def set_position(self, degrees: float) -> Command:
        """Command a servo position in degrees, 0..360."""
        if not 0.0 <= degrees <= 360.0:
            raise ValueError(f"position must be within 0..360 degrees, got {degrees}")
        self._send(_CanPacketId.SET_POS, struct.pack(">i", round(degrees * 1_000_000)))
        return self._package_command("position.cmd", degrees, time.time_ns())

    @publish_command
    def set_relative_current(self, fraction: float) -> Command:
        """Command motor current as a fraction (-1.0..1.0) of the configured maximum."""
        if not -1.0 <= fraction <= 1.0:
            raise ValueError(f"relative current must be within -1.0..1.0, got {fraction}")
        self._send(_CanPacketId.SET_CURRENT_REL, struct.pack(">i", round(fraction * 100_000)))
        return self._package_command("current_rel.cmd", fraction, time.time_ns())

    @publish_command
    def set_relative_brake_current(self, fraction: float) -> Command:
        """Command braking current as a fraction (0..1.0) of the configured maximum."""
        if not 0.0 <= fraction <= 1.0:
            raise ValueError(f"relative brake current must be within 0..1.0, got {fraction}")
        self._send(_CanPacketId.SET_CURRENT_BRAKE_REL, struct.pack(">i", round(fraction * 100_000)))
        return self._package_command("brake_current_rel.cmd", fraction, time.time_ns())

    @publish_command
    def stop_motor(self) -> Command:
        """Release the motor by commanding zero current."""
        self._send(_CanPacketId.SET_CURRENT, struct.pack(">i", 0))
        return self._package_command("stop.cmd", True, time.time_ns())

    @publish_measurement
    def get_telemetry(self, **kwargs) -> Measurement | None:
        """Drain broadcast status frames and publish the latest value of each telemetry field seen."""
        updates = self._drain_status_frames()
        if not updates:
            return None
        return Measurement(
            channel_data={f"{self.name}.{key}": [value] for key, value in updates.items()},
            timestamps=[time.time_ns()],
            tags={**self.default_tags, **kwargs},
        )

    @publish_measurement
    def ping(self, timeout: float = 1.0) -> Measurement:
        """Ping the controller; publishes 1.0 if a PONG arrives within timeout, else 0.0."""
        bus = self._require_bus()
        self._send(_CanPacketId.PING, bytes([self._host_id]))
        deadline = time.monotonic() + timeout
        alive = 0.0
        while time.monotonic() < deadline:
            with self._bus_lock:
                message = bus.recv(timeout=max(0.0, deadline - time.monotonic()))
            if message is None:
                break
            if self._is_pong(message):
                alive = 1.0
                break
        return self._package_measurement("ping", alive, time.time_ns())

    def _is_pong(self, message: can.Message) -> bool:
        return (
            message.is_extended_id
            and (message.arbitration_id >> 8) & 0xFF == _CanPacketId.PONG
            and message.arbitration_id & 0xFF == self._host_id
            and len(message.data) >= 1
            and message.data[0] == self._controller_id
        )

    def _drain_status_frames(self) -> dict[str, float]:
        bus = self._require_bus()
        updates: dict[str, float] = {}
        while True:
            with self._bus_lock:
                message = bus.recv(timeout=0.0)
            if message is None:
                return updates
            updates.update(self._parse_status_frame(message))

    def _parse_status_frame(self, message: can.Message) -> dict[str, float]:
        if not message.is_extended_id or message.arbitration_id & 0xFF != self._controller_id:
            return {}
        parser = _STATUS_PARSERS.get((message.arbitration_id >> 8) & 0xFF)
        if parser is None or len(message.data) != 8:
            return {}
        return parser(bytes(message.data))

    def _define_background_daemon(self) -> None:
        self.add_background_daemon_function(self.get_telemetry)
