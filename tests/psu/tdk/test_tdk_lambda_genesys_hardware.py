"""Optional TDK Lambda Genesys-family hardware smoke tests."""

from __future__ import annotations

import time

import pytest

from instro.lib.exceptions import FeatureNotSupportedError
from instro.lib.transports import VisaConfig
from instro.psu.drivers.tdk_lambda_genesys import TDKLambdaGenesys

pytestmark = pytest.mark.hardware

# HARDWARE TEST SETUP - EDIT THESE VALUES BEFORE RUNNING THIS FILE.
# Set VISA_ADDRESSES to the bench unit's connected VISA resource strings.
# Remove any interface that is not connected before running this file.
# Use this driver for TDK Genesys-family supplies and white-label
# Agilent/Keysight N5700-series supplies.
# Keep the programmed values comfortably inside the specific unit's ratings.
VISA_ADDRESSES = [
    pytest.param("TCPIP0::169.254.57.0::INSTR", id="lan"),
    pytest.param("USB0::2391::41991::US17D5493P::0::INSTR", id="usb"),
]
CHANNEL = 1
PROGRAMMED_VOLTAGE = 1.0
PROGRAMMED_CURRENT_LIMIT = 0.1
OVP_LEVEL = 5.0
VOLTAGE_READBACK_TOLERANCE = 0.15
CURRENT_READBACK_TOLERANCE = 0.01


@pytest.fixture(scope="module", params=VISA_ADDRESSES)
def driver(request: pytest.FixtureRequest) -> TDKLambdaGenesys:
    visa_address = str(request.param)
    psu_driver = TDKLambdaGenesys(
        VisaConfig(
            visa_resource=visa_address,
        )
    )
    try:
        psu_driver.open()
    except Exception:
        psu_driver.close()
        raise

    request.addfinalizer(psu_driver.close)
    return psu_driver


def _reset_driver(driver: TDKLambdaGenesys) -> None:
    driver._visa.write("*CLS")
    driver._visa.write("*RST")
    time.sleep(0.25)
    driver._visa.write("*CLS")
    driver._check_errors()


@pytest.fixture(autouse=True)
def reset_before_each_test(driver: TDKLambdaGenesys) -> None:
    _reset_driver(driver)


def test_set_voltage(driver: TDKLambdaGenesys) -> None:
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


def test_set_voltage_rejects_negative_value(driver: TDKLambdaGenesys) -> None:
    try:
        with pytest.raises(RuntimeError, match="TDK Lambda Genesys-family PSU reported error"):
            driver.set_voltage(-1.0, channel=CHANNEL)
    finally:
        _reset_driver(driver)


def test_set_voltage_rejects_value_at_ovp_limit(driver: TDKLambdaGenesys) -> None:
    driver.set_overvoltage_protection_level(OVP_LEVEL, channel=CHANNEL)

    try:
        with pytest.raises(RuntimeError, match="TDK Lambda Genesys-family PSU reported error"):
            driver.set_voltage(OVP_LEVEL, channel=CHANNEL)
    finally:
        _reset_driver(driver)


def test_get_voltage(driver: TDKLambdaGenesys) -> None:
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


def test_set_current_limit(driver: TDKLambdaGenesys) -> None:
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


def test_set_current_limit_rejects_negative_value(driver: TDKLambdaGenesys) -> None:
    try:
        with pytest.raises(RuntimeError, match="TDK Lambda Genesys-family PSU reported error"):
            driver.set_current_limit(-1.0, channel=CHANNEL)
    finally:
        _reset_driver(driver)


def test_get_current(driver: TDKLambdaGenesys) -> None:
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


def test_output_enable(driver: TDKLambdaGenesys) -> None:
    driver.set_current_limit(PROGRAMMED_CURRENT_LIMIT, channel=CHANNEL)
    driver.set_voltage(PROGRAMMED_VOLTAGE, channel=CHANNEL)
    try:
        driver.output_enable(True, channel=CHANNEL)
        assert driver.get_output_status(channel=CHANNEL) is True

        driver.output_enable(False, channel=CHANNEL)
        assert driver.get_output_status(channel=CHANNEL) is False
    finally:
        driver.output_enable(False, channel=CHANNEL)


def test_get_output_status(driver: TDKLambdaGenesys) -> None:
    assert driver.get_output_status(channel=CHANNEL) is False

    driver.set_current_limit(PROGRAMMED_CURRENT_LIMIT, channel=CHANNEL)
    driver.set_voltage(PROGRAMMED_VOLTAGE, channel=CHANNEL)
    try:
        driver.output_enable(True, channel=CHANNEL)

        assert driver.get_output_status(channel=CHANNEL) is True
    finally:
        driver.output_enable(False, channel=CHANNEL)


def test_set_overvoltage_protection_level(driver: TDKLambdaGenesys) -> None:
    driver.set_overvoltage_protection_level(OVP_LEVEL, channel=CHANNEL)

    assert driver.get_overvoltage_protection_level(channel=CHANNEL) == pytest.approx(OVP_LEVEL)


def test_set_overvoltage_protection_level_rejects_value_at_programmed_voltage(driver: TDKLambdaGenesys) -> None:
    driver.set_voltage(PROGRAMMED_VOLTAGE, channel=CHANNEL)

    try:
        with pytest.raises(RuntimeError, match="TDK Lambda Genesys-family PSU reported error"):
            driver.set_overvoltage_protection_level(PROGRAMMED_VOLTAGE, channel=CHANNEL)
    finally:
        _reset_driver(driver)


def test_get_overvoltage_protection_level(driver: TDKLambdaGenesys) -> None:
    driver.set_overvoltage_protection_level(OVP_LEVEL, channel=CHANNEL)

    level = driver.get_overvoltage_protection_level(channel=CHANNEL)

    assert level == pytest.approx(OVP_LEVEL)


def test_set_overvoltage_protection_enabled_unsupported(driver: TDKLambdaGenesys) -> None:
    with pytest.raises(
        FeatureNotSupportedError,
        match="set_overvoltage_protection_enabled is not supported by the TDK Lambda Genesys-family PSU",
    ):
        driver.set_overvoltage_protection_enabled(True, channel=CHANNEL)


def test_get_overvoltage_protection_enabled_unsupported(driver: TDKLambdaGenesys) -> None:
    with pytest.raises(
        FeatureNotSupportedError,
        match="get_overvoltage_protection_enabled is not supported by the TDK Lambda Genesys-family PSU",
    ):
        driver.get_overvoltage_protection_enabled(channel=CHANNEL)


def test_set_overvoltage_protection_delay_unsupported(driver: TDKLambdaGenesys) -> None:
    with pytest.raises(
        FeatureNotSupportedError,
        match="set_overvoltage_protection_delay is not supported by the TDK Lambda Genesys-family PSU",
    ):
        driver.set_overvoltage_protection_delay(0.1, channel=CHANNEL)


def test_get_overvoltage_protection_delay_unsupported(driver: TDKLambdaGenesys) -> None:
    with pytest.raises(
        FeatureNotSupportedError,
        match="get_overvoltage_protection_delay is not supported by the TDK Lambda Genesys-family PSU",
    ):
        driver.get_overvoltage_protection_delay(channel=CHANNEL)


def test_set_overcurrent_protection_level_unsupported(driver: TDKLambdaGenesys) -> None:
    with pytest.raises(
        FeatureNotSupportedError,
        match="set_overcurrent_protection_level is not supported by the TDK Lambda Genesys-family PSU",
    ):
        driver.set_overcurrent_protection_level(PROGRAMMED_CURRENT_LIMIT, channel=CHANNEL)


def test_get_overcurrent_protection_level_unsupported(driver: TDKLambdaGenesys) -> None:
    with pytest.raises(
        FeatureNotSupportedError,
        match="get_overcurrent_protection_level is not supported by the TDK Lambda Genesys-family PSU",
    ):
        driver.get_overcurrent_protection_level(channel=CHANNEL)


def test_set_overcurrent_protection_enabled(driver: TDKLambdaGenesys) -> None:
    driver.set_overcurrent_protection_enabled(True, channel=CHANNEL)
    assert driver.get_overcurrent_protection_enabled(channel=CHANNEL) is True

    driver.set_overcurrent_protection_enabled(False, channel=CHANNEL)
    assert driver.get_overcurrent_protection_enabled(channel=CHANNEL) is False


def test_get_overcurrent_protection_enabled(driver: TDKLambdaGenesys) -> None:
    assert driver.get_overcurrent_protection_enabled(channel=CHANNEL) is False

    driver.set_overcurrent_protection_enabled(True, channel=CHANNEL)

    assert driver.get_overcurrent_protection_enabled(channel=CHANNEL) is True


def test_set_remote_sense_enabled_unsupported(driver: TDKLambdaGenesys) -> None:
    with pytest.raises(
        FeatureNotSupportedError,
        match="set_remote_sense_enabled is not supported by the TDK Lambda Genesys-family PSU",
    ):
        driver.set_remote_sense_enabled(True, channel=CHANNEL)


def test_get_remote_sense_enabled_unsupported(driver: TDKLambdaGenesys) -> None:
    with pytest.raises(
        FeatureNotSupportedError,
        match="get_remote_sense_enabled is not supported by the TDK Lambda Genesys-family PSU",
    ):
        driver.get_remote_sense_enabled(channel=CHANNEL)


def test_invalid_channel_raises(driver: TDKLambdaGenesys) -> None:
    with pytest.raises(ValueError, match="supports only channel 1"):
        driver.set_voltage(PROGRAMMED_VOLTAGE, channel=2)


def test_check_errors_raises_after_instrument_error(driver: TDKLambdaGenesys) -> None:
    driver._visa.write("INSTRO:INVALID")

    with pytest.raises(RuntimeError, match="TDK Lambda Genesys-family PSU reported error"):
        driver._check_errors()
