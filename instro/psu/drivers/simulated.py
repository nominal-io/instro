"""Simulated PSU driver."""

from instro.lib.exceptions import FeatureNotSupportedError
from instro.lib.transports.visa import VisaConfig, VisaDriver
from instro.psu import PSUDriverBase


class SimulatedPSU(PSUDriverBase):
    """Client for the in-process simulated PSU SCPI server."""

    def __init__(self, visa_resource: str | VisaConfig) -> None:
        self._visa = VisaDriver(visa_resource)

    def open(self) -> None:
        self._visa.open()

    def close(self) -> None:
        self._visa.close()

    def set_voltage(self, voltage: float, channel: int) -> None:
        self._write_checked(f":SOUR{channel}:VOLT {voltage:.3f}")

    def get_voltage(self, channel: int) -> float:
        return self._query_checked_float(f":MEAS{channel}:VOLT?")

    def set_current_limit(self, current_limit: float, channel: int) -> None:
        self._write_checked(f":SOUR{channel}:CURR {current_limit:.3f}")

    def get_current(self, channel: int) -> float:
        return self._query_checked_float(f":MEAS{channel}:CURR?")

    def output_enable(self, enable: bool, channel: int) -> None:
        self._write_checked(f":OUTP{channel}:STAT {'ON' if enable else 'OFF'}")

    def get_output_status(self, channel: int) -> bool:
        with self._visa.lock():
            resp = self._visa.query(f":OUTP{channel}:STAT?")
            self._check_errors()
        return resp.strip() == "1"

    def set_overvoltage_protection_level(self, voltage: float, channel: int) -> None:
        self._write_checked(f":SOUR{channel}:VOLT:PROT {voltage:.3f}")

    def get_overvoltage_protection_level(self, channel: int) -> float:
        return self._query_checked_float(f":SOUR{channel}:VOLT:PROT:LEV?")

    def set_overvoltage_protection_enabled(self, enabled: bool, channel: int) -> None:
        self._write_checked(f":SOUR{channel}:VOLT:PROT:STAT {'ON' if enabled else 'OFF'}")

    def get_overvoltage_protection_enabled(self, channel: int) -> bool:
        with self._visa.lock():
            resp = self._visa.query(f":SOUR{channel}:VOLT:PROT:STAT?")
            self._check_errors()
        return resp.strip() == "1"

    def set_overvoltage_protection_delay(self, delay: float, channel: int) -> None:
        raise FeatureNotSupportedError("set_overvoltage_protection_delay is not supported by SimulatedPSU")

    def get_overvoltage_protection_delay(self, channel: int) -> float:
        raise FeatureNotSupportedError("get_overvoltage_protection_delay is not supported by SimulatedPSU")

    def set_overcurrent_protection_level(self, current: float, channel: int) -> None:
        self._write_checked(f":SOUR{channel}:CURR:PROT {current:.3f}")

    def get_overcurrent_protection_level(self, channel: int) -> float:
        return self._query_checked_float(f":SOUR{channel}:CURR:PROT:LEV?")

    def set_overcurrent_protection_enabled(self, enabled: bool, channel: int) -> None:
        self._write_checked(f":SOUR{channel}:CURR:PROT:STAT {'ON' if enabled else 'OFF'}")

    def get_overcurrent_protection_enabled(self, channel: int) -> bool:
        with self._visa.lock():
            resp = self._visa.query(f":SOUR{channel}:CURR:PROT:STAT?")
            self._check_errors()
        return resp.strip() == "1"

    def set_remote_sense_enabled(self, enabled: bool, channel: int) -> None:
        self._write_checked(f":SYST{channel}:SENS {'REM' if enabled else 'LOC'}")

    def get_remote_sense_enabled(self, channel: int) -> bool:
        with self._visa.lock():
            resp = self._visa.query(f":SYST{channel}:SENS?")
            self._check_errors()
        return resp.strip().upper() == "REM"

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
        err = self._visa.query(":SYST:ERR?")
        if err.split(",", 1)[0].strip().lstrip("+") != "0":
            raise RuntimeError(f"Simulated PSU reported error: {err}")
