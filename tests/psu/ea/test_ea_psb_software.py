"""Feature test: EA PSB/PSBE driven through both HALs, exercising the whole command set."""

from collections.abc import Iterator
from unittest.mock import MagicMock, call, patch

import pytest

from instro.eload import InstroELoad, LoadMode, SlewRateDirection
from instro.lib.exceptions import FeatureNotSupportedError
from instro.lib.transports import VisaConfig
from instro.lib.types import Measurement
from instro.psu import InstroPSU
from instro.psu.drivers import EAPSB

RESOURCE = "TCPIP::192.168.0.2::INSTR"

# Canned query responses keyed by the exact SCPI query string.
_QUERY_RESPONSES = {
    "SYST:ERR?": '0,"No error"',
    "SYST:LOCK:OWN?": "REMOTE",
    "MEAS:VOLT?": "48.00V",
    "MEAS:CURR?": "20.00A",
    "OUTP?": "ON",
    "VOLT:PROT?": "60.00",
    "CURR:PROT?": "25.00",
}


@pytest.fixture
def visa_cls() -> Iterator[MagicMock]:
    with patch("instro.psu.drivers.ea_psb.VisaDriver", autospec=True) as cls:
        yield cls


@pytest.fixture
def visa(visa_cls: MagicMock) -> MagicMock:
    inst = visa_cls.return_value
    inst.query.side_effect = lambda cmd: _QUERY_RESPONSES[cmd]
    return inst


def _writes(visa: MagicMock) -> list[str]:
    return [c.args[0] for c in visa.write.call_args_list]


def _latest(measurement: Measurement | None) -> float | str:
    assert measurement is not None
    return measurement.latest


@pytest.fixture
def drv(visa_cls: MagicMock) -> EAPSB:
    return EAPSB(RESOURCE)


def test_init_builds_visa_driver_from_resource(visa_cls: MagicMock) -> None:
    EAPSB(RESOURCE)
    visa_cls.assert_called_once_with(RESOURCE)


def test_init_accepts_prebuilt_connection_config(visa_cls: MagicMock) -> None:
    config = VisaConfig(visa_resource=RESOURCE)
    EAPSB(config)
    visa_cls.assert_called_once_with(config)


def test_open_acquires_remote_lock_and_verifies_ownership(drv: EAPSB, visa: MagicMock) -> None:
    drv.open()
    visa.open.assert_called_once()
    assert _writes(visa) == ["SYST:LOCK ON"]
    assert call("SYST:LOCK:OWN?") in visa.query.call_args_list


def test_open_raises_when_ownership_not_remote(drv: EAPSB, visa: MagicMock) -> None:
    visa.query.side_effect = lambda cmd: {"SYST:ERR?": '0,"No error"', "SYST:LOCK:OWN?": "LOCAL"}[cmd]
    with pytest.raises(RuntimeError, match="remote lock"):
        drv.open()


def test_close_releases_lock_then_closes(drv: EAPSB, visa: MagicMock) -> None:
    drv.close()
    assert _writes(visa) == ["SYST:LOCK OFF"]
    visa.close.assert_called_once()


def test_check_errors_raises_on_error_response(drv: EAPSB, visa: MagicMock) -> None:
    visa.query.side_effect = None
    visa.query.return_value = '-201,"Cannot be done in local mode"'
    with pytest.raises(RuntimeError, match="EA PSB reported error"):
        drv.open()


def test_set_voltage_writes_checked(drv: EAPSB, visa: MagicMock) -> None:
    drv.set_voltage(48.0, channel=1)
    visa.write.assert_called_once_with("VOLT 48.000")
    visa.query.assert_called_once_with("SYST:ERR?")


def test_get_voltage_strips_unit_suffix(drv: EAPSB, visa: MagicMock) -> None:
    visa.query.side_effect = ["48.00V", '0,"No error"']
    assert drv.get_voltage(channel=1) == pytest.approx(48.0)
    assert visa.query.call_args_list == [call("MEAS:VOLT?"), call("SYST:ERR?")]


def test_set_current_limit_writes_checked(drv: EAPSB, visa: MagicMock) -> None:
    drv.set_current_limit(20.0, channel=1)
    visa.write.assert_called_once_with("CURR 20.000")
    visa.query.assert_called_once_with("SYST:ERR?")


def test_get_current_parses_negative_sink_current(drv: EAPSB, visa: MagicMock) -> None:
    visa.query.side_effect = ["-5.00A", '0,"No error"']
    assert drv.get_current(channel=1) == pytest.approx(-5.0)
    assert visa.query.call_args_list == [call("MEAS:CURR?"), call("SYST:ERR?")]


def test_output_enable_writes_checked(drv: EAPSB, visa: MagicMock) -> None:
    drv.output_enable(True, channel=1)
    visa.write.assert_called_once_with("OUTP ON")
    drv.output_enable(False, channel=1)
    assert visa.write.call_args_list[-1] == call("OUTP OFF")


def test_get_output_status_parses(drv: EAPSB, visa: MagicMock) -> None:
    visa.query.side_effect = ["ON", '0,"No error"']
    assert drv.get_output_status(channel=1) is True
    visa.query.side_effect = ["OFF", '0,"No error"']
    assert drv.get_output_status(channel=1) is False


def test_overvoltage_protection_level_round_trip(drv: EAPSB, visa: MagicMock) -> None:
    drv.set_overvoltage_protection_level(60.0, channel=1)
    visa.write.assert_called_once_with("VOLT:PROT 60.000")
    visa.query.side_effect = ["60.00", '0,"No error"']
    assert drv.get_overvoltage_protection_level(channel=1) == pytest.approx(60.0)
    assert visa.query.call_args_list[-2:] == [call("VOLT:PROT?"), call("SYST:ERR?")]


def test_overcurrent_protection_level_round_trip(drv: EAPSB, visa: MagicMock) -> None:
    drv.set_overcurrent_protection_level(25.0, channel=1)
    visa.write.assert_called_once_with("CURR:PROT 25.000")
    visa.query.side_effect = ["25.00", '0,"No error"']
    assert drv.get_overcurrent_protection_level(channel=1) == pytest.approx(25.0)
    assert visa.query.call_args_list[-2:] == [call("CURR:PROT?"), call("SYST:ERR?")]


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("set_overvoltage_protection_enabled", (True,)),
        ("get_overvoltage_protection_enabled", ()),
        ("set_overvoltage_protection_delay", (0.1,)),
        ("get_overvoltage_protection_delay", ()),
        ("set_overcurrent_protection_enabled", (True,)),
        ("get_overcurrent_protection_enabled", ()),
        ("set_remote_sense_enabled", (True,)),
        ("get_remote_sense_enabled", ()),
    ],
)
def test_unsupported_psu_optionals_raise_without_scpi(
    drv: EAPSB,
    visa: MagicMock,
    method_name: str,
    args: tuple[object, ...],
) -> None:
    with pytest.raises(FeatureNotSupportedError, match=f"{method_name} is not supported"):
        getattr(drv, method_name)(*args, channel=1)
    visa.write.assert_not_called()
    visa.query.assert_not_called()


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("set_voltage", (48.0,)),
        ("get_voltage", ()),
        ("set_current_limit", (20.0,)),
        ("get_current", ()),
        ("output_enable", (True,)),
        ("get_output_status", ()),
        ("set_overvoltage_protection_level", (60.0,)),
        ("get_overvoltage_protection_level", ()),
        ("set_overcurrent_protection_level", (25.0,)),
        ("get_overcurrent_protection_level", ()),
    ],
)
def test_invalid_channel_raises_without_scpi(
    drv: EAPSB,
    visa: MagicMock,
    method_name: str,
    args: tuple[object, ...],
) -> None:
    with pytest.raises(ValueError, match="EA PSB channel must be 1"):
        getattr(drv, method_name)(*args, channel=2)
    visa.write.assert_not_called()
    visa.query.assert_not_called()


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (LoadMode.CC, "SYST:CONF:MODE UIP"),
        (LoadMode.CP, "SYST:CONF:MODE UIP"),
        (LoadMode.CV, "SYST:CONF:MODE UIP"),
        (LoadMode.CR, "SYST:CONF:MODE UIR"),
    ],
)
def test_set_mode_selects_operation_mode(drv: EAPSB, visa: MagicMock, mode: LoadMode, expected: str) -> None:
    drv.set_mode(mode, channel=1)
    visa.write.assert_called_once_with(expected)


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (LoadMode.CC, "SINK:CURR 10.000"),
        (LoadMode.CP, "SINK:POW 500.000"),
        (LoadMode.CR, "SINK:RES 5.000"),
    ],
)
def test_set_level_writes_sink_setpoint(drv: EAPSB, visa: MagicMock, mode: LoadMode, expected: str) -> None:
    drv.set_level(mode, {LoadMode.CC: 10.0, LoadMode.CP: 500.0, LoadMode.CR: 5.0}[mode], channel=1, curr_limit=None)
    visa.write.assert_called_once_with(expected)


def test_set_level_cv_writes_shared_voltage_setpoint(drv: EAPSB, visa: MagicMock) -> None:
    drv.set_level(LoadMode.CV, 24.0, channel=1, curr_limit=None)
    visa.write.assert_called_once_with("VOLT 24.000")


def test_set_level_cv_with_curr_limit_also_writes_sink_current(drv: EAPSB, visa: MagicMock) -> None:
    drv.set_level(LoadMode.CV, 24.0, channel=1, curr_limit=15.0)
    assert _writes(visa) == ["VOLT 24.000", "SINK:CURR 15.000"]


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("set_range", (LoadMode.CC, 10.0)),
        ("set_slewrate", (SlewRateDirection.RISE, 1.0)),
        ("short_output", (True,)),
    ],
)
def test_unsupported_eload_methods_raise_without_scpi(
    drv: EAPSB,
    visa: MagicMock,
    method_name: str,
    args: tuple[object, ...],
) -> None:
    with pytest.raises(FeatureNotSupportedError, match=f"{method_name} is not supported"):
        getattr(drv, method_name)(*args, channel=1)
    visa.write.assert_not_called()
    visa.query.assert_not_called()


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("set_mode", (LoadMode.CC,)),
        ("set_level", (LoadMode.CC, 10.0)),
    ],
)
def test_eload_invalid_channel_raises_without_scpi(
    drv: EAPSB,
    visa: MagicMock,
    method_name: str,
    args: tuple[object, ...],
) -> None:
    kwargs: dict[str, object] = {"curr_limit": None} if method_name == "set_level" else {}
    with pytest.raises(ValueError, match="EA PSB channel must be 1"):
        getattr(drv, method_name)(*args, channel=2, **kwargs)
    visa.write.assert_not_called()
    visa.query.assert_not_called()


def test_psb_full_interface_through_both_hals(visa_cls: MagicMock, visa: MagicMock) -> None:
    # One driver, wrapped as both a source (InstroPSU) and a sink (InstroELoad).
    driver = EAPSB(VisaConfig(visa_resource=RESOURCE))
    visa_cls.assert_called_once_with(VisaConfig(visa_resource=RESOURCE))

    psu = InstroPSU("psb", driver=driver, num_channels=1)
    eload = InstroELoad("psb_sink", driver=driver)

    psu.open()
    visa.open.assert_called_once()
    assert "SYST:LOCK ON" in _writes(visa)
    assert call("SYST:LOCK:OWN?") in visa.query.call_args_list

    # --- PSU (source) surface ---
    psu.set_voltage(48, channel=1)
    psu.set_current_limit(20, channel=1)
    psu.output_enable(True, channel=1)
    assert _latest(psu.get_output_status(channel=1)) == pytest.approx(1.0)  # "ON" -> True -> 1.0
    assert _latest(psu.get_voltage(channel=1)) == pytest.approx(48.0)
    assert _latest(psu.get_current(channel=1)) == pytest.approx(20.0)
    psu.set_overvoltage_protection_level(60, channel=1)
    assert _latest(psu.get_overvoltage_protection_level(channel=1)) == pytest.approx(60.0)
    psu.set_overcurrent_protection_level(25, channel=1)
    assert _latest(psu.get_overcurrent_protection_level(channel=1)) == pytest.approx(25.0)

    for expected in ("VOLT 48.000", "CURR 20.000", "OUTP ON", "VOLT:PROT 60.000", "CURR:PROT 25.000"):
        assert expected in _writes(visa)

    # --- E-Load (sink) surface: every LoadMode ---
    eload.set_mode(LoadMode.CC)
    eload.set_level(10)
    eload.set_mode(LoadMode.CP)
    eload.set_level(500)
    eload.set_mode(LoadMode.CR)
    eload.set_level(5)
    eload.set_mode(LoadMode.CV)
    eload.set_level(24, curr_limit=15)

    writes = _writes(visa)
    for expected in (
        "SYST:CONF:MODE UIP",  # CC/CP/CV
        "SYST:CONF:MODE UIR",  # CR unlocks the resistance subsystem
        "SINK:CURR 10.000",
        "SINK:POW 500.000",
        "SINK:RES 5.000",
        "VOLT 24.000",  # CV uses the shared voltage setpoint
        "SINK:CURR 15.000",  # CV curr_limit
    ):
        assert expected in writes

    # --- Unsupported over SCPI: raise FeatureNotSupportedError and emit no new SCPI ---
    before = len(visa.write.call_args_list)
    unsupported = [
        lambda: eload.set_range(1.0),
        lambda: eload.set_slewrate(SlewRateDirection.RISE, 1.0),
        lambda: eload.short_output(True),
        lambda: psu.set_overvoltage_protection_enabled(True, channel=1),
        lambda: psu.set_overvoltage_protection_delay(0.1, channel=1),
        lambda: psu.set_overcurrent_protection_enabled(True, channel=1),
        lambda: psu.set_remote_sense_enabled(True, channel=1),
    ]
    for invoke in unsupported:
        with pytest.raises(FeatureNotSupportedError):
            invoke()
    assert len(visa.write.call_args_list) == before

    psu.close()
    assert "SYST:LOCK OFF" in _writes(visa)
    visa.close.assert_called_once()
