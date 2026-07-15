"""Simulated DMM driver."""

from instro.dmm import DMMDriverBase
from instro.dmm.types import MeasurementFunction
from instro.lib.transports.visa import VisaConfig, VisaDriver

_FUNCTION_NAMES = {
    MeasurementFunction.DC_VOLTAGE: "VOLT:DC",
    MeasurementFunction.AC_VOLTAGE: "VOLT:AC",
    MeasurementFunction.DC_CURRENT: "CURR:DC",
    MeasurementFunction.AC_CURRENT: "CURR:AC",
    MeasurementFunction.TWO_WIRE_RESISTANCE: "RES",
}


class SimulatedDMM(DMMDriverBase):
    """Client for the in-process simulated DMM SCPI server."""

    def __init__(self, visa_resource: str | VisaConfig) -> None:
        self._visa = VisaDriver(visa_resource)

    def open(self) -> None:
        self._visa.open()

    def close(self) -> None:
        self._visa.close()

    def set_measurement_function(self, function: MeasurementFunction) -> None:
        name = _FUNCTION_NAMES.get(function)
        if name is None:
            raise NotImplementedError(f"SimulatedDMM does not support {function.name}.")
        self._write_checked(f'FUNC "{name}"')

    def measure_dc_voltage(self) -> float:
        return self._query_checked_float("MEAS:VOLT:DC?")

    def measure_ac_voltage(self) -> float:
        return self._query_checked_float("MEAS:VOLT:AC?")

    def measure_dc_current(self) -> float:
        return self._query_checked_float("MEAS:CURR:DC?")

    def measure_ac_current(self) -> float:
        return self._query_checked_float("MEAS:CURR:AC?")

    def measure_resistance(self) -> float:
        return self._query_checked_float("MEAS:RES?")

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
        if err.split(",", 1)[0].strip().lstrip("+") != "0":
            raise RuntimeError(f"Simulated DMM reported error: {err}")
