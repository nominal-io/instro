"""Thermocouple scaling for DAQ: voltage → °C with cold-junction compensation."""

from enum import Enum

import thermocouples as tc

from instro.daq.scaling.scaling import Scaler


class TC_TYPE(Enum):
    B = "B"
    E = "E"
    J = "J"
    K = "K"
    N = "N"
    R = "R"
    S = "S"
    T = "T"


class TC_UNIT(Enum):
    CELSIUS = "degC"
    KELVIN = "K"
    FAHRENHEIT = "degF"
    RANKINE = "degR"


def kelvin_to_unit(temp_k: float, unit: TC_UNIT) -> float:
    """Convert a Kelvin temperature to ``unit``."""
    match unit:
        case TC_UNIT.CELSIUS:
            return temp_k - 273.15
        case TC_UNIT.KELVIN:
            return temp_k
        case TC_UNIT.FAHRENHEIT:
            return temp_k * 9 / 5 - 459.67
        case TC_UNIT.RANKINE:
            return temp_k * 9 / 5
        case _:
            raise AssertionError(f"unhandled TC_UNIT {unit}")


def unit_to_kelvin(temp: float, unit: TC_UNIT) -> float:
    """Convert a temperature in ``unit`` to Kelvin."""
    match unit:
        case TC_UNIT.CELSIUS:
            return temp + 273.15
        case TC_UNIT.KELVIN:
            return temp
        case TC_UNIT.FAHRENHEIT:
            return (temp + 459.67) * 5 / 9
        case TC_UNIT.RANKINE:
            return temp * 5 / 9
        case _:
            raise AssertionError(f"unhandled TC_UNIT {unit}")


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


class InverseThermocoupleSensor(Scaler):
    """Temperature (°C) → voltage with cold-junction compensation — inverse of ``ThermocoupleSensor``.

    >>> InverseThermocoupleSensor(TC_TYPE.K, cjc_temp=25.0)  # Type K, 25 °C reference junction
    """

    def __init__(self, type: TC_TYPE, cjc_temp: float):
        """Initialize the inverse thermocouple sensor.

        Args:
            type: Thermocouple type (B/E/J/K/N/R/S/T).
            cjc_temp: Cold-junction reference temperature in °C.
        """
        self._type = type
        self._cjc = cjc_temp
        self._tc = tc.get_thermocouple(self._type.value)

    def scale(self, raw: float | int) -> float:
        """Temperature (°C) → voltage with cold-junction compensation."""
        return self._tc.temp_to_volt(raw) - self._tc.temp_to_volt(self._cjc)

    @property
    def units(self) -> str:
        return "V"
