"""TDK Lambda Genesys-family PSU driver (single-channel)."""

from instro.lib.transports.visa import VisaConfig, VisaDriver
from instro.psu import PSUDriverBase


class TDKLambdaGenesys(PSUDriverBase):
    """TDK Lambda Genesys-family single-channel PSU."""

    def __init__(self, visa_resource: str | VisaConfig) -> None:
        self._visa = VisaDriver(visa_resource)

    def open(self) -> None:
        self._visa.open()

    def close(self) -> None:
        self._visa.close()

    def set_voltage(self, voltage: float, channel: int = 1) -> None:
        self._write_checked(f"VOLT {voltage:.3f}")

    def get_voltage(self, channel: int = 1) -> float:
        return self._query_checked_float("MEAS:VOLT?")

    def set_current_limit(self, current_limit: float, channel: int = 1) -> None:
        self._write_checked(f"CURR {current_limit:.3f}")

    def get_current(self, channel: int = 1) -> float:
        return self._query_checked_float("MEAS:CURR?")

    def output_enable(self, enable: bool, channel: int = 1) -> None:
        self._write_checked("OUTP:STAT ON" if enable else "OUTP:STAT OFF")

    def get_output_status(self, channel: int = 1) -> bool:
        with self._visa.lock():
            resp = self._visa.query("OUTP:STAT?")
            self._check_errors()
        return resp == "ON"

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
        err = self._visa.query("SYSTEM:ERROR?")
        if not err.startswith("+0"):
            raise RuntimeError(f"TDK Lambda PSU reported error: {err}")
