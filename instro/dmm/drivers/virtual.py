"""Virtual DMM driver: readings simulated in-process, with no transport, server, or hardware."""

from __future__ import annotations

from instro.dmm import DMMDriverBase
from instro.dmm.scpi_sim_server import (
    DEFAULT_STIMULUS,
    FUNCTION_AC_CURRENT,
    FUNCTION_AC_VOLTAGE,
    FUNCTION_DC_CURRENT,
    FUNCTION_DC_VOLTAGE,
    FUNCTION_RESISTANCE,
    NOISE_PERCENT,
    add_noise,
)
from instro.dmm.types import MeasurementFunction

# the SCPI simulator's defaults, so both simulated DMMs read the same values
_DEFAULT_STIMULUS = {
    MeasurementFunction.DC_VOLTAGE: DEFAULT_STIMULUS[FUNCTION_DC_VOLTAGE],
    MeasurementFunction.AC_VOLTAGE: DEFAULT_STIMULUS[FUNCTION_AC_VOLTAGE],
    MeasurementFunction.DC_CURRENT: DEFAULT_STIMULUS[FUNCTION_DC_CURRENT],
    MeasurementFunction.AC_CURRENT: DEFAULT_STIMULUS[FUNCTION_AC_CURRENT],
    MeasurementFunction.TWO_WIRE_RESISTANCE: DEFAULT_STIMULUS[FUNCTION_RESISTANCE],
    MeasurementFunction.FOUR_WIRE_RESISTANCE: DEFAULT_STIMULUS[FUNCTION_RESISTANCE],
}


class VirtualDMM(DMMDriverBase):
    """DMM simulated in-process: no transport, server, or hardware.

    Each function reads its stimulus value plus noise (0.5%, the SCPI simulator's level).
    Range, NPLC, digits, and aperture settings are accepted and have no effect on readings,
    so any `InstroDMM` call works. To exercise the VISA transport as well, use `SimulatedDMM`
    with the bundled SCPI simulator.
    """

    def __init__(self, stimulus: dict[MeasurementFunction, float] | None = None) -> None:
        """Create a virtual DMM.

        Args:
            stimulus (dict[MeasurementFunction, float] | None): The value each function measures
                before noise, in volts, amperes, or ohms. Functions left out keep their defaults
                (1.25 V DC, 0.5 V AC, 0.1 A DC, 0.05 A AC, 1000 ohms).

        Example::

            from instro.dmm import InstroDMM, MeasurementFunction
            from instro.dmm.drivers import VirtualDMM

            dmm = InstroDMM(name="dmm", driver=VirtualDMM({MeasurementFunction.DC_VOLTAGE: 3.3}))
            with dmm:
                print(dmm.read_dc_voltage().latest)  # about 3.3
        """
        self.stimulus: dict[MeasurementFunction, float] = {**_DEFAULT_STIMULUS, **(stimulus or {})}

    def open(self) -> None:
        """Nothing to connect to."""

    def close(self) -> None:
        """Nothing to disconnect."""

    def set_measurement_function(self, function: MeasurementFunction) -> None:
        """Accept any function; `InstroDMM` calls the matching ``measure_*`` method to read it."""

    def set_digits(self, n: int) -> None:
        """Accepted; no effect on readings."""

    def set_aperture_seconds(self, seconds: float) -> None:
        """Accepted; no effect on readings."""

    def set_dc_voltage_range(self, value: float | None) -> None:
        """Accepted; no effect on readings."""

    def set_ac_voltage_range(self, value: float | None) -> None:
        """Accepted; no effect on readings."""

    def set_dc_current_range(self, value: float | None) -> None:
        """Accepted; no effect on readings."""

    def set_ac_current_range(self, value: float | None) -> None:
        """Accepted; no effect on readings."""

    def set_two_wire_resistance_range(self, value: float | None) -> None:
        """Accepted; no effect on readings."""

    def set_four_wire_resistance_range(self, value: float | None) -> None:
        """Accepted; no effect on readings."""

    def set_dc_voltage_nplc(self, nplc: float) -> None:
        """Accepted; no effect on readings."""

    def set_ac_voltage_nplc(self, nplc: float) -> None:
        """Accepted; no effect on readings."""

    def set_dc_current_nplc(self, nplc: float) -> None:
        """Accepted; no effect on readings."""

    def set_ac_current_nplc(self, nplc: float) -> None:
        """Accepted; no effect on readings."""

    def set_two_wire_resistance_nplc(self, nplc: float) -> None:
        """Accepted; no effect on readings."""

    def set_four_wire_resistance_nplc(self, nplc: float) -> None:
        """Accepted; no effect on readings."""

    def measure_dc_voltage(self) -> float:
        return self._read(MeasurementFunction.DC_VOLTAGE)

    def measure_ac_voltage(self) -> float:
        return self._read(MeasurementFunction.AC_VOLTAGE)

    def measure_dc_current(self) -> float:
        return self._read(MeasurementFunction.DC_CURRENT)

    def measure_ac_current(self) -> float:
        return self._read(MeasurementFunction.AC_CURRENT)

    def measure_resistance(self) -> float:
        return self._read(MeasurementFunction.TWO_WIRE_RESISTANCE)

    def measure_four_wire_resistance(self) -> float:
        return self._read(MeasurementFunction.FOUR_WIRE_RESISTANCE)

    def _read(self, function: MeasurementFunction) -> float:
        return add_noise(self.stimulus[function], NOISE_PERCENT)
