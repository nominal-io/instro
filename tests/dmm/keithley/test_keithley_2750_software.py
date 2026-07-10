"""Software tests for the Keithley 2750 front-panel DMM driver (unstable)."""

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest

from instro.dmm.types import MeasurementFunction
from instro.lib.transports.visa import VisaConfig
from instro.unstable.dmm.drivers import Keithley2750


@pytest.fixture
def keithley_visa_cls() -> Iterator[MagicMock]:
    with patch("instro.unstable.dmm.drivers.keithley_2750.VisaDriver", autospec=True) as driver_cls:
        yield driver_cls


@pytest.fixture
def keithley_visa(keithley_visa_cls: MagicMock) -> MagicMock:
    visa = keithley_visa_cls.return_value
    visa.query.return_value = '0,"No error"'
    return visa


@pytest.fixture
def keithley(keithley_visa_cls: MagicMock, keithley_visa: MagicMock) -> Keithley2750:
    return Keithley2750("GPIB0::16::INSTR")


def _writes(visa: MagicMock) -> list[str]:
    return [c.args[0] for c in visa.write.call_args_list]


def test_init_builds_visa_from_resource(keithley_visa_cls: MagicMock) -> None:
    Keithley2750("GPIB0::16::INSTR")
    keithley_visa_cls.assert_called_once_with("GPIB0::16::INSTR")


def test_init_builds_visa_from_config(keithley_visa_cls: MagicMock) -> None:
    config = VisaConfig(visa_resource="GPIB0::16::INSTR")
    Keithley2750(config)
    keithley_visa_cls.assert_called_once_with(config)


def test_open_resets_and_sets_reading_element(keithley: Keithley2750, keithley_visa: MagicMock) -> None:
    keithley.open()
    keithley_visa.open.assert_called_once_with()
    writes = _writes(keithley_visa)
    assert writes == ["*CLS", "*RST", ":FORMat:ELEMents READing"]
    keithley_visa.query.assert_called_once_with(":SYSTem:ERRor?")


def test_close_delegates_to_transport(keithley: Keithley2750, keithley_visa: MagicMock) -> None:
    keithley.close()
    keithley_visa.close.assert_called_once_with()


@pytest.mark.parametrize(
    ("function", "expected"),
    [
        (MeasurementFunction.DC_VOLTAGE, ':FUNCtion "VOLTage:DC"'),
        (MeasurementFunction.AC_VOLTAGE, ':FUNCtion "VOLTage:AC"'),
        (MeasurementFunction.DC_CURRENT, ':FUNCtion "CURRent:DC"'),
        (MeasurementFunction.AC_CURRENT, ':FUNCtion "CURRent:AC"'),
        (MeasurementFunction.TWO_WIRE_RESISTANCE, ':FUNCtion "RESistance"'),
        (MeasurementFunction.FOUR_WIRE_RESISTANCE, ':FUNCtion "FRESistance"'),
    ],
)
def test_set_measurement_function(
    keithley: Keithley2750, keithley_visa: MagicMock, function: MeasurementFunction, expected: str
) -> None:
    keithley.set_measurement_function(function)
    assert expected in _writes(keithley_visa)
    keithley_visa.query.assert_called_once_with(":SYSTem:ERRor?")


def test_set_digits_unsupported(keithley: Keithley2750) -> None:
    with pytest.raises(NotImplementedError, match="set_aperture_nplc"):
        keithley.set_digits(6)


def test_set_aperture_seconds_unsupported(keithley: Keithley2750) -> None:
    with pytest.raises(NotImplementedError, match="set_aperture_nplc"):
        keithley.set_aperture_seconds(0.1)


def test_set_dc_voltage_nplc(keithley: Keithley2750, keithley_visa: MagicMock) -> None:
    keithley.set_dc_voltage_nplc(2.5)
    assert ":SENSe:VOLTage:DC:NPLCycles 2.5" in _writes(keithley_visa)


def test_set_two_wire_resistance_nplc(keithley: Keithley2750, keithley_visa: MagicMock) -> None:
    keithley.set_two_wire_resistance_nplc(1)
    assert ":SENSe:RESistance:NPLCycles 1" in _writes(keithley_visa)


def test_set_ac_voltage_nplc_sets_detector_bandwidth_first(keithley: Keithley2750, keithley_visa: MagicMock) -> None:
    keithley.set_ac_voltage_nplc(5)
    writes = _writes(keithley_visa)
    assert writes == [":SENSe:VOLTage:AC:DETector:BANDwidth 300", ":SENSe:VOLTage:AC:NPLCycles 5"]


def test_set_ac_current_nplc_sets_detector_bandwidth_first(keithley: Keithley2750, keithley_visa: MagicMock) -> None:
    keithley.set_ac_current_nplc(1)
    writes = _writes(keithley_visa)
    assert writes == [":SENSe:CURRent:AC:DETector:BANDwidth 300", ":SENSe:CURRent:AC:NPLCycles 1"]


def test_set_dc_voltage_range_auto(keithley: Keithley2750, keithley_visa: MagicMock) -> None:
    keithley.set_dc_voltage_range(None)
    assert ":SENSe:VOLTage:DC:RANGe:AUTO ON" in _writes(keithley_visa)


def test_set_dc_voltage_range_manual(keithley: Keithley2750, keithley_visa: MagicMock) -> None:
    keithley.set_dc_voltage_range(10.0)
    writes = _writes(keithley_visa)
    assert ":SENSe:VOLTage:DC:RANGe:AUTO OFF" in writes
    assert ":SENSe:VOLTage:DC:RANGe:UPPer 10" in writes


def test_set_four_wire_resistance_range_manual(keithley: Keithley2750, keithley_visa: MagicMock) -> None:
    keithley.set_four_wire_resistance_range(1000.0)
    writes = _writes(keithley_visa)
    assert ":SENSe:FRESistance:RANGe:AUTO OFF" in writes
    assert ":SENSe:FRESistance:RANGe:UPPer 1000" in writes


@pytest.mark.parametrize(
    "measure",
    [
        "measure_dc_voltage",
        "measure_ac_voltage",
        "measure_dc_current",
        "measure_ac_current",
        "measure_resistance",
        "measure_four_wire_resistance",
    ],
)
def test_measure_triggers_read_and_parses(keithley: Keithley2750, keithley_visa: MagicMock, measure: str) -> None:
    keithley_visa.query.side_effect = ["+1.234560E-03", '0,"No error"']
    value = getattr(keithley, measure)()
    assert value == pytest.approx(1.23456e-3)
    assert keithley_visa.query.call_args_list[0].args[0] == ":READ?"


def test_check_errors_passes_on_bare_zero(keithley: Keithley2750, keithley_visa: MagicMock) -> None:
    keithley_visa.query.return_value = '0,"No error"'
    keithley._check_errors()


def test_check_errors_passes_on_signed_zero(keithley: Keithley2750, keithley_visa: MagicMock) -> None:
    keithley_visa.query.return_value = '+0,"No error"'
    keithley._check_errors()


def test_check_errors_raises_on_nonzero(keithley: Keithley2750, keithley_visa: MagicMock) -> None:
    keithley_visa.query.return_value = '-113,"Undefined header"'
    with pytest.raises(RuntimeError, match="Keithley 2750 reported error"):
        keithley._check_errors()
