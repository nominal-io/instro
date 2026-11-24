"""Magtrol DSP7010-series dynamometer controller driver (proprietary ASCII over USB serial or GPIB, no error queue)."""

from __future__ import annotations

import enum
import math
import re
import time

from instro.lib import InstroError
from instro.lib.instrument import Instrument, publish_command, publish_measurement
from instro.lib.publishers import Publisher
from instro.lib.transports.visa import VisaConfig, VisaDriver
from instro.lib.types import Command, Measurement

# The manual spells it "COMAND ERROR"; DSP7011 firmware sends "COMMAND ERROR".
_COMMAND_ERROR = re.compile(r"COMM?AND ERROR")
# Speed is space-padded ("S 1725T22.60R") and the torque decimal point moves with the dyno range.
_OD_PATTERN = re.compile(r"S\s*(\d+(?:\.\d*)?)\s*T\s*(-?\s*(?:\d+\.?\d*|\.\d+))\s*([LRA])")
_MAX_RPM = 999_999.0

# STAT bit positions, manual section 7.5.2. DPA/DPB and the IO bits aren't documented.
_DSP7012_BIT = 5
_GLOBAL_BITS = {
    "status.dsp7012": _DSP7012_BIT,
    "status.engineering_mode": 22,
    "alarm.global_power": 29,
    "alarm.global_torque": 30,
}
# (ch1, ch2). The manual labels the input.* bits "Alarm", but they track the input wires (they're the
# "Digital Inputs" of manual p.70): on a DSP7011 they were set while the front panel showed no such alarm.
_CHANNEL_BITS = {
    "input.external": (4, 31),
    "input.electrical": (8, 9),
    "input.water_flow": (10, 11),
    "input.temperature": (12, 13),
    "input.air_flow": (14, 15),  # ch2 doubles as the clutch input in tandem setups
    "alarm.contact": (16, 17),
    "ramp_down_complete": (18, 19),
    "ramp_up_complete": (20, 21),
    "alarm.speed": (23, 24),
    "alarm.torque": (25, 26),
    "alarm.power": (27, 28),
}


class TorqueUnit(enum.IntEnum):
    OZ_IN = 0
    OZ_FT = 1
    LB_IN = 2
    LB_FT = 3
    G_CM = 4
    KG_CM = 5
    MN_M = 6
    CN_M = 7
    N_M = 8


_NEWTON_METRES = {
    TorqueUnit.OZ_IN: 0.00706155181,
    TorqueUnit.OZ_FT: 0.0847386218,
    TorqueUnit.LB_IN: 0.112984829,
    TorqueUnit.LB_FT: 1.35581795,
    TorqueUnit.G_CM: 9.80665e-5,
    TorqueUnit.KG_CM: 0.0980665,
    TorqueUnit.MN_M: 1e-3,
    TorqueUnit.CN_M: 1e-2,
    TorqueUnit.N_M: 1.0,
}


def _parse_status_word(reply: str) -> int:
    # The manual only says "ASCII status bits"; a DSP7011 sends 8 hex digits, e.g. "8000cc5f".
    if re.fullmatch(r"[0-9A-Fa-f]{8}", reply):
        return int(reply, 16)
    raise InstroError(f"Magtrol DSP7010: unparseable STAT1 reply {reply!r}")


class MagtrolDSP7010(Instrument):
    """Magtrol DSP7011 / DSP7012 dynamometer controller. Any set point turns the brake on."""

    def __init__(
        self,
        visa_resource: str | VisaConfig,
        name: str = "dyno",
        publishers: list[Publisher] | None = None,
        **kwargs,
    ) -> None:
        """Configure the driver; the port is not opened until ``open()``.

        Args:
            visa_resource (str | VisaConfig): ``"ASRL3::INSTR"`` (USB virtual COM port) or
                ``"GPIB0::9::INSTR"``. The default ``VisaConfig`` (9600 8N1, CR LF out, LF in)
                matches the controller.
            name (str): Channel-name prefix for published data.
            publishers (list[Publisher] | None): Publishers for readings and commands.

        Example:
            >>> from instro.unstable.dynamometer.drivers import MagtrolDSP7010, TorqueUnit
            >>> dyno = MagtrolDSP7010("ASRL3::INSTR")
            >>> dyno.open()
            >>> dyno.set_torque_units(1, TorqueUnit.N_M)
            >>> dyno.set_alarm_limits(1, max_speed_rpm=4000.0, max_torque=5.0, max_power_kw=1.5)
            >>> dyno.set_alarms_enabled(1, True)
            >>> dyno.set_torque(1, 2.5)
            >>> dyno.read_data(1)
            >>> dyno.brake_off(1)
            >>> dyno.close()
        """
        super().__init__(name=name, publishers=publishers, **kwargs)
        self._visa = VisaDriver(visa_resource)
        # UR can't be read back, so power is only computed once we've set the units ourselves.
        self._torque_units: dict[int, TorqueUnit] = {}

    def open(self) -> None:
        self._visa.open()
        # Units may not survive a power cycle; missing power beats silently wrong power.
        self._torque_units.clear()

    def close(self) -> None:
        try:
            super().close()
        finally:
            self._visa.close()

    def _query(self, command: str) -> str:
        reply = self._visa.query(command).strip()
        if _COMMAND_ERROR.search(reply):
            raise InstroError(f"Magtrol DSP7010 rejected {command!r}: {reply}")
        return reply

    def _write(self, command: str) -> None:
        # Set commands answer with an empty line (not in the manual); read it so the next query doesn't.
        reply = self._query(command)
        if reply:
            raise InstroError(f"Magtrol DSP7010: unexpected reply {reply!r} to {command!r}")

    @staticmethod
    def _channel(channel: int) -> int:
        if channel not in (1, 2):
            raise ValueError(f"channel must be 1 or 2, got {channel}")
        return channel

    @staticmethod
    def _in_range(name: str, value: float, low: float, high: float) -> float:
        if not low <= value <= high:
            raise ValueError(f"{name} must be in [{low}, {high}], got {value}")
        return value

    def _package_commands(self, values: dict[str, float]) -> Command:
        return Command(
            channel_data={f"{self.name}.{channel}": value for channel, value in values.items()},
            timestamp=time.time_ns(),
            tags=dict(self.default_tags),
        )

    def identify(self) -> str:
        return self._query("*IDN?")

    @publish_measurement
    def read_data(self, channel: int) -> Measurement:
        """Speed (rpm), torque (readout units), alarm, direction (+1 CW / -1 CCW), and power (W) if units are set.

        ``alarm`` is 1 when the controller is in an alarm condition (manual p.65). The manual says "the unit",
        so on a DSP7012 it may reflect either channel. The alarm replaces the direction letter in the reply,
        so ``direction`` is omitted while ``alarm`` is 1.
        """
        ch = self._channel(channel)
        with self._visa.lock():
            reply = self._query(f"OD{ch}")
            units = self._torque_units.get(ch)
        timestamp = time.time_ns()
        match = _OD_PATTERN.search(reply)
        if match is None:
            raise InstroError(f"Magtrol DSP7010: unparseable OD reply {reply!r}")
        speed = float(match.group(1))
        torque = float(match.group(2).replace(" ", ""))
        direction = match.group(3)

        prefix = f"{self.name}.ch{ch}"
        data: dict[str, list[float] | list[str]] = {
            f"{prefix}.speed": [speed],
            f"{prefix}.torque": [torque],
            f"{prefix}.alarm": [float(direction == "A")],
        }
        if direction != "A":
            data[f"{prefix}.direction"] = [1.0 if direction == "R" else -1.0]
        if units is not None:
            data[f"{prefix}.power"] = [torque * _NEWTON_METRES[units] * speed * 2 * math.pi / 60]
        return Measurement(channel_data=data, timestamps=[timestamp], tags=dict(self.default_tags))

    @publish_measurement
    def get_status(self) -> Measurement:
        """Raw status word plus each flag as 0/1. Channel 2 flags only on a DSP7012.

        ``input.*`` flags report the external, water-flow, air-flow, electrical, and temperature input
        wires, not whether that alarm is enabled or tripped. Use ``alarm`` from ``read_data`` for that.
        """
        word = _parse_status_word(self._query("STAT1"))
        timestamp = time.time_ns()
        bits = dict(_GLOBAL_BITS)
        for index, ch in enumerate((1, 2) if word >> _DSP7012_BIT & 1 else (1,)):
            bits.update({f"ch{ch}.{key}": pair[index] for key, pair in _CHANNEL_BITS.items()})

        data: dict[str, list[float] | list[str]] = {f"{self.name}.status.word": [float(word)]}
        for key, bit in bits.items():
            data[f"{self.name}.{key}"] = [float(word >> bit & 1)]
        return Measurement(channel_data=data, timestamps=[timestamp], tags=dict(self.default_tags))

    @publish_command
    def set_torque_units(self, channel: int, units: TorqueUnit) -> Command:
        ch = self._channel(channel)
        with self._visa.lock():
            self._write(f"UR{ch},{units.value}")
            self._torque_units[ch] = units
        return self._package_command(f"ch{ch}.torque_units.cmd", float(units.value), time.time_ns())

    @publish_command
    def set_max_speed(self, channel: int, rpm: float) -> Command:
        """Set at or just above free-run speed, otherwise speed mode loads the motor as soon as the brake is on."""
        ch = self._channel(channel)
        self._write(f"F{ch},{self._in_range('rpm', rpm, 0.0, _MAX_RPM):.1f}")
        return self._package_command(f"ch{ch}.max_speed.cmd", rpm, time.time_ns())

    @publish_command
    def set_speed(self, channel: int, rpm: float) -> Command:
        ch = self._channel(channel)
        self._write(f"N{ch},{self._in_range('rpm', rpm, 0.0, _MAX_RPM):.1f}")
        return self._package_commands({f"ch{ch}.speed_setpoint.cmd": rpm, f"ch{ch}.brake.cmd": 1.0})

    @publish_command
    def set_torque(self, channel: int, torque: float) -> Command:
        ch = self._channel(channel)
        # The manual gives no range for Q#,X.
        if not math.isfinite(torque):
            raise ValueError(f"torque must be finite, got {torque}")
        self._write(f"Q{ch},{torque:.3f}")
        return self._package_commands({f"ch{ch}.torque_setpoint.cmd": torque, f"ch{ch}.brake.cmd": 1.0})

    @publish_command
    def set_open_loop_output(self, channel: int, percent: float) -> Command:
        """Open-loop brake drive; 99.99 % is 1 A."""
        ch = self._channel(channel)
        self._write(f"I{ch},{self._in_range('percent', percent, 0.0, 99.99):.2f}")
        # Unlike N# and Q#, the manual doesn't say I# turns the brake on, so no brake.cmd here.
        return self._package_command(f"ch{ch}.open_loop_output.cmd", percent, time.time_ns())

    @publish_command
    def brake_off(self, channel: int) -> Command:
        """Reset every set point. ``Q#`` also clears the output limit and freeze speed."""
        ch = self._channel(channel)
        # There's no standalone brake-off command; bare N# and Q# each drop the brake (manual p.72-73).
        for command in (f"I{ch}", f"N{ch}", f"Q{ch}"):
            self._write(command)
        return self._package_command(f"ch{ch}.brake.cmd", 0.0, time.time_ns())

    @publish_command
    def tare(self, channel: int) -> Command:
        """Zero the torque offset (up to 10 % of full scale)."""
        ch = self._channel(channel)
        self._write(f"TS{ch}")
        return self._package_command(f"ch{ch}.tare.cmd", 1.0, time.time_ns())

    @publish_command
    def set_alarm_limits(self, channel: int, max_speed_rpm: float, max_torque: float, max_power_kw: float) -> Command:
        """Torque is in readout units. Limits do nothing until ``set_alarms_enabled``."""
        ch = self._channel(channel)
        commands = [
            f"ALS{ch},{self._in_range('max_speed_rpm', max_speed_rpm, 0.0, _MAX_RPM):.1f}",
            f"ALT{ch},{self._in_range('max_torque', max_torque, 0.0, 10_000.0):.3f}",
            f"ALP{ch},{self._in_range('max_power_kw', max_power_kw, 0.0, 99_999.0):.3f}",
        ]
        for command in commands:
            self._write(command)
        return self._package_commands(
            {
                f"ch{ch}.alarm_limit.speed.cmd": max_speed_rpm,
                f"ch{ch}.alarm_limit.torque.cmd": max_torque,
                f"ch{ch}.alarm_limit.power.cmd": max_power_kw,
            }
        )

    @publish_command
    def set_alarms_enabled(self, channel: int, enabled: bool) -> Command:
        """Alarms are off at power-up."""
        ch = self._channel(channel)
        self._write(f"ALL{ch},{int(enabled)}")
        return self._package_command(f"ch{ch}.alarms_enabled.cmd", float(enabled), time.time_ns())

    @publish_command
    def clear_alarms(self) -> Command:
        self._write("CLR")
        return self._package_command("clear_alarms.cmd", 1.0, time.time_ns())
