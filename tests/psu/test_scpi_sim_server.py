"""End-to-end tests for the simulated PSU SCPI server."""

from __future__ import annotations

import asyncio
import math
import time

import pytest
from textual.widgets import Input

from instro.psu.scpi_sim_server import (
    OperatingMode,
    SCPIError,
    SimulatedLoad,
    SimulatedPSU,
    SimulatedPSUApp,
    SimulatedPSUServer,
)


@pytest.fixture
def psu() -> SimulatedPSU:
    return SimulatedPSU()


def _error_code(psu: SimulatedPSU) -> int:
    return int(psu.process_scpi_command("SYST:ERR?").split(",")[0])


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


def _trip_ocp(psu: SimulatedPSU) -> None:
    psu.channels[0].load = SimulatedLoad(resistance=1.0, emf=10.0, probe_resistance=0.0)
    psu.process_scpi_command("CURR 2.0")
    psu.process_scpi_command("CURR:PROT 2.0")
    psu.process_scpi_command("CURR:PROT:STAT ON")
    psu.process_scpi_command("OUTP ON")


def _trip_ovp(psu: SimulatedPSU) -> None:
    psu.process_scpi_command("VOLT 5.0")
    psu.process_scpi_command("CURR 1.0")
    psu.process_scpi_command("VOLT:PROT:STAT ON")
    psu.process_scpi_command("OUTP ON")
    psu.channels[0].overvoltage_protection_level = 4.0
    psu.process_scpi_command("MEAS:VOLT?")


# --- Identity and error queue ---


def test_default_protection_levels_equal_max(psu: SimulatedPSU) -> None:
    ch = psu.channels[0]

    assert ch.current_limit == pytest.approx(0.0)
    assert ch.overvoltage_protection_level == pytest.approx(ch.voltage_max)
    assert ch.overcurrent_protection_level == pytest.approx(ch.current_max)


@pytest.mark.parametrize("command", ["*IDN?", "*idn?"])
def test_idn_returns_nominal_id(psu: SimulatedPSU, command: str) -> None:
    assert psu.process_scpi_command(command).startswith("NOMINAL,SIMULATED_PSU")


@pytest.mark.parametrize(
    "command",
    [
        "SYST:ERR?",
        "system:error?",
    ],
)
def test_syst_err_no_error_when_empty(psu: SimulatedPSU, command: str) -> None:
    assert psu.process_scpi_command(command) == '0,"No error"'


def test_syst_err_next_is_not_supported(psu: SimulatedPSU) -> None:
    assert psu.process_scpi_command("SYST:ERR:NEXT?") is None
    assert _error_code(psu) == SCPIError.UNDEFINED_HEADER.value


def test_unknown_command_records_undefined_header(psu: SimulatedPSU) -> None:
    psu.process_scpi_command(":BOGUS:THING")
    assert _error_code(psu) == SCPIError.UNDEFINED_HEADER.value


def test_error_queue_clears_after_read(psu: SimulatedPSU) -> None:
    psu.process_scpi_command(":BOGUS")
    psu.process_scpi_command(":BOGUS")

    assert _error_code(psu) == SCPIError.UNDEFINED_HEADER.value
    assert _error_code(psu) == SCPIError.UNDEFINED_HEADER.value
    assert _error_code(psu) == SCPIError.NO_ERROR.value


def test_invalid_bool_parameter_records_illegal_value(psu: SimulatedPSU) -> None:
    psu.process_scpi_command(":OUTP MAYBE")
    assert _error_code(psu) == SCPIError.ILLEGAL_PARAMETER_VALUE.value


@pytest.mark.parametrize(
    "command",
    [
        ":SOUR:VOLT",
        ":SOUR:VOLT:LEV:IMM:AMPL",
        ":SOUR:CURR",
        ":SOUR:CURR:LEV:IMM:AMPL",
        ":CURR:PROT",
        ":CURR:PROT:STAT",
        ":VOLT:PROT:STAT",
        ":VOLT:PROT:LEV",
        ":OUTP",
        ":SYST:SENS",
    ],
)
def test_missing_parameter_records_missing_parameter(psu: SimulatedPSU, command: str) -> None:
    assert psu.process_scpi_command(command) is None
    assert _error_code(psu) == SCPIError.MISSING_PARAMETER.value


def test_unparseable_numeric_arg_records_error_not_crash(psu: SimulatedPSU) -> None:
    assert psu.process_scpi_command(":SOUR:VOLT 5.000 1") is None
    assert _error_code(psu) == SCPIError.INVALID_CHARACTER_DATA.value


def test_command_log_records_commands_and_responses(psu: SimulatedPSU) -> None:
    psu.process_scpi_command("VOLT 3.3")
    psu.process_scpi_command("*IDN?")
    log = list(psu._command_log)
    assert any("VOLT 3.3" in entry for entry in log)
    assert any("NOMINAL,SIMULATED_PSU" in entry for entry in log)
    assert psu._command_log_seq == 2


def test_command_log_annotates_errors(psu: SimulatedPSU) -> None:
    psu.process_scpi_command(":BOGUS")
    log = list(psu._command_log)
    assert log[-1].startswith(time.strftime("%H:%M:%S")[:5]) or True
    assert "BOGUS" in log[-1]
    assert "-113" in log[-1]
    assert "Undefined header" in log[-1]


@pytest.mark.parametrize(
    ("param", "value", "max_attr"),
    [
        ("voltage", "70.0", "voltage_max"),
        ("current", "12.0", "current_max"),
    ],
)
def test_tui_limit_edit_resets_without_recording_rst(
    param: str,
    value: str,
    max_attr: str,
) -> None:
    async def run() -> None:
        psu = SimulatedPSU()
        psu.process_scpi_command(":SOUR:VOLT 5.0")
        psu.process_scpi_command(":CURR 2.0")
        psu.process_scpi_command(":OUTP ON")
        log_before = list(psu._command_log)
        seq_before = psu._command_log_seq

        app = SimulatedPSUApp(SimulatedPSUServer(psu))
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause(0.2)
            app._prompt_set_limit(1, param, f"{param.upper()} LIMIT:")
            await pilot.pause(0.2)
            input_widget = app.screen.query_one(Input)
            input_widget.value = value
            await input_widget.action_submit()
            await pilot.pause(0.2)

        assert getattr(psu.channels[0], max_attr) == pytest.approx(float(value))
        assert psu.channels[0].voltage_setpoint == pytest.approx(0.0)
        assert psu.channels[0].current_limit == pytest.approx(0.0)
        assert psu.channels[0].output_enabled is False
        assert list(psu._command_log) == log_before
        assert psu._command_log_seq == seq_before
        assert not any("*RST" in entry for entry in psu._command_log)

    asyncio.run(run())


def test_invalid_channel_records_suffix_out_of_range(psu: SimulatedPSU) -> None:
    psu.process_scpi_command(":OUTP99 ON")
    assert _error_code(psu) == SCPIError.HEADER_SUFFIX_OUT_OF_RANGE.value


# --- Numeric-suffix channel addressing ---


def test_default_channel_is_one(psu: SimulatedPSU) -> None:
    psu.process_scpi_command(":SOUR:VOLT 5.0")
    assert psu.process_scpi_command(":SOUR:VOLT?") == pytest.approx(5.0)
    assert psu.channels[0].voltage_setpoint == pytest.approx(5.0)


def test_numeric_suffix_addresses_channel(psu: SimulatedPSU) -> None:
    psu.process_scpi_command(":SOUR2:VOLT 3.0")
    assert psu.process_scpi_command(":SOUR2:VOLT?") == pytest.approx(3.0)
    assert psu.channels[1].voltage_setpoint == pytest.approx(3.0)
    assert psu.channels[0].voltage_setpoint == pytest.approx(0.0)


def test_long_and_short_form_dispatch_the_same(psu: SimulatedPSU) -> None:
    psu.process_scpi_command(":SOURce:VOLTage 4.5")
    assert psu.process_scpi_command(":SOUR:VOLT?") == pytest.approx(4.5)


# --- Voltage and current settings ---


@pytest.mark.parametrize(
    "header",
    _path_forms(
        ("VOLT",),
        ("voltage",),
        ("LEV", "IMM", "AMPL"),
        ("level", "immediate", "amplitude"),
        source_optional=True,
    ),
)
def test_voltage_accepted_forms_set_and_query_same_value(psu: SimulatedPSU, header: str) -> None:
    psu.process_scpi_command(f"{header} 2.5")
    assert psu.process_scpi_command(f"{header}?") == pytest.approx(2.5)


@pytest.mark.parametrize(
    "header",
    _path_forms(
        ("CURR",),
        ("current",),
        ("LEV", "IMM", "AMPL"),
        ("level", "immediate", "amplitude"),
        source_optional=True,
    ),
)
def test_current_accepted_forms_set_and_query_same_limit(psu: SimulatedPSU, header: str) -> None:
    psu.process_scpi_command(f"{header} 0.5")
    assert psu.process_scpi_command(f"{header}?") == pytest.approx(0.5)


def test_min_max_keywords_use_channel_limits(psu: SimulatedPSU) -> None:
    ch = psu.channels[0]
    ch.voltage_max = 7.0
    ch.current_max = 3.0

    psu.process_scpi_command(":VOLT MIN")
    assert psu.process_scpi_command(":VOLT?") == pytest.approx(0.0)
    psu.process_scpi_command(":VOLT MAX")
    assert psu.process_scpi_command(":VOLT?") == pytest.approx(7.0)
    psu.process_scpi_command(":CURR MIN")
    assert psu.process_scpi_command(":CURR?") == pytest.approx(0.0)
    psu.process_scpi_command(":CURR MAX")
    assert psu.process_scpi_command(":CURR?") == pytest.approx(3.0)
    psu.process_scpi_command(":VOLT MIN")
    psu.process_scpi_command(":VOLT:PROT MIN")
    assert psu.process_scpi_command(":VOLT:PROT?") == pytest.approx(0.0)
    psu.process_scpi_command(":VOLT:PROT MAX")
    assert psu.process_scpi_command(":VOLT:PROT?") == pytest.approx(7.0)
    psu.process_scpi_command(":CURR MIN")
    psu.process_scpi_command(":CURR:PROT MIN")
    assert psu.process_scpi_command(":CURR:PROT?") == pytest.approx(0.0)
    psu.process_scpi_command(":CURR:PROT MAX")
    assert psu.process_scpi_command(":CURR:PROT?") == pytest.approx(3.0)


@pytest.mark.parametrize(
    ("command", "attribute"),
    [
        (":VOLT 5.1", "voltage_setpoint"),
        (":CURR 5.1", "current_limit"),
        (":VOLT:PROT 5.1", "overvoltage_protection_level"),
        (":CURR:PROT 5.1", "overcurrent_protection_level"),
    ],
)
def test_numeric_values_above_channel_limits_record_out_of_range(
    psu: SimulatedPSU,
    command: str,
    attribute: str,
) -> None:
    ch = psu.channels[0]
    ch.voltage_max = 5.0
    ch.current_max = 5.0
    unchanged = getattr(ch, attribute)

    psu.process_scpi_command(command)

    assert getattr(ch, attribute) == pytest.approx(unchanged)
    assert _error_code(psu) == SCPIError.DATA_OUT_OF_RANGE.value


@pytest.mark.parametrize(
    ("command", "attribute"),
    [
        (":VOLT -0.1", "voltage_setpoint"),
        (":CURR -0.1", "current_limit"),
        (":VOLT:PROT -0.1", "overvoltage_protection_level"),
        (":CURR:PROT -0.1", "overcurrent_protection_level"),
    ],
)
def test_numeric_values_below_channel_limits_record_out_of_range(
    psu: SimulatedPSU,
    command: str,
    attribute: str,
) -> None:
    ch = psu.channels[0]
    unchanged = getattr(ch, attribute)

    psu.process_scpi_command(command)

    assert getattr(ch, attribute) == pytest.approx(unchanged)
    assert _error_code(psu) == SCPIError.DATA_OUT_OF_RANGE.value


def test_current_limit_max_keyword_maps_to_channel_limit(psu: SimulatedPSU) -> None:
    psu.process_scpi_command(":CURR MAX")
    assert psu.process_scpi_command(":CURR?") == pytest.approx(psu.channels[0].current_max)


@pytest.mark.parametrize(
    "command",
    [
        ":VOLT? MIN",
        ":VOLT? MAX",
        ":CURR? MIN",
        ":CURR? MAX",
        ":VOLT:PROT? MIN",
        ":VOLT:PROT? MAX",
        ":CURR:PROT? MIN",
        ":CURR:PROT? MAX",
        ":OUTP? ON",
        ":SYST:SENS? REM",
    ],
)
def test_query_parameters_are_not_accepted(psu: SimulatedPSU, command: str) -> None:
    assert psu.process_scpi_command(command) is None
    assert _error_code(psu) == SCPIError.PARAMETER_NOT_ALLOWED.value


# --- Output enable ---


@pytest.mark.parametrize(
    "header",
    _path_forms(
        ("OUTP",),
        ("output",),
        ("STAT",),
        ("state",),
    ),
)
def test_output_accepted_forms_round_trip(psu: SimulatedPSU, header: str) -> None:
    assert psu.process_scpi_command(f"{header}?") == 0
    psu.process_scpi_command(f"{header} ON")
    assert psu.process_scpi_command(f"{header}?") == 1
    psu.process_scpi_command(f"{header} OFF")
    assert psu.process_scpi_command(f"{header}?") == 0


def test_disabled_output_measures_zero(psu: SimulatedPSU) -> None:
    psu.process_scpi_command(":SOUR:VOLT 5.0")
    assert psu.process_scpi_command(":MEAS:VOLT?") == pytest.approx(0.0, abs=0.001)
    assert psu.process_scpi_command(":MEAS:CURR?") == pytest.approx(0.0, abs=0.001)


@pytest.mark.parametrize("header", _path_forms(("MEAS", "VOLT"), ("measure", "voltage")))
def test_measure_voltage_accepted_query_forms(psu: SimulatedPSU, header: str) -> None:
    psu.process_scpi_command(":CURR 1.0")
    psu.process_scpi_command(":VOLT 5.0")
    psu.process_scpi_command(":OUTP ON")
    assert psu.process_scpi_command(f"{header}?") == pytest.approx(5.0, rel=0.05)


@pytest.mark.parametrize("header", _path_forms(("MEAS", "CURR"), ("measure", "current")))
def test_measure_current_accepted_query_forms(psu: SimulatedPSU, header: str) -> None:
    psu.process_scpi_command(":CURR 1.0")
    psu.process_scpi_command(":VOLT 5.0")
    psu.process_scpi_command(":OUTP ON")
    assert psu.process_scpi_command(f"{header}?") == pytest.approx(0.005, rel=0.1)


# --- Overvoltage protection ---


@pytest.mark.parametrize(
    "header",
    _path_forms(
        ("VOLT", "PROT"),
        ("voltage", "protection"),
        ("LEV",),
        ("level",),
        source_optional=True,
    ),
)
def test_ovp_level_accepted_forms_round_trip(psu: SimulatedPSU, header: str) -> None:
    psu.process_scpi_command(f"{header} 12.0")
    assert psu.process_scpi_command(f"{header}?") == pytest.approx(12.0)


def test_ovp_max_keyword_maps_to_channel_limit(psu: SimulatedPSU) -> None:
    psu.process_scpi_command(":VOLT:PROT:LEV MAX")
    assert psu.process_scpi_command(":VOLT:PROT:LEV?") == pytest.approx(psu.channels[0].voltage_max)


@pytest.mark.parametrize(
    "header",
    _path_forms(
        ("VOLT", "PROT", "STAT"),
        ("voltage", "protection", "state"),
        source_optional=True,
    ),
)
def test_ovp_state_accepted_forms_round_trip(psu: SimulatedPSU, header: str) -> None:
    assert psu.process_scpi_command(f"{header}?") == 0
    psu.process_scpi_command(f"{header} ON")
    assert psu.process_scpi_command(f"{header}?") == 1
    psu.process_scpi_command(f"{header} OFF")
    assert psu.process_scpi_command(f"{header}?") == 0


def test_voltage_above_ovp_records_pv_above_ovp_error(psu: SimulatedPSU) -> None:
    psu.process_scpi_command(":VOLT:PROT:LEV 4.0")
    psu.process_scpi_command(":VOLT 5.0")

    assert psu.process_scpi_command("SYST:ERR?") == '301,"PV Above OVP"'
    assert psu.channels[0].voltage_setpoint == pytest.approx(0.0)


def test_ovp_below_voltage_records_ovp_below_pv_error(psu: SimulatedPSU) -> None:
    psu.process_scpi_command(":VOLT 5.0")
    psu.process_scpi_command(":VOLT:PROT:LEV 4.0")

    assert psu.process_scpi_command("SYST:ERR?") == '304,"OVP Below PV"'
    assert psu.channels[0].overvoltage_protection_level == pytest.approx(psu.channels[0].voltage_max)


def test_disabled_ovp_does_not_latch_output(psu: SimulatedPSU) -> None:
    psu.process_scpi_command(":VOLT 5.0")
    psu.process_scpi_command(":OUTP ON")
    psu.channels[0].overvoltage_protection_level = 4.0

    assert psu.process_scpi_command(":OUTP:PROT:TRIP?") == 0
    assert psu.channels[0].output_enabled is True
    assert psu.channels[0].overvoltage_tripped is False


def test_ovp_latches_output_and_queues_error(psu: SimulatedPSU) -> None:
    _trip_ovp(psu)

    assert psu.channels[0].output_enabled is False
    assert psu.channels[0].protection_latched is True
    assert psu.channels[0].overvoltage_tripped is True
    assert psu.process_scpi_command(":OUTP:PROT:TRIP?") == 1
    assert _error_code(psu) == SCPIError.OVERVOLTAGE_PROTECTION_TRIPPED.value


# --- Overcurrent protection ---


@pytest.mark.parametrize(
    "header",
    _path_forms(
        ("CURR", "PROT"),
        ("current", "protection"),
        ("LEV",),
        ("level",),
        source_optional=True,
    ),
)
def test_ocp_level_accepted_forms_round_trip(psu: SimulatedPSU, header: str) -> None:
    psu.process_scpi_command(":CURR 1.0")
    psu.process_scpi_command(f"{header} 2.0")
    assert psu.process_scpi_command(f"{header}?") == pytest.approx(2.0)


def test_ocp_max_keyword_maps_to_channel_limit(psu: SimulatedPSU) -> None:
    psu.process_scpi_command(":CURR:PROT MAX")
    assert psu.process_scpi_command(":CURR:PROT?") == pytest.approx(psu.channels[0].current_max)


@pytest.mark.parametrize(
    "header",
    _path_forms(
        ("CURR", "PROT", "STAT"),
        ("current", "protection", "state"),
        source_optional=True,
    ),
)
def test_ocp_state_accepted_forms_round_trip(psu: SimulatedPSU, header: str) -> None:
    assert psu.process_scpi_command(f"{header}?") == 0
    psu.process_scpi_command(f"{header} ON")
    assert psu.process_scpi_command(f"{header}?") == 1
    psu.process_scpi_command(f"{header} OFF")
    assert psu.process_scpi_command(f"{header}?") == 0


def test_current_above_ocp_records_pc_above_ocp_error(psu: SimulatedPSU) -> None:
    psu.process_scpi_command(":CURR 1.0")
    psu.process_scpi_command(":CURR:PROT 2.0")
    psu.process_scpi_command(":CURR 3.0")

    assert psu.process_scpi_command("SYST:ERR?") == '303,"PC Above OCP"'
    assert psu.channels[0].current_limit == pytest.approx(1.0)


def test_ocp_below_current_records_ocp_below_pc_error(psu: SimulatedPSU) -> None:
    psu.process_scpi_command(":CURR 3.0")
    psu.process_scpi_command(":CURR:PROT 2.0")

    assert psu.process_scpi_command("SYST:ERR?") == '305,"OCP Below PC"'
    assert psu.channels[0].overcurrent_protection_level == pytest.approx(psu.channels[0].current_max)


@pytest.mark.parametrize(
    "header",
    _path_forms(
        ("OUTP", "PROT", "TRIP"),
        ("output", "protection", "tripped"),
    ),
)
def test_output_protection_tripped_query_forms(psu: SimulatedPSU, header: str) -> None:
    assert psu.process_scpi_command(f"{header}?") == 0
    _trip_ocp(psu)
    assert psu.process_scpi_command(f"{header}?") == 1


def test_ocp_latches_output_and_queues_error(psu: SimulatedPSU) -> None:
    _trip_ocp(psu)

    assert psu.channels[0].output_enabled is False
    assert psu.channels[0].protection_latched is True
    assert psu.channels[0].overcurrent_tripped is True
    assert psu.process_scpi_command(":OUTP:PROT:TRIP?") == 1
    assert _error_code(psu) == SCPIError.OVERCURRENT_PROTECTION_TRIPPED.value


def test_ocp_disabled_does_not_trip(psu: SimulatedPSU) -> None:
    psu.channels[0].load = SimulatedLoad(resistance=1.0, emf=10.0, probe_resistance=0.0)
    psu.process_scpi_command(":CURR 2.0")
    psu.process_scpi_command(":CURR:PROT 2.0")
    psu.process_scpi_command(":OUTP ON")

    assert psu.channels[0].output_enabled is True
    assert psu.channels[0].overcurrent_tripped is False


def test_current_limit_can_enter_cc_without_ocp_trip(psu: SimulatedPSU) -> None:
    psu.channels[0].load = SimulatedLoad(resistance=0.1, probe_resistance=0.0)
    psu.process_scpi_command(":CURR 1.0")
    psu.process_scpi_command(":VOLT 5.0")
    psu.process_scpi_command(":CURR:PROT 2.0")
    psu.process_scpi_command(":CURR:PROT:STAT ON")
    psu.process_scpi_command(":OUTP ON")

    assert psu.channels[0].mode == OperatingMode.CC
    assert psu.channels[0].output_enabled is True
    assert psu.channels[0].overcurrent_tripped is False


@pytest.mark.parametrize(
    "header",
    _path_forms(
        ("OUTP", "PROT", "CLE"),
        ("output", "protection", "clear"),
    ),
)
def test_output_protection_clear_accepted_forms_clear_all_latches(psu: SimulatedPSU, header: str) -> None:
    _trip_ocp(psu)
    assert psu.channels[0].protection_latched is True

    psu.process_scpi_command(header)

    assert psu.channels[0].overcurrent_tripped is False
    assert psu.channels[0].protection_latched is False


@pytest.mark.parametrize(
    "header",
    _path_forms(
        ("OUTP", "PROT", "CLE"),
        ("output", "protection", "clear"),
    ),
)
def test_output_protection_clear_accepted_forms_clear_ovp_latch(psu: SimulatedPSU, header: str) -> None:
    _trip_ovp(psu)
    assert psu.channels[0].protection_latched is True

    psu.process_scpi_command(header)

    assert psu.channels[0].overvoltage_tripped is False
    assert psu.channels[0].protection_latched is False


def test_output_on_clears_latch_after_fault_removed(psu: SimulatedPSU) -> None:
    _trip_ocp(psu)
    assert psu.channels[0].protection_latched is True

    psu.channels[0].load = SimulatedLoad(resistance=1000.0)
    psu.process_scpi_command(":OUTP ON")

    assert psu.channels[0].protection_latched is False
    assert psu.channels[0].output_enabled is True


def test_clear_protection_latch_allows_re_enable(psu: SimulatedPSU) -> None:
    _trip_ocp(psu)
    assert psu.channels[0].protection_latched is True

    psu.channels[0].load = SimulatedLoad(resistance=1000.0)
    psu.process_scpi_command(":OUTP:PROT:CLE")
    psu.process_scpi_command(":OUTP ON")

    assert psu.channels[0].protection_latched is False
    assert psu.channels[0].output_enabled is True


# --- Remote sense ---


@pytest.mark.parametrize(
    "header",
    _path_forms(
        ("SYST", "SENS"),
        ("system", "sense"),
        ("STAT",),
        ("state",),
    ),
)
def test_remote_sense_accepted_forms_round_trip(psu: SimulatedPSU, header: str) -> None:
    assert psu.process_scpi_command(f"{header}?") == "LOC"
    psu.process_scpi_command(f"{header} REM")
    assert psu.process_scpi_command(f"{header}?") == "REM"
    psu.process_scpi_command(f"{header} LOC")
    assert psu.process_scpi_command(f"{header}?") == "LOC"


@pytest.mark.parametrize("state", ["0", "1", "OFF", "ON", "LOCAL", "REMOTE"])
def test_remote_sense_rejects_ambiguous_states(psu: SimulatedPSU, state: str) -> None:
    psu.process_scpi_command("SYST:SENS REM")

    psu.process_scpi_command(f"SYST:SENS {state}")

    assert psu.process_scpi_command("SYST:SENS?") == "REM"
    assert _error_code(psu) == SCPIError.ILLEGAL_PARAMETER_VALUE.value


def test_remote_sense_eliminates_probe_drop(psu: SimulatedPSU) -> None:
    psu.channels[0].load = SimulatedLoad(resistance=100.0, probe_resistance=10.0)
    psu.process_scpi_command(":CURR 1.0")
    psu.process_scpi_command(":VOLT 5.0")
    psu.process_scpi_command(":OUTP ON")

    i_local = psu.process_scpi_command(":MEAS:CURR?")
    assert i_local == pytest.approx(5.0 / 110.0, rel=0.1)

    psu.process_scpi_command(":SYST:SENS REM")
    i_remote = psu.process_scpi_command(":MEAS:CURR?")
    assert i_remote == pytest.approx(5.0 / 100.0, rel=0.1)
    assert i_remote > i_local


# --- CV/CC and EMF-driven current ---


def test_cv_mode_measures_setpoint_voltage(psu: SimulatedPSU) -> None:
    psu.process_scpi_command(":CURR 1.0")
    psu.process_scpi_command(":VOLT 5.0")
    psu.process_scpi_command(":OUTP ON")

    assert psu.channels[0].mode == OperatingMode.CV
    assert psu.process_scpi_command(":MEAS:VOLT?") == pytest.approx(5.0, rel=0.05)


def test_current_limit_clamps_into_cc_mode(psu: SimulatedPSU) -> None:
    psu.channels[0].load = SimulatedLoad(resistance=0.1, probe_resistance=0.0)
    psu.process_scpi_command(":CURR 1.0")
    psu.process_scpi_command(":VOLT 5.0")
    psu.process_scpi_command(":OUTP ON")

    assert psu.channels[0].mode == OperatingMode.CC
    assert psu.process_scpi_command(":MEAS:CURR?") == pytest.approx(1.0, rel=0.05)


def test_emf_load_draws_charging_current(psu: SimulatedPSU) -> None:
    psu.channels[0].load = SimulatedLoad(resistance=1.0, emf=3.0, probe_resistance=0.0)
    psu.process_scpi_command(":CURR 10.0")
    psu.process_scpi_command(":VOLT 5.0")
    psu.process_scpi_command(":OUTP ON")

    assert psu.process_scpi_command(":MEAS:CURR?") == pytest.approx(2.0, rel=0.1)


def test_zero_resistance_load_enters_cc_mode(psu: SimulatedPSU) -> None:
    psu.channels[0].load = SimulatedLoad(resistance=0.0, emf=0.0, probe_resistance=0.0)
    psu.process_scpi_command(":CURR 1.0")
    psu.process_scpi_command(":VOLT 5.0")
    psu.process_scpi_command(":OUTP ON")

    assert psu.channels[0].mode == OperatingMode.CC
    assert psu.process_scpi_command(":MEAS:CURR?") == pytest.approx(1.0, rel=0.05)
    assert psu.process_scpi_command(":MEAS:VOLT?") == pytest.approx(0.0, abs=0.01)


def test_zero_resistance_load_at_matching_emf_stays_in_cv_mode(psu: SimulatedPSU) -> None:
    psu.channels[0].load = SimulatedLoad(resistance=0.0, emf=5.0, probe_resistance=0.0)
    psu.process_scpi_command(":CURR 1.0")
    psu.process_scpi_command(":VOLT 5.0")
    psu.process_scpi_command(":OUTP ON")

    assert psu.channels[0].mode == OperatingMode.CV
    assert psu.process_scpi_command(":MEAS:CURR?") == pytest.approx(0.0, abs=0.01)
    assert psu.process_scpi_command(":MEAS:VOLT?") == pytest.approx(5.0, rel=0.05)


def test_infinite_resistance_load_stays_in_cv_mode(psu: SimulatedPSU) -> None:
    psu.channels[0].load = SimulatedLoad(resistance=math.inf, emf=0.0, probe_resistance=0.0)
    psu.process_scpi_command(":CURR 1.0")
    psu.process_scpi_command(":VOLT 5.0")
    psu.process_scpi_command(":OUTP ON")

    assert psu.channels[0].mode == OperatingMode.CV
    assert psu.process_scpi_command(":MEAS:CURR?") == pytest.approx(0.0, abs=0.01)
    assert psu.process_scpi_command(":MEAS:VOLT?") == pytest.approx(5.0, rel=0.05)


@pytest.mark.parametrize(
    "command",
    [
        ":SENS:VOLT:PROT 5.0",
        ":SENS:CURR:PROT 1.0",
        ":SENS:REM ON",
        ":OUTP:PROT ON",
        ":OUTP:PROT:MODE CC",
        ":CURR:PROT:CLE",
        ":CURR:PROT:TRIP?",
        ":CURRent:PROTection:CLEar",
        ":CURRent:PROTection:TRIPped?",
        ":STAT:QUES?",
        ":STAT:QUES:COND?",
        ":STAT:QUES:EVEN?",
        ":STATus:QUEStionable?",
        ":STATus:QUEStionable:CONDition?",
        ":STATus:QUEStionable:EVENt?",
        ":MEAS:VOLT:DC?",
    ],
)
def test_non_matching_command_forms_record_undefined_header(psu: SimulatedPSU, command: str) -> None:
    assert psu.process_scpi_command(command) is None
    assert _error_code(psu) == SCPIError.UNDEFINED_HEADER.value


# --- *RST and *CLS ---


@pytest.mark.parametrize("command", ["*RST", "*rst"])
def test_rst_resets_channel_state(psu: SimulatedPSU, command: str) -> None:
    psu.process_scpi_command(":SOUR:VOLT 5.0")
    psu.process_scpi_command(":CURR 2.0")
    psu.process_scpi_command(":VOLT:PROT:STAT ON")
    psu.process_scpi_command(":CURR:PROT:STAT ON")
    psu.process_scpi_command(":OUTP ON")
    psu.process_scpi_command(command)

    assert psu.channels[0].voltage_setpoint == 0.0
    assert psu.channels[0].current_limit == 0.0
    assert psu.channels[0].output_enabled is False
    assert psu.channels[0].overvoltage_protection_enabled is False
    assert psu.channels[0].overcurrent_protection_enabled is False


def test_rst_preserves_channel_limits(psu: SimulatedPSU) -> None:
    ch = psu.channels[0]
    ch.voltage_max = 70.0
    ch.current_max = 12.0

    psu.process_scpi_command("*RST")

    assert ch.voltage_max == pytest.approx(70.0)
    assert ch.current_max == pytest.approx(12.0)
    assert ch.current_limit == pytest.approx(0.0)
    assert ch.overvoltage_protection_level == pytest.approx(ch.voltage_max)
    assert ch.overcurrent_protection_level == pytest.approx(ch.current_max)


def test_rst_preserves_sim_channel_configuration(psu: SimulatedPSU) -> None:
    ch = psu.channels[0]
    ch.load = SimulatedLoad(resistance=123.0, emf=4.0, probe_resistance=5.0)
    ch.remote_sense = True
    ch.overvoltage_tripped = True
    ch.overcurrent_tripped = True
    ch.protection_latched = True

    psu.process_scpi_command("*RST")

    assert ch.load.resistance == pytest.approx(123.0)
    assert ch.load.emf == pytest.approx(4.0)
    assert ch.load.probe_resistance == pytest.approx(5.0)
    assert ch.remote_sense is True
    assert ch.overvoltage_tripped is False
    assert ch.overcurrent_tripped is False
    assert ch.protection_latched is False


@pytest.mark.parametrize("command", ["*CLS", "*cls"])
def test_cls_clears_error_queue(psu: SimulatedPSU, command: str) -> None:
    psu.process_scpi_command(":BOGUS")
    psu.process_scpi_command(command)
    assert _error_code(psu) == SCPIError.NO_ERROR.value
