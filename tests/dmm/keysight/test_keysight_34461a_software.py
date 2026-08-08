"""Software tests for the Keysight 34461A Truevolt DMM driver."""

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest

from instro.dmm.drivers import Keysight34461A
from instro.dmm.types import MeasurementFunction
from instro.lib.transports import VisaConfig

# Every measurement function the 34461A supports: the FUNC selection string and
# the sense-subsystem root used by the range/NPLC setters.
_FUNCTIONS = [
    (MeasurementFunction.DC_VOLTAGE, "measure_dc_voltage", "VOLT", "VOLT:DC"),
    (MeasurementFunction.AC_VOLTAGE, "measure_ac_voltage", "VOLT:AC", "VOLT:AC"),
    (MeasurementFunction.DC_CURRENT, "measure_dc_current", "CURR", "CURR:DC"),
    (MeasurementFunction.AC_CURRENT, "measure_ac_current", "CURR:AC", "CURR:AC"),
    (MeasurementFunction.TWO_WIRE_RESISTANCE, "measure_resistance", "RES", "RES"),
    (MeasurementFunction.FOUR_WIRE_RESISTANCE, "measure_four_wire_resistance", "FRES", "FRES"),
]


@pytest.fixture
def keysight_visa_cls() -> Iterator[MagicMock]:
    with patch("instro.dmm.drivers.keysight_34461a.VisaDriver", autospec=True) as driver_cls:
        yield driver_cls


@pytest.fixture
def keysight_visa(keysight_visa_cls: MagicMock) -> MagicMock:
    visa = keysight_visa_cls.return_value
    visa.query.return_value = '+0,"No error"'
    return visa


@pytest.fixture
def keysight(keysight_visa_cls: MagicMock) -> Keysight34461A:
    return Keysight34461A("TCPIP0::192.168.1.10::INSTR")


# --- init / transport ---


def test_keysight_init_builds_visa_from_resource(keysight_visa_cls: MagicMock) -> None:
    Keysight34461A("TCPIP0::192.168.1.10::INSTR")

    keysight_visa_cls.assert_called_once_with("TCPIP0::192.168.1.10::INSTR")


def test_keysight_init_accepts_prebuilt_connection_config(keysight_visa_cls: MagicMock) -> None:
    config = VisaConfig(visa_resource="TCPIP0::192.168.1.10::INSTR")

    Keysight34461A(config)

    keysight_visa_cls.assert_called_once_with(config)


# --- open / close lifecycle ---


def test_keysight_open_clears_without_syst_rem(keysight: Keysight34461A, keysight_visa: MagicMock) -> None:
    # Truevolt has no SYST:REM (sending it errors with -113); open() must only *CLS.
    keysight.open()
    keysight_visa.open.assert_called_once()
    assert [c.args[0] for c in keysight_visa.write.call_args_list] == ["*CLS"]
    keysight_visa.query.assert_called_once_with("SYST:ERR?")


def test_keysight_close_closes_visa(keysight: Keysight34461A, keysight_visa: MagicMock) -> None:
    keysight.close()
    keysight_visa.close.assert_called_once()


# --- set_measurement_function (FUNC selection, not CONF/MEAS which reset settings) ---


@pytest.mark.parametrize(
    "function, func_cmd",
    [(function, func_cmd) for function, _method, func_cmd, _root in _FUNCTIONS],
)
def test_keysight_set_measurement_function_writes_func(
    keysight: Keysight34461A, keysight_visa: MagicMock, function: MeasurementFunction, func_cmd: str
) -> None:
    keysight.set_measurement_function(function)
    keysight_visa.write.assert_called_once_with(f'FUNC "{func_cmd}"')
    keysight_visa.query.assert_called_once_with("SYST:ERR?")


# --- measure_* per function (FUNC + READ?, float parse) ---


@pytest.mark.parametrize(
    "method, func_cmd",
    [(method, func_cmd) for _function, method, func_cmd, _root in _FUNCTIONS],
)
def test_keysight_measure_methods_select_function_then_read(
    keysight: Keysight34461A, keysight_visa: MagicMock, method: str, func_cmd: str
) -> None:
    keysight_visa.query.side_effect = ["+4.98748741E-01", '+0,"No error"']

    assert getattr(keysight, method)() == pytest.approx(0.498748741)

    keysight_visa.write.assert_called_once_with(f'FUNC "{func_cmd}"')
    assert [c.args[0] for c in keysight_visa.query.call_args_list] == ["READ?", "SYST:ERR?"]


# --- range setters (manual value and auto-range) ---

_RANGE_SETTERS = [
    ("set_dc_voltage_range", "VOLT:DC"),
    ("set_ac_voltage_range", "VOLT:AC"),
    ("set_dc_current_range", "CURR:DC"),
    ("set_ac_current_range", "CURR:AC"),
    ("set_two_wire_resistance_range", "RES"),
    ("set_four_wire_resistance_range", "FRES"),
]


@pytest.mark.parametrize("setter, root", _RANGE_SETTERS)
def test_keysight_range_setters_write_manual_range(
    keysight: Keysight34461A, keysight_visa: MagicMock, setter: str, root: str
) -> None:
    getattr(keysight, setter)(10.0)
    keysight_visa.write.assert_called_once_with(f"{root}:RANG 1.000000e+01")
    keysight_visa.query.assert_called_once_with("SYST:ERR?")


@pytest.mark.parametrize("setter, root", _RANGE_SETTERS)
def test_keysight_range_setters_none_selects_auto(
    keysight: Keysight34461A, keysight_visa: MagicMock, setter: str, root: str
) -> None:
    getattr(keysight, setter)(None)
    keysight_visa.write.assert_called_once_with(f"{root}:RANG:AUTO ON")


# --- NPLC setters (DC and resistance only; AC has no NPLC on Truevolt) ---


@pytest.mark.parametrize(
    "setter, root",
    [
        ("set_dc_voltage_nplc", "VOLT:DC"),
        ("set_dc_current_nplc", "CURR:DC"),
        ("set_two_wire_resistance_nplc", "RES"),
        ("set_four_wire_resistance_nplc", "FRES"),
    ],
)
def test_keysight_nplc_setters_write_nplc(
    keysight: Keysight34461A, keysight_visa: MagicMock, setter: str, root: str
) -> None:
    getattr(keysight, setter)(10)
    keysight_visa.write.assert_called_once_with(f"{root}:NPLC 10.0000")
    keysight_visa.query.assert_called_once_with("SYST:ERR?")


@pytest.mark.parametrize("setter", ["set_ac_voltage_nplc", "set_ac_current_nplc"])
def test_keysight_ac_nplc_not_supported(keysight: Keysight34461A, setter: str) -> None:
    with pytest.raises(NotImplementedError, match="filter bandwidth"):
        getattr(keysight, setter)(10)


# --- digits (no Truevolt SCPI digits command) ---


def test_keysight_set_digits_not_supported(keysight: Keysight34461A) -> None:
    with pytest.raises(NotImplementedError, match="set_aperture_nplc"):
        keysight.set_digits(6)


# --- error query ---


def test_keysight_check_errors_passes_on_plus_zero(keysight: Keysight34461A, keysight_visa: MagicMock) -> None:
    keysight_visa.query.return_value = '+0,"No error"'
    keysight._check_errors()


def test_keysight_check_errors_raises_on_nonzero(keysight: Keysight34461A, keysight_visa: MagicMock) -> None:
    keysight_visa.query.return_value = '-113,"Undefined header"'
    with pytest.raises(RuntimeError, match="Keysight 34461A reported error"):
        keysight._check_errors()
