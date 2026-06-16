"""Optional B&K Precision 914X-series hardware smoke tests."""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from instro.lib.exceptions import FeatureNotSupportedError
from instro.lib.transports import VisaConfig
from instro.psu.drivers.bk_914x import BK914X

pytestmark = pytest.mark.hardware

# HARDWARE TEST SETUP - EDIT THESE VALUES BEFORE RUNNING THIS FILE.
# Set VISA_RESOURCE to the bench unit's VISA resource string. Set VISA_BACKEND
# to "@ivi" for NI-VISA or Keysight IO Libraries, or "@py" for pyvisa-py.
# Keep the programmed values comfortably inside the specific unit's ratings.
VISA_RESOURCE = "TCPIP0::IP_ADDRESS_HERE::INSTR"


@dataclass(frozen=True)
class ChannelConfig:
    channel: int
    programmed_voltage: float
    programmed_current_limit: float
    ovp_level: float
    ocp_level: float
    voltage_readback_tolerance: float
    current_readback_tolerance: float


CHANNELS = [
    ChannelConfig(
        channel=1,
        programmed_voltage=1.0,
        programmed_current_limit=0.1,
        ovp_level=5.0,
        ocp_level=0.5,
        voltage_readback_tolerance=0.15,
        current_readback_tolerance=0.02,
    ),
    ChannelConfig(
        channel=2,
        programmed_voltage=1.0,
        programmed_current_limit=0.1,
        ovp_level=5.0,
        ocp_level=0.5,
        voltage_readback_tolerance=0.15,
        current_readback_tolerance=0.02,
    ),
    ChannelConfig(
        channel=3,
        programmed_voltage=1.0,
        programmed_current_limit=0.1,
        ovp_level=5.0,
        ocp_level=0.5,
        voltage_readback_tolerance=0.15,
        current_readback_tolerance=0.02,
    ),
]


def _shutdown_outputs(driver: BK914X) -> None:
    for channel_config in CHANNELS:
        driver.output_enable(False, channel=channel_config.channel)


def _clear_driver_error_state(driver: BK914X) -> None:
    driver._visa.write("*CLS")
    driver._active_channel = None


@pytest.fixture(scope="module")
def driver() -> Iterator[BK914X]:
    psu_driver = BK914X(
        VisaConfig(
            visa_resource=VISA_RESOURCE,
        )
    )
    opened = False
    try:
        psu_driver.open()
        opened = True
        yield psu_driver
    finally:
        if opened:
            _shutdown_outputs(psu_driver)
        psu_driver.close()


@pytest.fixture(autouse=True)
def reset_before_each_test(driver: BK914X) -> None:
    driver._visa.write("*RST")
    driver._active_channel = None
    driver._check_errors()


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_voltage(driver: BK914X, channel_config: ChannelConfig) -> None:
    driver.set_current_limit(channel_config.programmed_current_limit, channel=channel_config.channel)
    driver.set_voltage(channel_config.programmed_voltage, channel=channel_config.channel)
    try:
        driver.output_enable(True, channel=channel_config.channel)
        time.sleep(1)

        assert driver.get_voltage(channel=channel_config.channel) == pytest.approx(
            channel_config.programmed_voltage,
            abs=channel_config.voltage_readback_tolerance,
        )
    finally:
        driver.output_enable(False, channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_voltage(driver: BK914X, channel_config: ChannelConfig) -> None:
    driver.set_current_limit(channel_config.programmed_current_limit, channel=channel_config.channel)
    driver.set_voltage(channel_config.programmed_voltage, channel=channel_config.channel)
    try:
        driver.output_enable(True, channel=channel_config.channel)
        time.sleep(1)

        voltage = driver.get_voltage(channel=channel_config.channel)

        assert voltage == pytest.approx(
            channel_config.programmed_voltage,
            abs=channel_config.voltage_readback_tolerance,
        )
    finally:
        driver.output_enable(False, channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_current_limit(driver: BK914X, channel_config: ChannelConfig) -> None:
    driver.set_current_limit(channel_config.programmed_current_limit, channel=channel_config.channel)
    driver.set_voltage(channel_config.programmed_voltage, channel=channel_config.channel)
    try:
        driver.output_enable(True, channel=channel_config.channel)
        time.sleep(1)

        assert driver.get_current(channel=channel_config.channel) == pytest.approx(
            0.0,
            abs=channel_config.current_readback_tolerance,
        )
    finally:
        driver.output_enable(False, channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_current(driver: BK914X, channel_config: ChannelConfig) -> None:
    driver.set_current_limit(channel_config.programmed_current_limit, channel=channel_config.channel)
    driver.set_voltage(channel_config.programmed_voltage, channel=channel_config.channel)
    try:
        driver.output_enable(True, channel=channel_config.channel)
        time.sleep(1)

        current = driver.get_current(channel=channel_config.channel)

        assert current == pytest.approx(
            0.0,
            abs=channel_config.current_readback_tolerance,
        )
    finally:
        driver.output_enable(False, channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_output_enable(driver: BK914X, channel_config: ChannelConfig) -> None:
    driver.output_enable(True, channel=channel_config.channel)
    assert driver.get_output_status(channel=channel_config.channel) is True

    driver.output_enable(False, channel=channel_config.channel)
    assert driver.get_output_status(channel=channel_config.channel) is False


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_output_status(driver: BK914X, channel_config: ChannelConfig) -> None:
    assert driver.get_output_status(channel=channel_config.channel) is False

    driver.output_enable(True, channel=channel_config.channel)

    assert driver.get_output_status(channel=channel_config.channel) is True


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_overvoltage_protection_level(driver: BK914X, channel_config: ChannelConfig) -> None:
    driver.set_overvoltage_protection_level(channel_config.ovp_level, channel=channel_config.channel)

    assert driver.get_overvoltage_protection_level(channel=channel_config.channel) == pytest.approx(
        channel_config.ovp_level
    )


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_overvoltage_protection_level(driver: BK914X, channel_config: ChannelConfig) -> None:
    driver.set_overvoltage_protection_level(channel_config.ovp_level, channel=channel_config.channel)

    level = driver.get_overvoltage_protection_level(channel=channel_config.channel)

    assert level == pytest.approx(channel_config.ovp_level)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_overvoltage_protection_enabled_unsupported(driver: BK914X, channel_config: ChannelConfig) -> None:
    with pytest.raises(
        FeatureNotSupportedError,
        match="set_overvoltage_protection_enabled is not supported by the B&K Precision 914X-series PSU",
    ):
        driver.set_overvoltage_protection_enabled(False, channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_overvoltage_protection_enabled_unsupported(driver: BK914X, channel_config: ChannelConfig) -> None:
    with pytest.raises(
        FeatureNotSupportedError,
        match="get_overvoltage_protection_enabled is not supported by the B&K Precision 914X-series PSU",
    ):
        driver.get_overvoltage_protection_enabled(channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_overvoltage_protection_delay_unsupported(driver: BK914X, channel_config: ChannelConfig) -> None:
    with pytest.raises(
        FeatureNotSupportedError,
        match="set_overvoltage_protection_delay is not supported by the B&K Precision 914X-series PSU",
    ):
        driver.set_overvoltage_protection_delay(0.1, channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_overvoltage_protection_delay_unsupported(driver: BK914X, channel_config: ChannelConfig) -> None:
    with pytest.raises(
        FeatureNotSupportedError,
        match="get_overvoltage_protection_delay is not supported by the B&K Precision 914X-series PSU",
    ):
        driver.get_overvoltage_protection_delay(channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_overcurrent_protection_level(driver: BK914X, channel_config: ChannelConfig) -> None:
    driver.set_overcurrent_protection_level(channel_config.ocp_level, channel=channel_config.channel)

    assert driver.get_overcurrent_protection_level(channel=channel_config.channel) == pytest.approx(
        channel_config.ocp_level
    )


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_overcurrent_protection_level(driver: BK914X, channel_config: ChannelConfig) -> None:
    driver.set_overcurrent_protection_level(channel_config.ocp_level, channel=channel_config.channel)

    level = driver.get_overcurrent_protection_level(channel=channel_config.channel)

    assert level == pytest.approx(channel_config.ocp_level)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_overcurrent_protection_enabled(driver: BK914X, channel_config: ChannelConfig) -> None:
    driver.set_overcurrent_protection_enabled(True, channel=channel_config.channel)
    assert driver.get_overcurrent_protection_enabled(channel=channel_config.channel) is True

    driver.set_overcurrent_protection_enabled(False, channel=channel_config.channel)
    assert driver.get_overcurrent_protection_enabled(channel=channel_config.channel) is False


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_overcurrent_protection_enabled(driver: BK914X, channel_config: ChannelConfig) -> None:
    driver.set_overcurrent_protection_enabled(False, channel=channel_config.channel)
    assert driver.get_overcurrent_protection_enabled(channel=channel_config.channel) is False

    driver.set_overcurrent_protection_enabled(True, channel=channel_config.channel)

    assert driver.get_overcurrent_protection_enabled(channel=channel_config.channel) is True


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_remote_sense_enabled(driver: BK914X, channel_config: ChannelConfig) -> None:
    try:
        driver.set_remote_sense_enabled(True, channel=channel_config.channel)
        assert driver.get_remote_sense_enabled(channel=channel_config.channel) is True
    finally:
        driver.set_remote_sense_enabled(False, channel=channel_config.channel)

    assert driver.get_remote_sense_enabled(channel=channel_config.channel) is False


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_remote_sense_enabled(driver: BK914X, channel_config: ChannelConfig) -> None:
    driver.set_remote_sense_enabled(False, channel=channel_config.channel)
    assert driver.get_remote_sense_enabled(channel=channel_config.channel) is False

    try:
        driver.set_remote_sense_enabled(True, channel=channel_config.channel)

        assert driver.get_remote_sense_enabled(channel=channel_config.channel) is True
    finally:
        driver.set_remote_sense_enabled(False, channel=channel_config.channel)


def test_check_errors_raises_after_instrument_error(driver: BK914X) -> None:
    driver._visa.write("INSTRO:INVALID")

    with pytest.raises(RuntimeError, match="BK914X PSU reported error"):
        driver._check_errors()


def test_set_voltage_out_of_range_raises_instrument_error(driver: BK914X) -> None:
    try:
        with pytest.raises(RuntimeError, match="BK914X PSU reported error"):
            driver.set_voltage(10_000.0, channel=1)
    finally:
        _clear_driver_error_state(driver)


def test_set_current_limit_out_of_range_raises_instrument_error(driver: BK914X) -> None:
    try:
        with pytest.raises(RuntimeError, match="BK914X PSU reported error"):
            driver.set_current_limit(10_000.0, channel=1)
    finally:
        _clear_driver_error_state(driver)


def test_set_overvoltage_protection_level_out_of_range_raises_instrument_error(driver: BK914X) -> None:
    try:
        with pytest.raises(RuntimeError, match="BK914X PSU reported error"):
            driver.set_overvoltage_protection_level(10_000.0, channel=1)
    finally:
        _clear_driver_error_state(driver)


def test_set_overcurrent_protection_level_out_of_range_raises_instrument_error(driver: BK914X) -> None:
    try:
        with pytest.raises(RuntimeError, match="BK914X PSU reported error"):
            driver.set_overcurrent_protection_level(10_000.0, channel=1)
    finally:
        _clear_driver_error_state(driver)


def test_get_voltage_invalid_channel_raises_instrument_error(driver: BK914X) -> None:
    try:
        with pytest.raises(RuntimeError, match="BK914X PSU reported error"):
            driver.get_voltage(channel=4)
    finally:
        _clear_driver_error_state(driver)
