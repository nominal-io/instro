"""Siglent SPS5042X PSU driver."""

from instro.lib.exceptions import FeatureNotSupportedError
from instro.lib.transports.visa import VisaConfig, VisaDriver
from instro.psu import PSUDriverBase
from instro.psu.types import OperatingMode

FRIENDLY_NAME = "Siglent SPS5042X PSU"
# Half a count of the 1 mV / 1 mA setting resolution; anything further off was clamped by the instrument.
_CLAMP_TOLERANCE = 0.0005


class SiglentSPS5042X(PSUDriverBase):
    """Siglent SPS5042X single-output wide-range PSU."""

    def __init__(self, visa_resource: str | VisaConfig) -> None:
        self._visa = VisaDriver(visa_resource)

    def open(self) -> None:
        self._visa.open()

    def close(self) -> None:
        self._visa.close()

    def set_voltage(self, voltage: float, channel: int) -> None:
        """Set the output voltage (volts) on `channel`.

        Raises:
            ValueError: The instrument clamped ``voltage`` to its range and applied the clamped value instead.
        """
        _check_channel(channel)
        self._write_verified("VOLT", "voltage setpoint", voltage, channel)

    def get_voltage(self, channel: int) -> float:
        _check_channel(channel)
        return self._query_checked_float(f"MEAS:VOLT? CH{channel}")

    def set_current_limit(self, current_limit: float, channel: int) -> None:
        """Set the current limit (amperes) on `channel`.

        Raises:
            ValueError: The instrument clamped ``current_limit`` to its range and applied the clamped value instead.
        """
        _check_channel(channel)
        self._write_verified("CURR", "current limit", current_limit, channel)

    def get_current(self, channel: int) -> float:
        _check_channel(channel)
        return self._query_checked_float(f"MEAS:CURR? CH{channel}")

    def get_voltage_setpoint(self, channel: int) -> float:
        _check_channel(channel)
        return self._query_checked_float(f"VOLT? CH{channel}")

    def get_current_setpoint(self, channel: int) -> float:
        _check_channel(channel)
        return self._query_checked_float(f"CURR? CH{channel}")

    def output_enable(self, enable: bool, channel: int) -> None:
        """Enable or disable the output on `channel`. Enabling has no effect while a protection trip is latched; see `clear_protection`."""
        _check_channel(channel)
        self._write_checked("OUTP ON" if enable else "OUTP OFF")

    def get_output_status(self, channel: int) -> bool:
        _check_channel(channel)
        return self._query_checked_bool("OUTP?")

    def get_operating_mode(self, channel: int) -> OperatingMode:
        _check_channel(channel)
        # MEAS:RUN:MODE? keeps reporting CV while the output is off, so the output state decides OFF.
        with self._visa.lock():
            if not self._query_checked_bool("OUTP?"):
                return OperatingMode.OFF
            mode = self._visa.query(f"MEAS:RUN:MODE? CH{channel}").strip()
            self._check_errors()
        if mode == "CV":
            return OperatingMode.CONSTANT_VOLTAGE
        if mode == "CC":
            return OperatingMode.CONSTANT_CURRENT
        raise RuntimeError(f"Unexpected {FRIENDLY_NAME} regulation mode: {mode}")

    def set_overvoltage_protection_level(self, voltage: float, channel: int) -> None:
        """Set the overvoltage protection threshold (volts) on `channel`.

        Raises:
            ValueError: The instrument clamped ``voltage`` to its OVP range and applied the clamped value instead.
        """
        _check_channel(channel)
        self._write_verified("OVP", "overvoltage protection level", voltage, channel)

    def get_overvoltage_protection_level(self, channel: int) -> float:
        _check_channel(channel)
        return self._query_checked_float(f"OVP? CH{channel}")

    def set_overcurrent_protection_level(self, current: float, channel: int) -> None:
        """Set the overcurrent protection threshold (amperes) on `channel`.

        Raises:
            ValueError: The instrument clamped ``current`` to its OCP range and applied the clamped value instead.
        """
        _check_channel(channel)
        self._write_verified("OCP", "overcurrent protection level", current, channel)

    def get_overcurrent_protection_level(self, channel: int) -> float:
        _check_channel(channel)
        return self._query_checked_float(f"OCP? CH{channel}")

    # The firmware takes the manual's "SYStem" literally: SYST:OCP:STAT is rejected with -113.
    def set_overcurrent_protection_enabled(self, enabled: bool, channel: int) -> None:
        _check_channel(channel)
        self._write_checked(f"SYS:OCP:STAT CH{channel},{'ON' if enabled else 'OFF'}")

    def get_overcurrent_protection_enabled(self, channel: int) -> bool:
        _check_channel(channel)
        return self._query_checked_bool(f"SYS:OCP:STAT? CH{channel}")

    def clear_protection(self, channel: int) -> None:
        _check_channel(channel)
        self._write_checked(f"RESET:CHAN:PROT CH{channel}")

    def set_overvoltage_protection_enabled(self, enabled: bool, channel: int) -> None:
        _check_channel(channel)
        raise FeatureNotSupportedError(
            f"set_overvoltage_protection_enabled is not supported by the {FRIENDLY_NAME}; OVP is always on"
        )

    def get_overvoltage_protection_enabled(self, channel: int) -> bool:
        _check_channel(channel)
        raise FeatureNotSupportedError(
            f"get_overvoltage_protection_enabled is not supported by the {FRIENDLY_NAME}; OVP is always on"
        )

    def set_overvoltage_protection_delay(self, delay: float, channel: int) -> None:
        _check_channel(channel)
        raise FeatureNotSupportedError(f"set_overvoltage_protection_delay is not supported by the {FRIENDLY_NAME}")

    def get_overvoltage_protection_delay(self, channel: int) -> float:
        _check_channel(channel)
        raise FeatureNotSupportedError(f"get_overvoltage_protection_delay is not supported by the {FRIENDLY_NAME}")

    # A trip turns the output off and latches it, but MEAS:RUN:MODE? and the status registers don't reflect it.
    def get_overvoltage_protection_tripped(self, channel: int) -> bool:
        _check_channel(channel)
        raise FeatureNotSupportedError(
            f"get_overvoltage_protection_tripped is not supported by the {FRIENDLY_NAME}; it reports no trip state"
        )

    def get_overcurrent_protection_tripped(self, channel: int) -> bool:
        _check_channel(channel)
        raise FeatureNotSupportedError(
            f"get_overcurrent_protection_tripped is not supported by the {FRIENDLY_NAME}; it reports no trip state"
        )

    def set_remote_sense_enabled(self, enabled: bool, channel: int) -> None:
        _check_channel(channel)
        raise FeatureNotSupportedError(
            f"set_remote_sense_enabled is not supported by the {FRIENDLY_NAME}; remote sense is set by rear-panel wiring"
        )

    def get_remote_sense_enabled(self, channel: int) -> bool:
        _check_channel(channel)
        raise FeatureNotSupportedError(
            f"get_remote_sense_enabled is not supported by the {FRIENDLY_NAME}; remote sense is set by rear-panel wiring"
        )

    def _write_verified(self, header: str, label: str, value: float, channel: int) -> None:
        # The instrument silently clamps out-of-range values instead of reporting a SCPI error.
        sent = round(value, 3)
        with self._visa.lock():
            self._visa.write(f"{header} CH{channel},{sent:.3f}")
            self._check_errors()
            applied = float(self._visa.query(f"{header}? CH{channel}"))
            self._check_errors()
        if abs(applied - sent) > _CLAMP_TOLERANCE:
            raise ValueError(
                f"{label} {value:.3f} is out of range for the {FRIENDLY_NAME}; it applied {applied:.3f} instead"
            )

    def _write_checked(self, command: str) -> None:
        with self._visa.lock():
            self._visa.write(command)
            self._check_errors()

    def _query_checked_float(self, command: str) -> float:
        with self._visa.lock():
            value = self._visa.query(command)
            self._check_errors()
            return float(value)

    def _query_checked_bool(self, command: str) -> bool:
        with self._visa.lock():
            value = self._visa.query(command)
            self._check_errors()
        return value.strip() == "1"

    def _check_errors(self) -> None:
        # Usually "+0, No error"; some commands (e.g. RESET:CHAN:PROT) acknowledge with "0,No error,<header>".
        err = self._visa.query("SYST:ERR?").strip()
        if err.split(",", 1)[0].lstrip("+").strip() != "0":
            raise RuntimeError(f"Siglent SPS5042X reported error: {err}")


def _check_channel(channel: int) -> None:
    # The SPS5042X answers CH2/CH3 commands without an error, so validate here.
    if channel != 1:
        raise ValueError("Siglent SPS5042X channel must be 1")
