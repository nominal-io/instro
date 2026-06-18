"""Optional Rigol DP800-series hardware smoke tests."""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from instro.lib.exceptions import FeatureNotSupportedError
from instro.lib.transports import VisaConfig
from instro.psu.drivers.rigol_dp800 import RigolDP800

pytestmark = pytest.mark.hardware

# HARDWARE TEST SETUP - EDIT THESE VALUES BEFORE RUNNING THIS FILE.
# Set VISA_RESOURCE to the bench unit's VISA resource string. Set VISA_BACKEND to
# "@ivi" or "" for the system VISA library, or "@py" for pyvisa-py.
# Keep the programmed values comfortably inside the specific unit's ratings.
# For DP811/DP813, set CHANNELS to one channel with supports_remote_sense=True.
# For DP821/DP822, keep channel 2 supports_remote_sense=True and remove channel 3.
VISA_RESOURCE = "TCPIP0::IP_ADDRESS::INSTR"
VISA_BACKEND = "@ivi"
INVALID_CHANNEL = 4


@dataclass(frozen=True)
class ChannelConfig:
    channel: int
    programmed_voltage: float
    programmed_current_limit: float
    ovp_level: float
    ocp_level: float
    voltage_readback_tolerance: float
    current_readback_tolerance: float
    supports_remote_sense: bool


CHANNELS = [
    ChannelConfig(
        channel=1,
        programmed_voltage=1.0,
        programmed_current_limit=0.1,
        ovp_level=5.0,
        ocp_level=0.5,
        voltage_readback_tolerance=0.15,
        current_readback_tolerance=0.02,
        supports_remote_sense=False,
    ),
    ChannelConfig(
        channel=2,
        programmed_voltage=1.0,
        programmed_current_limit=0.1,
        ovp_level=5.0,
        ocp_level=0.5,
        voltage_readback_tolerance=0.15,
        current_readback_tolerance=0.02,
        supports_remote_sense=False,
    ),
    ChannelConfig(
        channel=3,
        programmed_voltage=1.0,
        programmed_current_limit=0.1,
        ovp_level=5.0,
        ocp_level=0.5,
        voltage_readback_tolerance=0.15,
        current_readback_tolerance=0.02,
        supports_remote_sense=False,
    ),
]


def _shutdown_outputs(driver: RigolDP800) -> None:
    for channel_config in CHANNELS:
        driver.output_enable(False, channel=channel_config.channel)


def _reset_driver(driver: RigolDP800) -> None:
    driver._visa.write("*CLS")
    driver._visa.write("*RST")
    time.sleep(0.25)
    driver._visa.write("*CLS")
    driver._check_errors()


@pytest.fixture(scope="module")
def driver() -> Iterator[RigolDP800]:
    psu_driver = RigolDP800(
        VisaConfig(
            visa_resource=VISA_RESOURCE,
            visa_backend=VISA_BACKEND,
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
def reset_before_each_test(driver: RigolDP800) -> None:
    _reset_driver(driver)


def test_query_status(driver: RigolDP800) -> None:
    status = driver.query_status()

    assert set(status) == {f"ch{channel_config.channel}" for channel_config in CHANNELS}
    for channel_config in CHANNELS:
        channel_status = status[f"ch{channel_config.channel}"]
        assert channel_status["enable"] is False
        assert channel_status["mode"] in {"off", "CC", "CV", "UNREGULATED"}
        assert isinstance(channel_status["OVP"], bool)
        assert isinstance(channel_status["OCP"], bool)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_voltage(driver: RigolDP800, channel_config: ChannelConfig) -> None:
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
def test_get_voltage(driver: RigolDP800, channel_config: ChannelConfig) -> None:
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
def test_set_current_limit(driver: RigolDP800, channel_config: ChannelConfig) -> None:
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
def test_get_current(driver: RigolDP800, channel_config: ChannelConfig) -> None:
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
def test_output_enable(driver: RigolDP800, channel_config: ChannelConfig) -> None:
    driver.set_current_limit(channel_config.programmed_current_limit, channel=channel_config.channel)
    driver.set_voltage(channel_config.programmed_voltage, channel=channel_config.channel)
    try:
        driver.output_enable(True, channel=channel_config.channel)
        assert driver.get_output_status(channel=channel_config.channel) is True

        driver.output_enable(False, channel=channel_config.channel)
        assert driver.get_output_status(channel=channel_config.channel) is False
    finally:
        driver.output_enable(False, channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_output_status(driver: RigolDP800, channel_config: ChannelConfig) -> None:
    assert driver.get_output_status(channel=channel_config.channel) is False

    driver.set_current_limit(channel_config.programmed_current_limit, channel=channel_config.channel)
    driver.set_voltage(channel_config.programmed_voltage, channel=channel_config.channel)
    try:
        driver.output_enable(True, channel=channel_config.channel)

        assert driver.get_output_status(channel=channel_config.channel) is True
    finally:
        driver.output_enable(False, channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_overvoltage_protection_level(driver: RigolDP800, channel_config: ChannelConfig) -> None:
    driver.set_overvoltage_protection_level(channel_config.ovp_level, channel=channel_config.channel)

    assert driver.get_overvoltage_protection_level(channel=channel_config.channel) == pytest.approx(
        channel_config.ovp_level
    )


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_overvoltage_protection_level(driver: RigolDP800, channel_config: ChannelConfig) -> None:
    driver.set_overvoltage_protection_level(channel_config.ovp_level, channel=channel_config.channel)

    level = driver.get_overvoltage_protection_level(channel=channel_config.channel)

    assert level == pytest.approx(channel_config.ovp_level)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_overvoltage_protection_enabled(driver: RigolDP800, channel_config: ChannelConfig) -> None:
    driver.set_overvoltage_protection_level(channel_config.ovp_level, channel=channel_config.channel)
    driver.set_overvoltage_protection_enabled(True, channel=channel_config.channel)
    assert driver.get_overvoltage_protection_enabled(channel=channel_config.channel) is True

    driver.set_overvoltage_protection_enabled(False, channel=channel_config.channel)
    assert driver.get_overvoltage_protection_enabled(channel=channel_config.channel) is False


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_overvoltage_protection_enabled(driver: RigolDP800, channel_config: ChannelConfig) -> None:
    driver.set_overvoltage_protection_level(channel_config.ovp_level, channel=channel_config.channel)
    driver.set_overvoltage_protection_enabled(False, channel=channel_config.channel)
    assert driver.get_overvoltage_protection_enabled(channel=channel_config.channel) is False

    driver.set_overvoltage_protection_enabled(True, channel=channel_config.channel)

    assert driver.get_overvoltage_protection_enabled(channel=channel_config.channel) is True


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_overcurrent_protection_level(driver: RigolDP800, channel_config: ChannelConfig) -> None:
    driver.set_overcurrent_protection_level(channel_config.ocp_level, channel=channel_config.channel)

    assert driver.get_overcurrent_protection_level(channel=channel_config.channel) == pytest.approx(
        channel_config.ocp_level
    )


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_overcurrent_protection_level(driver: RigolDP800, channel_config: ChannelConfig) -> None:
    driver.set_overcurrent_protection_level(channel_config.ocp_level, channel=channel_config.channel)

    level = driver.get_overcurrent_protection_level(channel=channel_config.channel)

    assert level == pytest.approx(channel_config.ocp_level)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_overcurrent_protection_enabled(driver: RigolDP800, channel_config: ChannelConfig) -> None:
    driver.set_overcurrent_protection_level(channel_config.ocp_level, channel=channel_config.channel)
    driver.set_overcurrent_protection_enabled(True, channel=channel_config.channel)
    assert driver.get_overcurrent_protection_enabled(channel=channel_config.channel) is True

    driver.set_overcurrent_protection_enabled(False, channel=channel_config.channel)
    assert driver.get_overcurrent_protection_enabled(channel=channel_config.channel) is False


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_overcurrent_protection_enabled(driver: RigolDP800, channel_config: ChannelConfig) -> None:
    driver.set_overcurrent_protection_level(channel_config.ocp_level, channel=channel_config.channel)
    driver.set_overcurrent_protection_enabled(False, channel=channel_config.channel)
    assert driver.get_overcurrent_protection_enabled(channel=channel_config.channel) is False

    driver.set_overcurrent_protection_enabled(True, channel=channel_config.channel)

    assert driver.get_overcurrent_protection_enabled(channel=channel_config.channel) is True


def test_set_overvoltage_protection_delay_unsupported(driver: RigolDP800) -> None:
    with pytest.raises(FeatureNotSupportedError, match="OVP delay command"):
        driver.set_overvoltage_protection_delay(0.1, channel=CHANNELS[0].channel)


def test_get_overvoltage_protection_delay_unsupported(driver: RigolDP800) -> None:
    with pytest.raises(FeatureNotSupportedError, match="OVP delay query"):
        driver.get_overvoltage_protection_delay(channel=CHANNELS[0].channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_remote_sense_enabled(driver: RigolDP800, channel_config: ChannelConfig) -> None:
    if not channel_config.supports_remote_sense:
        with pytest.raises(RuntimeError, match="Rigol DP800-series PSU reported error"):
            driver.set_remote_sense_enabled(True, channel=channel_config.channel)
        return

    try:
        driver.set_remote_sense_enabled(True, channel=channel_config.channel)
        assert driver.get_remote_sense_enabled(channel=channel_config.channel) is True
    finally:
        driver.set_remote_sense_enabled(False, channel=channel_config.channel)

    assert driver.get_remote_sense_enabled(channel=channel_config.channel) is False


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_remote_sense_enabled(driver: RigolDP800, channel_config: ChannelConfig) -> None:
    if not channel_config.supports_remote_sense:
        with pytest.raises(FeatureNotSupportedError, match="remote sense is not supported"):
            driver.get_remote_sense_enabled(channel=channel_config.channel)
        return

    driver.set_remote_sense_enabled(False, channel=channel_config.channel)
    assert driver.get_remote_sense_enabled(channel=channel_config.channel) is False

    try:
        driver.set_remote_sense_enabled(True, channel=channel_config.channel)

        assert driver.get_remote_sense_enabled(channel=channel_config.channel) is True
    finally:
        driver.set_remote_sense_enabled(False, channel=channel_config.channel)


def test_check_errors_raises_after_instrument_error(driver: RigolDP800) -> None:
    driver._visa.write("INSTRO:INVALID")

    with pytest.raises(RuntimeError, match="Rigol DP800-series PSU reported error"):
        driver._check_errors()


def test_set_voltage_out_of_range_raises_value_error(driver: RigolDP800) -> None:
    with pytest.raises(ValueError, match="out of range"):
        driver.set_voltage(10_000.0, channel=CHANNELS[0].channel)


def test_set_current_limit_out_of_range_raises_value_error(driver: RigolDP800) -> None:
    with pytest.raises(ValueError, match="out of range"):
        driver.set_current_limit(10_000.0, channel=CHANNELS[0].channel)


def test_set_overvoltage_protection_level_out_of_range_raises_value_error(driver: RigolDP800) -> None:
    with pytest.raises(ValueError, match="out of range"):
        driver.set_overvoltage_protection_level(10_000.0, channel=CHANNELS[0].channel)


def test_set_overcurrent_protection_level_out_of_range_raises_value_error(driver: RigolDP800) -> None:
    with pytest.raises(ValueError, match="out of range"):
        driver.set_overcurrent_protection_level(10_000.0, channel=CHANNELS[0].channel)


def test_invalid_channel_raises_instrument_error(driver: RigolDP800) -> None:
    try:
        with pytest.raises(RuntimeError, match="Rigol DP800-series PSU reported error"):
            driver.set_voltage(1.0, channel=INVALID_CHANNEL)
    finally:
        driver._visa.write("*CLS")
