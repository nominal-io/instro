"""Rigol DP800-series PSU driver. Covers DP811, DP821, DP831, DP832."""

from dataclasses import dataclass

from instro.lib.exceptions import FeatureNotSupportedError
from instro.lib.transports.visa import VisaConfig, VisaDriver
from instro.psu import OperatingMode, PSUDriverBase

# Regulation state in the low two bits of :STAT:QUES:INST:ISUM<n>:COND? (bit 2 is OVP, bit 3 OCP).
_CONDITION_MODES = {
    0: OperatingMode.OFF,
    1: OperatingMode.CONSTANT_CURRENT,
    2: OperatingMode.CONSTANT_VOLTAGE,
    3: OperatingMode.UNREGULATED,
}


@dataclass(frozen=True)
class _Range:
    minimum: float
    maximum: float


@dataclass(frozen=True)
class _ChannelLimits:
    voltage: _Range
    current: _Range
    ovp: _Range
    ocp: _Range


class RigolDP800(PSUDriverBase):
    """Rigol DP800-series multi-channel PSU (DP811/DP821/DP831/DP832)."""

    FRIENDLY_NAME = "Rigol DP800-series PSU"

    def __init__(self, visa_resource: str | VisaConfig) -> None:
        self._visa = VisaDriver(visa_resource)
        self.idn = ""
        self._limits: dict[int, _ChannelLimits] = {}

    def open(self) -> None:
        self._visa.open()
        self._load_limits()

    def close(self) -> None:
        self._visa.close()

    def set_voltage(self, voltage: float, channel: int) -> None:
        if (limits := self._limits.get(channel)) is not None:
            self._validate_in_range(voltage, limits.voltage, channel, "voltage")
        self._write_checked(f":SOUR{channel}:VOLT {voltage:.3f}")

    def get_voltage(self, channel: int) -> float:
        return self._query_checked_float(f":MEAS:VOLT? CH{channel}")

    def set_current_limit(self, current_limit: float, channel: int) -> None:
        if (limits := self._limits.get(channel)) is not None:
            self._validate_in_range(current_limit, limits.current, channel, "current limit")
        self._write_checked(f":SOUR{channel}:CURR {current_limit:.3f}")

    def get_current(self, channel: int) -> float:
        return self._query_checked_float(f":MEAS:CURR? CH{channel}")

    def output_enable(self, enable: bool, channel: int) -> None:
        cmd = f":OUTP CH{channel},ON" if enable else f":OUTP CH{channel},OFF"
        self._write_checked(cmd)

    def get_output_status(self, channel: int) -> bool:
        return self._query_checked_bool(f":OUTP? CH{channel}")

    def get_operating_mode(self, channel: int) -> OperatingMode:
        """Query the regulation state of ``channel``.

        Reads the channel's questionable-status condition register in one query, so a disabled
        channel reports ``OFF`` directly. (``:OUTP:MODE?`` is not used: it still answers ``CV``
        for a disabled channel.)

        Args:
            channel: 1-based output channel.

        Returns:
            OperatingMode: ``OFF``, ``CONSTANT_CURRENT``, ``CONSTANT_VOLTAGE``, or ``UNREGULATED``.

        Raises:
            RuntimeError: On a non-numeric register reply or a SCPI error.
        """
        return _CONDITION_MODES[self._query_channel_condition(channel) & 3]

    def get_voltage_setpoint(self, channel: int) -> float:
        return self._query_checked_float(f":SOUR{channel}:VOLT?")

    def get_current_setpoint(self, channel: int) -> float:
        return self._query_checked_float(f":SOUR{channel}:CURR?")

    def set_overvoltage_protection_level(self, voltage: float, channel: int) -> None:
        if (limits := self._limits.get(channel)) is not None:
            self._validate_in_range(voltage, limits.ovp, channel, "overvoltage protection level")
        self._write_checked(f":SOUR{channel}:VOLT:PROT {voltage:.3f}")

    def get_overvoltage_protection_level(self, channel: int) -> float:
        return self._query_checked_float(f":SOUR{channel}:VOLT:PROT:LEV?")

    def set_overvoltage_protection_enabled(self, enabled: bool, channel: int) -> None:
        self._write_checked(f":SOUR{channel}:VOLT:PROT:STAT {'ON' if enabled else 'OFF'}")

    def get_overvoltage_protection_enabled(self, channel: int) -> bool:
        return self._query_checked_bool(f":SOUR{channel}:VOLT:PROT:STAT?")

    def set_overvoltage_protection_delay(self, delay: float, channel: int) -> None:
        raise FeatureNotSupportedError(
            f"set_overvoltage_protection_delay is not supported by the {self.FRIENDLY_NAME}; "
            "the DP800 programming guide does not define an OVP delay command"
        )

    def get_overvoltage_protection_delay(self, channel: int) -> float:
        raise FeatureNotSupportedError(
            f"get_overvoltage_protection_delay is not supported by the {self.FRIENDLY_NAME}; "
            "the DP800 programming guide does not define an OVP delay query"
        )

    def set_overcurrent_protection_level(self, current: float, channel: int) -> None:
        if (limits := self._limits.get(channel)) is not None:
            self._validate_in_range(current, limits.ocp, channel, "overcurrent protection level")
        self._write_checked(f":SOUR{channel}:CURR:PROT {current:.3f}")

    def get_overcurrent_protection_level(self, channel: int) -> float:
        return self._query_checked_float(f":SOUR{channel}:CURR:PROT:LEV?")

    def set_overcurrent_protection_enabled(self, enabled: bool, channel: int) -> None:
        self._write_checked(f":SOUR{channel}:CURR:PROT:STAT {'ON' if enabled else 'OFF'}")

    def get_overcurrent_protection_enabled(self, channel: int) -> bool:
        return self._query_checked_bool(f":SOUR{channel}:CURR:PROT:STAT?")

    def set_remote_sense_enabled(self, enabled: bool, channel: int) -> None:
        self._write_checked(f":OUTP:SENS CH{channel},{'ON' if enabled else 'OFF'}")

    def get_remote_sense_enabled(self, channel: int) -> bool:
        state = self._query_checked(f":OUTP:SENS? CH{channel}").strip().upper()

        match state:
            case "ON" | "1":
                return True
            case "OFF" | "0":
                return False
            case "NONE":
                raise FeatureNotSupportedError(
                    f"remote sense is not supported by {self.FRIENDLY_NAME} channel {channel}"
                )
            case _:
                raise RuntimeError(f"Unexpected Rigol remote-sense state for channel {channel}: {state}")

    def _query_channel_condition(self, channel: int) -> int:
        """Read ``:STAT:QUES:INST:ISUM<n>:COND?`` for ``channel`` as an integer bit field."""
        reply = self._query_checked(f":STAT:QUES:INST:ISUM{channel}:COND?").strip()
        try:
            return int(reply)
        except ValueError:
            raise RuntimeError(f"Unexpected Rigol condition register for channel {channel}: {reply}") from None

    def _channel_count(self) -> int:
        if not self.idn:
            self.idn = self._query_checked("*IDN?")

        if "DP832" in self.idn or "DP831" in self.idn:
            return 3
        if "DP821" in self.idn:
            return 2
        if "DP811" in self.idn or "DP813" in self.idn:
            return 1
        raise RuntimeError(f"Unrecognized Rigol PSU model: {self.idn}")

    def _load_limits(self) -> None:
        self._limits = {
            channel: _ChannelLimits(
                voltage=self._query_range(f":SOUR{channel}:VOLT?"),
                current=self._query_range(f":SOUR{channel}:CURR?"),
                ovp=self._query_range(f":SOUR{channel}:VOLT:PROT:LEV?"),
                ocp=self._query_range(f":SOUR{channel}:CURR:PROT:LEV?"),
            )
            for channel in range(1, self._channel_count() + 1)
        }

    def _query_range(self, command: str) -> _Range:
        return _Range(
            minimum=self._query_checked_float(f"{command} MIN"),
            maximum=self._query_checked_float(f"{command} MAX"),
        )

    def _validate_in_range(self, value: float, limit: _Range, channel: int, label: str) -> None:
        if limit.minimum <= value <= limit.maximum:
            return

        raise ValueError(
            f"{label} {value} is out of range for {self.FRIENDLY_NAME} channel {channel}: "
            f"{limit.minimum} to {limit.maximum}"
        )

    def _write_checked(self, command: str) -> None:
        with self._visa.lock():
            self._visa.write(command)
            self._check_errors()

    def _query_checked(self, command: str) -> str:
        with self._visa.lock():
            value = self._visa.query(command)
            self._check_errors()
            return value

    def _query_checked_float(self, command: str) -> float:
        return float(self._query_checked(command))

    def _query_checked_bool(self, command: str) -> bool:
        return self._query_checked(command).strip().upper() in {"1", "ON"}

    def _check_errors(self) -> None:
        err = self._visa.query(":SYST:ERR?")
        code = err.strip().split(",", 1)[0].lstrip("+")
        if code != "0":
            raise RuntimeError(f"The {self.FRIENDLY_NAME} reported error: {err.strip()}")
