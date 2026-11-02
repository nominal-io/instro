"""Tests for the transport-free VirtualPSU driver."""

from __future__ import annotations

import pytest

from instro.psu import InstroPSU, OperatingMode, scpi_sim_server
from instro.psu.drivers import VirtualPSU, virtual


def test_regulates_voltage_until_the_load_hits_the_current_limit() -> None:
    psu = InstroPSU(name="psu", driver=VirtualPSU(num_channels=1, load_resistance=10.0), num_channels=1)
    with psu:
        assert psu.get_operating_mode(channel=1).latest == OperatingMode.OFF.value

        psu.apply(current_limit=1.0, voltage=5.0, enable=True, channel=1)
        assert psu.get_voltage(channel=1).latest == pytest.approx(5.0, rel=0.01)
        assert psu.get_current(channel=1).latest == pytest.approx(0.5, rel=0.01)
        assert psu.get_operating_mode(channel=1).latest == OperatingMode.CONSTANT_VOLTAGE.value

        psu.set_current_limit(0.2, channel=1)
        assert psu.get_voltage(channel=1).latest == pytest.approx(2.0, rel=0.01)
        assert psu.get_current(channel=1).latest == pytest.approx(0.2, rel=0.01)
        assert psu.get_operating_mode(channel=1).latest == OperatingMode.CONSTANT_CURRENT.value

        psu.output_enable(False, channel=1)
        assert psu.get_voltage(channel=1).latest == 0.0
        assert psu.get_current(channel=1).latest == 0.0


def test_protection_trip_latches_the_output_off_until_cleared_and_re_enabled() -> None:
    driver = VirtualPSU(num_channels=1, load_resistance=10.0)
    psu = InstroPSU(name="psu", driver=driver, num_channels=1)
    with psu:
        psu.set_overcurrent_protection_level(1.0, channel=1)
        psu.set_overcurrent_protection_enabled(True, channel=1)
        psu.apply(current_limit=2.0, voltage=5.0, enable=True, channel=1)
        assert not psu.get_overcurrent_protection_tripped(channel=1).latest

        driver.load_resistance = 1.0
        assert psu.get_overcurrent_protection_tripped(channel=1).latest
        assert not psu.get_output_status(channel=1).latest
        assert psu.get_voltage(channel=1).latest == 0.0

        psu.clear_protection(channel=1)
        assert not psu.get_overcurrent_protection_tripped(channel=1).latest
        assert not psu.get_output_status(channel=1).latest

        driver.load_resistance = 10.0
        psu.output_enable(True, channel=1)
        assert psu.get_current(channel=1).latest == pytest.approx(0.5, rel=0.01)


def test_every_setting_reads_back_per_channel() -> None:
    psu = InstroPSU(name="psu", driver=VirtualPSU(), num_channels=2)
    with psu:
        psu.set_voltage(3.3, channel=2)
        psu.set_current_limit(0.5, channel=2)
        psu.set_overvoltage_protection_level(4.0, channel=2)
        psu.set_overvoltage_protection_enabled(True, channel=2)
        psu.set_overvoltage_protection_delay(0.01, channel=2)
        psu.set_overcurrent_protection_level(0.6, channel=2)
        psu.set_overcurrent_protection_enabled(True, channel=2)
        psu.set_remote_sense_enabled(True, channel=2)

        assert psu.get_voltage_setpoint(channel=2).latest == 3.3
        assert psu.get_current_setpoint(channel=2).latest == 0.5
        assert psu.get_overvoltage_protection_level(channel=2).latest == 4.0
        assert psu.get_overvoltage_protection_enabled(channel=2).latest
        assert psu.get_overvoltage_protection_delay(channel=2).latest == 0.01
        assert psu.get_overcurrent_protection_level(channel=2).latest == 0.6
        assert psu.get_overcurrent_protection_enabled(channel=2).latest
        assert psu.get_remote_sense_enabled(channel=2).latest
        assert psu.get_voltage_setpoint(channel=1).latest == 0.0
        assert not psu.get_remote_sense_enabled(channel=1).latest


def test_defaults_match_the_scpi_simulator() -> None:
    """VirtualPSU copies these instead of importing the simulator module, which loads its terminal UI."""
    assert virtual.DEFAULT_NUM_CHANNELS == scpi_sim_server.DEFAULT_NUM_CHANNELS
    assert virtual.DEFAULT_LOAD_RESISTANCE == scpi_sim_server.DEFAULT_LOAD_RESISTANCE
    assert virtual.DEFAULT_VOLTAGE_MAX == scpi_sim_server.DEFAULT_VOLTAGE_MAX
    assert virtual.DEFAULT_CURRENT_MAX == scpi_sim_server.DEFAULT_CURRENT_MAX
