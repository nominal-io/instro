"""Tests for the eload driver shape: BK85XXB owning VisaDriver, and InstroELoad delegating to its driver."""

from collections.abc import Iterator
from unittest.mock import MagicMock, call, patch

import pytest

from instro.eload import ELoadDriverBase, InstroELoad
from instro.eload.drivers.bk_85xxb import BK85XXB, loadmode_to_unit
from instro.eload.types import LoadMode, SlewRateDirection
from instro.lib.transports import SerialConfig, VisaConfig

# --- BK85XXB unit tests (driver-owned transport over a mocked VisaDriver) ---


@pytest.fixture
def visa_driver_cls() -> Iterator[MagicMock]:
    with patch("instro.eload.drivers.bk_85xxb.VisaDriver", autospec=True) as driver_cls:
        yield driver_cls


@pytest.fixture
def visa_mock(visa_driver_cls: MagicMock) -> MagicMock:
    visa = visa_driver_cls.return_value
    visa.query.return_value = '0,"No error"'
    return visa


@pytest.fixture
def bk(visa_driver_cls: MagicMock) -> BK85XXB:
    return BK85XXB("ASRL19::INSTR")


def test_init_builds_visa_driver_from_resource(visa_driver_cls: MagicMock) -> None:
    BK85XXB("ASRL19::INSTR")

    visa_driver_cls.assert_called_once_with("ASRL19::INSTR")


def test_init_accepts_prebuilt_connection_config(visa_driver_cls: MagicMock) -> None:
    config = VisaConfig(
        visa_resource="ASRL19::INSTR",
        serial_config=SerialConfig(baud_rate=19_200),
    )

    BK85XXB(config)

    visa_driver_cls.assert_called_once_with(config)


def test_open_opens_visa_and_takes_remote(bk: BK85XXB, visa_mock: MagicMock) -> None:
    bk.open()
    visa_mock.open.assert_called_once()
    visa_mock.write.assert_called_once_with("SYST:REM")
    visa_mock.query.assert_called_once_with("SYST:ERR?")


def test_close_closes_visa(bk: BK85XXB, visa_mock: MagicMock) -> None:
    bk.close()
    visa_mock.close.assert_called_once()


@pytest.mark.parametrize(
    ("mode", "expected_unit"),
    [
        (LoadMode.CC, "CURR"),
        (LoadMode.CV, "VOLT"),
        (LoadMode.CP, "POW"),
        (LoadMode.CR, "RES"),
    ],
)
def test_loadmode_to_unit(mode: LoadMode, expected_unit: str) -> None:
    assert loadmode_to_unit(mode) == expected_unit


def test_set_mode_writes_function(bk: BK85XXB, visa_mock: MagicMock) -> None:
    bk.set_mode(LoadMode.CC, channel=1)
    visa_mock.write.assert_called_once_with("FUNCtion CURR")
    visa_mock.query.assert_called_once_with("SYST:ERR?")


def test_set_level_uses_parameter_mode(bk: BK85XXB, visa_mock: MagicMock) -> None:
    bk.set_level(mode=LoadMode.CC, value=3.5, channel=1, curr_limit=None)
    visa_mock.write.assert_called_once_with("CURR 3.5")
    visa_mock.query.assert_called_once_with("SYST:ERR?")


@pytest.mark.parametrize(
    ("mode", "expected_command"),
    [
        (LoadMode.CC, "CURR:RANGe 2.0"),
        (LoadMode.CV, "VOLT:RANGe 2.0"),
    ],
)
def test_set_range_writes_for_cc_and_cv(
    bk: BK85XXB, visa_mock: MagicMock, mode: LoadMode, expected_command: str
) -> None:
    bk.set_range(mode, value=2.0, channel=1)
    visa_mock.write.assert_called_once_with(expected_command)
    visa_mock.query.assert_called_once_with("SYST:ERR?")


@pytest.mark.parametrize("mode", [LoadMode.CP, LoadMode.CR])
def test_set_range_rejects_cp_and_cr(bk: BK85XXB, visa_mock: MagicMock, mode: LoadMode) -> None:
    with pytest.raises(NotImplementedError, match="only exposes :RANGe for CC and CV"):
        bk.set_range(mode, value=2.0, channel=1)
    visa_mock.write.assert_not_called()


def test_set_slewrate_writes(bk: BK85XXB, visa_mock: MagicMock) -> None:
    bk.set_slewrate(SlewRateDirection.RISE, rate=0.1, channel=1)
    visa_mock.write.assert_called_once_with("CURRent:SLEW:RISE 0.1")
    visa_mock.query.assert_called_once_with("SYST:ERR?")


def test_output_enable_writes(bk: BK85XXB, visa_mock: MagicMock) -> None:
    bk.output_enable(True, channel=1)
    visa_mock.write.assert_called_once_with("INPut 1")
    visa_mock.query.assert_called_once_with("SYST:ERR?")


def test_short_output_writes_both_short_and_input(bk: BK85XXB, visa_mock: MagicMock) -> None:
    bk.short_output(True, channel=1)
    assert visa_mock.write.call_args_list == [call("INPut:SHORt 1"), call("INPut 1")]
    visa_mock.query.assert_called_once_with("SYST:ERR?")


def test_get_current_parses_response(bk: BK85XXB, visa_mock: MagicMock) -> None:
    visa_mock.query.side_effect = ["1.234", '0,"No error"']
    assert bk.get_current(channel=1) == pytest.approx(1.234)
    assert visa_mock.query.call_args_list == [call("MEASure:CURRent?"), call("SYST:ERR?")]


def test_get_voltage_parses_response(bk: BK85XXB, visa_mock: MagicMock) -> None:
    visa_mock.query.side_effect = ["5.000", '0,"No error"']
    assert bk.get_voltage(channel=1) == pytest.approx(5.0)
    assert visa_mock.query.call_args_list == [call("MEASure:VOLTage?"), call("SYST:ERR?")]


def test_driver_method_raises_on_nonzero_error(bk: BK85XXB, visa_mock: MagicMock) -> None:
    visa_mock.query.return_value = '-100,"Command error"'
    with pytest.raises(RuntimeError, match="BK85XXB reported error"):
        bk.set_mode(LoadMode.CC, channel=1)


# --- InstroELoad composition tests (instrument delegates to driver) ---


def _stub_driver() -> MagicMock:
    driver = MagicMock(spec=ELoadDriverBase)
    driver.get_current.return_value = 0.5
    driver.get_voltage.return_value = 12.0
    return driver


def test_nominal_eload_stores_driver() -> None:
    driver = _stub_driver()
    eload = InstroELoad(name="ut", driver=driver)
    assert eload._driver is driver


def test_nominal_eload_open_close_delegate_to_driver() -> None:
    driver = _stub_driver()
    eload = InstroELoad(name="ut", driver=driver)
    eload.open()
    driver.open.assert_called_once()
    eload.close()
    driver.close.assert_called_once()


def test_nominal_eload_close_stops_background_before_closing_driver() -> None:
    events: list[str] = []
    driver = _stub_driver()
    driver.close.side_effect = lambda: events.append("driver.close")
    eload = InstroELoad(name="ut", driver=driver)
    eload.stop = MagicMock(side_effect=lambda: events.append("stop"))  # type: ignore[method-assign]

    eload.close()

    assert events == ["stop", "driver.close"]


def test_nominal_eload_set_mode_delegates() -> None:
    driver = _stub_driver()
    eload = InstroELoad(name="ut", driver=driver)
    eload.set_mode(LoadMode.CC, channel=2)
    driver.set_mode.assert_called_once_with(mode=LoadMode.CC, channel=2)


def test_nominal_eload_set_level_requires_mode() -> None:
    driver = _stub_driver()
    eload = InstroELoad(name="ut", driver=driver)
    with pytest.raises(ValueError, match="Mode must be set"):
        eload.set_level(value=1.0)


def test_nominal_eload_set_level_delegates_with_current_mode() -> None:
    driver = _stub_driver()
    eload = InstroELoad(name="ut", driver=driver)
    eload.set_mode(LoadMode.CC)
    driver.reset_mock()
    eload.set_level(value=1.0, channel=1)
    driver.set_level.assert_called_once_with(mode=LoadMode.CC, value=1.0, channel=1, curr_limit=None)


def test_nominal_eload_get_current_returns_measurement() -> None:
    driver = _stub_driver()
    eload = InstroELoad(name="ut", driver=driver)
    measurement = eload.get_current(channel=1)
    assert measurement is not None
    assert "ut.ch1.current" in measurement.channel_data
    assert measurement.channel_data["ut.ch1.current"] == [0.5]


def test_legacy_naming_publishes_old_eload_channel_names() -> None:
    """`legacy_naming=True` round-trips pre-v1.0 ELoad channel names, including `_enable` (vs PSU's `_en`)."""
    driver = _stub_driver()
    eload = InstroELoad(name="ut", driver=driver, legacy_naming=True)

    assert "ut.ch1_v" in eload.get_voltage(channel=1).channel_data  # type: ignore[union-attr]
    assert "ut.ch1_i" in eload.get_current(channel=1).channel_data  # type: ignore[union-attr]
    assert "ut.ch1_mode.cmd" in eload.set_mode(LoadMode.CC, channel=1).channel_data
    assert "ut.ch1_level.cmd" in eload.set_level(2.0, channel=1).channel_data
    assert "ut.ch1_enable.cmd" in eload.output_enable(True, channel=1).channel_data
