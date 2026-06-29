"""Tests for the DMM driver shape.

Covers InstroDMM delegating to its driver via per-function dispatch. Per-vendor
driver software tests live under tests/dmm/<vendor>/ (e.g.
tests/dmm/agilent/test_agilent_34401a_software.py,
tests/dmm/keithley/test_keithley_2400_software.py).
"""

from typing import Any, Callable
from unittest.mock import MagicMock

import pytest

from instro.dmm import DMMDriverBase, InstroDMM
from instro.dmm.types import MeasurementFunction

# --- InstroDMM composition tests ---


class _StubDMMDriver(DMMDriverBase):
    """Minimal DMMDriverBase implementation for testing InstroDMM behavior."""

    def __init__(self) -> None:
        self.opened = False
        self.closed = False
        self.last_function: MeasurementFunction | None = None
        self.last_nplc_call: tuple[str, float] | None = None
        self.last_range_call: tuple[str, float | None] | None = None
        self.measured = 0.0

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.closed = True

    def set_measurement_function(self, function: MeasurementFunction) -> None:
        self.last_function = function

    # NPLC overrides — record (method_name, nplc).
    def set_dc_voltage_nplc(self, nplc: float) -> None:
        self.last_nplc_call = ("dc_voltage", nplc)

    def set_dc_current_nplc(self, nplc: float) -> None:
        self.last_nplc_call = ("dc_current", nplc)

    def set_two_wire_resistance_nplc(self, nplc: float) -> None:
        self.last_nplc_call = ("two_wire_resistance", nplc)

    # Range overrides — record (method_name, value).
    def set_dc_voltage_range(self, value: float | None) -> None:
        self.last_range_call = ("dc_voltage", value)

    def set_two_wire_resistance_range(self, value: float | None) -> None:
        self.last_range_call = ("two_wire_resistance", value)

    def measure_dc_voltage(self) -> float:
        return self.measured

    def measure_ac_voltage(self) -> float:
        return self.measured

    def measure_resistance(self) -> float:
        return self.measured

    def measure_dc_current(self) -> float:
        return self.measured

    def measure_ac_current(self) -> float:
        return self.measured


@pytest.fixture
def stub_driver() -> _StubDMMDriver:
    return _StubDMMDriver()


@pytest.fixture
def unconfigured_dmm(stub_driver: _StubDMMDriver) -> InstroDMM:
    return InstroDMM(name="test_dmm", driver=stub_driver)


@pytest.mark.parametrize(
    "action",
    [
        lambda d: d.start(),
        lambda d: d.read(),
        lambda d: d.set_digits(5),
        lambda d: d.set_aperture_seconds(0.1),
        lambda d: d.set_aperture_nplc(1.0),
        lambda d: d.set_range(None),
    ],
)
def test_unconfigured_dmm_raises_value_error(unconfigured_dmm: InstroDMM, action: Callable[[InstroDMM], Any]) -> None:
    with pytest.raises(ValueError, match="set_measurement_function"):
        action(unconfigured_dmm)


def test_nominal_dmm_stores_driver(stub_driver: _StubDMMDriver) -> None:
    dmm = InstroDMM(name="ut", driver=stub_driver)
    assert dmm._driver is stub_driver


def test_nominal_dmm_open_close_delegate(stub_driver: _StubDMMDriver) -> None:
    dmm = InstroDMM(name="ut", driver=stub_driver)
    dmm.open()
    assert stub_driver.opened
    dmm.close()
    assert stub_driver.closed


def test_nominal_dmm_close_stops_background_before_closing_driver(stub_driver: _StubDMMDriver) -> None:
    events: list[str] = []
    original_close = stub_driver.close

    def record_close() -> None:
        events.append("driver.close")
        original_close()

    stub_driver.close = record_close  # type: ignore[method-assign]
    dmm = InstroDMM(name="ut", driver=stub_driver)
    dmm.stop = MagicMock(side_effect=lambda: events.append("stop"))  # type: ignore[method-assign]

    dmm.close()

    assert events == ["stop", "driver.close"]


def test_nominal_dmm_set_measurement_function_delegates(stub_driver: _StubDMMDriver) -> None:
    dmm = InstroDMM(name="ut", driver=stub_driver)
    dmm.set_measurement_function(MeasurementFunction.DC_VOLTAGE)
    assert stub_driver.last_function is MeasurementFunction.DC_VOLTAGE


def test_nominal_dmm_set_measurement_function_keeps_config_when_driver_rejects(stub_driver: _StubDMMDriver) -> None:
    dmm = InstroDMM(name="ut", driver=stub_driver)
    dmm.set_measurement_function(MeasurementFunction.DC_VOLTAGE)

    stub_driver.set_measurement_function = MagicMock(  # type: ignore[method-assign]
        side_effect=NotImplementedError("unsupported")
    )
    with pytest.raises(NotImplementedError):
        dmm.set_measurement_function(MeasurementFunction.AC_VOLTAGE)

    # The rejected function must not be recorded: config still reflects the hardware.
    assert dmm._measurement_config is not None
    assert dmm._measurement_config.function is MeasurementFunction.DC_VOLTAGE


def test_nominal_dmm_first_set_measurement_function_not_recorded_when_driver_rejects(
    stub_driver: _StubDMMDriver,
) -> None:
    dmm = InstroDMM(name="ut", driver=stub_driver)
    stub_driver.set_measurement_function = MagicMock(  # type: ignore[method-assign]
        side_effect=NotImplementedError("unsupported")
    )
    with pytest.raises(NotImplementedError):
        dmm.set_measurement_function(MeasurementFunction.AC_VOLTAGE)

    assert dmm._measurement_config is None


def test_nominal_dmm_set_aperture_nplc_dispatches_to_function_method(stub_driver: _StubDMMDriver) -> None:
    dmm = InstroDMM(name="ut", driver=stub_driver)
    dmm.set_measurement_function(MeasurementFunction.DC_CURRENT)
    dmm.set_aperture_nplc(2.5)
    assert stub_driver.last_nplc_call == ("dc_current", 2.5)


def test_nominal_dmm_set_range_dispatches_to_function_method(stub_driver: _StubDMMDriver) -> None:
    dmm = InstroDMM(name="ut", driver=stub_driver)
    dmm.set_measurement_function(MeasurementFunction.TWO_WIRE_RESISTANCE)
    dmm.set_range(1000.0)
    assert stub_driver.last_range_call == ("two_wire_resistance", 1000.0)


def test_nominal_dmm_read_returns_measurement(stub_driver: _StubDMMDriver) -> None:
    stub_driver.measured = 3.3
    dmm = InstroDMM(name="ut", driver=stub_driver)
    dmm.set_measurement_function(MeasurementFunction.DC_VOLTAGE)
    measurement = dmm.read()
    assert "ut.dc_voltage" in measurement.channel_data
    assert measurement.channel_data["ut.dc_voltage"] == [3.3]
