"""VESC 6 motor controller driver (CAN bus via CanDriver, extended-ID simple command frames)."""

from __future__ import annotations

import enum
import logging
import struct
from typing import Any, cast

import can

from instro.unstable.motorcontroller.motorcontroller import MotorControllerDriverBase
from instro.unstable.motorcontroller.types import MotorTelemetry
from instro.unstable.transports.can import CanConfig, CanDriver, CanSubscription

logger = logging.getLogger(__name__)


def _pack_scaled_int32(value: float, description: str) -> bytes:
    """Wire values are big-endian int32; reject scaled setpoints that would overflow."""
    raw = round(value)
    if not -(2**31) <= raw < 2**31:
        raise ValueError(f"{description}: scaled value {raw} exceeds the int32 wire format")
    return struct.pack(">i", raw)


class _CanPacketId(enum.IntEnum):
    SET_DUTY = 0
    SET_CURRENT = 1
    SET_CURRENT_BRAKE = 2
    SET_RPM = 3
    SET_POS = 4
    STATUS_1 = 9
    STATUS_4 = 16
    STATUS_5 = 27


class VESC6(MotorControllerDriverBase):
    """VESC 6 motor controller over CAN. Firmware stops the motor ~0.5 s after the last command (re-send setpoints to keep it running); poll telemetry at >=10 Hz, e.g. background_interval=0.1 (slow drains overflow the adapter FIFO, which drops the newest frames)."""

    def __init__(
        self,
        channel: str | int | CanDriver,
        pole_pairs: int,
        controller_id: int = 0,
        interface: str = "gs_usb",
        bitrate: int = 500_000,
        bus_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Pass channel as an adapter channel (a private bus is constructed) or a shared CanDriver for several drivers on one adapter (interface/bitrate/bus_kwargs then come from the shared transport); pole_pairs converts mechanical RPM to VESC ERPM (ERPM = RPM x pole_pairs)."""
        if not 0 <= controller_id <= 255:
            raise ValueError(f"controller_id must be 0-255, got {controller_id}")
        if pole_pairs < 1:
            raise ValueError(f"pole_pairs must be >= 1, got {pole_pairs}")
        self._pole_pairs = pole_pairs
        self._controller_id = controller_id
        if isinstance(channel, CanDriver):
            self._can = channel
        else:
            self._can = CanDriver(
                CanConfig(channel=channel, interface=interface, bitrate=bitrate, bus_kwargs=bus_kwargs or {})
            )
        self._subscription: CanSubscription | None = None

    def open(self) -> None:
        if self._subscription is not None:
            raise RuntimeError("VESC6 is already open; call close() before re-opening")
        self._can.open(self)
        self._subscription = self._can.subscribe(self._is_own_status_frame)

    def close(self) -> None:
        if self._subscription is None:
            return
        try:
            self.stop()
        except Exception:
            logger.warning("VESC6 safe-stop on close failed", exc_info=True)
        finally:
            self._can.unsubscribe(self._subscription)
            self._subscription = None
            self._can.close(self)

    def stop(self) -> None:
        """Release the motor by commanding zero current; the VESC cannot hold position."""
        self._send(_CanPacketId.SET_CURRENT, struct.pack(">i", 0))

    def set_duty_cycle(self, duty: float) -> None:
        if not -1.0 <= duty <= 1.0:
            raise ValueError(f"duty must be within -1.0..1.0, got {duty}")
        self._send(_CanPacketId.SET_DUTY, struct.pack(">i", round(duty * 100_000)))

    def set_current(self, amps: float) -> None:
        """Command a motor current in amps (sign sets direction; device clamps to its configured limits)."""
        self._send(_CanPacketId.SET_CURRENT, _pack_scaled_int32(amps * 1000, f"current {amps} A"))

    def set_brake_current(self, amps: float) -> None:
        if amps < 0:
            raise ValueError(f"brake current must be >= 0, got {amps}")
        self._send(_CanPacketId.SET_CURRENT_BRAKE, struct.pack(">i", round(amps * 1000)))

    def set_velocity(self, rpm: float) -> None:
        """Command a mechanical speed in RPM, converted to ERPM via pole_pairs."""
        self._send(_CanPacketId.SET_RPM, _pack_scaled_int32(rpm * self._pole_pairs, f"velocity {rpm} RPM"))

    def set_position(self, degrees: float) -> None:
        """Command a servo position in degrees, 0..360 (single-turn)."""
        if not 0.0 <= degrees <= 360.0:
            raise ValueError(f"position must be within 0..360 degrees, got {degrees}")
        self._send(_CanPacketId.SET_POS, struct.pack(">i", round(degrees * 1_000_000)))

    def get_telemetry(self) -> MotorTelemetry:
        """Drain this controller's status frames, returning the latest value per field (empty if none); poll frequently — an overflowed RX FIFO (e.g. gs_usb) drops the newest frames."""
        updates: dict[str, float] = {}
        for message in self._require_subscription().drain():
            updates.update(self._parse_status_frame(message))
        return cast(MotorTelemetry, updates)

    def _is_own_status_frame(self, message: can.Message) -> bool:
        return message.is_extended_id and message.arbitration_id & 0xFF == self._controller_id

    def _require_subscription(self) -> CanSubscription:
        if self._subscription is None:
            raise RuntimeError("VESC6 is not open; call open() first")
        return self._subscription

    def _send(self, packet_id: _CanPacketId, payload: bytes) -> None:
        self._require_subscription()
        self._can.send((int(packet_id) << 8) | self._controller_id, payload)

    def _parse_status_frame(self, message: can.Message) -> dict[str, float]:
        if len(message.data) != 8:
            return {}
        data = bytes(message.data)
        packet_id = (message.arbitration_id >> 8) & 0xFF
        if packet_id == _CanPacketId.STATUS_1:
            erpm, current, duty = struct.unpack(">ihh", data)
            return {"velocity": erpm / self._pole_pairs, "motor_current": current / 10, "duty_cycle": duty / 1000}
        if packet_id == _CanPacketId.STATUS_4:
            fet_temp, motor_temp, input_current, pid_position = struct.unpack(">hhhh", data)
            return {
                "fet_temperature": fet_temp / 10,
                "motor_temperature": motor_temp / 10,
                "input_current": input_current / 10,
                "position": pid_position / 50,
            }
        if packet_id == _CanPacketId.STATUS_5:
            _, input_voltage, _ = struct.unpack(">ihh", data)
            return {"bus_voltage": input_voltage / 10}
        return {}
