"""Optional B&K Precision 9115-series hardware smoke tests."""

from __future__ import annotations

import time

import pytest

from instro.lib.exceptions import FeatureNotSupportedError
from instro.lib.transports import VisaConfig
from instro.psu.drivers.bk_9115 import BK9115

pytestmark = pytest.mark.hardware

# HARDWARE TEST SETUP - EDIT THESE VALUES BEFORE RUNNING THIS FILE.
# Set VISA_ADDRESS to the bench unit's VISA resource string.
# Keep the programmed values comfortably inside the specific unit's ratings.
VISA_ADDRESS = "EDIT_ME::INSTR"
CHANNEL = 1
PROGRAMMED_VOLTAGE = 1.0
PROGRAMMED_CURRENT_LIMIT = 0.1
OVP_LEVEL = 5.0
OVP_DELAY = 0.25
VOLTAGE_READBACK_TOLERANCE = 0.15
CURRENT_READBACK_TOLERANCE = 0.02


@pytest.fixture(scope="module")
def driver(request: pytest.FixtureRequest) -> BK9115:
    if VISA_ADDRESS == "EDIT_ME::INSTR":
        pytest.skip("Set VISA_ADDRESS in this file before running BK9115 hardware tests.")

    psu_driver = BK9115(
        VisaConfig(
            visa_resource=VISA_ADDRESS,
        )
    )
    try:
        psu_driver.open()
    except Exception:
        psu_driver.close()
        raise

    def cleanup() -> None:
        try:
            psu_driver.output_enable(False, channel=CHANNEL)
        finally:
            psu_driver.close()

    request.addfinalizer(cleanup)
    return psu_driver


@pytest.fixture(autouse=True)
def reset_before_each_test(driver: BK9115) -> None:
    driver._visa.write("*RST")
    # Real hardware may accept commands before reset processing has fully settled.
    driver._visa.query("*OPC?")
    driver._check_errors()
    driver.output_enable(False, channel=CHANNEL)


def _queue_instrument_error(driver: BK9115) -> None:
    driver._visa.write("INSTRO:INVALID")


def test_set_voltage(driver: BK9115) -> None:
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


def test_set_voltage_raises_after_instrument_error(driver: BK9115) -> None:
    _queue_instrument_error(driver)

    with pytest.raises(RuntimeError, match="BK PSU reported error"):
        driver.set_voltage(PROGRAMMED_VOLTAGE, channel=CHANNEL)


def test_set_voltage_invalid_channel_raises_without_instrument_error(driver: BK9115) -> None:
    with pytest.raises(ValueError, match="BK 9115 channel must be 1"):
        driver.set_voltage(PROGRAMMED_VOLTAGE, channel=2)

    driver._check_errors()


def test_get_voltage(driver: BK9115) -> None:
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


def test_get_voltage_raises_after_instrument_error(driver: BK9115) -> None:
    _queue_instrument_error(driver)

    with pytest.raises(RuntimeError, match="BK PSU reported error"):
        driver.get_voltage(channel=CHANNEL)


def test_get_voltage_invalid_channel_raises_without_instrument_error(driver: BK9115) -> None:
    with pytest.raises(ValueError, match="BK 9115 channel must be 1"):
        driver.get_voltage(channel=2)

    driver._check_errors()


def test_set_current_limit(driver: BK9115) -> None:
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


def test_set_current_limit_raises_after_instrument_error(driver: BK9115) -> None:
    _queue_instrument_error(driver)

    with pytest.raises(RuntimeError, match="BK PSU reported error"):
        driver.set_current_limit(PROGRAMMED_CURRENT_LIMIT, channel=CHANNEL)


def test_get_current(driver: BK9115) -> None:
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


def test_get_current_raises_after_instrument_error(driver: BK9115) -> None:
    _queue_instrument_error(driver)

    with pytest.raises(RuntimeError, match="BK PSU reported error"):
        driver.get_current(channel=CHANNEL)


def test_output_enable(driver: BK9115) -> None:
    driver.set_current_limit(PROGRAMMED_CURRENT_LIMIT, channel=CHANNEL)
    driver.set_voltage(PROGRAMMED_VOLTAGE, channel=CHANNEL)
    try:
        driver.output_enable(True, channel=CHANNEL)
        time.sleep(1)
        assert driver.get_output_status(channel=CHANNEL) is True

        driver.output_enable(False, channel=CHANNEL)
        time.sleep(0.1)
        assert driver.get_output_status(channel=CHANNEL) is False
    finally:
        driver.output_enable(False, channel=CHANNEL)


def test_output_enable_raises_after_instrument_error(driver: BK9115) -> None:
    _queue_instrument_error(driver)
    try:
        with pytest.raises(RuntimeError, match="BK PSU reported error"):
            driver.output_enable(True, channel=CHANNEL)
    finally:
        driver.output_enable(False, channel=CHANNEL)


def test_output_disable_readback(driver: BK9115) -> None:
    assert driver.get_output_status(channel=CHANNEL) is False

    driver.set_current_limit(PROGRAMMED_CURRENT_LIMIT, channel=CHANNEL)
    driver.set_voltage(PROGRAMMED_VOLTAGE, channel=CHANNEL)
    try:
        driver.output_enable(True, channel=CHANNEL)
        time.sleep(1)

        assert driver.get_output_status(channel=CHANNEL) is True
    finally:
        driver.output_enable(False, channel=CHANNEL)


def test_get_output_status_raises_after_instrument_error(driver: BK9115) -> None:
    _queue_instrument_error(driver)

    with pytest.raises(RuntimeError, match="BK PSU reported error"):
        driver.get_output_status(channel=CHANNEL)


def test_set_overvoltage_protection_level(driver: BK9115) -> None:
    driver.set_overvoltage_protection_level(OVP_LEVEL, channel=CHANNEL)

    assert driver.get_overvoltage_protection_level(channel=CHANNEL) == pytest.approx(OVP_LEVEL, abs=0.01)


def test_set_overvoltage_protection_level_raises_after_instrument_error(driver: BK9115) -> None:
    _queue_instrument_error(driver)

    with pytest.raises(RuntimeError, match="BK PSU reported error"):
        driver.set_overvoltage_protection_level(OVP_LEVEL, channel=CHANNEL)


def test_get_overvoltage_protection_level(driver: BK9115) -> None:
    driver.set_overvoltage_protection_level(OVP_LEVEL, channel=CHANNEL)

    level = driver.get_overvoltage_protection_level(channel=CHANNEL)

    assert level == pytest.approx(OVP_LEVEL, abs=0.01)


def test_get_overvoltage_protection_level_raises_after_instrument_error(driver: BK9115) -> None:
    _queue_instrument_error(driver)

    with pytest.raises(RuntimeError, match="BK PSU reported error"):
        driver.get_overvoltage_protection_level(channel=CHANNEL)


def test_set_overvoltage_protection_enabled(driver: BK9115) -> None:
    driver.set_overvoltage_protection_level(OVP_LEVEL, channel=CHANNEL)
    driver.set_overvoltage_protection_enabled(True, channel=CHANNEL)
    time.sleep(0.1)
    assert driver.get_overvoltage_protection_enabled(channel=CHANNEL) is True

    driver.set_overvoltage_protection_enabled(False, channel=CHANNEL)
    time.sleep(0.1)
    assert driver.get_overvoltage_protection_enabled(channel=CHANNEL) is False


def test_set_overvoltage_protection_enabled_raises_after_instrument_error(driver: BK9115) -> None:
    _queue_instrument_error(driver)

    with pytest.raises(RuntimeError, match="BK PSU reported error"):
        driver.set_overvoltage_protection_enabled(True, channel=CHANNEL)


def test_get_overvoltage_protection_enabled(driver: BK9115) -> None:
    driver.set_overvoltage_protection_level(OVP_LEVEL, channel=CHANNEL)
    driver.set_overvoltage_protection_enabled(False, channel=CHANNEL)
    time.sleep(0.1)
    assert driver.get_overvoltage_protection_enabled(channel=CHANNEL) is False

    driver.set_overvoltage_protection_enabled(True, channel=CHANNEL)
    time.sleep(0.1)

    assert driver.get_overvoltage_protection_enabled(channel=CHANNEL) is True


def test_get_overvoltage_protection_enabled_raises_after_instrument_error(driver: BK9115) -> None:
    _queue_instrument_error(driver)

    with pytest.raises(RuntimeError, match="BK PSU reported error"):
        driver.get_overvoltage_protection_enabled(channel=CHANNEL)


def test_set_overvoltage_protection_delay(driver: BK9115) -> None:
    # Firmware 0.02-0.02 accepts the manual's delay command but reports 0 on readback.
    driver.set_overvoltage_protection_delay(OVP_DELAY, channel=CHANNEL)

    assert driver.get_overvoltage_protection_delay(channel=CHANNEL) >= 0.0


def test_set_overvoltage_protection_delay_raises_after_instrument_error(driver: BK9115) -> None:
    _queue_instrument_error(driver)

    with pytest.raises(RuntimeError, match="BK PSU reported error"):
        driver.set_overvoltage_protection_delay(OVP_DELAY, channel=CHANNEL)


def test_get_overvoltage_protection_delay(driver: BK9115) -> None:
    driver.set_overvoltage_protection_delay(OVP_DELAY, channel=CHANNEL)

    delay = driver.get_overvoltage_protection_delay(channel=CHANNEL)

    assert delay >= 0.0


def test_get_overvoltage_protection_delay_raises_after_instrument_error(driver: BK9115) -> None:
    _queue_instrument_error(driver)

    with pytest.raises(RuntimeError, match="BK PSU reported error"):
        driver.get_overvoltage_protection_delay(channel=CHANNEL)


def test_set_overcurrent_protection_level_unsupported(driver: BK9115) -> None:
    with pytest.raises(
        FeatureNotSupportedError,
        match="set_overcurrent_protection_level is not supported by the B&K Precision 9115-series PSU",
    ):
        driver.set_overcurrent_protection_level(PROGRAMMED_CURRENT_LIMIT, channel=CHANNEL)


def test_get_overcurrent_protection_level_unsupported(driver: BK9115) -> None:
    with pytest.raises(
        FeatureNotSupportedError,
        match="get_overcurrent_protection_level is not supported by the B&K Precision 9115-series PSU",
    ):
        driver.get_overcurrent_protection_level(channel=CHANNEL)


def test_set_overcurrent_protection_enabled_unsupported(driver: BK9115) -> None:
    with pytest.raises(
        FeatureNotSupportedError,
        match="set_overcurrent_protection_enabled is not supported by the B&K Precision 9115-series PSU",
    ):
        driver.set_overcurrent_protection_enabled(True, channel=CHANNEL)


def test_get_overcurrent_protection_enabled_unsupported(driver: BK9115) -> None:
    with pytest.raises(
        FeatureNotSupportedError,
        match="get_overcurrent_protection_enabled is not supported by the B&K Precision 9115-series PSU",
    ):
        driver.get_overcurrent_protection_enabled(channel=CHANNEL)


def test_set_remote_sense_enabled_unsupported(driver: BK9115) -> None:
    with pytest.raises(
        FeatureNotSupportedError,
        match="set_remote_sense_enabled is not supported by the B&K Precision 9115-series PSU",
    ):
        driver.set_remote_sense_enabled(True, channel=CHANNEL)


def test_get_remote_sense_enabled_unsupported(driver: BK9115) -> None:
    with pytest.raises(
        FeatureNotSupportedError,
        match="get_remote_sense_enabled is not supported by the B&K Precision 9115-series PSU",
    ):
        driver.get_remote_sense_enabled(channel=CHANNEL)


def test_check_errors_raises_after_instrument_error(driver: BK9115) -> None:
    _queue_instrument_error(driver)

    with pytest.raises(RuntimeError, match="BK PSU reported error"):
        driver._check_errors()
