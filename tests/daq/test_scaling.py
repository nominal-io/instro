"""Unit tests for DAQ scalers."""

import pytest

from instro.daq.scaling.thermocouple import (
    TC_TYPE,
    TC_UNIT,
    InverseThermocoupleSensor,
    ThermocoupleSensor,
    kelvin_to_unit,
    unit_to_kelvin,
)


@pytest.mark.parametrize("cjc_temp", [0.0, 23.0])
@pytest.mark.parametrize("temp", [-100.0, 0.0, 100.0])
def test_thermocouple_inverse_scale_roundtrip(cjc_temp: float, temp: float) -> None:
    forward = ThermocoupleSensor(TC_TYPE.K, cjc_temp=cjc_temp)
    inverse = InverseThermocoupleSensor(TC_TYPE.K, cjc_temp=cjc_temp)
    assert forward.scale(inverse.scale(temp)) == pytest.approx(temp, abs=0.1)


# Water's freezing point in each unit, so the offset and the scale factor are both pinned.
@pytest.mark.parametrize(
    "unit,freezing",
    [(TC_UNIT.CELSIUS, 0.0), (TC_UNIT.KELVIN, 273.15), (TC_UNIT.FAHRENHEIT, 32.0), (TC_UNIT.RANKINE, 491.67)],
)
def test_kelvin_conversion_roundtrip(unit: TC_UNIT, freezing: float) -> None:
    assert kelvin_to_unit(273.15, unit) == pytest.approx(freezing)
    assert unit_to_kelvin(freezing, unit) == pytest.approx(273.15)
