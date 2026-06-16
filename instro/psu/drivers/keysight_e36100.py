"""Keysight E36100-series PSU driver."""

from instro.lib.exceptions import FeatureNotSupportedError
from instro.lib.transports.visa import VisaConfig, VisaDriver
from instro.psu import PSUDriverBase


class KeysightE36100(PSUDriverBase):
    """Keysight E36100-series single-channel PSU."""

    FRIENDLY_NAME = "Keysight E36100-series PSU"

    def __init__(self, visa_resource: str | VisaConfig) -> None:
        self._visa = VisaDriver(visa_resource)

    def open(self) -> None:
        self._visa.open()

    def close(self) -> None:
        self._visa.close()

    def set_voltage(self, voltage: float, channel: int) -> None:
        self._write_checked(f"VOLT {voltage:.3f}")

    def get_voltage(self, channel: int) -> float:
        return self._query_checked_float("MEAS:VOLT?")

    def set_current_limit(self, current_limit: float, channel: int) -> None:
        self._write_checked(f"CURR {current_limit:.3f}")

    def get_current(self, channel: int) -> float:
        return self._query_checked_float("MEAS:CURR?")

    def output_enable(self, enable: bool, channel: int) -> None:
        self._write_checked(f"OUTP:STAT {'ON' if enable else 'OFF'}")

    def get_output_status(self, channel: int) -> bool:
        return self._query_checked_bool("OUTP:STAT?")

    def set_overvoltage_protection_level(self, voltage: float, channel: int) -> None:
        self._write_checked(f"VOLT:PROT {voltage:.3f}")

    def get_overvoltage_protection_level(self, channel: int) -> float:
        return self._query_checked_float("VOLT:PROT:LEV?")

    def set_overvoltage_protection_enabled(self, enabled: bool, channel: int) -> None:
        self._write_checked(f"VOLT:PROT:STAT {'ON' if enabled else 'OFF'}")

    def get_overvoltage_protection_enabled(self, channel: int) -> bool:
        return self._query_checked_bool("VOLT:PROT:STAT?")

    def set_overvoltage_protection_delay(self, delay: float, channel: int) -> None:
        raise FeatureNotSupportedError(f"set_overvoltage_protection_delay is not supported by the {self.FRIENDLY_NAME}")

    def get_overvoltage_protection_delay(self, channel: int) -> float:
        raise FeatureNotSupportedError(f"get_overvoltage_protection_delay is not supported by the {self.FRIENDLY_NAME}")

    def set_overcurrent_protection_level(self, current: float, channel: int) -> None:
        raise FeatureNotSupportedError(
            f"The {self.FRIENDLY_NAME} has no separate OCP level command. Set the current limit with CURR; "
            f"CURR:PROT:STAT enables OCP, which puts the instrument in a protected state when the power supply "
            f"status is in constant current mode."
        )

    def get_overcurrent_protection_level(self, channel: int) -> float:
        raise FeatureNotSupportedError(
            f"The {self.FRIENDLY_NAME} has no separate OCP level query. CURR? queries the programmed current limit; "
            f"CURR:PROT:STAT? returns whether OCP is enabled."
        )

    def set_overcurrent_protection_enabled(self, enabled: bool, channel: int) -> None:
        self._write_checked(f"CURR:PROT:STAT {'ON' if enabled else 'OFF'}")

    def get_overcurrent_protection_enabled(self, channel: int) -> bool:
        return self._query_checked_bool("CURR:PROT:STAT?")

    def set_remote_sense_enabled(self, enabled: bool, channel: int) -> None:
        self._write_checked(f"VOLT:SENS {'EXT' if enabled else 'INT'}")

    def get_remote_sense_enabled(self, channel: int) -> bool:
        return self._query_checked_bool("VOLT:SENS?")

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
        return value.strip().upper() in {"1", "ON", "EXT"}

    def _check_errors(self) -> None:
        err = self._visa.query("SYST:ERR?")
        code = err.strip().split(",", 1)[0].lstrip("+")
        if code != "0":
            raise RuntimeError(f"The {self.FRIENDLY_NAME} reported error: {err.strip()}")
