"""B&K Precision 9115-series PSU driver. SCPI surface is shared with other single-channel B&K models."""

from instro.lib.exceptions import FeatureNotSupportedError
from instro.lib.transports.visa import VisaConfig, VisaDriver
from instro.psu import PSUDriverBase

FRIENDLY_NAME = "B&K Precision 9115-series PSU"


class BK9115(PSUDriverBase):
    """B&K Precision 9115-series single-channel PSU."""

    def __init__(self, visa_resource: str | VisaConfig) -> None:
        self._visa = VisaDriver(visa_resource)

    def open(self) -> None:
        self._visa.open()

    def close(self) -> None:
        self._visa.close()

    def set_voltage(self, voltage: float, channel: int) -> None:
        _check_channel(channel)
        self._write_checked(f"VOLT {voltage:.3f}")

    def get_voltage(self, channel: int) -> float:
        _check_channel(channel)
        return self._query_checked_float("MEAS:VOLT?")

    def set_current_limit(self, current_limit: float, channel: int) -> None:
        _check_channel(channel)
        self._write_checked(f"CURR {current_limit:.3f}")

    def get_current(self, channel: int) -> float:
        _check_channel(channel)
        return self._query_checked_float("MEAS:CURR?")

    def output_enable(self, enable: bool, channel: int) -> None:
        _check_channel(channel)
        self._write_checked("OUTP:STAT ON" if enable else "OUTP:STAT OFF")

    def get_output_status(self, channel: int) -> bool:
        _check_channel(channel)
        with self._visa.lock():
            resp = self._visa.query("OUTP:STAT?")
            self._check_errors()
        return resp == "1"

    def set_overvoltage_protection_level(self, voltage: float, channel: int) -> None:
        _check_channel(channel)
        self._write_checked(f"VOLT:PROT {voltage:.3f}")

    def get_overvoltage_protection_level(self, channel: int) -> float:
        _check_channel(channel)
        return self._query_checked_float("VOLT:PROT?")

    def set_overvoltage_protection_enabled(self, enabled: bool, channel: int) -> None:
        _check_channel(channel)
        self._write_checked("VOLT:PROT:STAT ON" if enabled else "VOLT:PROT:STAT OFF")

    def get_overvoltage_protection_enabled(self, channel: int) -> bool:
        _check_channel(channel)
        with self._visa.lock():
            resp = self._visa.query("VOLT:PROT:STAT?")
            self._check_errors()
        return resp == "1"

    def set_overvoltage_protection_delay(self, delay: float, channel: int) -> None:
        _check_channel(channel)
        self._write_checked(f"VOLT:PROT:DEL {delay:.3f}")

    def get_overvoltage_protection_delay(self, channel: int) -> float:
        _check_channel(channel)
        return self._query_checked_float("VOLT:PROT:DEL?")

    def set_overcurrent_protection_level(self, current: float, channel: int) -> None:
        _check_channel(channel)
        raise FeatureNotSupportedError(f"set_overcurrent_protection_level is not supported by the {FRIENDLY_NAME}")

    def get_overcurrent_protection_level(self, channel: int) -> float:
        _check_channel(channel)
        raise FeatureNotSupportedError(f"get_overcurrent_protection_level is not supported by the {FRIENDLY_NAME}")

    def set_overcurrent_protection_enabled(self, enabled: bool, channel: int) -> None:
        _check_channel(channel)
        raise FeatureNotSupportedError(f"set_overcurrent_protection_enabled is not supported by the {FRIENDLY_NAME}")

    def get_overcurrent_protection_enabled(self, channel: int) -> bool:
        _check_channel(channel)
        raise FeatureNotSupportedError(f"get_overcurrent_protection_enabled is not supported by the {FRIENDLY_NAME}")

    def set_remote_sense_enabled(self, enabled: bool, channel: int) -> None:
        _check_channel(channel)
        raise FeatureNotSupportedError(f"set_remote_sense_enabled is not supported by the {FRIENDLY_NAME}")

    def get_remote_sense_enabled(self, channel: int) -> bool:
        _check_channel(channel)
        raise FeatureNotSupportedError(f"get_remote_sense_enabled is not supported by the {FRIENDLY_NAME}")

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
        if not err.startswith("0"):
            raise RuntimeError(f"BK PSU reported error: {err}")


def _check_channel(channel: int) -> None:
    if channel != 1:
        raise ValueError("BK 9115 channel must be 1")
