"""Keithley 2750 Multimeter/Switch System driver (front-panel DMM only).

Supports DC/AC voltage, DC/AC current, and 2- and 4-wire resistance via the
front-panel INPUT terminals. Switching/scan-card functionality is not covered.
"""

from __future__ import annotations

from instro.dmm import DMMDriverBase
from instro.dmm.types import MeasurementFunction
from instro.lib.transports.visa import VisaConfig, VisaDriver

# :FUNCtion strings the 2750 expects (quoted on the wire). All six DMM functions
# are supported; the switch/scan-only functions are intentionally omitted.
_FUNCTION_SCPI: dict[MeasurementFunction, str] = {
    MeasurementFunction.DC_VOLTAGE: "VOLTage:DC",
    MeasurementFunction.AC_VOLTAGE: "VOLTage:AC",
    MeasurementFunction.DC_CURRENT: "CURRent:DC",
    MeasurementFunction.AC_CURRENT: "CURRent:AC",
    MeasurementFunction.TWO_WIRE_RESISTANCE: "RESistance",
    MeasurementFunction.FOUR_WIRE_RESISTANCE: "FRESistance",
}


class Keithley2750(DMMDriverBase):
    """Keithley 2750 as a front-panel DMM. Integration time is set via NPLC (0.01–60)."""

    def __init__(self, visa_resource: str | VisaConfig) -> None:
        self._visa = VisaDriver(visa_resource)

    def open(self) -> None:
        self._visa.open()
        with self._visa.lock():
            self._visa.write("*CLS")
            self._visa.write("*RST")
            # Reduce a reading to a bare float; the *RST default returns a
            # unit-suffixed "reading,timestamp,rdng#" string that won't parse.
            self._visa.write(":FORMat:ELEMents READing")
            self._check_errors()

    def close(self) -> None:
        self._visa.close()

    def set_measurement_function(self, function: MeasurementFunction) -> None:
        """Select ``function`` on the front-panel input."""
        try:
            scpi = _FUNCTION_SCPI[function]
        except KeyError:
            raise NotImplementedError(f"Keithley 2750 DMM driver does not support {function.name}.") from None
        self._write_checked(f':FUNCtion "{scpi}"')

    def set_digits(self, n: int) -> None:
        """Unsupported — :DIGits is display-only on the 2750; use ``set_aperture_nplc`` for resolution."""
        raise NotImplementedError(
            "Keithley 2750 :DIGits sets front-panel display resolution only and does not change the "
            "returned reading; use set_aperture_nplc(...) for measurement resolution."
        )

    def set_aperture_seconds(self, seconds: float) -> None:
        """Unsupported — aperture is per-function on the 2750; use ``set_aperture_nplc`` instead."""
        raise NotImplementedError("Keithley 2750 aperture is per-function; use set_aperture_nplc(...) instead.")

    # --- NPLC, scoped per function ---

    def _set_nplc(self, scpi_root: str, nplc: float, ac_detector: bool = False) -> None:
        with self._visa.lock():
            if ac_detector:
                # AC NPLC control is only honored when the AC detector bandwidth is 300 Hz.
                self._visa.write(f"{scpi_root}:DETector:BANDwidth 300")
            self._visa.write(f"{scpi_root}:NPLCycles {nplc:g}")
            self._check_errors()

    def set_dc_voltage_nplc(self, nplc: float) -> None:
        self._set_nplc(":SENSe:VOLTage:DC", nplc)

    def set_ac_voltage_nplc(self, nplc: float) -> None:
        self._set_nplc(":SENSe:VOLTage:AC", nplc, ac_detector=True)

    def set_dc_current_nplc(self, nplc: float) -> None:
        self._set_nplc(":SENSe:CURRent:DC", nplc)

    def set_ac_current_nplc(self, nplc: float) -> None:
        self._set_nplc(":SENSe:CURRent:AC", nplc, ac_detector=True)

    def set_two_wire_resistance_nplc(self, nplc: float) -> None:
        self._set_nplc(":SENSe:RESistance", nplc)

    def set_four_wire_resistance_nplc(self, nplc: float) -> None:
        self._set_nplc(":SENSe:FRESistance", nplc)

    # --- Range, scoped per function ---

    def _set_range(self, scpi_root: str, value: float | None) -> None:
        with self._visa.lock():
            if value is None:
                self._visa.write(f"{scpi_root}:RANGe:AUTO ON")
            else:
                self._visa.write(f"{scpi_root}:RANGe:AUTO OFF")
                self._visa.write(f"{scpi_root}:RANGe:UPPer {value:g}")
            self._check_errors()

    def set_dc_voltage_range(self, value: float | None) -> None:
        self._set_range(":SENSe:VOLTage:DC", value)

    def set_ac_voltage_range(self, value: float | None) -> None:
        self._set_range(":SENSe:VOLTage:AC", value)

    def set_dc_current_range(self, value: float | None) -> None:
        self._set_range(":SENSe:CURRent:DC", value)

    def set_ac_current_range(self, value: float | None) -> None:
        self._set_range(":SENSe:CURRent:AC", value)

    def set_two_wire_resistance_range(self, value: float | None) -> None:
        self._set_range(":SENSe:RESistance", value)

    def set_four_wire_resistance_range(self, value: float | None) -> None:
        self._set_range(":SENSe:FRESistance", value)

    # --- Measurements. Function is already selected; :READ? triggers a fresh reading. ---

    def _read_value(self) -> float:
        with self._visa.lock():
            response = self._visa.query(":READ?")
            self._check_errors()
        return float(response.strip().split(",")[0])

    def measure_dc_voltage(self) -> float:
        return self._read_value()

    def measure_ac_voltage(self) -> float:
        return self._read_value()

    def measure_dc_current(self) -> float:
        return self._read_value()

    def measure_ac_current(self) -> float:
        return self._read_value()

    def measure_resistance(self) -> float:
        return self._read_value()

    def measure_four_wire_resistance(self) -> float:
        return self._read_value()

    def _write_checked(self, command: str) -> None:
        with self._visa.lock():
            self._visa.write(command)
            self._check_errors()

    def _check_errors(self) -> None:
        err = self._visa.query(":SYSTem:ERRor?")
        parts = err.strip().split(",", 1)
        code_str = parts[0] if parts else ""
        code_val = int(code_str) if code_str.lstrip("-+").isdigit() else -1
        if code_val != 0:
            raise RuntimeError(f"Keithley 2750 reported error: {err.strip()}")
