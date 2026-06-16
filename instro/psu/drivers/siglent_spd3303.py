"""Siglent SPD3303-series PSU driver."""

from instro.lib.exceptions import FeatureNotSupportedError
from instro.lib.transports.visa import VisaConfig, VisaDriver
from instro.psu import PSUDriverBase


class SiglentSPD3303(PSUDriverBase):
    """Siglent SPD3303-series PSU."""

    def __init__(self, visa_resource: str | VisaConfig) -> None:
        self._visa = VisaDriver(visa_resource)

    def open(self) -> None:
        self._visa.open()

    def close(self) -> None:
        self._visa.close()

    def set_voltage(self, voltage: float, channel: int) -> None:
        _require_programmable_channel("set_voltage", channel)
        self._write_checked(f"CH{channel}:VOLT {voltage:.3f}")

    def get_voltage(self, channel: int) -> float:
        _require_programmable_channel("get_voltage", channel)
        return self._query_checked_float(f"MEAS:VOLT? CH{channel}")

    def set_current_limit(self, current_limit: float, channel: int) -> None:
        _require_programmable_channel("set_current_limit", channel)
        self._write_checked(f"CH{channel}:CURR {current_limit:.3f}")

    def get_current(self, channel: int) -> float:
        _require_programmable_channel("get_current", channel)
        return self._query_checked_float(f"MEAS:CURR? CH{channel}")

    def output_enable(self, enable: bool, channel: int) -> None:
        _require_programmable_channel("output_enable", channel)
        cmd = f"OUTP CH{channel},ON" if enable else f"OUTP CH{channel},OFF"
        self._write_checked(cmd)

    def get_output_status(self, channel: int) -> bool:
        _require_programmable_channel("get_output_status", channel)
        return bool(self.query_status()[f"ch{channel}_enable"])

    # OCP exists as a front-panel mode, but the published SCPI table has no OVP, OCP, or remote-sense commands.
    def set_overvoltage_protection_level(self, voltage: float, channel: int) -> None:
        raise FeatureNotSupportedError(
            "set_overvoltage_protection_level is not supported by SiglentSPD3303; "
            "the published SPD3303X/X-E SCPI command list does not define OVP, OCP, or remote-sense commands"
        )

    def get_overvoltage_protection_level(self, channel: int) -> float:
        raise FeatureNotSupportedError(
            "get_overvoltage_protection_level is not supported by SiglentSPD3303; "
            "the published SPD3303X/X-E SCPI command list does not define OVP, OCP, or remote-sense commands"
        )

    def set_overvoltage_protection_enabled(self, enabled: bool, channel: int) -> None:
        raise FeatureNotSupportedError(
            "set_overvoltage_protection_enabled is not supported by SiglentSPD3303; "
            "the published SPD3303X/X-E SCPI command list does not define OVP, OCP, or remote-sense commands"
        )

    def get_overvoltage_protection_enabled(self, channel: int) -> bool:
        raise FeatureNotSupportedError(
            "get_overvoltage_protection_enabled is not supported by SiglentSPD3303; "
            "the published SPD3303X/X-E SCPI command list does not define OVP, OCP, or remote-sense commands"
        )

    def set_overvoltage_protection_delay(self, delay: float, channel: int) -> None:
        raise FeatureNotSupportedError(
            "set_overvoltage_protection_delay is not supported by SiglentSPD3303; "
            "the published SPD3303X/X-E SCPI command list does not define OVP, OCP, or remote-sense commands"
        )

    def get_overvoltage_protection_delay(self, channel: int) -> float:
        raise FeatureNotSupportedError(
            "get_overvoltage_protection_delay is not supported by SiglentSPD3303; "
            "the published SPD3303X/X-E SCPI command list does not define OVP, OCP, or remote-sense commands"
        )

    def set_overcurrent_protection_level(self, current: float, channel: int) -> None:
        raise FeatureNotSupportedError(
            "set_overcurrent_protection_level is not supported by SiglentSPD3303; "
            "the published SPD3303X/X-E SCPI command list does not define OVP, OCP, or remote-sense commands"
        )

    def get_overcurrent_protection_level(self, channel: int) -> float:
        raise FeatureNotSupportedError(
            "get_overcurrent_protection_level is not supported by SiglentSPD3303; "
            "the published SPD3303X/X-E SCPI command list does not define OVP, OCP, or remote-sense commands"
        )

    def set_overcurrent_protection_enabled(self, enabled: bool, channel: int) -> None:
        raise FeatureNotSupportedError(
            "set_overcurrent_protection_enabled is not supported by SiglentSPD3303; "
            "the published SPD3303X/X-E SCPI command list does not define OVP, OCP, or remote-sense commands"
        )

    def get_overcurrent_protection_enabled(self, channel: int) -> bool:
        raise FeatureNotSupportedError(
            "get_overcurrent_protection_enabled is not supported by SiglentSPD3303; "
            "the published SPD3303X/X-E SCPI command list does not define OVP, OCP, or remote-sense commands"
        )

    def set_remote_sense_enabled(self, enabled: bool, channel: int) -> None:
        raise FeatureNotSupportedError(
            "set_remote_sense_enabled is not supported by SiglentSPD3303; "
            "the published SPD3303X/X-E SCPI command list does not define OVP, OCP, or remote-sense commands"
        )

    def get_remote_sense_enabled(self, channel: int) -> bool:
        raise FeatureNotSupportedError(
            "get_remote_sense_enabled is not supported by SiglentSPD3303; "
            "the published SPD3303X/X-E SCPI command list does not define OVP, OCP, or remote-sense commands"
        )

    def query_status(self) -> dict:
        """Query the status of the PSU (per-channel mode/enable + tracking mode)."""
        with self._visa.lock():
            resp = self._visa.query("SYST:STAT?")
            self._check_errors()
        return self._decode_status(int(resp, 16))

    def _decode_status(self, value: int) -> dict:
        return {
            "ch1_mode": "CC" if bool(value & 1) else "CV",
            "ch2_mode": "CC" if bool(value & 2) else "CV",
            "psu_mode": _psu_mode_to_str((value >> 2) & 3),
            "ch1_enable": bool(value & 16),
            "ch2_enable": bool(value & 32),
        }

    def _write_checked(self, command: str) -> None:
        with self._visa.lock():
            self._visa.write(command)
            self._check_errors()

    def _query_checked_float(self, command: str) -> float:
        with self._visa.lock():
            value = self._visa.query(command)
            self._check_errors()
            return float(value)

    def _check_errors(self) -> None:
        err = self._visa.query("SYST:ERR?")
        if not err.startswith("+0"):
            raise RuntimeError(f"Siglent PSU reported error: {err}")


def _psu_mode_to_str(mode: int) -> str:
    match mode:
        case 1:
            return "INDEPENDENT"
        case 2:
            return "PARALLEL"
        case 3:
            return "SERIES"
        case _:
            return "UNDEFINED"


def _require_programmable_channel(method_name: str, channel: int) -> None:
    if channel in (1, 2):
        return
    if channel == 3:
        raise FeatureNotSupportedError(
            f"{method_name} is not supported for channel 3 by SiglentSPD3303; "
            "the SPD3303X/X-E channel 3 output is selectable via front panel switch and does not expose a programming interface"
        )
    raise ValueError("SiglentSPD3303 channel must be 1, 2, or 3")
