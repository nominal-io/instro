"""Simulated E-Load driver."""

from instro.eload import ELoadDriverBase
from instro.eload.types import LoadMode, SlewRateDirection
from instro.lib.transports.visa import VisaConfig, VisaDriver

_MODE_ROOT = {
    LoadMode.CC: "CURR",
    LoadMode.CV: "VOLT",
    LoadMode.CP: "POW",
    LoadMode.CR: "RES",
}


class SimulatedELoad(ELoadDriverBase):
    """Client for the in-process simulated E-Load SCPI server."""

    def __init__(self, visa_resource: str | VisaConfig) -> None:
        self._visa = VisaDriver(visa_resource)

    def open(self) -> None:
        self._visa.open()

    def close(self) -> None:
        self._visa.close()

    def set_mode(self, mode: LoadMode, channel: int) -> None:
        self._write_checked(f":SOUR{channel}:FUNC {_MODE_ROOT[mode]}")

    def set_level(self, mode: LoadMode, value: float, channel: int, curr_limit: float | None) -> None:
        with self._visa.lock():
            self._visa.write(f":SOUR{channel}:{_MODE_ROOT[mode]} {value:.3f}")
            if mode is LoadMode.CV and curr_limit is not None:
                self._visa.write(f":SOUR{channel}:CURR:LIM {curr_limit:.3f}")
            self._check_errors()

    def set_range(self, mode: LoadMode, value: float, channel: int) -> None:
        self._write_checked(f":SOUR{channel}:{_MODE_ROOT[mode]}:RANG {value:.3f}")

    def set_slewrate(self, direction: SlewRateDirection, rate: float, channel: int) -> None:
        if direction is SlewRateDirection.BOTH:
            self._write_checked(f":SOUR{channel}:CURR:SLEW {rate:.3f}")
        else:
            self._write_checked(f":SOUR{channel}:CURR:SLEW:{direction.value} {rate:.3f}")

    def output_enable(self, enable: bool, channel: int) -> None:
        self._write_checked(f":INP{channel}:STAT {'ON' if enable else 'OFF'}")

    def short_output(self, enable: bool, channel: int) -> None:
        self._write_checked(f":INP{channel}:SHOR {'ON' if enable else 'OFF'}")

    def get_current(self, channel: int) -> float:
        return self._query_checked_float(f":MEAS{channel}:CURR?")

    def get_voltage(self, channel: int) -> float:
        return self._query_checked_float(f":MEAS{channel}:VOLT?")

    def get_power(self, channel: int) -> float:
        return self._query_checked_float(f":MEAS{channel}:POW?")

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
            raise RuntimeError(f"Simulated E-Load reported error: {err}")
