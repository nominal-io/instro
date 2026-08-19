"""Tests for AlicatLiquidFlowController and AlicatPressureController instantiation and basic operations."""

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest

from instro.lib.transports.visa import SerialConfig, VisaConfig
from instro.unstable.flowcontroller import (
    PRESSURE_KEY,
    SETPOINT_KEY,
    TEMPERATURE_KEY,
    VOLUMETRIC_FLOW_KEY,
)
from instro.unstable.flowcontroller.drivers.alicat_liquid import AlicatLiquidFlowController
from instro.unstable.flowcontroller.drivers.alicat_pressure import AlicatPressureController

_LIQUID_SAMPLE = "A +13.5424 +24.5782 +16.6670 +25.0000"
_PRESSURE_SAMPLE = "A +13.5424 +25.0000"


@pytest.fixture
def visa_driver_cls_liquid() -> Iterator[MagicMock]:
    with patch("instro.unstable.flowcontroller.drivers.alicat_liquid.VisaDriver", autospec=True) as driver_cls:
        yield driver_cls


@pytest.fixture
def visa_mock_liquid(visa_driver_cls_liquid: MagicMock) -> MagicMock:
    visa = visa_driver_cls_liquid.return_value
    visa.query.return_value = _LIQUID_SAMPLE
    return visa


@pytest.fixture
def visa_driver_cls_pressure() -> Iterator[MagicMock]:
    with patch("instro.unstable.flowcontroller.drivers.alicat_pressure.VisaDriver", autospec=True) as driver_cls:
        yield driver_cls


@pytest.fixture
def visa_mock_pressure(visa_driver_cls_pressure: MagicMock) -> MagicMock:
    visa = visa_driver_cls_pressure.return_value
    visa.query.return_value = _PRESSURE_SAMPLE
    return visa


# --- AlicatLiquidFlowController tests ---


def test_alicat_liquid_can_be_instantiated(visa_driver_cls_liquid: MagicMock) -> None:
    """AlicatLiquidFlowController can be instantiated without runtime errors or abstract method errors."""
    driver = AlicatLiquidFlowController("ASRL7::INSTR")
    assert driver is not None
    assert isinstance(driver, AlicatLiquidFlowController)


def test_alicat_liquid_init_coerces_string_to_visa_config(visa_driver_cls_liquid: MagicMock) -> None:
    AlicatLiquidFlowController("ASRL7::INSTR")

    visa_driver_cls_liquid.assert_called_once()
    cfg = visa_driver_cls_liquid.call_args[0][0]
    assert isinstance(cfg, VisaConfig)
    assert cfg.visa_resource == "ASRL7::INSTR"
    assert cfg.serial_config.baud_rate == 19200
    assert cfg.terminator.read == "\r"
    assert cfg.terminator.write == "\r"


def test_alicat_liquid_init_accepts_prebuilt_visa_config(visa_driver_cls_liquid: MagicMock) -> None:
    config = VisaConfig(
        visa_resource="ASRL7::INSTR",
        serial_config=SerialConfig(baud_rate=19200),
    )
    AlicatLiquidFlowController(config)
    visa_driver_cls_liquid.assert_called_once_with(config)


def test_alicat_liquid_open(visa_driver_cls_liquid: MagicMock, visa_mock_liquid: MagicMock) -> None:
    driver = AlicatLiquidFlowController("ASRL7::INSTR")
    driver.open()
    visa_mock_liquid.open.assert_called_once()


def test_alicat_liquid_close(visa_driver_cls_liquid: MagicMock, visa_mock_liquid: MagicMock) -> None:
    driver = AlicatLiquidFlowController("ASRL7::INSTR")
    driver.close()
    visa_mock_liquid.close.assert_called_once()


def test_alicat_liquid_get_flow_data(visa_driver_cls_liquid: MagicMock, visa_mock_liquid: MagicMock) -> None:
    driver = AlicatLiquidFlowController("ASRL7::INSTR")
    data = driver.get_flow_data()
    assert data[PRESSURE_KEY] == pytest.approx(13.5424)
    assert data[TEMPERATURE_KEY] == pytest.approx(24.5782)
    assert data[VOLUMETRIC_FLOW_KEY] == pytest.approx(16.6670)
    assert data[SETPOINT_KEY] == pytest.approx(25.0)


def test_alicat_liquid_set_setpoint(visa_driver_cls_liquid: MagicMock, visa_mock_liquid: MagicMock) -> None:
    driver = AlicatLiquidFlowController("ASRL7::INSTR")
    visa_mock_liquid.query.return_value = "A +13.5424 +24.5782 +16.6670 +50.0000"
    result = driver.set_setpoint(50.0)
    visa_mock_liquid.query.assert_called_once_with("As50.0")
    assert result == pytest.approx(50.0)


def test_alicat_liquid_select_working_fluid_raises_not_implemented(
    visa_driver_cls_liquid: MagicMock, visa_mock_liquid: MagicMock
) -> None:
    driver = AlicatLiquidFlowController("ASRL7::INSTR")
    with pytest.raises(NotImplementedError, match="does not support working fluid selection"):
        driver.select_working_fluid("Water")


def test_alicat_liquid_process_value_returns_volumetric_flow(
    visa_driver_cls_liquid: MagicMock, visa_mock_liquid: MagicMock
) -> None:
    driver = AlicatLiquidFlowController("ASRL7::INSTR")
    assert driver.process_value == pytest.approx(16.6670)


def test_alicat_liquid_process_value_source_is_volumetric_flow(
    visa_driver_cls_liquid: MagicMock, visa_mock_liquid: MagicMock
) -> None:
    driver = AlicatLiquidFlowController("ASRL7::INSTR")
    assert driver.process_value_source == VOLUMETRIC_FLOW_KEY


def test_alicat_liquid_tare_flow_raises_not_implemented(
    visa_driver_cls_liquid: MagicMock, visa_mock_liquid: MagicMock
) -> None:
    driver = AlicatLiquidFlowController("ASRL7::INSTR")
    with pytest.raises(NotImplementedError, match="does not support taring"):
        driver.tare_flow()


# --- AlicatPressureController tests ---


def test_alicat_pressure_can_be_instantiated(visa_driver_cls_pressure: MagicMock) -> None:
    """AlicatPressureController can be instantiated without runtime errors or abstract method errors."""
    driver = AlicatPressureController("ASRL8::INSTR")
    assert driver is not None
    assert isinstance(driver, AlicatPressureController)


def test_alicat_pressure_init_coerces_string_to_visa_config(visa_driver_cls_pressure: MagicMock) -> None:
    AlicatPressureController("ASRL8::INSTR")

    visa_driver_cls_pressure.assert_called_once()
    cfg = visa_driver_cls_pressure.call_args[0][0]
    assert isinstance(cfg, VisaConfig)
    assert cfg.visa_resource == "ASRL8::INSTR"
    assert cfg.serial_config.baud_rate == 19200
    assert cfg.terminator.read == "\r"
    assert cfg.terminator.write == "\r"


def test_alicat_pressure_init_accepts_prebuilt_visa_config(visa_driver_cls_pressure: MagicMock) -> None:
    config = VisaConfig(
        visa_resource="ASRL8::INSTR",
        serial_config=SerialConfig(baud_rate=19200),
    )
    AlicatPressureController(config)
    visa_driver_cls_pressure.assert_called_once_with(config)


def test_alicat_pressure_open(visa_driver_cls_pressure: MagicMock, visa_mock_pressure: MagicMock) -> None:
    driver = AlicatPressureController("ASRL8::INSTR")
    driver.open()
    visa_mock_pressure.open.assert_called_once()


def test_alicat_pressure_close(visa_driver_cls_pressure: MagicMock, visa_mock_pressure: MagicMock) -> None:
    driver = AlicatPressureController("ASRL8::INSTR")
    driver.close()
    visa_mock_pressure.close.assert_called_once()


def test_alicat_pressure_get_flow_data(visa_driver_cls_pressure: MagicMock, visa_mock_pressure: MagicMock) -> None:
    driver = AlicatPressureController("ASRL8::INSTR")
    data = driver.get_flow_data()
    assert data[PRESSURE_KEY] == pytest.approx(13.5424)
    assert data[SETPOINT_KEY] == pytest.approx(25.0)


def test_alicat_pressure_set_setpoint(visa_driver_cls_pressure: MagicMock, visa_mock_pressure: MagicMock) -> None:
    driver = AlicatPressureController("ASRL8::INSTR")
    visa_mock_pressure.query.return_value = "A +13.5424 +50.0000"
    result = driver.set_setpoint(50.0)
    visa_mock_pressure.query.assert_called_once_with("As50.0")
    assert result == pytest.approx(50.0)


def test_alicat_pressure_select_working_fluid_raises_not_implemented(
    visa_driver_cls_pressure: MagicMock, visa_mock_pressure: MagicMock
) -> None:
    driver = AlicatPressureController("ASRL8::INSTR")
    with pytest.raises(NotImplementedError, match="does not support working fluid selection"):
        driver.select_working_fluid("N2")


def test_alicat_pressure_process_value_returns_pressure(
    visa_driver_cls_pressure: MagicMock, visa_mock_pressure: MagicMock
) -> None:
    driver = AlicatPressureController("ASRL8::INSTR")
    assert driver.process_value == pytest.approx(13.5424)


def test_alicat_pressure_process_value_source_is_pressure(
    visa_driver_cls_pressure: MagicMock, visa_mock_pressure: MagicMock
) -> None:
    driver = AlicatPressureController("ASRL8::INSTR")
    assert driver.process_value_source == PRESSURE_KEY


def test_alicat_pressure_tare_flow_raises_not_implemented(
    visa_driver_cls_pressure: MagicMock, visa_mock_pressure: MagicMock
) -> None:
    driver = AlicatPressureController("ASRL8::INSTR")
    with pytest.raises(NotImplementedError, match="does not support taring"):
        driver.tare_flow()
