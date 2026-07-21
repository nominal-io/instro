"""Unit tests for DAQ scalers."""

import pytest

from instro.daq.scaling.thermocouple import (
    TC_TYPE,
    InverseThermocoupleSensor,
    ThermocoupleSensor,
)


@pytest.mark.parametrize("cjc_temp", [0.0, 23.0])
@pytest.mark.parametrize("temp", [-100.0, 0.0, 100.0])
def test_thermocouple_inverse_scale_roundtrip(cjc_temp: float, temp: float) -> None:
    forward = ThermocoupleSensor(TC_TYPE.K, cjc_temp=cjc_temp)
    inverse = InverseThermocoupleSensor(TC_TYPE.K, cjc_temp=cjc_temp)
    assert forward.scale(inverse.scale(temp)) == pytest.approx(temp, abs=0.1)
