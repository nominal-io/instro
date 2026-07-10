"""Keysight 34461A Truevolt DMM driver (SCPI over LAN, USB, or GPIB)."""

from __future__ import annotations

from instro.dmm import DMMDriverBase
from instro.dmm.types import MeasurementFunction
from instro.lib.transports.visa import VisaConfig, VisaDriver

_FUNCTION_COMMANDS = {
    MeasurementFunction.DC_VOLTAGE: "VOLT",
    MeasurementFunction.AC_VOLTAGE: "VOLT:AC",
    MeasurementFunction.DC_CURRENT: "CURR",
    MeasurementFunction.AC_CURRENT: "CURR:AC",
    MeasurementFunction.TWO_WIRE_RESISTANCE: "RES",
    MeasurementFunction.FOUR_WIRE_RESISTANCE: "FRES",
}


class Keysight34461A(DMMDriverBase):
    """Keysight 34461A Truevolt DMM."""

    def __init__(self, visa_resource: str | VisaConfig) -> None:
        self._visa = VisaDriver(visa_resource)

    def open(self) -> None:
        """Open transport and clear status/error queue. Truevolt has no ``SYST:REM``; remote is automatic."""
        self._visa.open()
        with self._visa.lock():
            self._visa.write("*CLS")
            self._check_errors()

    def close(self) -> None:
        self._visa.close()

    def set_measurement_function(self, function: MeasurementFunction) -> None:
        """Select the function with ``FUNC``; per-function range/NPLC settings are retained across switches."""
        self._write_checked(f'FUNC "{_FUNCTION_COMMANDS[function]}"')

    def set_digits(self, n: int) -> None:
        """Unsupported — Truevolt SCPI has no digits command; use ``set_aperture_nplc`` for resolution."""
        raise NotImplementedError(
            "Keysight 34461A does not support set_digits; use set_aperture_nplc(...) for resolution."
        )

    def _set_range(self, scpi_root: str, value: float | None) -> None:
        with self._visa.lock():
            if value is None:
                self._visa.write(f"{scpi_root}:RANG:AUTO ON")
            else:
                self._visa.write(f"{scpi_root}:RANG {value:.6e}")
            self._check_errors()

    def set_dc_voltage_range(self, value: float | None) -> None:
        self._set_range("VOLT:DC", value)

    def set_ac_voltage_range(self, value: float | None) -> None:
        self._set_range("VOLT:AC", value)

    def set_dc_current_range(self, value: float | None) -> None:
        self._set_range("CURR:DC", value)

    def set_ac_current_range(self, value: float | None) -> None:
        self._set_range("CURR:AC", value)

    def set_two_wire_resistance_range(self, value: float | None) -> None:
        self._set_range("RES", value)

    def set_four_wire_resistance_range(self, value: float | None) -> None:
        self._set_range("FRES", value)

    def _set_nplc(self, scpi_root: str, nplc: float) -> None:
        self._write_checked(f"{scpi_root}:NPLC {nplc:.4f}")

    def set_dc_voltage_nplc(self, nplc: float) -> None:
        self._set_nplc("VOLT:DC", nplc)

    def set_dc_current_nplc(self, nplc: float) -> None:
        self._set_nplc("CURR:DC", nplc)

    def set_two_wire_resistance_nplc(self, nplc: float) -> None:
        self._set_nplc("RES", nplc)

    def set_four_wire_resistance_nplc(self, nplc: float) -> None:
        self._set_nplc("FRES", nplc)

    def set_ac_voltage_nplc(self, nplc: float) -> None:
        """Unsupported — AC functions integrate via filter bandwidth (``VOLT:AC:BAND``), not NPLC."""
        raise NotImplementedError("Keysight 34461A AC voltage has no NPLC; integration is set by filter bandwidth.")

    def set_ac_current_nplc(self, nplc: float) -> None:
        """Unsupported — AC functions integrate via filter bandwidth (``CURR:AC:BAND``), not NPLC."""
        raise NotImplementedError("Keysight 34461A AC current has no NPLC; integration is set by filter bandwidth.")

    def _measure(self, function_command: str) -> float:
        # FUNC + READ? instead of MEAS:...? — MEAS (like CONF) resets range/NPLC
        # to defaults, discarding anything the set_* methods programmed.
        with self._visa.lock():
            self._visa.write(f'FUNC "{function_command}"')
            value = self._visa.query("READ?")
            self._check_errors()
            return float(value)

    def measure_dc_voltage(self) -> float:
        return self._measure("VOLT")

    def measure_ac_voltage(self) -> float:
        return self._measure("VOLT:AC")

    def measure_dc_current(self) -> float:
        return self._measure("CURR")

    def measure_ac_current(self) -> float:
        return self._measure("CURR:AC")

    def measure_resistance(self) -> float:
        return self._measure("RES")

    def measure_four_wire_resistance(self) -> float:
        return self._measure("FRES")

    def _write_checked(self, command: str) -> None:
        with self._visa.lock():
            self._visa.write(command)
            self._check_errors()

    def _check_errors(self) -> None:
        err = self._visa.query("SYST:ERR?")
        parts = err.strip().split(",", 1)
        code_str = parts[0] if parts else ""
        # No-error response is +0,"No error" (Truevolt Operating and Service Guide, SYSTem:ERRor?).
        code_val = int(code_str) if code_str.lstrip("-+").isdigit() else -1
        if code_val != 0:
            raise RuntimeError(f"Keysight 34461A reported error: {err.strip()}")
