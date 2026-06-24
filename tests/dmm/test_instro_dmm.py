"""Tests for the DMM driver shape.

Covers the Agilent34401A vendor driver owning a VisaDriver, and InstroDMM
delegating to its driver via per-function dispatch. Keithley2400 software tests
live in tests/dmm/keithley/test_keithley_2400_software.py.
"""

from collections.abc import Iterator
from typing import Any, Callable
from unittest.mock import MagicMock, patch

import pytest

from instro.dmm import DMMDriverBase, InstroDMM
from instro.dmm.drivers import Agilent34401A
from instro.dmm.types import MeasurementFunction
from instro.lib.transports import SerialConfig, VisaConfig

# --- Agilent34401A unit tests (driver-owned transport over a mocked VisaDriver) ---


@pytest.fixture
def agilent_visa_cls() -> Iterator[MagicMock]:
    with patch("instro.dmm.drivers.agilent_a34401a.VisaDriver", autospec=True) as driver_cls:
        yield driver_cls


@pytest.fixture
def agilent_visa(agilent_visa_cls: MagicMock) -> MagicMock:
    visa = agilent_visa_cls.return_value
    visa.query.return_value = '0,"No error"'
    return visa


@pytest.fixture
def agilent(agilent_visa_cls: MagicMock) -> Agilent34401A:
    return Agilent34401A("ASRL3::INSTR")


def test_agilent_init_builds_visa_from_resource(agilent_visa_cls: MagicMock) -> None:
    Agilent34401A("ASRL3::INSTR")

    agilent_visa_cls.assert_called_once_with("ASRL3::INSTR")


def test_agilent_init_accepts_prebuilt_connection_config(agilent_visa_cls: MagicMock) -> None:
    config = VisaConfig(
        visa_resource="ASRL3::INSTR",
        serial_config=SerialConfig(baud_rate=19_200),
    )

    Agilent34401A(config)

    agilent_visa_cls.assert_called_once_with(config)


def test_agilent_open_clears_and_takes_remote(agilent: Agilent34401A, agilent_visa: MagicMock) -> None:
    agilent.open()
    agilent_visa.open.assert_called_once()
    assert [c.args[0] for c in agilent_visa.write.call_args_list] == ["*CLS", "SYST:REM"]


def test_agilent_close_closes_visa(agilent: Agilent34401A, agilent_visa: MagicMock) -> None:
    agilent.close()
    agilent_visa.close.assert_called_once()


def test_agilent_measure_dc_voltage_uses_meas_query(agilent: Agilent34401A, agilent_visa: MagicMock) -> None:
    agilent_visa.query.side_effect = ["1.234", '0,"No error"']
    assert agilent.measure_dc_voltage() == pytest.approx(1.234)
    assert agilent_visa.query.call_args_list[0].args == ("MEAS:VOLT:DC?",)


def test_agilent_measure_with_range_and_resolution_includes_params(
    agilent: Agilent34401A, agilent_visa: MagicMock
) -> None:
    agilent.set_dc_voltage_range(10.0)
    agilent.set_digits(6)
    agilent_visa.query.side_effect = ["0.5", '0,"No error"']

    agilent.measure_dc_voltage()

    cmd = agilent_visa.query.call_args_list[0].args[0]
    assert cmd.startswith("MEAS:VOLT:DC?")
    assert "1.000000e+01" in cmd
    assert "1.000000e-05" in cmd


def test_agilent_measure_with_range_only_appends_range(agilent: Agilent34401A, agilent_visa: MagicMock) -> None:
    # Range set without set_digits must still reach the wire (issue #145).
    agilent.set_dc_voltage_range(10.0)
    agilent_visa.query.side_effect = ["0.5", '0,"No error"']

    agilent.measure_dc_voltage()

    cmd = agilent_visa.query.call_args_list[0].args[0]
    assert cmd == "MEAS:VOLT:DC? 1.000000e+01"


def test_agilent_per_function_range_methods_share_state(agilent: Agilent34401A) -> None:
    # The 34401A applies one shared range cache regardless of function. All
    # per-function range setters should write to the same private slot.
    agilent.set_dc_current_range(5.0)
    assert agilent._range == 5.0
    agilent.set_two_wire_resistance_range(1000.0)
    assert agilent._range == 1000.0


def test_agilent_set_digits_rejects_invalid(agilent: Agilent34401A) -> None:
    with pytest.raises(ValueError, match="34401A"):
        agilent.set_digits(7)


def test_agilent_check_errors_passes_on_zero(agilent: Agilent34401A, agilent_visa: MagicMock) -> None:
    agilent_visa.query.return_value = '0,"No error"'
    agilent._check_errors()


def test_agilent_check_errors_raises_on_nonzero(agilent: Agilent34401A, agilent_visa: MagicMock) -> None:
    agilent_visa.query.return_value = '-113,"Undefined header"'
    with pytest.raises(RuntimeError, match="Agilent 34401A reported error"):
        agilent._check_errors()


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
