"""Unit tests for Rigol DL3031A eload driver w/ mocked VisaDriver transport."""

from collections.abc import Iterator
from unittest.mock import MagicMock, call, patch

import pytest

from instro.contrib.eload.drivers.rigol_dl3031a import RigolDL3031A, loadmode_to_rigol, slew_direction_to_rigol
from instro.eload.types import LoadMode, SlewRateDirection
from instro.lib.transports import VisaConfig


@pytest.fixture
def visa_driver_cls() -> Iterator[MagicMock]:
    with patch("instro.contrib.eload.drivers.rigol_dl3031a.VisaDriver", autospec=True) as driver_cls:
        yield driver_cls


@pytest.fixture
def visa_mock(visa_driver_cls: MagicMock) -> MagicMock:
    visa = visa_driver_cls.return_value
    visa.query.return_value = '0,"No error"'
    return visa


@pytest.fixture
def rigol(visa_driver_cls: MagicMock) -> RigolDL3031A:
    return RigolDL3031A("USB0::0x1AB1::0x0E11::DL3D254300331::INSTR")


def test_init_builds_visa_driver_from_resource(visa_driver_cls: MagicMock) -> None:
    RigolDL3031A("USB0::0x1AB1::0x0E11::DL3D254300331::INSTR")
    visa_driver_cls.assert_called_once_with("USB0::0x1AB1::0x0E11::DL3D254300331::INSTR")


def test_init_accepts_prebuilt_connection_config(visa_driver_cls: MagicMock) -> None:
    config = VisaConfig(visa_resource="USB0::0x1AB1::0x0E11::DL3D254300331::INSTR")
    RigolDL3031A(config)
    visa_driver_cls.assert_called_once_with(config)


def test_open_opens_visa(rigol: RigolDL3031A, visa_mock: MagicMock) -> None:
    rigol.open()
    visa_mock.open.assert_called_once()
    # No write to go remote bc Rigol auto-enters remote mode when receiving valid SCPI command
    visa_mock.write.assert_not_called()


def test_close_closes_visa(rigol: RigolDL3031A, visa_mock: MagicMock) -> None:
    rigol.close()
    visa_mock.close.assert_called_once()


@pytest.mark.parametrize(
    ("mode", "expected_unit"),
    [
        (LoadMode.CC, "CURRent"),
        (LoadMode.CV, "VOLTage"),
        (LoadMode.CP, "POWer"),
        (LoadMode.CR, "RESistance"),
    ],
)
def test_loadmode_to_rigol(mode: LoadMode, expected_unit: str) -> None:
    assert loadmode_to_rigol(mode) == expected_unit


@pytest.mark.parametrize(
    ("direction", "expected"),
    [
        (SlewRateDirection.RISE, "POSitive"),
        (SlewRateDirection.FALL, "NEGative"),
        (SlewRateDirection.BOTH, "BOTH"),
    ],
)
def test_slew_direction_to_rigol(direction: SlewRateDirection, expected: str) -> None:
    assert slew_direction_to_rigol(direction) == expected


@pytest.mark.parametrize(
    ("mode", "expected_cmd"),
    [
        (LoadMode.CC, "FUNC CURRent"),
        (LoadMode.CV, "FUNC VOLTage"),
        (LoadMode.CR, "FUNC RESistance"),
        (LoadMode.CP, "FUNC POWer"),
    ],
)
def test_set_mode_writes_function(rigol: RigolDL3031A, visa_mock: MagicMock, mode: LoadMode, expected_cmd: str) -> None:
    rigol.set_mode(mode, channel=1)
    visa_mock.write.assert_called_once_with(expected_cmd)


def test_set_level_writes_level_w_no_curr_limit(rigol: RigolDL3031A, visa_mock: MagicMock) -> None:
    rigol.set_level(mode=LoadMode.CC, value=3.5, channel=1, curr_limit=None)
    visa_mock.write.assert_called_once_with("CURRent:LEVel:IMMediate 3.5")


def test_set_level_cv_writes_curr_limit(rigol: RigolDL3031A, visa_mock: MagicMock) -> None:
    rigol.set_level(mode=LoadMode.CV, value=5.0, channel=1, curr_limit=2.0)
    assert visa_mock.write.call_args_list == [
        call("VOLTage:LEVel:IMMediate 5.0"),
        call("VOLTage:ILIMt 2.0"),
    ]


def test_set_level_curr_limit_ignored_outside_cv(rigol: RigolDL3031A, visa_mock: MagicMock) -> None:
    rigol.set_level(mode=LoadMode.CC, value=1.0, channel=1, curr_limit=2.0)
    visa_mock.write.assert_called_once_with("CURRent:LEVel:IMMediate 1.0")


@pytest.mark.parametrize(
    ("mode", "expected_cmd"),
    [
        (LoadMode.CC, "CURRent:RANGe 6.0"),
        (LoadMode.CV, "VOLTage:RANGe 6.0"),
        (LoadMode.CR, "RESistance:RANGe 6.0"),
    ],
)
def test_set_range_writes_for_cc_cv_cr(
    rigol: RigolDL3031A, visa_mock: MagicMock, mode: LoadMode, expected_cmd: str
) -> None:
    rigol.set_range(mode, value=6.0, channel=1)
    visa_mock.write.assert_called_once_with(expected_cmd)


def test_set_range_rejects_cp(rigol: RigolDL3031A, visa_mock: MagicMock) -> None:
    with pytest.raises(NotImplementedError, match="no :RANGe command in CP mode"):
        rigol.set_range(LoadMode.CP, value=10.0, channel=1)
    visa_mock.write.assert_not_called()


@pytest.mark.parametrize(
    ("direction", "expected_cmd"),
    [
        (SlewRateDirection.RISE, "CURRent:SLEW:POSitive 0.5"),
        (SlewRateDirection.FALL, "CURRent:SLEW:NEGative 0.5"),
        (SlewRateDirection.BOTH, "CURRent:SLEW:BOTH 0.5"),
    ],
)
def test_set_slewrate(
    rigol: RigolDL3031A, visa_mock: MagicMock, direction: SlewRateDirection, expected_cmd: str
) -> None:
    rigol.set_slewrate(direction, rate=0.5, channel=1)
    visa_mock.write.assert_called_once_with(expected_cmd)


def test_output_enable_writes(rigol: RigolDL3031A, visa_mock: MagicMock) -> None:
    rigol.output_enable(True, channel=1)
    visa_mock.write.assert_called_once_with("INPut 1")


def test_short_output_toggles_only_on_state_change(rigol: RigolDL3031A, visa_mock: MagicMock) -> None:
    rigol.short_output(True, channel=1)
    visa_mock.write.assert_called_once_with("SYSTem:KEY 33")

    visa_mock.reset_mock()
    rigol.short_output(True, channel=1)  # already enabled, so don't call
    visa_mock.write.assert_not_called()

    rigol.short_output(False, channel=1)  # toggle off
    visa_mock.write.assert_called_once_with("SYSTem:KEY 33")


def test_get_current_parses_response(rigol: RigolDL3031A, visa_mock: MagicMock) -> None:
    visa_mock.query.side_effect = ["5.5", '0,"No error"']
    assert rigol.get_current(channel=1) == pytest.approx(5.5)
    assert visa_mock.query.call_args_list[0] == call("MEASure:CURRent?")


def test_get_voltage_parses_response(rigol: RigolDL3031A, visa_mock: MagicMock) -> None:
    visa_mock.query.side_effect = ["5.5", '0,"No error"']
    assert rigol.get_voltage(channel=1) == pytest.approx(5.5)
    assert visa_mock.query.call_args_list[0] == call("MEASure:VOLTage?")


def test_driver_method_raises_on_nonzero_error(rigol: RigolDL3031A, visa_mock: MagicMock) -> None:
    visa_mock.query.return_value = '-113,"Undefined header; keyword cannot be found"'
    with pytest.raises(RuntimeError, match="Rigol DL3031A reported error"):
        rigol.set_mode(LoadMode.CC, channel=1)


# Unit tests for Rigol DL3031A-specific extension functions
def test_trigger_writes_immediate(rigol: RigolDL3031A, visa_mock: MagicMock) -> None:
    rigol.trigger()
    visa_mock.write.assert_called_once_with("TRIGger:IMMediate")


@pytest.mark.parametrize("source", ["BUS", "EXTernal", "MANual"])
def test_set_trigger_source(rigol: RigolDL3031A, visa_mock: MagicMock, source: str) -> None:
    rigol.set_trigger_source(source)
    visa_mock.write.assert_called_once_with(f"TRIGger:SOURce {source}")


def test_set_input_state(rigol: RigolDL3031A, visa_mock: MagicMock) -> None:
    rigol.set_input_state(1)
    visa_mock.write.assert_called_once_with("INPut:STATe 1")


@pytest.mark.parametrize("mode", ["FIXed", "LIST", "WAVe", "BATTery", "OCP", "OPP"])
def test_set_function_mode(rigol: RigolDL3031A, visa_mock: MagicMock, mode: str) -> None:
    rigol.set_function_mode(mode)
    visa_mock.write.assert_called_once_with(f"FUNCtion:MODE {mode}")


@pytest.mark.parametrize("state", [0, 1, "ON", "OFF"])
def test_set_transient_trigger(rigol: RigolDL3031A, visa_mock: MagicMock, state) -> None:
    rigol.set_transient_trigger(state)
    visa_mock.write.assert_called_once_with(f"TRANsient:STATe {state}")


def test_set_sense_state(rigol: RigolDL3031A, visa_mock: MagicMock) -> None:
    rigol.set_sense_state("ON")
    visa_mock.write.assert_called_once_with("SENSe ON")


# Following functions all use _write_cmd_with_params helper + tested with partial set of args
@pytest.mark.parametrize(
    ("method_name", "kwargs", "expected_calls"),
    [
        (
            "set_cc_params",
            {"v_on": 1.0, "i_limit": 10.0},
            [call("CURRent:VON 1.0"), call("CURRent:ILIMt 10.0")],
        ),
        (
            "set_cv_params",
            {"v_limit": 150.0},
            [call("VOLTage:VLIMt 150.0")],
        ),
        (
            "set_cr_params",
            {"range": 15000.0},
            [call("RESistance:RANGe 15000.0")],
        ),
        (
            "set_cp_params",
            {"i_limit": 10.0},
            [call("POWer:ILIMt 10.0")],
        ),
        (
            "set_transient_curr_params",
            {"mode": "TOGG", "a_level": 1.0, "b_level": 0.1},
            [
                call("CURRent:TRANsient:MODE TOGG"),
                call("CURRent:TRANsient:ALEVel 1.0"),
                call("CURRent:TRANsient:BLEVel 0.1"),
            ],
        ),
        (
            "set_ocp_params",
            {"i_set": 0.5, "i_max": 1.0},
            [call("OCP:ISET 0.5"), call("OCP:IMAX 1.0")],
        ),
        (
            "set_opp_params",
            {"p_set": 1.0, "p_min": 0.1},
            [call("OPP:PSET 1.0"), call("OPP:PMIN 0.1")],
        ),
        (
            "set_wave_params",
            {"time": "ADD"},
            [call("WAVe:TIMe ADD")],
        ),
        (
            "set_battery_params",
            {"level": 0.1, "v_stop": 3.0, "v_enab_stop": "ON"},
            [
                call("BATTary:LEVel:IMMediate 0.1"),
                call("BATTary:VSTop 3.0"),
                call("BATTary:VENabstop ON"),
            ],
        ),
    ],
)
def test_sparse_param_methods_write_only_provided_fields(
    rigol: RigolDL3031A, visa_mock: MagicMock, method_name: str, kwargs: dict, expected_calls: list
) -> None:
    getattr(rigol, method_name)(**kwargs)
    assert visa_mock.write.call_args_list == expected_calls


def test_sparse_param_method_writes_nothing_when_all_none(rigol: RigolDL3031A, visa_mock: MagicMock) -> None:
    rigol.set_cc_params()
    visa_mock.write.assert_not_called()


def test_set_list_params_uses_loadmode_value(rigol: RigolDL3031A, visa_mock: MagicMock) -> None:
    rigol.set_list_params(mode=LoadMode.CC, range=6.0, count=2, step=3, end_state="LAST")
    assert visa_mock.write.call_args_list == [
        call("LIST:MODE CC"),
        call("LIST:RANGe 6.0"),
        call("LIST:COUNt 2"),
        call("LIST:STEP 3"),
        call("LIST:END LAST"),
    ]


def test_set_list_params_mode_none_omits_mode_write(rigol: RigolDL3031A, visa_mock: MagicMock) -> None:
    rigol.set_list_params(count=2)
    visa_mock.write.assert_called_once_with("LIST:COUNt 2")


def test_set_list_step_params_writes_indexed_form(rigol: RigolDL3031A, visa_mock: MagicMock) -> None:
    rigol.set_list_step_params(step_num=2, level=1.8, width=3.5, slew=0.2)
    assert visa_mock.write.call_args_list == [
        call("LIST:LEVel 2,1.8"),
        call("LIST:WIDth 2,3.5"),
        call("LIST:SLEW 2,0.2"),
    ]


def test_set_list_step_params_partial_fields(rigol: RigolDL3031A, visa_mock: MagicMock) -> None:
    rigol.set_list_step_params(step_num=0, level=0.5)
    visa_mock.write.assert_called_once_with("LIST:LEVel 0,0.5")
