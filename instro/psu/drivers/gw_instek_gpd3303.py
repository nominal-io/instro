"""GW Instek GPD-3303D PSU driver."""

import dataclasses

from instro.lib.exceptions import FeatureNotSupportedError
from instro.lib.transports.visa import TerminatorConfig, VisaConfig, VisaDriver
from instro.psu import PSUDriverBase
from instro.psu.types import OperatingMode

# The device replies with a bare CR but needs CR/LF on writes (an LF-only write is rejected as "Invalid Character").
_TERMINATOR = TerminatorConfig(read="\r", write="\r\n")
_STATUS_LEGEND_LINES = 2
_STATUS_BITS = 8
_STATUS_DEFINED_BITS = (0, 1, 2, 3, 4, 6)
_NO_PROTECTION_REASON = "the GPD-3303 remote command set defines no OVP, OCP, or remote-sense commands"


class GWInstekGPD3303(PSUDriverBase):
    """GW Instek GPD-3303D 2-channel PSU over its USB virtual COM port."""

    def __init__(self, visa_resource: str | VisaConfig) -> None:
        config = VisaConfig(visa_resource=visa_resource) if isinstance(visa_resource, str) else visa_resource
        self._visa = VisaDriver(dataclasses.replace(config, terminator=_TERMINATOR))

    def open(self) -> None:
        self._visa.open()

    def close(self) -> None:
        self._visa.close()

    def set_voltage(self, voltage: float, channel: int) -> None:
        _require_programmable_channel("set_voltage", channel)
        self._write(f"VSET{channel}:{voltage:.1f}")

    def get_voltage(self, channel: int) -> float:
        _require_programmable_channel("get_voltage", channel)
        return self._query_float(f"VOUT{channel}?")

    def set_current_limit(self, current_limit: float, channel: int) -> None:
        _require_programmable_channel("set_current_limit", channel)
        self._write(f"ISET{channel}:{current_limit:.2f}")

    def get_current(self, channel: int) -> float:
        _require_programmable_channel("get_current", channel)
        return self._query_float(f"IOUT{channel}?")

    def get_voltage_setpoint(self, channel: int) -> float:
        _require_programmable_channel("get_voltage_setpoint", channel)
        return self._query_float(f"VSET{channel}?")

    def get_current_setpoint(self, channel: int) -> float:
        _require_programmable_channel("get_current_setpoint", channel)
        return self._query_float(f"ISET{channel}?")

    def output_enable(self, enable: bool, channel: int) -> None:
        """Switch the output on or off. The GPD-3303D has one output switch, so ``channel`` is only validated and every channel is affected."""
        _require_programmable_channel("output_enable", channel)
        self._write("OUT1" if enable else "OUT0")

    def get_output_status(self, channel: int) -> bool:
        """Query the output state, which is shared by every channel."""
        _require_programmable_channel("get_output_status", channel)
        return bool(self._query_status() & 0x40)

    def get_operating_mode(self, channel: int) -> OperatingMode:
        _require_programmable_channel("get_operating_mode", channel)
        status = self._query_status()
        if not status & 0x40:
            return OperatingMode.OFF
        return OperatingMode.CONSTANT_VOLTAGE if status & (1 << (channel - 1)) else OperatingMode.CONSTANT_CURRENT

    def set_overvoltage_protection_level(self, voltage: float, channel: int) -> None:
        raise FeatureNotSupportedError(
            f"set_overvoltage_protection_level is not supported by GWInstekGPD3303; {_NO_PROTECTION_REASON}"
        )

    def get_overvoltage_protection_level(self, channel: int) -> float:
        raise FeatureNotSupportedError(
            f"get_overvoltage_protection_level is not supported by GWInstekGPD3303; {_NO_PROTECTION_REASON}"
        )

    def set_overvoltage_protection_enabled(self, enabled: bool, channel: int) -> None:
        raise FeatureNotSupportedError(
            f"set_overvoltage_protection_enabled is not supported by GWInstekGPD3303; {_NO_PROTECTION_REASON}"
        )

    def get_overvoltage_protection_enabled(self, channel: int) -> bool:
        raise FeatureNotSupportedError(
            f"get_overvoltage_protection_enabled is not supported by GWInstekGPD3303; {_NO_PROTECTION_REASON}"
        )

    def set_overvoltage_protection_delay(self, delay: float, channel: int) -> None:
        raise FeatureNotSupportedError(
            f"set_overvoltage_protection_delay is not supported by GWInstekGPD3303; {_NO_PROTECTION_REASON}"
        )

    def get_overvoltage_protection_delay(self, channel: int) -> float:
        raise FeatureNotSupportedError(
            f"get_overvoltage_protection_delay is not supported by GWInstekGPD3303; {_NO_PROTECTION_REASON}"
        )

    def set_overcurrent_protection_level(self, current: float, channel: int) -> None:
        raise FeatureNotSupportedError(
            f"set_overcurrent_protection_level is not supported by GWInstekGPD3303; {_NO_PROTECTION_REASON}"
        )

    def get_overcurrent_protection_level(self, channel: int) -> float:
        raise FeatureNotSupportedError(
            f"get_overcurrent_protection_level is not supported by GWInstekGPD3303; {_NO_PROTECTION_REASON}"
        )

    def set_overcurrent_protection_enabled(self, enabled: bool, channel: int) -> None:
        raise FeatureNotSupportedError(
            f"set_overcurrent_protection_enabled is not supported by GWInstekGPD3303; {_NO_PROTECTION_REASON}"
        )

    def get_overcurrent_protection_enabled(self, channel: int) -> bool:
        raise FeatureNotSupportedError(
            f"get_overcurrent_protection_enabled is not supported by GWInstekGPD3303; {_NO_PROTECTION_REASON}"
        )

    def clear_protection(self, channel: int) -> None:
        raise FeatureNotSupportedError(f"clear_protection is not supported by GWInstekGPD3303; {_NO_PROTECTION_REASON}")

    def get_overvoltage_protection_tripped(self, channel: int) -> bool:
        raise FeatureNotSupportedError(
            f"get_overvoltage_protection_tripped is not supported by GWInstekGPD3303; {_NO_PROTECTION_REASON}"
        )

    def get_overcurrent_protection_tripped(self, channel: int) -> bool:
        raise FeatureNotSupportedError(
            f"get_overcurrent_protection_tripped is not supported by GWInstekGPD3303; {_NO_PROTECTION_REASON}"
        )

    def set_remote_sense_enabled(self, enabled: bool, channel: int) -> None:
        raise FeatureNotSupportedError(
            f"set_remote_sense_enabled is not supported by GWInstekGPD3303; {_NO_PROTECTION_REASON}"
        )

    def get_remote_sense_enabled(self, channel: int) -> bool:
        raise FeatureNotSupportedError(
            f"get_remote_sense_enabled is not supported by GWInstekGPD3303; {_NO_PROTECTION_REASON}"
        )

    def _write(self, command: str) -> None:
        with self._visa.lock():
            self._visa.write(command)
            self._check_errors()

    def _query_float(self, command: str) -> float:
        return float(self._visa.query(command).rstrip("VA"))

    def _query_status(self) -> int:
        with self._visa.lock():
            reply = self._visa.query("STATUS?")
            for _ in range(_STATUS_LEGEND_LINES):
                self._visa.read()
        bits = reply.split()
        if len(bits) != _STATUS_BITS or any(bits[i] not in ("0", "1") for i in _STATUS_DEFINED_BITS):
            raise RuntimeError(f"GW Instek PSU returned an unexpected STATUS? reply: {reply!r}")
        return sum(1 << i for i, bit in enumerate(bits) if bit == "1")

    def _check_errors(self) -> None:
        err = self._visa.query("ERR?")
        if err != "No Error.":
            raise RuntimeError(f"GW Instek PSU reported error: {err}")


def _require_programmable_channel(method_name: str, channel: int) -> None:
    if channel in (1, 2):
        return
    if channel == 3:
        raise FeatureNotSupportedError(
            f"{method_name} is not supported for channel 3 by GWInstekGPD3303; "
            "the fixed 2.5/3.3/5.0 V CH3 output does not expose a programming interface"
        )
    raise ValueError("GWInstekGPD3303 channel must be 1, 2, or 3")
