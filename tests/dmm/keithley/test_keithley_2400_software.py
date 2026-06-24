"""Software tests for the Keithley 2400 SourceMeter (sense-only DMM) driver."""

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest

from instro.dmm.drivers import Keithley2400
from instro.dmm.types import MeasurementFunction


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


def test_keithley_close_turns_output_off_and_closes(keithley: Keithley2400, keithley_visa: MagicMock) -> None:
    keithley_visa.is_open = True
    keithley.close()
    writes = [c.args[0] for c in keithley_visa.write.call_args_list]
    assert ":OUTP OFF" in writes
    keithley_visa.close.assert_called_once_with()


def test_keithley_close_skips_output_off_when_not_open(keithley: Keithley2400, keithley_visa: MagicMock) -> None:
    keithley_visa.is_open = False
    keithley.close()
    writes = [c.args[0] for c in keithley_visa.write.call_args_list]
    assert ":OUTP OFF" not in writes
    keithley_visa.close.assert_called_once_with()


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


def test_keithley_set_two_wire_resistance_nplc_writes_scoped_scpi(
    keithley: Keithley2400, keithley_visa: MagicMock
) -> None:
    keithley.set_two_wire_resistance_nplc(1.0)
    writes = [c.args[0] for c in keithley_visa.write.call_args_list]
    assert ":SENS:RES:NPLC 1.0000" in writes


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


def test_keithley_set_two_wire_resistance_range_auto(keithley: Keithley2400, keithley_visa: MagicMock) -> None:
    # Resistance ranges through the sense path (auto-ohms manages the source), unlike V/I.
    keithley.set_two_wire_resistance_range(None)
    writes = [c.args[0] for c in keithley_visa.write.call_args_list]
    assert ":SENS:RES:RANG:AUTO 1" in writes


def test_keithley_set_two_wire_resistance_range_manual(keithley: Keithley2400, keithley_visa: MagicMock) -> None:
    keithley.set_two_wire_resistance_range(1.0e3)
    writes = [c.args[0] for c in keithley_visa.write.call_args_list]
    assert ":SENS:RES:RANG:AUTO 0" in writes
    assert any(w.startswith(":SENS:RES:RANG ") for w in writes)


def test_keithley_unsupported_range_function_raises(keithley: Keithley2400) -> None:
    with pytest.raises(NotImplementedError):
        keithley.set_ac_current_range(1.0)


def test_keithley_set_digits_unsupported(keithley: Keithley2400) -> None:
    with pytest.raises(NotImplementedError, match="set_digits"):
        keithley.set_digits(5)


def test_keithley_measure_ac_voltage_unsupported(keithley: Keithley2400) -> None:
    with pytest.raises(NotImplementedError):
        keithley.measure_ac_voltage()


def test_keithley_measure_ac_current_unsupported(keithley: Keithley2400) -> None:
    with pytest.raises(NotImplementedError):
        keithley.measure_ac_current()


def test_keithley_measure_dc_voltage_parses_first_field(keithley: Keithley2400, keithley_visa: MagicMock) -> None:
    keithley_visa.query.side_effect = ["1.234,5.678", '0,"No error"']
    assert keithley.measure_dc_voltage() == pytest.approx(1.234)


def test_keithley_measure_dc_current_parses_first_field(keithley: Keithley2400, keithley_visa: MagicMock) -> None:
    keithley_visa.query.side_effect = ["0.0123,4.567", '0,"No error"']
    assert keithley.measure_dc_current() == pytest.approx(0.0123)


def test_keithley_measure_resistance_parses_first_field(keithley: Keithley2400, keithley_visa: MagicMock) -> None:
    keithley_visa.query.side_effect = ["1000.5,2.3", '0,"No error"']
    assert keithley.measure_resistance() == pytest.approx(1000.5)


def test_keithley_check_errors_passes_on_signed_zero(keithley: Keithley2400, keithley_visa: MagicMock) -> None:
    keithley_visa.query.return_value = '+0,"No error"'
    keithley._check_errors()


def test_keithley_check_errors_raises_on_nonzero(keithley: Keithley2400, keithley_visa: MagicMock) -> None:
    keithley_visa.query.return_value = '-100,"Command error"'
    with pytest.raises(RuntimeError, match="Keithley 2400 reported error"):
        keithley._check_errors()
