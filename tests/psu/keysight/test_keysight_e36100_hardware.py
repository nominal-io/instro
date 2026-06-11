"""Optional Keysight E36100-series hardware smoke tests."""

from __future__ import annotations

import time

import pytest

from instro.lib.exceptions import FeatureNotSupportedError
from instro.lib.transports import VisaConfig
from instro.psu.drivers.keysight_e36100 import KeysightE36100

pytestmark = pytest.mark.hardware

# HARDWARE TEST SETUP - EDIT THESE VALUES BEFORE RUNNING THIS FILE.
# Set VISA_ADDRESS to the bench unit's VISA resource string.
# Keep the programmed values comfortably inside the specific unit's ratings.
VISA_ADDRESS = "USB0::0x0957::0x1502::MY_SERIAL_NUMBER::INSTR"
CHANNEL = 1
PROGRAMMED_VOLTAGE = 1.0
PROGRAMMED_CURRENT_LIMIT = 0.1
OVP_LEVEL = 5.0
VOLTAGE_READBACK_TOLERANCE = 0.15
CURRENT_READBACK_TOLERANCE = 0.01


@pytest.fixture(scope="module")
def driver(request: pytest.FixtureRequest) -> KeysightE36100:
    psu_driver = KeysightE36100(
        VisaConfig(
            visa_resource=VISA_ADDRESS,
        )
    )
    try:
        psu_driver.open()
    except Exception:
        psu_driver.close()
        raise

    request.addfinalizer(psu_driver.close)
    return psu_driver


@pytest.fixture(autouse=True)
def reset_before_each_test(driver: KeysightE36100) -> None:
    driver._visa.write("*RST")
    driver._check_errors()


def test_set_voltage(driver: KeysightE36100) -> None:
    driver.set_current_limit(PROGRAMMED_CURRENT_LIMIT, channel=CHANNEL)
    driver.set_voltage(PROGRAMMED_VOLTAGE, channel=CHANNEL)
    try:
        driver.output_enable(True, channel=CHANNEL)
        time.sleep(1)

        assert driver.get_voltage(channel=CHANNEL) == pytest.approx(
            PROGRAMMED_VOLTAGE,
            abs=VOLTAGE_READBACK_TOLERANCE,
        )
    finally:
        driver.output_enable(False, channel=CHANNEL)


def test_get_voltage(driver: KeysightE36100) -> None:
    driver.set_current_limit(PROGRAMMED_CURRENT_LIMIT, channel=CHANNEL)
    driver.set_voltage(PROGRAMMED_VOLTAGE, channel=CHANNEL)
    try:
        driver.output_enable(True, channel=CHANNEL)
        time.sleep(1)

        voltage = driver.get_voltage(channel=CHANNEL)

        assert voltage == pytest.approx(
            PROGRAMMED_VOLTAGE,
            abs=VOLTAGE_READBACK_TOLERANCE,
        )
    finally:
        driver.output_enable(False, channel=CHANNEL)


def test_set_current_limit(driver: KeysightE36100) -> None:
    driver.set_current_limit(PROGRAMMED_CURRENT_LIMIT, channel=CHANNEL)
    driver.set_voltage(PROGRAMMED_VOLTAGE, channel=CHANNEL)
    try:
        driver.output_enable(True, channel=CHANNEL)
        time.sleep(1)

        assert driver.get_current(channel=CHANNEL) == pytest.approx(
            0.0,
            abs=CURRENT_READBACK_TOLERANCE,
        )
    finally:
        driver.output_enable(False, channel=CHANNEL)


def test_get_current(driver: KeysightE36100) -> None:
    driver.set_current_limit(PROGRAMMED_CURRENT_LIMIT, channel=CHANNEL)
    driver.set_voltage(PROGRAMMED_VOLTAGE, channel=CHANNEL)
    try:
        driver.output_enable(True, channel=CHANNEL)
        time.sleep(1)

        current = driver.get_current(channel=CHANNEL)

        assert current == pytest.approx(
            0.0,
            abs=CURRENT_READBACK_TOLERANCE,
        )
    finally:
        driver.output_enable(False, channel=CHANNEL)


def test_output_enable(driver: KeysightE36100) -> None:
    driver.set_current_limit(PROGRAMMED_CURRENT_LIMIT, channel=CHANNEL)
    driver.set_voltage(PROGRAMMED_VOLTAGE, channel=CHANNEL)
    try:
        driver.output_enable(True, channel=CHANNEL)
        assert driver.get_output_status(channel=CHANNEL) is True

        driver.output_enable(False, channel=CHANNEL)
        assert driver.get_output_status(channel=CHANNEL) is False
    finally:
        driver.output_enable(False, channel=CHANNEL)


def test_get_output_status(driver: KeysightE36100) -> None:
    assert driver.get_output_status(channel=CHANNEL) is False

    driver.set_current_limit(PROGRAMMED_CURRENT_LIMIT, channel=CHANNEL)
    driver.set_voltage(PROGRAMMED_VOLTAGE, channel=CHANNEL)
    try:
        driver.output_enable(True, channel=CHANNEL)

        assert driver.get_output_status(channel=CHANNEL) is True
    finally:
        driver.output_enable(False, channel=CHANNEL)


def test_set_overvoltage_protection_level(driver: KeysightE36100) -> None:
    driver.set_overvoltage_protection_level(OVP_LEVEL, channel=CHANNEL)

    assert driver.get_overvoltage_protection_level(channel=CHANNEL) == pytest.approx(OVP_LEVEL)


def test_get_overvoltage_protection_level(driver: KeysightE36100) -> None:
    driver.set_overvoltage_protection_level(OVP_LEVEL, channel=CHANNEL)

    level = driver.get_overvoltage_protection_level(channel=CHANNEL)

    assert level == pytest.approx(OVP_LEVEL)


def test_set_overvoltage_protection_enabled(driver: KeysightE36100) -> None:
    driver.set_overvoltage_protection_level(OVP_LEVEL, channel=CHANNEL)
    driver.set_overvoltage_protection_enabled(True, channel=CHANNEL)
    assert driver.get_overvoltage_protection_enabled(channel=CHANNEL) is True

    driver.set_overvoltage_protection_enabled(False, channel=CHANNEL)
    assert driver.get_overvoltage_protection_enabled(channel=CHANNEL) is False


def test_get_overvoltage_protection_enabled(driver: KeysightE36100) -> None:
    # *RST leaves OVP enabled on the E36100 series, so set a known baseline first.
    driver.set_overvoltage_protection_level(OVP_LEVEL, channel=CHANNEL)
    driver.set_overvoltage_protection_enabled(False, channel=CHANNEL)
    assert driver.get_overvoltage_protection_enabled(channel=CHANNEL) is False

    driver.set_overvoltage_protection_enabled(True, channel=CHANNEL)
    assert driver.get_overvoltage_protection_enabled(channel=CHANNEL) is True


def test_set_overvoltage_protection_delay_unsupported(driver: KeysightE36100) -> None:
    with pytest.raises(FeatureNotSupportedError, match="set_overvoltage_protection_delay is not supported"):
        driver.set_overvoltage_protection_delay(0.1, channel=CHANNEL)


def test_get_overvoltage_protection_delay_unsupported(driver: KeysightE36100) -> None:
    with pytest.raises(FeatureNotSupportedError, match="get_overvoltage_protection_delay is not supported"):
        driver.get_overvoltage_protection_delay(channel=CHANNEL)


def test_set_overcurrent_protection_level_unsupported(driver: KeysightE36100) -> None:
    with pytest.raises(FeatureNotSupportedError, match="no separate OCP level"):
        driver.set_overcurrent_protection_level(PROGRAMMED_CURRENT_LIMIT, channel=CHANNEL)


def test_get_overcurrent_protection_level_unsupported(driver: KeysightE36100) -> None:
    with pytest.raises(FeatureNotSupportedError, match="no separate OCP level"):
        driver.get_overcurrent_protection_level(channel=CHANNEL)


def test_set_overcurrent_protection_enabled(driver: KeysightE36100) -> None:
    driver.set_overcurrent_protection_enabled(True, channel=CHANNEL)
    assert driver.get_overcurrent_protection_enabled(channel=CHANNEL) is True

    driver.set_overcurrent_protection_enabled(False, channel=CHANNEL)
    assert driver.get_overcurrent_protection_enabled(channel=CHANNEL) is False


def test_get_overcurrent_protection_enabled(driver: KeysightE36100) -> None:
    assert driver.get_overcurrent_protection_enabled(channel=CHANNEL) is False

    driver.set_overcurrent_protection_enabled(True, channel=CHANNEL)

    assert driver.get_overcurrent_protection_enabled(channel=CHANNEL) is True


def test_set_remote_sense_enabled(driver: KeysightE36100) -> None:
    driver.set_remote_sense_enabled(True, channel=CHANNEL)
    assert driver.get_remote_sense_enabled(channel=CHANNEL) is True

    driver.set_remote_sense_enabled(False, channel=CHANNEL)
    assert driver.get_remote_sense_enabled(channel=CHANNEL) is False


def test_get_remote_sense_enabled(driver: KeysightE36100) -> None:
    assert driver.get_remote_sense_enabled(channel=CHANNEL) is False

    driver.set_remote_sense_enabled(True, channel=CHANNEL)

    assert driver.get_remote_sense_enabled(channel=CHANNEL) is True


def test_check_errors_raises_after_instrument_error(driver: KeysightE36100) -> None:
    driver._visa.write("INSTRO:INVALID")

    with pytest.raises(RuntimeError, match="Keysight E36100-series PSU reported error"):
        driver._check_errors()
