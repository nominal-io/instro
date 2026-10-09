"""Fluke 8808A 5.5-digit multimeter driver over RS-232.

The 8808A's only remote interface is its RS-232 port, reached as a VISA ASRL resource. It
speaks the Fluke 45-style command set (``VDC``, ``RANGE 3``, ``MEAS1?``), not SCPI, and has no
error queue: the meter reports each command line's outcome with a prompt (``=>`` ok,
``?>`` command error, ``!>`` execution error).
"""

from __future__ import annotations

import pyvisa

from instro.dmm import DMMDriverBase
from instro.dmm.types import MeasurementFunction
from instro.lib import InstroError
from instro.lib.transports.visa import VisaConfig, VisaDriver

_PROMPT_OK = "=>"
_PROMPT_ERRORS = {
    "?>": "command error (not understood)",
    "!>": "execution error (understood but not executed)",
}
# Standard event status bits QYE | DDE | EXE | CME.
_ESR_ERROR_MASK = 0b0011_1100
_DEVICE_CLEAR = b"\x03"
_PROBE_TIMEOUT_MS = 500

_FUNCTION_COMMAND: dict[MeasurementFunction, str] = {
    MeasurementFunction.DC_VOLTAGE: "VDC",
    MeasurementFunction.AC_VOLTAGE: "VAC",
    MeasurementFunction.DC_CURRENT: "ADC",
    MeasurementFunction.AC_CURRENT: "AAC",
    # WIRE2/WIRE4 are only accepted once OHMS is active.
    MeasurementFunction.TWO_WIRE_RESISTANCE: "OHMS; WIRE2",
    MeasurementFunction.FOUR_WIRE_RESISTANCE: "OHMS; WIRE4",
}

_OHMS_RANGES = (200.0, 2e3, 20e3, 200e3, 2e6, 20e6, 100e6)

# Full scale (V, A, ohm) of each ``RANGE <n>`` index, n = 1, 2, ... (manual Table 4-11A).
_RANGES: dict[MeasurementFunction, tuple[float, ...]] = {
    MeasurementFunction.DC_VOLTAGE: (0.2, 2.0, 20.0, 200.0, 1000.0),
    MeasurementFunction.AC_VOLTAGE: (0.2, 2.0, 20.0, 200.0, 750.0),
    MeasurementFunction.DC_CURRENT: (200e-6, 2e-3, 20e-3, 200e-3, 2.0, 10.0),
    MeasurementFunction.AC_CURRENT: (20e-3, 200e-3, 2.0, 10.0),
    MeasurementFunction.TWO_WIRE_RESISTANCE: _OHMS_RANGES,
    MeasurementFunction.FOUR_WIRE_RESISTANCE: _OHMS_RANGES,
}

_DIGITS_RATE = {5: "S", 4: "F"}


class Fluke8808A(DMMDriverBase):
    """Fluke 8808A on its RS-232 port, primary display only. Resolution is set via ``set_digits``."""

    def __init__(self, visa_resource: str | VisaConfig, lockout: bool = False) -> None:
        """Configure the driver; the port is not opened until ``open()``.

        Args:
            visa_resource (str | VisaConfig): ASRL resource string, e.g. ``"ASRL3::INSTR"`` or
                ``"ASRL/dev/ttyUSB0::INSTR"``, or a ``VisaConfig`` whose ``serial_config`` matches
                the meter's front-panel RS-232 setup. The defaults (9600 baud, 8N1, no flow
                control) match the factory setup.
            lockout (bool): If ``True``, lock the front panel while in remote (``RWLS``) instead of
                leaving it usable (``REMS``).

        Example:
            >>> from instro.dmm import InstroDMM
            >>> from instro.unstable.dmm.drivers import Fluke8808A
            >>> dmm = InstroDMM(name="fluke", driver=Fluke8808A("ASRL/dev/ttyUSB0::INSTR"))
            >>> dmm.open()
            >>> dmm.read_dc_voltage()
        """
        self._visa = VisaDriver(visa_resource)
        self._lockout = lockout
        self._prompts = True
        self._function: MeasurementFunction | None = None

    def open(self) -> None:
        """Open the port, verify the meter's identity, and reset it into remote mode."""
        self._visa.open()
        try:
            with self._visa.lock():
                self._device_clear()
                idn = self._probe_identity()
                if "8808A" not in idn.upper():
                    raise InstroError(f"Device is not a Fluke 8808A: {idn!r}")
                self._transact("*RST")
                self._transact("*CLS")
                self._transact("RWLS" if self._lockout else "REMS")
                # Bare mantissa/exponent readings, and continuous triggering so MEAS1? returns.
                self._transact("FORMAT 1")
                self._transact("TRIGGER 1")
            self._function = MeasurementFunction.DC_VOLTAGE
        except BaseException:
            self._visa.close()
            raise

    def close(self) -> None:
        """Return the meter to local control and close the port."""
        try:
            if self._visa.is_open:
                self._transact("LOCS")
        finally:
            self._visa.close()
            self._function = None

    def set_measurement_function(self, function: MeasurementFunction) -> None:
        """Select ``function`` on the primary display."""
        self._transact(_FUNCTION_COMMAND[function])
        self._function = function

    def set_digits(self, n: int) -> None:
        """Set resolution: 5 selects the slow rate (2.5 rdg/s), 4 the fast rate (100 rdg/s).

        The medium rate has the same 4.5-digit resolution as fast, so it is not reachable here.
        """
        try:
            rate = _DIGITS_RATE[n]
        except KeyError:
            raise ValueError(f"Fluke 8808A supports 4 or 5 digit resolution, got {n}.") from None
        self._transact(f"RATE {rate}")

    def _set_range(self, function: MeasurementFunction, value: float | None) -> None:
        if self._function is not function:
            active = self._function.name if self._function else "none"
            raise InstroError(
                f"Fluke 8808A ranges apply to the active function ({active}); "
                f"select {function.name} with set_measurement_function first."
            )
        if value is None:
            self._transact("AUTO")
            return
        ranges = _RANGES[function]
        index = next((i for i, full_scale in enumerate(ranges, start=1) if abs(value) <= full_scale), None)
        if index is None:
            raise ValueError(f"Fluke 8808A {function.name} range {value:g} exceeds the top range {ranges[-1]:g}.")
        self._transact(f"RANGE {index}")

    def set_dc_voltage_range(self, value: float | None) -> None:
        self._set_range(MeasurementFunction.DC_VOLTAGE, value)

    def set_ac_voltage_range(self, value: float | None) -> None:
        self._set_range(MeasurementFunction.AC_VOLTAGE, value)

    def set_dc_current_range(self, value: float | None) -> None:
        self._set_range(MeasurementFunction.DC_CURRENT, value)

    def set_ac_current_range(self, value: float | None) -> None:
        self._set_range(MeasurementFunction.AC_CURRENT, value)

    def set_two_wire_resistance_range(self, value: float | None) -> None:
        self._set_range(MeasurementFunction.TWO_WIRE_RESISTANCE, value)

    def set_four_wire_resistance_range(self, value: float | None) -> None:
        self._set_range(MeasurementFunction.FOUR_WIRE_RESISTANCE, value)

    # --- Measurements. Function is already selected; MEAS1? waits for the next fresh reading. ---
    # An overload ("0L" on the display) reads as +/-1.0E+9.

    def _read_value(self) -> float:
        response = self._transact("MEAS1?")
        if response is None:
            raise InstroError("Fluke 8808A returned no reading for MEAS1?")
        return float(response)

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

    # --- Line framing ---

    def _device_clear(self) -> None:
        """Send ^C to discard any partial command in the meter's input buffer, then drain its ``=>`` reply."""
        with self._visa.lock():
            self._visa.write_raw(_DEVICE_CLEAR)
            try:
                with self._visa.temporary_timeout(_PROBE_TIMEOUT_MS):
                    self._visa.read()
            except pyvisa.errors.VisaIOError:
                pass

    def _probe_identity(self) -> str:
        """Query ``*IDN?`` and record whether the meter follows each command line with a prompt.

        The manual says prompts are only sent with Echo on (a front-panel-only setting), so
        detect them rather than assume.
        """
        with self._visa.lock():
            self._visa.write("*IDN?")
            idn = self._read_response("*IDN?")
            try:
                with self._visa.temporary_timeout(_PROBE_TIMEOUT_MS):
                    prompt = self._read_line()
            except pyvisa.errors.VisaIOError:
                prompt = ""
        self._prompts = bool(prompt)
        if prompt:
            self._check_prompt("*IDN?", prompt)
        return idn

    def _transact(self, command: str) -> str | None:
        """Send one command line, check it executed, and return its response (``None`` for a non-query)."""
        with self._visa.lock():
            if self._prompts:
                self._visa.write(command)
                response: str | None = None
                while True:
                    line = self._read_line()
                    if line == _PROMPT_OK or line in _PROMPT_ERRORS:
                        self._check_prompt(command, line)
                        return response
                    if line.upper() != command.upper():
                        response = line
            if command.endswith("?"):
                self._visa.write(command)
                return self._read_response(command)
            # Without prompts, chain *ESR? so the reply both confirms completion and carries errors.
            line = f"{command}; *ESR?"
            self._visa.write(line)
            esr = int(self._read_response(line))
        if esr & _ESR_ERROR_MASK:
            raise InstroError(f"Fluke 8808A rejected {command!r}: *ESR? = {esr}")
        return None

    def _read_response(self, command: str) -> str:
        """Read the response line to ``command``, skipping its echo when Echo is on."""
        while True:
            line = self._read_line()
            if line.upper() != command.upper():
                return line

    def _read_line(self) -> str:
        """Read the next non-blank line; the meter terminates lines with CR LF."""
        while True:
            line = self._visa.read().strip()
            if line:
                return line

    @staticmethod
    def _check_prompt(command: str, prompt: str) -> None:
        if prompt == _PROMPT_OK:
            return
        reason = _PROMPT_ERRORS.get(prompt, f"unexpected reply {prompt!r}")
        raise InstroError(f"Fluke 8808A {reason} for {command!r}")
