"""Software tests for the Agilent/HP/Keysight 34401A DMM driver."""

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest

from instro.dmm.drivers import Agilent34401A
from instro.dmm.types import MeasurementFunction
from instro.lib.transports import SerialConfig, VisaConfig

# Every measurement function the 34401A supports, paired with the bare MEAS query
# it must issue. Drives both the set_measurement_function dispatch and the
# measure_* wire-level tests.
_FUNCTION_QUERIES = [
    (MeasurementFunction.DC_VOLTAGE, "measure_dc_voltage", "MEAS:VOLT:DC?"),
    (MeasurementFunction.AC_VOLTAGE, "measure_ac_voltage", "MEAS:VOLT:AC?"),
    (MeasurementFunction.DC_CURRENT, "measure_dc_current", "MEAS:CURR:DC?"),
    (MeasurementFunction.AC_CURRENT, "measure_ac_current", "MEAS:CURR:AC?"),
    (MeasurementFunction.TWO_WIRE_RESISTANCE, "measure_resistance", "MEAS:RES?"),
    (MeasurementFunction.FOUR_WIRE_RESISTANCE, "measure_four_wire_resistance", "MEAS:FRES?"),
]


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


# --- init / transport ---


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


# --- open / close lifecycle ---


def test_agilent_open_clears_and_takes_remote(agilent: Agilent34401A, agilent_visa: MagicMock) -> None:
    agilent.open()
    agilent_visa.open.assert_called_once()
    assert [c.args[0] for c in agilent_visa.write.call_args_list] == ["*CLS", "SYST:REM"]


def test_agilent_close_closes_visa(agilent: Agilent34401A, agilent_visa: MagicMock) -> None:
    agilent.close()
    agilent_visa.close.assert_called_once()


# --- set_measurement_function dispatch (each function arms its MEAS query) ---


@pytest.mark.parametrize(
    "function, expected_cmd",
    [(function, cmd) for function, _method, cmd in _FUNCTION_QUERIES],
)
def test_agilent_set_measurement_function_dispatches_to_measure(
    agilent: Agilent34401A, agilent_visa: MagicMock, function: MeasurementFunction, expected_cmd: str
) -> None:
    agilent_visa.query.side_effect = ["1.0", '0,"No error"']
    agilent.set_measurement_function(function)
    assert agilent_visa.query.call_args_list[0].args == (expected_cmd,)


# --- measure_* per function (wire-level MEAS query + float parse) ---


@pytest.mark.parametrize(
    "method, expected_cmd",
    [(method, cmd) for _function, method, cmd in _FUNCTION_QUERIES],
)
def test_agilent_measure_methods_use_meas_query(
    agilent: Agilent34401A, agilent_visa: MagicMock, method: str, expected_cmd: str
) -> None:
    agilent_visa.query.side_effect = ["1.234", '0,"No error"']
    assert getattr(agilent, method)() == pytest.approx(1.234)
    assert agilent_visa.query.call_args_list[0].args == (expected_cmd,)


# --- range (shared cache across functions) ---


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


def test_agilent_measure_with_digits_only_issues_bare_command(agilent: Agilent34401A, agilent_visa: MagicMock) -> None:
    # Resolution-without-range is not expressible in MEAS?, so set_digits alone is dropped.
    agilent.set_digits(6)
    agilent_visa.query.side_effect = ["0.5", '0,"No error"']

    agilent.measure_dc_voltage()

    cmd = agilent_visa.query.call_args_list[0].args[0]
    assert cmd == "MEAS:VOLT:DC?"


@pytest.mark.parametrize(
    "setter",
    [
        "set_dc_voltage_range",
        "set_ac_voltage_range",
        "set_dc_current_range",
        "set_ac_current_range",
        "set_two_wire_resistance_range",
        "set_four_wire_resistance_range",
    ],
)
def test_agilent_per_function_range_methods_share_state(agilent: Agilent34401A, setter: str) -> None:
    # The 34401A applies one shared range cache regardless of function. Every
    # per-function range setter must write to the same private slot.
    getattr(agilent, setter)(1234.0)
    assert agilent._range == 1234.0


# --- set_digits (resolution argument to MEAS) ---


@pytest.mark.parametrize("digits, resolution", [(4, "1.000000e-03"), (5, "1.000000e-04"), (6, "1.000000e-05")])
def test_agilent_set_digits_sets_resolution(
    agilent: Agilent34401A, agilent_visa: MagicMock, digits: int, resolution: str
) -> None:
    agilent.set_dc_voltage_range(10.0)
    agilent.set_digits(digits)
    agilent_visa.query.side_effect = ["0.5", '0,"No error"']

    agilent.measure_dc_voltage()

    cmd = agilent_visa.query.call_args_list[0].args[0]
    assert cmd == f"MEAS:VOLT:DC? 1.000000e+01,{resolution}"


def test_agilent_set_digits_rejects_invalid(agilent: Agilent34401A) -> None:
    with pytest.raises(ValueError, match="34401A"):
        agilent.set_digits(7)


# --- error query ---


def test_agilent_check_errors_passes_on_zero(agilent: Agilent34401A, agilent_visa: MagicMock) -> None:
    agilent_visa.query.return_value = '0,"No error"'
    agilent._check_errors()


def test_agilent_check_errors_raises_on_nonzero(agilent: Agilent34401A, agilent_visa: MagicMock) -> None:
    agilent_visa.query.return_value = '-113,"Undefined header"'
    with pytest.raises(RuntimeError, match="Agilent 34401A reported error"):
        agilent._check_errors()
