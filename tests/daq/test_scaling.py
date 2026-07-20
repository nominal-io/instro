"""Unit tests for DAQ scalers."""

import pytest

from instro.daq.scaling.thermocouple import TC_TYPE, ThermocoupleSensor


@pytest.mark.parametrize("cjc_temp", [0.0, 23.0])
@pytest.mark.parametrize("temp", [-100.0, 0.0, 100.0])
def test_thermocouple_inverse_scale_roundtrip(cjc_temp: float, temp: float) -> None:
    scaler = ThermocoupleSensor(TC_TYPE.K, cjc_temp=cjc_temp)
    assert scaler.scale(scaler.inverse_scale(temp)) == pytest.approx(temp, abs=0.1)
