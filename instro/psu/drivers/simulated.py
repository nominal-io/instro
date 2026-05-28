"""Simulated PSU driver. Client-side counterpart to :mod:`instro.psu.scpi_sim_server`.

Connect via a TCP socket VISA resource such as ``TCPIP0::127.0.0.1::5025::SOCKET``.
"""

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

    def set_voltage(self, voltage: float, channel: int = 1) -> None:
        self._write_checked(f"VOLT {voltage:.3f} {channel}")

    def get_voltage(self, channel: int = 1) -> float:
        return self._query_checked_float(f"MEAS:VOLT? {channel}")

    def set_current_limit(self, current_limit: float, channel: int = 1) -> None:
        self._write_checked(f"CURR {current_limit:.3f} {channel}")

    def get_current(self, channel: int = 1) -> float:
        return self._query_checked_float(f"MEAS:CURR? {channel}")

    def output_enable(self, enable: bool, channel: int = 1) -> None:
        cmd = f"OUTP:STAT ON {channel}" if enable else f"OUTP:STAT OFF {channel}"
        self._write_checked(cmd)

    def get_output_status(self, channel: int = 1) -> bool:
        with self._visa.lock():
            resp = self._visa.query(f"OUTP:STAT? {channel}")
            self._check_errors()
        return resp == "ON"

    def set_overvoltage_protection(self, voltage: float, channel: int = 1) -> None:
        raise FeatureNotSupportedError("set_overvoltage_protection is not supported by SimulatedPSU")

    def get_overvoltage_protection(self, channel: int = 1) -> float:
        raise FeatureNotSupportedError("get_overvoltage_protection is not supported by SimulatedPSU")

    def set_overcurrent_protection(self, current: float, channel: int = 1) -> None:
        raise FeatureNotSupportedError("set_overcurrent_protection is not supported by SimulatedPSU")

    def get_overcurrent_protection(self, channel: int = 1) -> float:
        raise FeatureNotSupportedError("get_overcurrent_protection is not supported by SimulatedPSU")

    def set_remote_sense(self, enabled: bool, channel: int = 1) -> None:
        raise FeatureNotSupportedError("set_remote_sense is not supported by SimulatedPSU")

    def get_remote_sense(self, channel: int = 1) -> bool:
        raise FeatureNotSupportedError("get_remote_sense is not supported by SimulatedPSU")

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
        if not err.startswith("0"):
            raise RuntimeError(f"Simulated PSU reported error: {err}")
