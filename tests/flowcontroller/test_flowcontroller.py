"""Tests for InstroFlowController delegation and FlowControllerDriverBase contract."""

from unittest.mock import MagicMock

import pytest

from instro.flowcontroller import FlowControllerDriverBase, FlowData, InstroFlowController

mock_flow_data = FlowData(
    pressure=13.5424,
    temperature=24.5782,
    vol_flow=16.6670,
    mass_flow=15.4443,
    setpoint=25.0,
    gas="N2",
)


def _stub_driver() -> MagicMock:
    driver = MagicMock(spec=FlowControllerDriverBase)
    driver.get_flow_data.return_value = mock_flow_data
    driver.set_setpoint.return_value = 50.0
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
    assert measurement.channel_data["ut.mass_flow"] == [pytest.approx(mock_flow_data.mass_flow)]


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


def test_select_gas_delegates() -> None:
    driver = _stub_driver()
    fc = InstroFlowController(name="ut", driver=driver)
    fc.select_gas("N2")
    driver.select_gas.assert_called_once_with("N2")


def test_tare_flow_delegates() -> None:
    driver = _stub_driver()
    fc = InstroFlowController(name="ut", driver=driver)
    fc.tare_flow()
    driver.tare_flow.assert_called_once_with()
