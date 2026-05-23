"""Thermocouple scaling for DAQ: voltage → °C with cold-junction compensation."""

import enum

import thermocouples as tc

from instro.daq.scaling.scaling import Scaler


class TC_TYPE(enum.Enum):
    B = "B"
    E = "E"
    J = "J"
    K = "K"
    N = "N"
    R = "R"
    S = "S"
    T = "T"


class ThermocoupleSensor(Scaler):
    """Thermocouple voltage → °C with cold-junction compensation.

    >>> ThermocoupleSensor(TC_TYPE.K, cjc_temp=25.0)  # Type K, 25 °C reference junction
    """

    def __init__(self, type: TC_TYPE, cjc_temp: float):
        """Initialize the thermocouple sensor.

        Args:
            type: Thermocouple type (B/E/J/K/N/R/S/T).
            cjc_temp: Cold-junction reference temperature in °C.
        """
        self._type = type
        self._cjc = cjc_temp
        self._tc = tc.get_thermocouple(self._type.value)

    def scale(self, raw: float | int) -> float:
        """Voltage (volts or millivolts per the ``thermocouples`` library) → temperature (°C)."""
        return self._tc.volt_to_temp_with_cjc(voltage=raw, ref_temp=self._cjc)

    @property
    def units(self) -> str:
        return "degC"
