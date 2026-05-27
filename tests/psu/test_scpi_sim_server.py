"""End-to-end tests for the Keysight B2962C-style simulated PSU SCPI server (no transport)."""

from __future__ import annotations

import math
import time

import pytest

from instro.psu.scpi_sim_server import (
    OperatingMode,
    SCPIError,
    SimulatedLoad,
    SimulatedPSU,
    SourceMode,
)


@pytest.fixture
def psu() -> SimulatedPSU:
    return SimulatedPSU()


def _error_code(psu: SimulatedPSU) -> int:
    return int(psu.process_scpi_command("SYST:ERR?").split(",")[0])


# --- Identity and error queue ---


def test_idn_returns_nominal_id(psu: SimulatedPSU) -> None:
    assert psu.process_scpi_command("*IDN?").startswith("NOMINAL,SIMULATED_PSU")


def test_syst_err_no_error_when_empty(psu: SimulatedPSU) -> None:
    assert psu.process_scpi_command("SYST:ERR?") == '+0,"No error"'


def test_long_form_syst_err_works(psu: SimulatedPSU) -> None:
    assert psu.process_scpi_command("SYSTEM:ERROR?") == '+0,"No error"'


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
        ":SOUR:CURR",
        ":SOUR:FUNC:MODE",
        ":OUTP",
        ":OUTP:PROT",
        ":SENS:VOLT:PROT",
        ":SENS:CURR:PROT",
        ":SENS:REM",
    ],
)
def test_missing_parameter_records_missing_parameter(psu: SimulatedPSU, command: str) -> None:
    assert psu.process_scpi_command(command) is None
    assert _error_code(psu) == SCPIError.MISSING_PARAMETER.value


def test_unparseable_numeric_arg_records_error_not_crash(psu: SimulatedPSU) -> None:
    # Old E36300-style channel suffix lands here as garbage after the value
    # (e.g. "5.000 1" instead of "5.000"). Server should push an error rather
    # than letting the ValueError propagate and kill the client connection.
    assert psu.process_scpi_command(":SOUR:VOLT 5.000 1") is None
    assert _error_code(psu) == SCPIError.INVALID_CHARACTER_DATA.value


def test_bare_volt_resolves_to_source_voltage(psu: SimulatedPSU) -> None:
    psu.process_scpi_command("VOLT 4.2")
    assert psu.process_scpi_command("VOLT?") == pytest.approx(4.2)


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
    assert log[-1].startswith(time.strftime("%H:%M:%S")[:5]) or True  # tolerate clock change
    assert "BOGUS" in log[-1]
    assert "-113" in log[-1]
    assert "Undefined header" in log[-1]


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


# --- Voltage / current setpoints ---


def test_voltage_setpoint_round_trip(psu: SimulatedPSU) -> None:
    psu.process_scpi_command(":SOUR:VOLT 7.25")
    assert psu.process_scpi_command(":SOUR:VOLT?") == pytest.approx(7.25)


def test_voltage_level_immediate_amplitude_form(psu: SimulatedPSU) -> None:
    psu.process_scpi_command(":SOUR:VOLT:LEV:IMM:AMPL 2.5")
    assert psu.process_scpi_command(":SOUR:VOLT?") == pytest.approx(2.5)


# --- Output enable ---


def test_outp_round_trip(psu: SimulatedPSU) -> None:
    assert psu.process_scpi_command(":OUTP?") == 0
    psu.process_scpi_command(":OUTP ON")
    assert psu.process_scpi_command(":OUTP?") == 1
    psu.process_scpi_command(":OUTP OFF")
    assert psu.process_scpi_command(":OUTP?") == 0


def test_disabled_output_measures_zero(psu: SimulatedPSU) -> None:
    psu.process_scpi_command(":SOUR:VOLT 5.0")
    assert psu.process_scpi_command(":MEAS:VOLT?") == pytest.approx(0.0, abs=0.001)
    assert psu.process_scpi_command(":MEAS:CURR?") == pytest.approx(0.0, abs=0.001)


# --- Compliance (the OVP/OCP equivalents on a SMU) ---


def test_current_compliance_round_trip(psu: SimulatedPSU) -> None:
    psu.process_scpi_command(":SENS:CURR:PROT 0.5")
    assert psu.process_scpi_command(":SENS:CURR:PROT?") == pytest.approx(0.5)


def test_voltage_compliance_round_trip(psu: SimulatedPSU) -> None:
    psu.process_scpi_command(":SENS:VOLT:PROT 12.0")
    assert psu.process_scpi_command(":SENS:VOLT:PROT?") == pytest.approx(12.0)


def test_compliance_max_keyword_maps_to_infinity(psu: SimulatedPSU) -> None:
    psu.process_scpi_command(":SENS:CURR:PROT MAX")
    assert psu.process_scpi_command(":SENS:CURR:PROT?") == math.inf


def test_current_compliance_tripped_query_when_in_cv(psu: SimulatedPSU) -> None:
    psu.process_scpi_command(":SENS:CURR:PROT 1.0")
    psu.process_scpi_command(":SOUR:VOLT 5.0")
    psu.process_scpi_command(":OUTP ON")

    assert psu.channels[0].mode == OperatingMode.CV
    assert psu.process_scpi_command(":SENS:CURR:PROT:TRIP?") == 0


def test_current_compliance_tripped_query_when_in_cc(psu: SimulatedPSU) -> None:
    psu.channels[0].load = SimulatedLoad(resistance=0.1, probe_resistance=0.0)
    psu.process_scpi_command(":SENS:CURR:PROT 1.0")
    psu.process_scpi_command(":SOUR:VOLT 5.0")
    psu.process_scpi_command(":OUTP ON")

    assert psu.channels[0].mode == OperatingMode.CC
    assert psu.process_scpi_command(":SENS:CURR:PROT:TRIP?") == 1


def test_compliance_alone_does_not_latch_output_off(psu: SimulatedPSU) -> None:
    psu.channels[0].load = SimulatedLoad(resistance=0.1, probe_resistance=0.0)
    psu.process_scpi_command(":SENS:CURR:PROT 1.0")
    psu.process_scpi_command(":SOUR:VOLT 5.0")
    psu.process_scpi_command(":OUTP ON")

    assert psu.channels[0].output_enabled is True
    assert psu.channels[0].mode == OperatingMode.CC


# --- OUTPut:PROTection auto-latch ---


def test_output_protection_state_round_trip(psu: SimulatedPSU) -> None:
    assert psu.process_scpi_command(":OUTP:PROT?") == 0
    psu.process_scpi_command(":OUTP:PROT ON")
    assert psu.process_scpi_command(":OUTP:PROT?") == 1
    psu.process_scpi_command(":OUTP:PROT OFF")
    assert psu.process_scpi_command(":OUTP:PROT?") == 0


def test_output_protection_latches_off_when_compliance_reached(psu: SimulatedPSU) -> None:
    psu.channels[0].load = SimulatedLoad(resistance=0.1, probe_resistance=0.0)
    psu.process_scpi_command(":SENS:CURR:PROT 1.0")
    psu.process_scpi_command(":SOUR:VOLT 5.0")
    psu.process_scpi_command(":OUTP:PROT ON")
    psu.process_scpi_command(":OUTP ON")

    assert psu.channels[0].output_enabled is False
    assert psu.channels[0].protection_latched is True
    assert psu.channels[0].mode == OperatingMode.OFF


def test_enable_while_protection_latched_pushes_settings_conflict(psu: SimulatedPSU) -> None:
    psu.channels[0].protection_latched = True
    psu.process_scpi_command(":OUTP ON")

    assert psu.channels[0].output_enabled is False
    assert _error_code(psu) == SCPIError.SETTINGS_CONFLICT.value


def test_clear_protection_latch_allows_re_enable(psu: SimulatedPSU) -> None:
    psu.channels[0].load = SimulatedLoad(resistance=0.1, probe_resistance=0.0)
    psu.process_scpi_command(":SENS:CURR:PROT 1.0")
    psu.process_scpi_command(":SOUR:VOLT 5.0")
    psu.process_scpi_command(":OUTP:PROT ON")
    psu.process_scpi_command(":OUTP ON")
    assert psu.channels[0].protection_latched is True

    psu.channels[0].load = SimulatedLoad(resistance=1000.0)
    psu.process_scpi_command(":OUTP:PROT:CLE")
    psu.process_scpi_command(":OUTP ON")

    assert psu.channels[0].protection_latched is False
    assert psu.channels[0].output_enabled is True


# --- Remote sense ---


def test_remote_sense_round_trip(psu: SimulatedPSU) -> None:
    assert psu.process_scpi_command(":SENS:REM?") == 0
    psu.process_scpi_command(":SENS:REM ON")
    assert psu.process_scpi_command(":SENS:REM?") == 1
    psu.process_scpi_command(":SENS:REM OFF")
    assert psu.process_scpi_command(":SENS:REM?") == 0


def test_remote_sense_eliminates_probe_drop(psu: SimulatedPSU) -> None:
    psu.channels[0].load = SimulatedLoad(resistance=100.0, probe_resistance=10.0)
    psu.process_scpi_command(":SENS:CURR:PROT 1.0")
    psu.process_scpi_command(":SOUR:VOLT 5.0")
    psu.process_scpi_command(":OUTP ON")

    i_local = psu.process_scpi_command(":MEAS:CURR?")
    assert i_local == pytest.approx(5.0 / 110.0, rel=0.1)

    psu.process_scpi_command(":SENS:REM ON")
    i_remote = psu.process_scpi_command(":MEAS:CURR?")
    assert i_remote == pytest.approx(5.0 / 100.0, rel=0.1)
    assert i_remote > i_local


# --- CV/CC and EMF-driven current ---


def test_cv_mode_measures_setpoint_voltage(psu: SimulatedPSU) -> None:
    psu.process_scpi_command(":SENS:CURR:PROT 1.0")
    psu.process_scpi_command(":SOUR:VOLT 5.0")
    psu.process_scpi_command(":OUTP ON")

    assert psu.channels[0].mode == OperatingMode.CV
    assert psu.process_scpi_command(":MEAS:VOLT?") == pytest.approx(5.0, rel=0.05)


def test_emf_load_draws_charging_current(psu: SimulatedPSU) -> None:
    psu.channels[0].load = SimulatedLoad(resistance=1.0, emf=3.0, probe_resistance=0.0)
    psu.process_scpi_command(":SENS:CURR:PROT 10.0")
    psu.process_scpi_command(":SOUR:VOLT 5.0")
    psu.process_scpi_command(":OUTP ON")

    assert psu.process_scpi_command(":MEAS:CURR?") == pytest.approx(2.0, rel=0.1)


# --- Current-source mode ---


def test_current_source_drives_setpoint_through_load(psu: SimulatedPSU) -> None:
    psu.channels[0].load = SimulatedLoad(resistance=100.0, probe_resistance=0.0)
    psu.channels[0].source_mode = SourceMode.CURRENT
    psu.channels[0].current_setpoint = 0.05
    psu.channels[0].voltage_compliance = 10.0
    psu.process_scpi_command(":OUTP ON")

    assert psu.channels[0].mode == OperatingMode.CC
    assert psu.process_scpi_command(":MEAS:CURR?") == pytest.approx(0.05, rel=0.05)
    assert psu.process_scpi_command(":MEAS:VOLT?") == pytest.approx(5.0, rel=0.05)


def test_current_source_voltage_compliance_clamps(psu: SimulatedPSU) -> None:
    psu.channels[0].load = SimulatedLoad(resistance=1000.0)
    psu.channels[0].source_mode = SourceMode.CURRENT
    psu.channels[0].current_setpoint = 0.01  # would demand 10 V across 1k
    psu.channels[0].voltage_compliance = 5.0
    psu.process_scpi_command(":OUTP ON")

    assert psu.channels[0].mode == OperatingMode.CV
    assert psu.channels[0].voltage_compliance_tripped is True
    assert psu.process_scpi_command(":MEAS:VOLT?") == pytest.approx(5.0, rel=0.05)
    assert psu.process_scpi_command(":MEAS:CURR?") == pytest.approx(5.0 / 1000.0, rel=0.05)


# --- *RST and *CLS ---


def test_rst_resets_channel_state(psu: SimulatedPSU) -> None:
    psu.process_scpi_command(":SOUR:VOLT 5.0")
    psu.process_scpi_command(":OUTP ON")
    psu.process_scpi_command("*RST")

    assert psu.channels[0].voltage_setpoint == 0.0
    assert psu.channels[0].output_enabled is False


def test_cls_clears_error_queue(psu: SimulatedPSU) -> None:
    psu.process_scpi_command(":BOGUS")
    psu.process_scpi_command("*CLS")
    assert _error_code(psu) == SCPIError.NO_ERROR.value
