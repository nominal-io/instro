"""End-to-end tests for the simulated DMM SCPI server."""

from __future__ import annotations

import socket

import pytest

from instro.dmm.scpi_sim_server import (
    DEFAULT_STIMULUS,
    FUNCTION_AC_CURRENT,
    FUNCTION_AC_VOLTAGE,
    FUNCTION_DC_CURRENT,
    FUNCTION_DC_VOLTAGE,
    FUNCTION_RESISTANCE,
    SCPIError,
    SimulatedDMM,
    SimulatedDMMServer,
)


@pytest.fixture
def dmm() -> SimulatedDMM:
    return SimulatedDMM()


def _error_code(dmm: SimulatedDMM) -> int:
    return int(dmm.process_scpi_command("SYST:ERR?").split(",")[0])


# --- Identity and error queue ---


@pytest.mark.parametrize("command", ["*IDN?", "*idn?"])
def test_idn_returns_nominal_id(dmm: SimulatedDMM, command: str) -> None:
    assert dmm.process_scpi_command(command).startswith("NOMINAL,SIMULATED_DMM")


@pytest.mark.parametrize("command", ["SYST:ERR?", "system:error?"])
def test_syst_err_no_error_when_empty(dmm: SimulatedDMM, command: str) -> None:
    assert dmm.process_scpi_command(command) == '0,"No error"'


def test_unknown_command_records_undefined_header(dmm: SimulatedDMM) -> None:
    dmm.process_scpi_command(":BOGUS:THING")
    assert _error_code(dmm) == SCPIError.UNDEFINED_HEADER.value


def test_error_queue_clears_after_read(dmm: SimulatedDMM) -> None:
    dmm.process_scpi_command(":BOGUS")
    dmm.process_scpi_command(":BOGUS")

    assert _error_code(dmm) == SCPIError.UNDEFINED_HEADER.value
    assert _error_code(dmm) == SCPIError.UNDEFINED_HEADER.value
    assert _error_code(dmm) == SCPIError.NO_ERROR.value


def test_cls_clears_error_queue(dmm: SimulatedDMM) -> None:
    dmm.process_scpi_command(":BOGUS")
    dmm.process_scpi_command("*CLS")
    assert _error_code(dmm) == SCPIError.NO_ERROR.value


# --- Function switching ---


@pytest.mark.parametrize(
    "command",
    ['FUNC "VOLT:AC"', 'FUNCTION "VOLT:AC"', 'SENS:FUNC "VOLT:AC"', "func 'volt:ac'", "FUNC VOLT:AC"],
)
def test_func_sets_function(dmm: SimulatedDMM, command: str) -> None:
    dmm.process_scpi_command(command)
    assert dmm.function == FUNCTION_AC_VOLTAGE
    assert _error_code(dmm) == SCPIError.NO_ERROR.value


def test_func_query_returns_quoted_function(dmm: SimulatedDMM) -> None:
    assert dmm.process_scpi_command("FUNC?") == '"VOLT:DC"'
    dmm.process_scpi_command('FUNC "RES"')
    assert dmm.process_scpi_command("FUNC?") == '"RES"'


def test_func_bare_volt_and_curr_default_to_dc(dmm: SimulatedDMM) -> None:
    dmm.process_scpi_command('FUNC "CURR"')
    assert dmm.function == FUNCTION_DC_CURRENT
    dmm.process_scpi_command('FUNC "VOLT"')
    assert dmm.function == FUNCTION_DC_VOLTAGE


def test_func_unknown_records_illegal_value(dmm: SimulatedDMM) -> None:
    dmm.process_scpi_command('FUNC "TEMP"')
    assert _error_code(dmm) == SCPIError.ILLEGAL_PARAMETER_VALUE.value
    assert dmm.function == FUNCTION_DC_VOLTAGE


def test_func_missing_parameter(dmm: SimulatedDMM) -> None:
    dmm.process_scpi_command("FUNC")
    assert _error_code(dmm) == SCPIError.MISSING_PARAMETER.value


@pytest.mark.parametrize(
    ("command", "function"),
    [
        ("CONF:VOLT", FUNCTION_DC_VOLTAGE),
        ("CONF:VOLT:DC", FUNCTION_DC_VOLTAGE),
        ("configure:voltage:ac", FUNCTION_AC_VOLTAGE),
        ("CONF:CURR", FUNCTION_DC_CURRENT),
        ("CONF:CURR:AC", FUNCTION_AC_CURRENT),
        ("CONF:RES", FUNCTION_RESISTANCE),
    ],
)
def test_configure_sets_function(dmm: SimulatedDMM, command: str, function: str) -> None:
    dmm.process_scpi_command(command)
    assert dmm.function == function
    assert _error_code(dmm) == SCPIError.NO_ERROR.value


# --- Measurement ---


@pytest.mark.parametrize(
    ("command", "function"),
    [
        ("MEAS:VOLT?", FUNCTION_DC_VOLTAGE),
        ("MEAS:VOLT:DC?", FUNCTION_DC_VOLTAGE),
        ("measure:voltage:ac?", FUNCTION_AC_VOLTAGE),
        ("MEAS:CURR:DC?", FUNCTION_DC_CURRENT),
        ("MEAS:CURR:AC?", FUNCTION_AC_CURRENT),
        ("MEAS:RES?", FUNCTION_RESISTANCE),
    ],
)
def test_measure_returns_noisy_stimulus_and_switches_function(dmm: SimulatedDMM, command: str, function: str) -> None:
    value = dmm.process_scpi_command(command)
    assert value == pytest.approx(DEFAULT_STIMULUS[function], rel=0.05)
    assert dmm.function == function
    assert _error_code(dmm) == SCPIError.NO_ERROR.value


def test_read_uses_active_function(dmm: SimulatedDMM) -> None:
    dmm.set_stimulus(FUNCTION_RESISTANCE, 4700.0)
    dmm.process_scpi_command("CONF:RES")
    assert dmm.process_scpi_command("READ?") == pytest.approx(4700.0, rel=0.05)


def test_measure_reflects_set_stimulus(dmm: SimulatedDMM) -> None:
    dmm.set_stimulus(FUNCTION_DC_VOLTAGE, 3.3)
    assert dmm.process_scpi_command("MEAS:VOLT:DC?") == pytest.approx(3.3, rel=0.05)


def test_measure_returns_varying_values(dmm: SimulatedDMM) -> None:
    values = {dmm.process_scpi_command("MEAS:VOLT:DC?") for _ in range(10)}
    assert len(values) > 1


def test_set_stimulus_rejects_unknown_function(dmm: SimulatedDMM) -> None:
    with pytest.raises(ValueError, match="unknown function"):
        dmm.set_stimulus("TEMP", 25.0)


def test_measure_query_with_args_records_parameter_not_allowed(dmm: SimulatedDMM) -> None:
    assert dmm.process_scpi_command("MEAS:VOLT:DC? 10,0.001") is None
    assert _error_code(dmm) == SCPIError.PARAMETER_NOT_ALLOWED.value


# --- *RST ---


def test_rst_resets_function_and_errors_but_keeps_stimulus(dmm: SimulatedDMM) -> None:
    dmm.set_stimulus(FUNCTION_RESISTANCE, 4700.0)
    dmm.process_scpi_command("CONF:RES")
    dmm.process_scpi_command(":BOGUS")

    dmm.process_scpi_command("*RST")

    assert dmm.function == FUNCTION_DC_VOLTAGE
    assert dmm.stimulus[FUNCTION_RESISTANCE] == pytest.approx(4700.0)
    assert _error_code(dmm) == SCPIError.NO_ERROR.value


# --- Constructor stimulus ---


def test_constructor_stimulus_overrides_defaults() -> None:
    dmm = SimulatedDMM(stimulus={FUNCTION_DC_VOLTAGE: 9.0})
    assert dmm.stimulus[FUNCTION_DC_VOLTAGE] == pytest.approx(9.0)
    assert dmm.stimulus[FUNCTION_RESISTANCE] == pytest.approx(DEFAULT_STIMULUS[FUNCTION_RESISTANCE])


# --- TCP server ---


def test_server_round_trip_over_tcp() -> None:
    dmm = SimulatedDMM()
    server = SimulatedDMMServer(dmm, port=0)
    server.start()
    try:
        with socket.create_connection(("127.0.0.1", server.port), timeout=2.0) as conn:
            conn.settimeout(2.0)
            f = conn.makefile("rw", newline="\n")
            f.write("*IDN?\n")
            f.flush()
            assert f.readline().strip().startswith("NOMINAL,SIMULATED_DMM")

            f.write('FUNC "RES"\nFUNC?\n')
            f.flush()
            assert f.readline().strip() == '"RES"'

            f.write("MEAS:VOLT:DC?\n")
            f.flush()
            value = float(f.readline().strip())
            assert value == pytest.approx(DEFAULT_STIMULUS[FUNCTION_DC_VOLTAGE], rel=0.05)
    finally:
        server.shutdown()
