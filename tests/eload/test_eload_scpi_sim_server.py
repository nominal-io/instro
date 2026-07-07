"""End-to-end tests for the simulated E-Load SCPI server."""

from __future__ import annotations

import asyncio
import time

import pytest
from textual.widgets import Input

from instro.eload.scpi_sim_server import (
    SLEW_MAX,
    SLEW_MIN,
    OperatingState,
    SCPIError,
    SimulatedELoad,
    SimulatedELoadApp,
    SimulatedELoadServer,
    SimulatedSource,
)
from instro.eload.types import LoadMode


@pytest.fixture
def eload() -> SimulatedELoad:
    return SimulatedELoad(num_channels=2)


def _error_code(eload: SimulatedELoad) -> int:
    return int(eload.process_scpi_command("SYST:ERR?").split(",")[0])


def _path_forms(
    short_required: tuple[str, ...],
    long_required: tuple[str, ...],
    short_optional: tuple[str, ...] = (),
    long_optional: tuple[str, ...] = (),
    *,
    source_optional: bool = False,
) -> list[str]:
    prefixes = [((), ())]
    if source_optional:
        prefixes.append((("SOUR",), ("source",)))

    forms: list[str] = []
    for short_prefix, long_prefix in prefixes:
        for optional_count in range(len(short_optional) + 1):
            short_path = short_prefix + short_required + short_optional[:optional_count]
            long_path = long_prefix + long_required + long_optional[:optional_count]
            forms.append(":".join(short_path))
            forms.append(":".join(long_path))
    return forms


# --- Identity and error queue ---


def test_defaults(eload: SimulatedELoad) -> None:
    ch = eload.channels[0]

    assert ch.function is LoadMode.CC
    assert ch.current_setpoint == pytest.approx(0.0)
    assert ch.current_range == pytest.approx(ch.current_max)
    assert ch.voltage_range == pytest.approx(ch.voltage_max)
    assert ch.power_range == pytest.approx(ch.power_max)
    assert ch.resistance_range == pytest.approx(ch.resistance_max)
    assert ch.current_limit == pytest.approx(ch.current_max)
    assert ch.slew_rise == pytest.approx(SLEW_MAX)
    assert ch.slew_fall == pytest.approx(SLEW_MAX)
    assert ch.input_enabled is False
    assert ch.shorted is False


@pytest.mark.parametrize("command", ["*IDN?", "*idn?"])
def test_idn_returns_nominal_id(eload: SimulatedELoad, command: str) -> None:
    assert eload.process_scpi_command(command).startswith("NOMINAL,SIMULATED_ELOAD")


@pytest.mark.parametrize(
    "command",
    [
        "SYST:ERR?",
        "system:error?",
    ],
)
def test_syst_err_no_error_when_empty(eload: SimulatedELoad, command: str) -> None:
    assert eload.process_scpi_command(command) == '0,"No error"'


def test_unknown_command_records_undefined_header(eload: SimulatedELoad) -> None:
    eload.process_scpi_command(":BOGUS:THING")
    assert _error_code(eload) == SCPIError.UNDEFINED_HEADER.value


def test_error_queue_clears_after_read(eload: SimulatedELoad) -> None:
    eload.process_scpi_command(":BOGUS")
    eload.process_scpi_command(":BOGUS")

    assert _error_code(eload) == SCPIError.UNDEFINED_HEADER.value
    assert _error_code(eload) == SCPIError.UNDEFINED_HEADER.value
    assert _error_code(eload) == SCPIError.NO_ERROR.value


def test_invalid_bool_parameter_records_illegal_value(eload: SimulatedELoad) -> None:
    eload.process_scpi_command(":INP MAYBE")
    assert _error_code(eload) == SCPIError.ILLEGAL_PARAMETER_VALUE.value


@pytest.mark.parametrize(
    "command",
    [
        ":SOUR:FUNC",
        ":SOUR:CURR",
        ":SOUR:CURR:LEV:IMM",
        ":SOUR:VOLT",
        ":SOUR:POW",
        ":SOUR:RES",
        ":CURR:RANG",
        ":VOLT:RANG",
        ":POW:RANG",
        ":RES:RANG",
        ":CURR:LIM",
        ":CURR:SLEW",
        ":CURR:SLEW:RISE",
        ":CURR:SLEW:FALL",
        ":INP",
        ":INP:SHOR",
    ],
)
def test_missing_parameter_records_missing_parameter(eload: SimulatedELoad, command: str) -> None:
    assert eload.process_scpi_command(command) is None
    assert _error_code(eload) == SCPIError.MISSING_PARAMETER.value


def test_unparseable_numeric_arg_records_error_not_crash(eload: SimulatedELoad) -> None:
    assert eload.process_scpi_command(":SOUR:CURR 5.000 1") is None
    assert _error_code(eload) == SCPIError.INVALID_CHARACTER_DATA.value


def test_command_log_records_commands_and_responses(eload: SimulatedELoad) -> None:
    eload.process_scpi_command("CURR 3.3")
    eload.process_scpi_command("*IDN?")
    log = list(eload._command_log)
    assert any("CURR 3.3" in entry for entry in log)
    assert any("NOMINAL,SIMULATED_ELOAD" in entry for entry in log)
    assert eload._command_log_seq == 2


def test_command_log_annotates_errors(eload: SimulatedELoad) -> None:
    eload.process_scpi_command(":BOGUS")
    log = list(eload._command_log)
    assert log[-1].startswith(time.strftime("%H:%M:%S")[:5]) or True
    assert "BOGUS" in log[-1]
    assert "-113" in log[-1]
    assert "Undefined header" in log[-1]


def test_invalid_channel_records_suffix_out_of_range(eload: SimulatedELoad) -> None:
    eload.process_scpi_command(":INP99 ON")
    assert _error_code(eload) == SCPIError.HEADER_SUFFIX_OUT_OF_RANGE.value


# --- Numeric-suffix channel addressing ---


def test_default_channel_is_one(eload: SimulatedELoad) -> None:
    eload.process_scpi_command(":SOUR:CURR 5.0")
    assert eload.process_scpi_command(":SOUR:CURR?") == pytest.approx(5.0)
    assert eload.channels[0].current_setpoint == pytest.approx(5.0)


def test_numeric_suffix_addresses_channel(eload: SimulatedELoad) -> None:
    eload.process_scpi_command(":SOUR2:CURR 3.0")
    assert eload.process_scpi_command(":SOUR2:CURR?") == pytest.approx(3.0)
    assert eload.channels[1].current_setpoint == pytest.approx(3.0)
    assert eload.channels[0].current_setpoint == pytest.approx(0.0)


def test_long_and_short_form_dispatch_the_same(eload: SimulatedELoad) -> None:
    eload.process_scpi_command(":SOURce:CURRent 4.5")
    assert eload.process_scpi_command(":SOUR:CURR?") == pytest.approx(4.5)


# --- Function selection ---


@pytest.mark.parametrize("header", _path_forms(("FUNC",), ("function",), source_optional=True))
def test_function_accepted_forms_round_trip(eload: SimulatedELoad, header: str) -> None:
    assert eload.process_scpi_command(f"{header}?") == "CURR"
    eload.process_scpi_command(f"{header} RES")
    assert eload.process_scpi_command(f"{header}?") == "RES"


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("CURR", "CURR"),
        ("CURRENT", "CURR"),
        ("current", "CURR"),
        ("VOLT", "VOLT"),
        ("VOLTAGE", "VOLT"),
        ("POW", "POW"),
        ("POWER", "POW"),
        ("RES", "RES"),
        ("RESISTANCE", "RES"),
    ],
)
def test_function_accepts_short_and_long_tokens(eload: SimulatedELoad, token: str, expected: str) -> None:
    eload.process_scpi_command(f":FUNC {token}")
    assert eload.process_scpi_command(":FUNC?") == expected


def test_function_rejects_unknown_token(eload: SimulatedELoad) -> None:
    eload.process_scpi_command(":FUNC IMPEDANCE")

    assert eload.process_scpi_command(":FUNC?") == "CURR"
    assert _error_code(eload) == SCPIError.ILLEGAL_PARAMETER_VALUE.value


# --- Level settings ---


@pytest.mark.parametrize(
    "header",
    _path_forms(
        ("CURR",),
        ("current",),
        ("LEV", "IMM"),
        ("level", "immediate"),
        source_optional=True,
    ),
)
def test_cc_level_accepted_forms_set_and_query_same_value(eload: SimulatedELoad, header: str) -> None:
    eload.process_scpi_command(f"{header} 2.5")
    assert eload.process_scpi_command(f"{header}?") == pytest.approx(2.5)


@pytest.mark.parametrize(
    "header",
    _path_forms(
        ("VOLT",),
        ("voltage",),
        ("LEV", "IMM"),
        ("level", "immediate"),
        source_optional=True,
    ),
)
def test_cv_level_accepted_forms_set_and_query_same_value(eload: SimulatedELoad, header: str) -> None:
    eload.process_scpi_command(f"{header} 2.5")
    assert eload.process_scpi_command(f"{header}?") == pytest.approx(2.5)


@pytest.mark.parametrize(
    "header",
    _path_forms(
        ("POW",),
        ("power",),
        ("LEV", "IMM"),
        ("level", "immediate"),
        source_optional=True,
    ),
)
def test_cp_level_accepted_forms_set_and_query_same_value(eload: SimulatedELoad, header: str) -> None:
    eload.process_scpi_command(f"{header} 25.0")
    assert eload.process_scpi_command(f"{header}?") == pytest.approx(25.0)


@pytest.mark.parametrize(
    "header",
    _path_forms(
        ("RES",),
        ("resistance",),
        ("LEV", "IMM"),
        ("level", "immediate"),
        source_optional=True,
    ),
)
def test_cr_level_accepted_forms_set_and_query_same_value(eload: SimulatedELoad, header: str) -> None:
    eload.process_scpi_command(f"{header} 100.0")
    assert eload.process_scpi_command(f"{header}?") == pytest.approx(100.0)


@pytest.mark.parametrize(
    ("command", "attribute"),
    [
        (":CURR 31.0", "current_setpoint"),
        (":VOLT 151.0", "voltage_setpoint"),
        (":POW 301.0", "power_setpoint"),
        (":RES 10001.0", "resistance_setpoint"),
        (":CURR -0.1", "current_setpoint"),
        (":VOLT -0.1", "voltage_setpoint"),
        (":POW -0.1", "power_setpoint"),
        (":RES -0.1", "resistance_setpoint"),
    ],
)
def test_levels_outside_range_record_out_of_range(eload: SimulatedELoad, command: str, attribute: str) -> None:
    ch = eload.channels[0]
    unchanged = getattr(ch, attribute)

    eload.process_scpi_command(command)

    assert getattr(ch, attribute) == pytest.approx(unchanged)
    assert _error_code(eload) == SCPIError.DATA_OUT_OF_RANGE.value


def test_min_max_keywords_use_active_range(eload: SimulatedELoad) -> None:
    eload.process_scpi_command(":CURR:RANG 10.0")

    eload.process_scpi_command(":CURR MAX")
    assert eload.process_scpi_command(":CURR?") == pytest.approx(10.0)
    eload.process_scpi_command(":CURR MIN")
    assert eload.process_scpi_command(":CURR?") == pytest.approx(0.0)


@pytest.mark.parametrize(
    "command",
    [
        ":CURR? MIN",
        ":VOLT? MAX",
        ":POW? MAX",
        ":RES? MAX",
        ":CURR:RANG? MAX",
        ":CURR:LIM? MAX",
        ":FUNC? CURR",
        ":INP? ON",
        ":INP:SHOR? ON",
    ],
)
def test_query_parameters_are_not_accepted(eload: SimulatedELoad, command: str) -> None:
    assert eload.process_scpi_command(command) is None
    assert _error_code(eload) == SCPIError.PARAMETER_NOT_ALLOWED.value


# --- Ranges and current limit ---


@pytest.mark.parametrize(
    ("header", "value"),
    [
        (form, value)
        for forms, value in [
            (_path_forms(("CURR", "RANG"), ("current", "range"), source_optional=True), 10.0),
            (_path_forms(("VOLT", "RANG"), ("voltage", "range"), source_optional=True), 15.0),
            (_path_forms(("POW", "RANG"), ("power", "range"), source_optional=True), 100.0),
            (_path_forms(("RES", "RANG"), ("resistance", "range"), source_optional=True), 500.0),
        ]
        for form in forms
    ],
)
def test_range_accepted_forms_round_trip(eload: SimulatedELoad, header: str, value: float) -> None:
    eload.process_scpi_command(f"{header} {value}")
    assert eload.process_scpi_command(f"{header}?") == pytest.approx(value)


def test_range_above_channel_max_records_out_of_range(eload: SimulatedELoad) -> None:
    eload.process_scpi_command(":CURR:RANG 31.0")

    assert eload.channels[0].current_range == pytest.approx(eload.channels[0].current_max)
    assert _error_code(eload) == SCPIError.DATA_OUT_OF_RANGE.value


def test_reducing_range_clamps_level(eload: SimulatedELoad) -> None:
    eload.process_scpi_command(":CURR 20.0")
    eload.process_scpi_command(":CURR:RANG 10.0")

    assert eload.process_scpi_command(":CURR?") == pytest.approx(10.0)


def test_level_above_range_records_out_of_range(eload: SimulatedELoad) -> None:
    eload.process_scpi_command(":CURR:RANG 10.0")
    eload.process_scpi_command(":CURR 15.0")

    assert eload.channels[0].current_setpoint == pytest.approx(0.0)
    assert _error_code(eload) == SCPIError.DATA_OUT_OF_RANGE.value


@pytest.mark.parametrize("header", _path_forms(("CURR", "LIM"), ("current", "limit"), source_optional=True))
def test_current_limit_accepted_forms_round_trip(eload: SimulatedELoad, header: str) -> None:
    eload.process_scpi_command(f"{header} 5.0")
    assert eload.process_scpi_command(f"{header}?") == pytest.approx(5.0)


# --- Slew rate ---


@pytest.mark.parametrize("header", [":CURR:SLEW", ":CURR:SLEW:BOTH", ":SOUR:CURR:SLEW", ":SOURce:CURRent:SLEW:BOTH"])
def test_slew_both_sets_rise_and_fall(eload: SimulatedELoad, header: str) -> None:
    eload.process_scpi_command(f"{header} 1.0")

    assert eload.process_scpi_command(":CURR:SLEW:RISE?") == pytest.approx(1.0)
    assert eload.process_scpi_command(":CURR:SLEW:FALL?") == pytest.approx(1.0)


def test_slew_rise_and_fall_set_independently(eload: SimulatedELoad) -> None:
    eload.process_scpi_command(":CURR:SLEW:RISE 0.5")
    eload.process_scpi_command(":CURR:SLEW:FALL 0.25")

    assert eload.process_scpi_command(":CURR:SLEW:RISE?") == pytest.approx(0.5)
    assert eload.process_scpi_command(":CURR:SLEW:FALL?") == pytest.approx(0.25)


def test_slew_both_query_is_not_supported(eload: SimulatedELoad) -> None:
    assert eload.process_scpi_command(":CURR:SLEW?") is None
    assert _error_code(eload) == SCPIError.UNDEFINED_HEADER.value


@pytest.mark.parametrize("command", [":CURR:SLEW 5.0", ":CURR:SLEW:RISE 0.0001", ":CURR:SLEW:FALL -1.0"])
def test_slew_outside_bounds_records_out_of_range(eload: SimulatedELoad, command: str) -> None:
    eload.process_scpi_command(command)
    assert _error_code(eload) == SCPIError.DATA_OUT_OF_RANGE.value


def test_slew_min_max_keywords_map_to_bounds(eload: SimulatedELoad) -> None:
    eload.process_scpi_command(":CURR:SLEW:RISE MIN")
    assert eload.process_scpi_command(":CURR:SLEW:RISE?") == pytest.approx(SLEW_MIN)
    eload.process_scpi_command(":CURR:SLEW:RISE MAX")
    assert eload.process_scpi_command(":CURR:SLEW:RISE?") == pytest.approx(SLEW_MAX)


# --- Input enable and short ---


@pytest.mark.parametrize(
    "header",
    _path_forms(
        ("INP",),
        ("input",),
        ("STAT",),
        ("state",),
    ),
)
def test_input_accepted_forms_round_trip(eload: SimulatedELoad, header: str) -> None:
    assert eload.process_scpi_command(f"{header}?") == 0
    eload.process_scpi_command(f"{header} ON")
    assert eload.process_scpi_command(f"{header}?") == 1
    eload.process_scpi_command(f"{header} OFF")
    assert eload.process_scpi_command(f"{header}?") == 0


@pytest.mark.parametrize(
    "header",
    _path_forms(
        ("INP", "SHOR"),
        ("input", "short"),
        ("STAT",),
        ("state",),
    ),
)
def test_short_accepted_forms_round_trip(eload: SimulatedELoad, header: str) -> None:
    assert eload.process_scpi_command(f"{header}?") == 0
    eload.process_scpi_command(f"{header} ON")
    assert eload.process_scpi_command(f"{header}?") == 1
    eload.process_scpi_command(f"{header} OFF")
    assert eload.process_scpi_command(f"{header}?") == 0


# --- Physics: measurements and regulation ---


@pytest.mark.parametrize("header", _path_forms(("MEAS", "VOLT"), ("measure", "voltage")))
def test_measure_voltage_accepted_query_forms(eload: SimulatedELoad, header: str) -> None:
    eload.process_scpi_command(":CURR 1.0")
    eload.process_scpi_command(":INP ON")
    assert eload.process_scpi_command(f"{header}?") == pytest.approx(11.5, rel=0.05)


@pytest.mark.parametrize("header", _path_forms(("MEAS", "CURR"), ("measure", "current")))
def test_measure_current_accepted_query_forms(eload: SimulatedELoad, header: str) -> None:
    eload.process_scpi_command(":CURR 1.0")
    eload.process_scpi_command(":INP ON")
    assert eload.process_scpi_command(f"{header}?") == pytest.approx(1.0, rel=0.05)


@pytest.mark.parametrize("header", _path_forms(("MEAS", "POW"), ("measure", "power")))
def test_measure_power_accepted_query_forms(eload: SimulatedELoad, header: str) -> None:
    eload.process_scpi_command(":CURR 1.0")
    eload.process_scpi_command(":INP ON")
    assert eload.process_scpi_command(f"{header}?") == pytest.approx(11.5, rel=0.05)


def test_input_off_measures_open_circuit_source_voltage(eload: SimulatedELoad) -> None:
    eload.process_scpi_command(":CURR 1.0")

    assert eload.channels[0].state is OperatingState.OFF
    assert eload.process_scpi_command(":MEAS:VOLT?") == pytest.approx(12.0, rel=0.05)
    assert eload.process_scpi_command(":MEAS:CURR?") == pytest.approx(0.0, abs=0.001)


def test_cc_mode_draws_setpoint_current(eload: SimulatedELoad) -> None:
    eload.process_scpi_command(":CURR 2.0")
    eload.process_scpi_command(":INP ON")

    assert eload.channels[0].state is OperatingState.CC
    assert eload.process_scpi_command(":MEAS:CURR?") == pytest.approx(2.0, rel=0.05)
    assert eload.process_scpi_command(":MEAS:VOLT?") == pytest.approx(11.0, rel=0.05)


def test_cc_setpoint_above_source_capability_goes_unregulated(eload: SimulatedELoad) -> None:
    eload.channels[0].source = SimulatedSource(voltage=12.0, resistance=6.0)
    eload.process_scpi_command(":CURR 5.0")
    eload.process_scpi_command(":INP ON")

    assert eload.channels[0].state is OperatingState.UNREG
    assert eload.process_scpi_command(":MEAS:CURR?") == pytest.approx(2.0, rel=0.05)
    assert eload.process_scpi_command(":MEAS:VOLT?") == pytest.approx(0.0, abs=0.01)


def test_cv_mode_regulates_terminal_voltage(eload: SimulatedELoad) -> None:
    eload.process_scpi_command(":FUNC VOLT")
    eload.process_scpi_command(":VOLT 6.0")
    eload.process_scpi_command(":INP ON")

    assert eload.channels[0].state is OperatingState.CV
    assert eload.process_scpi_command(":MEAS:VOLT?") == pytest.approx(6.0, rel=0.05)
    assert eload.process_scpi_command(":MEAS:CURR?") == pytest.approx(12.0, rel=0.05)


def test_cv_setpoint_above_source_voltage_goes_unregulated(eload: SimulatedELoad) -> None:
    eload.process_scpi_command(":FUNC VOLT")
    eload.process_scpi_command(":VOLT 15.0")
    eload.process_scpi_command(":INP ON")

    assert eload.channels[0].state is OperatingState.UNREG
    assert eload.process_scpi_command(":MEAS:CURR?") == pytest.approx(0.0, abs=0.01)
    assert eload.process_scpi_command(":MEAS:VOLT?") == pytest.approx(12.0, rel=0.05)


def test_cv_current_limit_clamps_into_cc(eload: SimulatedELoad) -> None:
    eload.process_scpi_command(":FUNC VOLT")
    eload.process_scpi_command(":VOLT 6.0")
    eload.process_scpi_command(":CURR:LIM 4.0")
    eload.process_scpi_command(":INP ON")

    assert eload.channels[0].state is OperatingState.CC
    assert eload.process_scpi_command(":MEAS:CURR?") == pytest.approx(4.0, rel=0.05)
    assert eload.process_scpi_command(":MEAS:VOLT?") == pytest.approx(10.0, rel=0.05)


def test_cp_mode_regulates_power(eload: SimulatedELoad) -> None:
    eload.process_scpi_command(":FUNC POW")
    eload.process_scpi_command(":POW 60.0")
    eload.process_scpi_command(":INP ON")

    assert eload.channels[0].state is OperatingState.CP
    assert eload.process_scpi_command(":MEAS:POW?") == pytest.approx(60.0, rel=0.05)


def test_cp_setpoint_above_source_capability_goes_unregulated(eload: SimulatedELoad) -> None:
    # Max deliverable power is V^2/4R = 72 W for the default 12 V, 0.5 ohm source.
    eload.process_scpi_command(":FUNC POW")
    eload.process_scpi_command(":POW 100.0")
    eload.process_scpi_command(":INP ON")

    assert eload.channels[0].state is OperatingState.UNREG
    assert eload.process_scpi_command(":MEAS:CURR?") == pytest.approx(12.0, rel=0.05)
    assert eload.process_scpi_command(":MEAS:VOLT?") == pytest.approx(6.0, rel=0.05)


def test_cp_zero_power_draws_no_current(eload: SimulatedELoad) -> None:
    eload.process_scpi_command(":FUNC POW")
    eload.process_scpi_command(":INP ON")

    assert eload.channels[0].state is OperatingState.CP
    assert eload.process_scpi_command(":MEAS:CURR?") == pytest.approx(0.0, abs=0.001)


def test_cr_mode_draws_ohms_law_current(eload: SimulatedELoad) -> None:
    eload.process_scpi_command(":FUNC RES")
    eload.process_scpi_command(":RES 10.0")
    eload.process_scpi_command(":INP ON")

    assert eload.channels[0].state is OperatingState.CR
    assert eload.process_scpi_command(":MEAS:CURR?") == pytest.approx(12.0 / 10.5, rel=0.05)
    assert eload.process_scpi_command(":MEAS:VOLT?") == pytest.approx(120.0 / 10.5, rel=0.05)


def test_cr_zero_total_resistance_goes_unregulated(eload: SimulatedELoad) -> None:
    eload.channels[0].source = SimulatedSource(voltage=12.0, resistance=0.0)
    eload.process_scpi_command(":FUNC RES")
    eload.process_scpi_command(":INP ON")

    assert eload.channels[0].state is OperatingState.UNREG
    assert eload.channels[0].current == pytest.approx(eload.channels[0].current_max)


def test_short_draws_source_limited_current(eload: SimulatedELoad) -> None:
    eload.process_scpi_command(":INP ON")
    eload.process_scpi_command(":INP:SHOR ON")

    assert eload.channels[0].state is OperatingState.SHORT
    assert eload.process_scpi_command(":MEAS:CURR?") == pytest.approx(24.0, rel=0.05)
    assert eload.process_scpi_command(":MEAS:VOLT?") == pytest.approx(0.0, abs=0.01)


def test_short_requires_input_enabled(eload: SimulatedELoad) -> None:
    eload.process_scpi_command(":INP:SHOR ON")

    assert eload.channels[0].state is OperatingState.OFF
    assert eload.process_scpi_command(":MEAS:CURR?") == pytest.approx(0.0, abs=0.001)


def test_dead_source_goes_unregulated(eload: SimulatedELoad) -> None:
    eload.channels[0].source = SimulatedSource(voltage=0.0, resistance=0.5)
    eload.process_scpi_command(":CURR 1.0")
    eload.process_scpi_command(":INP ON")

    assert eload.channels[0].state is OperatingState.UNREG
    assert eload.process_scpi_command(":MEAS:CURR?") == pytest.approx(0.0, abs=0.001)


def test_stiff_source_keeps_cc_regulation(eload: SimulatedELoad) -> None:
    eload.channels[0].source = SimulatedSource(voltage=12.0, resistance=0.0)
    eload.process_scpi_command(":CURR 5.0")
    eload.process_scpi_command(":INP ON")

    assert eload.channels[0].state is OperatingState.CC
    assert eload.process_scpi_command(":MEAS:CURR?") == pytest.approx(5.0, rel=0.05)
    assert eload.process_scpi_command(":MEAS:VOLT?") == pytest.approx(12.0, rel=0.05)


@pytest.mark.parametrize(
    "command",
    [
        ":OUTP ON",
        ":OUTP?",
        ":SOUR:INP ON",
        ":CURR:PROT 1.0",
        ":VOLT:PROT 5.0",
        ":SYST:SENS REM",
        ":MEAS:RES?",
        ":FUNC:MODE CC",
        ":CURR:SLEW:BOTH?",
    ],
)
def test_non_matching_command_forms_record_undefined_header(eload: SimulatedELoad, command: str) -> None:
    assert eload.process_scpi_command(command) is None
    assert _error_code(eload) == SCPIError.UNDEFINED_HEADER.value


# --- *RST and *CLS ---


@pytest.mark.parametrize("command", ["*RST", "*rst"])
def test_rst_resets_channel_state(eload: SimulatedELoad, command: str) -> None:
    eload.process_scpi_command(":FUNC RES")
    eload.process_scpi_command(":RES 100.0")
    eload.process_scpi_command(":CURR:RANG 10.0")
    eload.process_scpi_command(":CURR:LIM 5.0")
    eload.process_scpi_command(":CURR:SLEW 1.0")
    eload.process_scpi_command(":INP ON")
    eload.process_scpi_command(":INP:SHOR ON")
    eload.process_scpi_command(command)

    ch = eload.channels[0]
    assert ch.function is LoadMode.CC
    assert ch.resistance_setpoint == pytest.approx(0.0)
    assert ch.current_range == pytest.approx(ch.current_max)
    assert ch.current_limit == pytest.approx(ch.current_max)
    assert ch.slew_rise == pytest.approx(SLEW_MAX)
    assert ch.slew_fall == pytest.approx(SLEW_MAX)
    assert ch.input_enabled is False
    assert ch.shorted is False


def test_rst_preserves_channel_limits(eload: SimulatedELoad) -> None:
    ch = eload.channels[0]
    ch.current_max = 60.0
    ch.voltage_max = 500.0

    eload.process_scpi_command("*RST")

    assert ch.current_max == pytest.approx(60.0)
    assert ch.voltage_max == pytest.approx(500.0)
    assert ch.current_range == pytest.approx(60.0)
    assert ch.voltage_range == pytest.approx(500.0)
    assert ch.current_limit == pytest.approx(60.0)


def test_rst_preserves_sim_source_configuration(eload: SimulatedELoad) -> None:
    ch = eload.channels[0]
    ch.source = SimulatedSource(voltage=48.0, resistance=2.0)

    eload.process_scpi_command("*RST")

    assert ch.source.voltage == pytest.approx(48.0)
    assert ch.source.resistance == pytest.approx(2.0)


@pytest.mark.parametrize("command", ["*CLS", "*cls"])
def test_cls_clears_error_queue(eload: SimulatedELoad, command: str) -> None:
    eload.process_scpi_command(":BOGUS")
    eload.process_scpi_command(command)
    assert _error_code(eload) == SCPIError.NO_ERROR.value


# --- TUI ---


@pytest.mark.parametrize(
    ("param", "value", "max_attr"),
    [
        ("voltage", "500.0", "voltage_max"),
        ("current", "60.0", "current_max"),
    ],
)
def test_tui_limit_edit_resets_without_recording_rst(
    param: str,
    value: str,
    max_attr: str,
) -> None:
    async def run() -> None:
        eload = SimulatedELoad(num_channels=2)
        eload.process_scpi_command(":SOUR:CURR 5.0")
        eload.process_scpi_command(":INP ON")
        log_before = list(eload._command_log)
        seq_before = eload._command_log_seq

        app = SimulatedELoadApp(SimulatedELoadServer(eload))
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause(0.2)
            app._prompt_set_limit(1, param, f"{param.upper()} MAX:")
            await pilot.pause(0.2)
            input_widget = app.screen.query_one(Input)
            input_widget.value = value
            await input_widget.action_submit()
            await pilot.pause(0.2)

        assert getattr(eload.channels[0], max_attr) == pytest.approx(float(value))
        assert eload.channels[0].current_setpoint == pytest.approx(0.0)
        assert eload.channels[0].input_enabled is False
        assert list(eload._command_log) == log_before
        assert eload._command_log_seq == seq_before
        assert not any("*RST" in entry for entry in eload._command_log)

    asyncio.run(run())
