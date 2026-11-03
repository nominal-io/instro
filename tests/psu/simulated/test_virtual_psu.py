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


def test_overvoltage_trip_latches_the_output_off_until_cleared_and_re_enabled() -> None:
    psu = InstroPSU(name="psu", driver=VirtualPSU(num_channels=1, load_resistance=10.0), num_channels=1)
    with psu:
        psu.set_overvoltage_protection_level(4.0, channel=1)
        psu.set_overvoltage_protection_enabled(True, channel=1)
        psu.apply(current_limit=1.0, voltage=3.0, enable=True, channel=1)
        assert not psu.get_overvoltage_protection_tripped(channel=1).latest

        psu.set_voltage(5.0, channel=1)
        assert psu.get_overvoltage_protection_tripped(channel=1).latest
        assert not psu.get_overcurrent_protection_tripped(channel=1).latest
        assert not psu.get_output_status(channel=1).latest
        assert psu.get_voltage(channel=1).latest == 0.0
        assert psu.get_current(channel=1).latest == 0.0

        psu.clear_protection(channel=1)
        assert not psu.get_overvoltage_protection_tripped(channel=1).latest
        assert not psu.get_output_status(channel=1).latest

        psu.set_voltage(3.0, channel=1)
        psu.output_enable(True, channel=1)
        assert psu.get_voltage(channel=1).latest == pytest.approx(3.0, rel=0.01)


def test_a_fault_trips_when_it_happens_not_when_it_is_next_read() -> None:
    psu = InstroPSU(name="psu", driver=VirtualPSU(num_channels=1, load_resistance=10.0), num_channels=1)
    with psu:
        psu.set_overvoltage_protection_level(4.0, channel=1)
        psu.set_overvoltage_protection_enabled(True, channel=1)
        psu.apply(current_limit=1.0, voltage=3.0, enable=True, channel=1)

        psu.set_voltage(5.0, channel=1)
        psu.set_voltage(3.0, channel=1)
        assert psu.get_overvoltage_protection_tripped(channel=1).latest
        assert not psu.get_output_status(channel=1).latest


def test_overcurrent_trip_latches_the_output_off_until_cleared_and_re_enabled() -> None:
    driver = VirtualPSU(num_channels=1, load_resistance=10.0)
    psu = InstroPSU(name="psu", driver=driver, num_channels=1)
    with psu:
        psu.set_overcurrent_protection_level(1.0, channel=1)
        psu.set_overcurrent_protection_enabled(True, channel=1)
        psu.apply(current_limit=2.0, voltage=5.0, enable=True, channel=1)
        assert not psu.get_overcurrent_protection_tripped(channel=1).latest

        driver.set_load_resistance(1.0)
        assert psu.get_overcurrent_protection_tripped(channel=1).latest
        assert not psu.get_output_status(channel=1).latest
        assert psu.get_voltage(channel=1).latest == 0.0

        psu.clear_protection(channel=1)
        assert not psu.get_overcurrent_protection_tripped(channel=1).latest
        assert not psu.get_output_status(channel=1).latest

        driver.set_load_resistance(10.0)
        psu.output_enable(True, channel=1)
        assert psu.get_current(channel=1).latest == pytest.approx(0.5, rel=0.01)


def test_a_voltage_alone_drives_the_output() -> None:
    psu = InstroPSU(name="psu", driver=VirtualPSU(num_channels=1, load_resistance=10.0), num_channels=1)
    with psu:
        psu.set_voltage(5.0, channel=1)
        psu.output_enable(True, channel=1)
        assert psu.get_voltage(channel=1).latest == pytest.approx(5.0, rel=0.01)
        assert psu.get_operating_mode(channel=1).latest == OperatingMode.CONSTANT_VOLTAGE.value


def test_each_channel_regulates_into_its_own_load() -> None:
    driver = VirtualPSU(num_channels=2, load_resistance={2: 1.0})
    psu = InstroPSU(name="psu", driver=driver, num_channels=2)
    with psu:
        for channel in (1, 2):
            psu.apply(current_limit=1.0, voltage=5.0, enable=True, channel=channel)
        assert psu.get_operating_mode(channel=1).latest == OperatingMode.CONSTANT_VOLTAGE.value
        assert psu.get_operating_mode(channel=2).latest == OperatingMode.CONSTANT_CURRENT.value

        driver.set_load_resistance(100.0, channel=2)
        assert psu.get_operating_mode(channel=2).latest == OperatingMode.CONSTANT_VOLTAGE.value
        assert driver.get_load_resistance(channel=1) == 1000.0


def test_rejects_values_outside_the_scpi_simulator_ranges() -> None:
    driver = VirtualPSU(num_channels=1)
    with pytest.raises(ValueError, match="voltage must be within 0 to 60 V"):
        driver.set_voltage(-5.0, channel=1)
    with pytest.raises(ValueError, match="current_limit must be within 0 to 10 A"):
        driver.set_current_limit(11.0, channel=1)
    with pytest.raises(ValueError, match="overvoltage protection level must be within 0 to 60 V"):
        driver.set_overvoltage_protection_level(61.0, channel=1)
    with pytest.raises(ValueError, match="load_resistance must not be negative"):
        driver.set_load_resistance(-10.0)
    with pytest.raises(ValueError, match="channels 1 to 1, got 2"):
        VirtualPSU(num_channels=1, load_resistance={2: 10.0})


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
