"""Agilent/HP/Keysight 34401A DMM driver (SCPI over GPIB or RS-232)."""

from __future__ import annotations

import time

from instro.dmm import DMMDriverBase
from instro.dmm.types import MeasurementFunction
from instro.lib.transports.visa import VisaConfig, VisaDriver


class Agilent34401A(DMMDriverBase):
    """Agilent/HP/Keysight 34401A DMM."""

    def __init__(self, visa_resource: str | VisaConfig) -> None:
        self._visa = VisaDriver(visa_resource)
        self._range: float | None = None
        self._resolution: float | None = None

    def open(self) -> None:
        """Open transport, ``*CLS``, then ``SYST:REM`` to disable the front panel and RS-232 echo."""
        self._visa.open()
        self._visa.write("*CLS")
        time.sleep(0.5)
        self._visa.write("SYST:REM")
        time.sleep(0.5)
        self._check_errors()

    def close(self) -> None:
        self._visa.close()

    def set_measurement_function(self, function: MeasurementFunction) -> None:
        """Configure the function for subsequent reads via a dummy measurement."""
        match function:
            case MeasurementFunction.DC_VOLTAGE:
                self.measure_dc_voltage()
            case MeasurementFunction.AC_VOLTAGE:
                self.measure_ac_voltage()
            case MeasurementFunction.DC_CURRENT:
                self.measure_dc_current()
            case MeasurementFunction.AC_CURRENT:
                self.measure_ac_current()
            case MeasurementFunction.TWO_WIRE_RESISTANCE:
                self.measure_resistance()
            case MeasurementFunction.FOUR_WIRE_RESISTANCE:
                self.measure_four_wire_resistance()
            case _:
                raise NotImplementedError(f"Agilent 34401A does not support {function.name}.")

    def set_digits(self, n: int) -> None:
        """Set the digits (4, 5, or 6) used as the resolution argument in ``MEAS:...?`` commands."""
        if n not in (4, 5, 6):
            raise ValueError("Agilent 34401A supports 4, 5, or 6 digit resolution.")
        res_map = {4: 0.001, 5: 0.0001, 6: 0.00001}
        self._resolution = res_map[n]

    def _store_range(self, value: float | None) -> None:
        """Cache the range for ``MEAS:...?`` calls. The 34401A's cache is shared across functions, so every ``set_*_range`` delegates here."""
        self._range = value

    set_dc_voltage_range = _store_range
    set_ac_voltage_range = _store_range
    set_dc_current_range = _store_range
    set_ac_current_range = _store_range
    set_two_wire_resistance_range = _store_range
    set_four_wire_resistance_range = _store_range

    def _build_meas_cmd(self, base_cmd: str) -> str:
        """Build ``MEAS:...?`` appending range when set, and resolution when set alongside it."""
        if self._range is None:
            return f"{base_cmd}?"
        if self._resolution is None:
            return f"{base_cmd}? {self._range:.6e}"
        return f"{base_cmd}? {self._range:.6e},{self._resolution:.6e}"

    def measure_dc_voltage(self) -> float:
        return self._query_checked_float(self._build_meas_cmd("MEAS:VOLT:DC"))

    def measure_dc_current(self) -> float:
        return self._query_checked_float(self._build_meas_cmd("MEAS:CURR:DC"))

    def measure_resistance(self) -> float:
        return self._query_checked_float(self._build_meas_cmd("MEAS:RES"))

    def measure_four_wire_resistance(self) -> float:
        return self._query_checked_float(self._build_meas_cmd("MEAS:FRES"))

    def measure_ac_voltage(self) -> float:
        return self._query_checked_float(self._build_meas_cmd("MEAS:VOLT:AC"))

    def measure_ac_current(self) -> float:
        return self._query_checked_float(self._build_meas_cmd("MEAS:CURR:AC"))

    def _query_checked_float(self, command: str) -> float:
        with self._visa.lock():
            value = self._visa.query(command)
            self._check_errors()
            return float(value)

    def _check_errors(self) -> None:
        err = self._visa.query("SYST:ERR?")
        parts = err.strip().split(",", 1)
        code_str = parts[0] if parts else ""
        code_val = int(code_str) if code_str.lstrip("-+").isdigit() else -1
        if code_val != 0:
            raise RuntimeError(f"Agilent 34401A reported error: {err.strip()}")
