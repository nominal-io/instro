"""Optional Siglent SPS5042X hardware smoke tests. Run with nothing connected to the output."""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from instro.contrib.psu.drivers.siglent_sps5042x import SiglentSPS5042X
from instro.psu.types import OperatingMode

pytestmark = pytest.mark.hardware

VISA_RESOURCE = "TCPIP0::192.168.10.2::INSTR"
CHANNEL = 1
TEST_VOLTAGE = 4.0
TEST_CURRENT_LIMIT = 1.0
VOLTAGE_READBACK_TOLERANCE = 0.1
NO_LOAD_CURRENT_TOLERANCE = 0.05
SETTLE_S = 1.5


@dataclass(frozen=True)
class Protection:
    ovp: float
    ocp: float
    ocp_enabled: bool


@pytest.fixture(scope="module")
def driver() -> Iterator[SiglentSPS5042X]:
    psu_driver = SiglentSPS5042X(VISA_RESOURCE)
    psu_driver.open()
    try:
        yield psu_driver
    finally:
        psu_driver.close()


@pytest.fixture(scope="module")
def saved_protection(driver: SiglentSPS5042X) -> Iterator[Protection]:
    saved = Protection(
        ovp=driver.get_overvoltage_protection_level(channel=CHANNEL),
        ocp=driver.get_overcurrent_protection_level(channel=CHANNEL),
        ocp_enabled=driver.get_overcurrent_protection_enabled(channel=CHANNEL),
    )
    try:
        yield saved
    finally:
        _restore(driver, saved)


@pytest.fixture(autouse=True)
def reset_before_each_test(driver: SiglentSPS5042X, saved_protection: Protection) -> None:
    _restore(driver, saved_protection)
    driver.set_voltage(0.0, channel=CHANNEL)
    driver.set_current_limit(TEST_CURRENT_LIMIT, channel=CHANNEL)


def _restore(driver: SiglentSPS5042X, saved: Protection) -> None:
    driver.output_enable(False, channel=CHANNEL)
    driver.clear_protection(channel=CHANNEL)
    driver.set_overvoltage_protection_level(saved.ovp, channel=CHANNEL)
    driver.set_overcurrent_protection_level(saved.ocp, channel=CHANNEL)
    driver.set_overcurrent_protection_enabled(saved.ocp_enabled, channel=CHANNEL)


def test_enabled_output_regulates_to_voltage_setpoint(driver: SiglentSPS5042X) -> None:
    driver.set_voltage(TEST_VOLTAGE, channel=CHANNEL)
    driver.output_enable(True, channel=CHANNEL)
    time.sleep(SETTLE_S)

    assert driver.get_voltage(channel=CHANNEL) == pytest.approx(TEST_VOLTAGE, abs=VOLTAGE_READBACK_TOLERANCE)
    assert driver.get_current(channel=CHANNEL) == pytest.approx(0.0, abs=NO_LOAD_CURRENT_TOLERANCE)


def test_setpoints_read_back(driver: SiglentSPS5042X) -> None:
    driver.set_voltage(TEST_VOLTAGE, channel=CHANNEL)

    assert driver.get_voltage_setpoint(channel=CHANNEL) == pytest.approx(TEST_VOLTAGE, abs=0.001)
    assert driver.get_current_setpoint(channel=CHANNEL) == pytest.approx(TEST_CURRENT_LIMIT, abs=0.001)


def test_output_enable_round_trip(driver: SiglentSPS5042X) -> None:
    driver.output_enable(True, channel=CHANNEL)
    assert driver.get_output_status(channel=CHANNEL) is True

    driver.output_enable(False, channel=CHANNEL)
    assert driver.get_output_status(channel=CHANNEL) is False


def test_operating_mode_follows_output(driver: SiglentSPS5042X) -> None:
    assert driver.get_operating_mode(channel=CHANNEL) is OperatingMode.OFF

    driver.set_voltage(TEST_VOLTAGE, channel=CHANNEL)
    driver.output_enable(True, channel=CHANNEL)
    time.sleep(SETTLE_S)

    assert driver.get_operating_mode(channel=CHANNEL) is OperatingMode.CONSTANT_VOLTAGE


def test_protection_levels_read_back(driver: SiglentSPS5042X) -> None:
    driver.set_overvoltage_protection_level(30.0, channel=CHANNEL)
    driver.set_overcurrent_protection_level(30.0, channel=CHANNEL)

    assert driver.get_overvoltage_protection_level(channel=CHANNEL) == pytest.approx(30.0, abs=0.001)
    assert driver.get_overcurrent_protection_level(channel=CHANNEL) == pytest.approx(30.0, abs=0.001)


def test_protection_level_below_instrument_minimum_raises(driver: SiglentSPS5042X) -> None:
    with pytest.raises(ValueError, match="overvoltage protection level 0.000 is out of range"):
        driver.set_overvoltage_protection_level(0.0, channel=CHANNEL)


def test_overcurrent_protection_enabled_round_trip(driver: SiglentSPS5042X) -> None:
    driver.set_overcurrent_protection_enabled(False, channel=CHANNEL)
    assert driver.get_overcurrent_protection_enabled(channel=CHANNEL) is False

    driver.set_overcurrent_protection_enabled(True, channel=CHANNEL)
    assert driver.get_overcurrent_protection_enabled(channel=CHANNEL) is True


def test_overvoltage_trip_latches_output_off_until_cleared(driver: SiglentSPS5042X) -> None:
    driver.set_overvoltage_protection_level(10.0, channel=CHANNEL)
    driver.set_voltage(5.0, channel=CHANNEL)
    driver.output_enable(True, channel=CHANNEL)
    time.sleep(SETTLE_S)
    assert driver.get_output_status(channel=CHANNEL) is True

    driver.set_voltage(12.0, channel=CHANNEL)
    time.sleep(SETTLE_S)
    assert driver.get_output_status(channel=CHANNEL) is False

    driver.set_voltage(5.0, channel=CHANNEL)
    driver.output_enable(True, channel=CHANNEL)
    time.sleep(SETTLE_S)
    assert driver.get_output_status(channel=CHANNEL) is False

    driver.clear_protection(channel=CHANNEL)
    assert driver.get_output_status(channel=CHANNEL) is False
    driver.output_enable(True, channel=CHANNEL)
    time.sleep(SETTLE_S)
    assert driver.get_output_status(channel=CHANNEL) is True


def test_check_errors_raises_after_instrument_error(driver: SiglentSPS5042X) -> None:
    # Bypass the driver once so the next error check sees a real instrument error queue entry.
    driver._visa.write("INSTRO:INVALID")

    with pytest.raises(RuntimeError, match="Siglent SPS5042X reported error"):
        driver._check_errors()
