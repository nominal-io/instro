"""Tests for InstroFlowController delegation and FlowControllerDriverBase contract."""

from unittest.mock import MagicMock

import pytest

from instro.unstable.flowcontroller import (
    MASS_FLOW_KEY,
    PRESSURE_KEY,
    SETPOINT_KEY,
    TEMPERATURE_KEY,
    VOLUMETRIC_FLOW_KEY,
    FlowControllerDriverBase,
    InstroFlowController,
)
from instro.unstable.flowcontroller.types import MassFlowData

mock_flow_data: MassFlowData = {
    "pressure": 13.5424,
    "temperature": 24.5782,
    "vol_flow": 16.6670,
    "mass_flow": 15.4443,
    "setpoint": 25.0,
}


def _stub_driver() -> MagicMock:
    driver = MagicMock(spec=FlowControllerDriverBase)
    driver.get_flow_data.return_value = mock_flow_data
    driver.set_setpoint.return_value = 50.0
    driver.select_working_fluid.return_value = "N2"
    type(driver).setpoint = property(lambda self: mock_flow_data[SETPOINT_KEY])
    type(driver).mass_flow = property(lambda self: mock_flow_data[MASS_FLOW_KEY])
    type(driver).volumetric_flow = property(lambda self: mock_flow_data[VOLUMETRIC_FLOW_KEY])
    type(driver).pressure = property(lambda self: mock_flow_data[PRESSURE_KEY])
    type(driver).process_value = property(lambda self: mock_flow_data[MASS_FLOW_KEY])
    type(driver).process_value_source = property(lambda self: MASS_FLOW_KEY)
    return driver


def test_instro_flow_controller_stores_driver() -> None:
    driver = _stub_driver()
    fc = InstroFlowController(name="ut", driver=driver)
    assert fc._driver is driver


def test_instro_flow_controller_open_close_delegate_to_driver() -> None:
    driver = _stub_driver()
    fc = InstroFlowController(name="ut", driver=driver)
    fc.open()
    driver.open.assert_called_once()
    fc.close()
    driver.close.assert_called_once()


def test_instro_flow_controller_close_stops_background_before_driver() -> None:
    events: list[str] = []
    driver = _stub_driver()
    driver.close.side_effect = lambda: events.append("driver.close")
    fc = InstroFlowController(name="ut", driver=driver)
    fc.stop = MagicMock(side_effect=lambda: events.append("stop"))  # type: ignore[method-assign]

    fc.close()

    assert events == ["stop", "driver.close"]


def test_get_flow_data_returns_measurement() -> None:
    driver = _stub_driver()
    fc = InstroFlowController(name="ut", driver=driver)
    measurement = fc.get_flow_data()
    assert measurement is not None
    assert "ut.mass_flow" in measurement.channel_data
    assert measurement.channel_data["ut.mass_flow"] == [pytest.approx(mock_flow_data[MASS_FLOW_KEY])]


def test_get_flow_data_publishes_all_fields() -> None:
    driver = _stub_driver()
    fc = InstroFlowController(name="ut", driver=driver)
    measurement = fc.get_flow_data()
    assert measurement is not None
    keys = set(measurement.channel_data.keys())
    assert keys == {
        "ut.setpoint",
        "ut.mass_flow",
        "ut.vol_flow",
        "ut.pressure",
        "ut.temperature",
    }


def test_get_flow_data_omits_absent_optional_fields() -> None:
    driver = _stub_driver()
    driver.get_flow_data.return_value = {
        "setpoint": 1.0,
        "pressure": 0.0,
        "mass_flow": 2.0,
        "vol_flow": 3.0,
    }
    fc = InstroFlowController(name="ut", driver=driver)
    measurement = fc.get_flow_data()
    assert measurement is not None
    assert set(measurement.channel_data.keys()) == {"ut.setpoint", "ut.mass_flow", "ut.vol_flow", "ut.pressure"}


def test_set_setpoint_delegates() -> None:
    driver = _stub_driver()
    fc = InstroFlowController(name="ut", driver=driver)
    fc.set_setpoint(50.0)
    driver.set_setpoint.assert_called_once_with(50.0)


def test_set_setpoint_returns_command() -> None:
    driver = _stub_driver()
    fc = InstroFlowController(name="ut", driver=driver)
    cmd = fc.set_setpoint(50.0)
    assert "ut.setpoint.cmd" in cmd.channel_data
    assert cmd.channel_data["ut.setpoint.cmd"] == 50.0


def test_select_working_fluid_delegates() -> None:
    driver = _stub_driver()
    fc = InstroFlowController(name="ut", driver=driver)
    fc.select_working_fluid("N2")
    driver.select_working_fluid.assert_called_once_with("N2")


def test_tare_flow_delegates() -> None:
    driver = _stub_driver()
    fc = InstroFlowController(name="ut", driver=driver)
    fc.tare_flow()
    driver.tare_flow.assert_called_once_with()


def test_setpoint_returns_measurement() -> None:
    driver = _stub_driver()
    fc = InstroFlowController(name="ut", driver=driver)
    m = fc.get_setpoint()
    assert m is not None
    assert list(m.channel_data.keys()) == ["ut.setpoint"]
    assert m.channel_data["ut.setpoint"] == [pytest.approx(25.0)]


def test_mass_flow_returns_measurement() -> None:
    driver = _stub_driver()
    fc = InstroFlowController(name="ut", driver=driver)
    m = fc.get_mass_flow()
    assert m is not None
    assert list(m.channel_data.keys()) == ["ut.mass_flow"]
    assert m.channel_data["ut.mass_flow"] == [pytest.approx(15.4443)]


def test_volumetric_flow_returns_measurement() -> None:
    driver = _stub_driver()
    fc = InstroFlowController(name="ut", driver=driver)
    m = fc.get_volumetric_flow()
    assert m is not None
    assert list(m.channel_data.keys()) == ["ut.vol_flow"]
    assert m.channel_data["ut.vol_flow"] == [pytest.approx(16.6670)]


def test_driver_process_value_returns_float() -> None:
    driver = _stub_driver()
    assert driver.process_value == pytest.approx(mock_flow_data[MASS_FLOW_KEY])


def test_driver_process_value_source_returns_key() -> None:
    driver = _stub_driver()
    assert driver.process_value_source == MASS_FLOW_KEY


def test_get_process_value_returns_measurement() -> None:
    driver = _stub_driver()
    fc = InstroFlowController(name="ut", driver=driver)
    m = fc.get_process_value()
    assert m is not None
    assert list(m.channel_data.keys()) == ["ut.mass_flow"]
    assert m.channel_data["ut.mass_flow"] == [pytest.approx(15.4443)]


# --- Regression tests for code review findings ---


def test_get_flow_data_with_string_fields_goes_to_tags() -> None:
    """String fields from driver (e.g., 'gas') should go to tags, not channel_data."""
    driver = _stub_driver()
    # Extend mock_flow_data with a string field
    flow_data_with_gas = {**mock_flow_data, "gas": "N2"}
    driver.get_flow_data.return_value = flow_data_with_gas
    fc = InstroFlowController(name="ut", driver=driver)
    m = fc.get_flow_data()
    assert m is not None
    # String field should NOT be in channel_data
    assert "ut.gas" not in m.channel_data
    # String field should be in tags
    assert m.tags is not None
    assert m.tags.get("gas") == "N2"


def test_get_pressure_returns_measurement() -> None:
    """get_pressure() should work like get_mass_flow() and get_volumetric_flow()."""
    driver = _stub_driver()
    fc = InstroFlowController(name="ut", driver=driver)
    m = fc.get_pressure()
    assert m is not None
    assert list(m.channel_data.keys()) == ["ut.pressure"]
    assert m.channel_data["ut.pressure"] == [pytest.approx(13.5424)]


def test_tare_flow_publishes_actual_flow_value() -> None:
    """tare_flow should publish the driver's returned volumetric flow, not hardcoded True."""
    driver = _stub_driver()
    # Override tare_flow to return actual FlowData
    driver.tare_flow.return_value = {"setpoint": 25.0, "pressure": 13.5, "vol_flow": 0.001, "mass_flow": 0.0}
    fc = InstroFlowController(name="ut", driver=driver)
    cmd = fc.tare_flow()
    assert cmd is not None
    assert "ut.tare.cmd" in cmd.channel_data
    # Should be the volumetric_flow value (0.001), not True
    assert cmd.channel_data["ut.tare.cmd"] == 0.001
