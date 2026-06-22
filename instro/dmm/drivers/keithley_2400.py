"""Keithley 2400 SMU driver (DMM-style sense only).

Supports DC voltage, DC current, and 2-wire resistance. Source is held at zero
to enable pure sensing. AC is not supported by the 2400.
"""

from __future__ import annotations

from instro.dmm import DMMDriverBase
from instro.dmm.types import MeasurementFunction
from instro.lib.transports.visa import VisaConfig, VisaDriver


class Keithley2400(DMMDriverBase):
    """Keithley 2400 SMU as a sense-only DMM.

    Integration time is set via NPLC (0.01–10); the 2400 has no aperture-in-seconds.
    Digits aren't directly settable — use NPLC. AC is not supported.
    """

    def __init__(self, visa_resource: str | VisaConfig) -> None:
        self._visa = VisaDriver(visa_resource)

    def open(self) -> None:
        self._visa.open()
        with self._visa.lock():
            self._visa.write("*CLS")
            self._check_errors()

    def close(self) -> None:
        try:
            if self._visa.is_open:
                self._visa.write(":OUTP OFF")
        finally:
            self._visa.close()

    def set_measurement_function(self, function: MeasurementFunction) -> None:
        """Configure the 2400 sense/source for ``function``."""
        with self._visa.lock():
            if function == MeasurementFunction.DC_VOLTAGE:
                # Release auto-ohms before changing the source function; otherwise
                # the 2400 rejects :SOUR:FUNC with error 825.
                self._visa.write(":SENS:RES:MODE MAN")
                self._visa.write(":SENS:FUNC 'VOLT'")
                self._visa.write(":FORM:ELEM VOLT")
                # SMU: hold source at zero so we can sense without forcing.
                self._visa.write(":SOUR:FUNC VOLT")
                self._visa.write(":SOUR:VOLT:LEV 0")
            elif function == MeasurementFunction.DC_CURRENT:
                self._visa.write(":SENS:RES:MODE MAN")
                self._visa.write(":SENS:FUNC 'CURR'")
                self._visa.write(":FORM:ELEM CURR")
                self._visa.write(":SOUR:FUNC CURR")
                self._visa.write(":SOUR:CURR:LEV 0")
            elif function == MeasurementFunction.TWO_WIRE_RESISTANCE:
                self._visa.write(":SENS:FUNC 'RES'")
                self._visa.write(":SENS:RES:MODE AUTO")
                self._visa.write(":FORM:ELEM RES")
            else:
                raise NotImplementedError(
                    f"Keithley 2400 DMM driver does not support {function.name}; "
                    "only DC_VOLTAGE, DC_CURRENT, and TWO_WIRE_RESISTANCE are supported."
                )
            self._check_errors()

    def set_digits(self, n: int) -> None:
        """Unsupported — the 2400 has no digits SCPI; use ``set_aperture_nplc`` for resolution."""
        raise NotImplementedError(
            "Keithley 2400 does not support set_digits; use set_aperture_nplc(...) for resolution."
        )

    def _set_nplc(self, scpi_root: str, nplc: float) -> None:
        self._write_checked(f"{scpi_root}:NPLC {nplc:.4f}")

    def set_dc_voltage_nplc(self, nplc: float) -> None:
        self._set_nplc(":SENS:VOLT", nplc)

    def set_dc_current_nplc(self, nplc: float) -> None:
        self._set_nplc(":SENS:CURR", nplc)

    def set_two_wire_resistance_nplc(self, nplc: float) -> None:
        self._set_nplc(":SENS:RES", nplc)

    def _set_sense_only_range(self, scpi_root: str, value: float | None) -> None:
        """Set the sense range alone — used for resistance, where auto-ohms manages source."""
        with self._visa.lock():
            if value is None:
                self._visa.write(f"{scpi_root}:RANG:AUTO 1")
            else:
                self._visa.write(f"{scpi_root}:RANG:AUTO 0")
                self._visa.write(f"{scpi_root}:RANG {value:.6e}")
            self._check_errors()

    def _set_source_range(self, source_root: str, value: float | None) -> None:
        """Set V/I range via the source path.

        In V or I mode, ``:SENS:VOLT:RANG``/``:SENS:CURR:RANG`` are rejected
        with error 823 — the sense path runs through the source range, so
        ``:SOUR:VOLT:RANG``/``:SOUR:CURR:RANG`` sets the effective sense range
        too. The 2400 rounds up to its nearest fixed range (e.g. 10 → 20V on the 2400-C).
        """
        with self._visa.lock():
            if value is None:
                self._visa.write(f"{source_root}:RANG:AUTO 1")
            else:
                self._visa.write(f"{source_root}:RANG:AUTO 0")
                self._visa.write(f"{source_root}:RANG {value:.6e}")
            self._check_errors()

    def set_dc_voltage_range(self, value: float | None) -> None:
        self._set_source_range(":SOUR:VOLT", value)

    def set_dc_current_range(self, value: float | None) -> None:
        self._set_source_range(":SOUR:CURR", value)

    def set_two_wire_resistance_range(self, value: float | None) -> None:
        self._set_sense_only_range(":SENS:RES", value)

    def measure_dc_voltage(self) -> float:
        with self._visa.lock():
            self._visa.write("OUTPUT ON")
            value = float(self._visa.query(":READ?").strip().split(",")[0])
            self._check_errors()
        return value

    def measure_dc_current(self) -> float:
        with self._visa.lock():
            self._visa.write("OUTPUT ON")
            value = float(self._visa.query(":READ?").strip().split(",")[0])
            self._check_errors()
        return value

    def measure_resistance(self) -> float:
        with self._visa.lock():
            self._visa.write("OUTPUT ON")
            value = float(self._visa.query(":READ?").strip().split(",")[0])
            self._check_errors()
        return value

    def measure_ac_voltage(self) -> float:
        raise NotImplementedError("Keithley 2400 DMM driver does not support AC voltage measurement.")

    def measure_ac_current(self) -> float:
        raise NotImplementedError("Keithley 2400 DMM driver does not support AC current measurement.")

    def _write_checked(self, command: str) -> None:
        with self._visa.lock():
            self._visa.write(command)
            self._check_errors()

    def _check_errors(self) -> None:
        err = self._visa.query(":SYST:ERR?")
        parts = err.strip().split(",", 1)
        code_str = parts[0] if parts else ""
        # The 2400 signs the no-error code: +0,"No error" (User's Manual Appendix B, Table B-1).
        code_val = int(code_str) if code_str.lstrip("-+").isdigit() else -1
        if code_val != 0:
            raise RuntimeError(f"Keithley 2400 reported error: {err.strip()}")
