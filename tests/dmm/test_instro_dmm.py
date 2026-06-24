"""Tests for the DMM driver shape.

Covers vendor drivers (Agilent34401A, Keithley2400) owning a VisaDriver,
and InstroDMM delegating to its driver via per-function dispatch.
"""

from collections.abc import Iterator
from typing import Any, Callable
from unittest.mock import MagicMock, patch

import pytest

from instro.dmm import DMMDriverBase, InstroDMM
from instro.dmm.drivers import Agilent34401A, Keithley2400
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


# --- Keithley2400 unit tests ---


@pytest.fixture
def keithley_visa_cls() -> Iterator[MagicMock]:
    with patch("instro.dmm.drivers.keithley_2400.VisaDriver", autospec=True) as driver_cls:
        yield driver_cls


@pytest.fixture
def keithley_visa(keithley_visa_cls: MagicMock) -> MagicMock:
    visa = keithley_visa_cls.return_value
    visa.query.return_value = '0,"No error"'
    return visa


@pytest.fixture
def keithley(keithley_visa_cls: MagicMock, keithley_visa: MagicMock) -> Keithley2400:
    return Keithley2400("GPIB0::24::INSTR")


def test_keithley_init_builds_visa_from_resource(keithley_visa_cls: MagicMock) -> None:
    Keithley2400("GPIB0::24::INSTR")
    keithley_visa_cls.assert_called_once_with("GPIB0::24::INSTR")


def test_keithley_set_measurement_function_voltage(keithley: Keithley2400, keithley_visa: MagicMock) -> None:
    keithley.set_measurement_function(MeasurementFunction.DC_VOLTAGE)
    writes = [c.args[0] for c in keithley_visa.write.call_args_list]
    assert ":SENS:FUNC 'VOLT'" in writes
    assert ":SOUR:FUNC VOLT" in writes


def test_keithley_set_measurement_function_unsupported(keithley: Keithley2400) -> None:
    with pytest.raises(NotImplementedError, match="AC_VOLTAGE"):
        keithley.set_measurement_function(MeasurementFunction.AC_VOLTAGE)


def test_keithley_set_dc_current_nplc_writes_scoped_scpi(keithley: Keithley2400, keithley_visa: MagicMock) -> None:
    keithley.set_dc_current_nplc(1.0)
    writes = [c.args[0] for c in keithley_visa.write.call_args_list]
    assert ":SENS:CURR:NPLC 1.0000" in writes


def test_keithley_set_dc_voltage_nplc_writes_scoped_scpi(keithley: Keithley2400, keithley_visa: MagicMock) -> None:
    keithley.set_dc_voltage_nplc(2.5)
    writes = [c.args[0] for c in keithley_visa.write.call_args_list]
    assert ":SENS:VOLT:NPLC 2.5000" in writes


def test_keithley_unsupported_nplc_function_raises(keithley: Keithley2400) -> None:
    with pytest.raises(NotImplementedError):
        keithley.set_ac_voltage_nplc(1.0)


def test_keithley_set_dc_voltage_range_auto(keithley: Keithley2400, keithley_visa: MagicMock) -> None:
    # V/I range is driven through :SOUR — :SENS:VOLT:RANG is rejected with error 823
    # because the sense path runs through the source range on the 2400.
    keithley.set_dc_voltage_range(None)
    writes = [c.args[0] for c in keithley_visa.write.call_args_list]
    assert ":SOUR:VOLT:RANG:AUTO 1" in writes


def test_keithley_set_dc_voltage_range_manual(keithley: Keithley2400, keithley_visa: MagicMock) -> None:
    keithley.set_dc_voltage_range(2.0)
    writes = [c.args[0] for c in keithley_visa.write.call_args_list]
    assert ":SOUR:VOLT:RANG:AUTO 0" in writes
    assert any(w.startswith(":SOUR:VOLT:RANG ") for w in writes)


def test_keithley_unsupported_range_function_raises(keithley: Keithley2400) -> None:
    with pytest.raises(NotImplementedError):
        keithley.set_ac_current_range(1.0)


def test_keithley_set_digits_unsupported(keithley: Keithley2400) -> None:
    with pytest.raises(NotImplementedError, match="set_digits"):
        keithley.set_digits(5)


def test_keithley_measure_ac_voltage_unsupported(keithley: Keithley2400) -> None:
    with pytest.raises(NotImplementedError):
        keithley.measure_ac_voltage()


def test_keithley_measure_dc_voltage_parses_first_field(keithley: Keithley2400, keithley_visa: MagicMock) -> None:
    keithley_visa.query.side_effect = ["1.234,5.678", '0,"No error"']
    assert keithley.measure_dc_voltage() == pytest.approx(1.234)


def test_keithley_check_errors_passes_on_signed_zero(keithley: Keithley2400, keithley_visa: MagicMock) -> None:
    keithley_visa.query.return_value = '+0,"No error"'
    keithley._check_errors()


def test_keithley_check_errors_raises_on_nonzero(keithley: Keithley2400, keithley_visa: MagicMock) -> None:
    keithley_visa.query.return_value = '-100,"Command error"'
    with pytest.raises(RuntimeError, match="Keithley 2400 reported error"):
        keithley._check_errors()


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
