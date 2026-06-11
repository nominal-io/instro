"""Optional Siglent SPD3303X hardware smoke tests."""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from instro.lib.exceptions import FeatureNotSupportedError
from instro.lib.transports import VisaConfig
from instro.psu.drivers.siglent_spd3303 import SiglentSPD3303

pytestmark = pytest.mark.hardware

VISA_RESOURCE = "USB0::62700::5168::SPD3XJGQ806726\x00\x00\x00\x00::0::INSTR"


@dataclass(frozen=True)
class ChannelConfig:
    channel: int
    voltage_range: tuple[float, float]
    current_range: tuple[float, float]
    voltage_readback_tolerance: float
    current_readback_tolerance: float

    @staticmethod
    def relative_range_value(value_range: tuple[float, float], fraction: float) -> float:
        minimum, maximum = value_range
        return minimum + (maximum - minimum) * fraction

    def programmed_voltage(self) -> float:
        return self.relative_range_value(self.voltage_range, 0.10)

    def programmed_current_limit(self) -> float:
        return self.relative_range_value(self.current_range, 0.10)


CHANNELS = [
    ChannelConfig(
        channel=1,
        voltage_range=(0.0, 32.0),
        current_range=(0.0, 3.2),
        voltage_readback_tolerance=0.15,
        current_readback_tolerance=0.01,
    ),
    ChannelConfig(
        channel=2,
        voltage_range=(0.0, 32.0),
        current_range=(0.0, 3.2),
        voltage_readback_tolerance=0.15,
        current_readback_tolerance=0.01,
    ),
]


@pytest.fixture(scope="module")
def driver() -> Iterator[SiglentSPD3303]:
    psu_driver = SiglentSPD3303(
        VisaConfig(
            visa_resource=VISA_RESOURCE,
        )
    )
    try:
        psu_driver.open()
        yield psu_driver
    finally:
        psu_driver.close()


@pytest.fixture(autouse=True)
def reset_before_each_test(driver: SiglentSPD3303) -> None:
    for channel_config in CHANNELS:
        driver.output_enable(False, channel=channel_config.channel)
        driver.set_voltage(0.0, channel=channel_config.channel)
        driver.set_current_limit(0.0, channel=channel_config.channel)


def test_query_status(driver: SiglentSPD3303) -> None:
    status = driver.query_status()

    assert set(status) == {"ch1_mode", "ch2_mode", "psu_mode", "ch1_enable", "ch2_enable"}
    assert status["ch1_mode"] in {"CV", "CC"}
    assert status["ch2_mode"] in {"CV", "CC"}
    assert status["psu_mode"] in {"INDEPENDENT", "PARALLEL", "SERIES", "UNDEFINED"}
    assert status["ch1_enable"] is False
    assert status["ch2_enable"] is False


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_voltage(driver: SiglentSPD3303, channel_config: ChannelConfig) -> None:
    driver.set_current_limit(channel_config.programmed_current_limit(), channel=channel_config.channel)
    driver.set_voltage(channel_config.programmed_voltage(), channel=channel_config.channel)
    driver.output_enable(True, channel=channel_config.channel)
    time.sleep(1)

    assert driver.get_voltage(channel=channel_config.channel) == pytest.approx(
        channel_config.programmed_voltage(),
        abs=channel_config.voltage_readback_tolerance,
    )


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_voltage(driver: SiglentSPD3303, channel_config: ChannelConfig) -> None:
    driver.set_current_limit(channel_config.programmed_current_limit(), channel=channel_config.channel)
    driver.set_voltage(channel_config.programmed_voltage(), channel=channel_config.channel)
    driver.output_enable(True, channel=channel_config.channel)
    time.sleep(1)

    voltage = driver.get_voltage(channel=channel_config.channel)

    assert voltage == pytest.approx(
        channel_config.programmed_voltage(),
        abs=channel_config.voltage_readback_tolerance,
    )


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_current_limit(driver: SiglentSPD3303, channel_config: ChannelConfig) -> None:
    driver.set_current_limit(channel_config.programmed_current_limit(), channel=channel_config.channel)
    driver.set_voltage(channel_config.programmed_voltage(), channel=channel_config.channel)
    driver.output_enable(True, channel=channel_config.channel)
    time.sleep(1)

    assert driver.get_current(channel=channel_config.channel) == pytest.approx(
        0.0,
        abs=channel_config.current_readback_tolerance,
    )


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_current(driver: SiglentSPD3303, channel_config: ChannelConfig) -> None:
    driver.set_current_limit(channel_config.programmed_current_limit(), channel=channel_config.channel)
    driver.set_voltage(channel_config.programmed_voltage(), channel=channel_config.channel)
    driver.output_enable(True, channel=channel_config.channel)
    time.sleep(1)

    current = driver.get_current(channel=channel_config.channel)

    assert current == pytest.approx(
        0.0,
        abs=channel_config.current_readback_tolerance,
    )


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_output_enable(driver: SiglentSPD3303, channel_config: ChannelConfig) -> None:
    driver.output_enable(True, channel=channel_config.channel)
    assert driver.get_output_status(channel=channel_config.channel) is True

    driver.output_enable(False, channel=channel_config.channel)
    assert driver.get_output_status(channel=channel_config.channel) is False


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_output_status(driver: SiglentSPD3303, channel_config: ChannelConfig) -> None:
    assert driver.get_output_status(channel=channel_config.channel) is False

    driver.output_enable(True, channel=channel_config.channel)

    assert driver.get_output_status(channel=channel_config.channel) is True


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_overvoltage_protection_level_unsupported(
    driver: SiglentSPD3303,
    channel_config: ChannelConfig,
) -> None:
    with pytest.raises(FeatureNotSupportedError, match="set_overvoltage_protection_level is not supported"):
        driver.set_overvoltage_protection_level(1.0, channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_overvoltage_protection_level_unsupported(
    driver: SiglentSPD3303,
    channel_config: ChannelConfig,
) -> None:
    with pytest.raises(FeatureNotSupportedError, match="get_overvoltage_protection_level is not supported"):
        driver.get_overvoltage_protection_level(channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_overvoltage_protection_enabled_unsupported(
    driver: SiglentSPD3303,
    channel_config: ChannelConfig,
) -> None:
    with pytest.raises(FeatureNotSupportedError, match="set_overvoltage_protection_enabled is not supported"):
        driver.set_overvoltage_protection_enabled(True, channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_overvoltage_protection_enabled_unsupported(
    driver: SiglentSPD3303,
    channel_config: ChannelConfig,
) -> None:
    with pytest.raises(FeatureNotSupportedError, match="get_overvoltage_protection_enabled is not supported"):
        driver.get_overvoltage_protection_enabled(channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_overvoltage_protection_delay_unsupported(
    driver: SiglentSPD3303,
    channel_config: ChannelConfig,
) -> None:
    with pytest.raises(FeatureNotSupportedError, match="set_overvoltage_protection_delay is not supported"):
        driver.set_overvoltage_protection_delay(0.1, channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_overvoltage_protection_delay_unsupported(
    driver: SiglentSPD3303,
    channel_config: ChannelConfig,
) -> None:
    with pytest.raises(FeatureNotSupportedError, match="get_overvoltage_protection_delay is not supported"):
        driver.get_overvoltage_protection_delay(channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_overcurrent_protection_level_unsupported(
    driver: SiglentSPD3303,
    channel_config: ChannelConfig,
) -> None:
    with pytest.raises(FeatureNotSupportedError, match="set_overcurrent_protection_level is not supported"):
        driver.set_overcurrent_protection_level(0.1, channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_overcurrent_protection_level_unsupported(
    driver: SiglentSPD3303,
    channel_config: ChannelConfig,
) -> None:
    with pytest.raises(FeatureNotSupportedError, match="get_overcurrent_protection_level is not supported"):
        driver.get_overcurrent_protection_level(channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_overcurrent_protection_enabled_unsupported(
    driver: SiglentSPD3303,
    channel_config: ChannelConfig,
) -> None:
    with pytest.raises(FeatureNotSupportedError, match="set_overcurrent_protection_enabled is not supported"):
        driver.set_overcurrent_protection_enabled(True, channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_overcurrent_protection_enabled_unsupported(
    driver: SiglentSPD3303,
    channel_config: ChannelConfig,
) -> None:
    with pytest.raises(FeatureNotSupportedError, match="get_overcurrent_protection_enabled is not supported"):
        driver.get_overcurrent_protection_enabled(channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_remote_sense_enabled_unsupported(driver: SiglentSPD3303, channel_config: ChannelConfig) -> None:
    with pytest.raises(FeatureNotSupportedError, match="set_remote_sense_enabled is not supported"):
        driver.set_remote_sense_enabled(True, channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_remote_sense_enabled_unsupported(driver: SiglentSPD3303, channel_config: ChannelConfig) -> None:
    with pytest.raises(FeatureNotSupportedError, match="get_remote_sense_enabled is not supported"):
        driver.get_remote_sense_enabled(channel=channel_config.channel)


def test_set_voltage_channel_three_unsupported(driver: SiglentSPD3303) -> None:
    with pytest.raises(FeatureNotSupportedError, match="set_voltage is not supported for channel 3"):
        driver.set_voltage(1.0, channel=3)


def test_get_voltage_channel_three_unsupported(driver: SiglentSPD3303) -> None:
    with pytest.raises(FeatureNotSupportedError, match="get_voltage is not supported for channel 3"):
        driver.get_voltage(channel=3)


def test_set_current_limit_channel_three_unsupported(driver: SiglentSPD3303) -> None:
    with pytest.raises(FeatureNotSupportedError, match="set_current_limit is not supported for channel 3"):
        driver.set_current_limit(0.1, channel=3)


def test_get_current_channel_three_unsupported(driver: SiglentSPD3303) -> None:
    with pytest.raises(FeatureNotSupportedError, match="get_current is not supported for channel 3"):
        driver.get_current(channel=3)


def test_output_enable_channel_three_unsupported(driver: SiglentSPD3303) -> None:
    with pytest.raises(FeatureNotSupportedError, match="output_enable is not supported for channel 3"):
        driver.output_enable(True, channel=3)


def test_get_output_status_channel_three_unsupported(driver: SiglentSPD3303) -> None:
    with pytest.raises(FeatureNotSupportedError, match="get_output_status is not supported for channel 3"):
        driver.get_output_status(channel=3)


def test_set_voltage_invalid_channel(driver: SiglentSPD3303) -> None:
    with pytest.raises(ValueError, match="channel must be 1, 2, or 3"):
        driver.set_voltage(1.0, channel=4)


def test_get_voltage_invalid_channel(driver: SiglentSPD3303) -> None:
    with pytest.raises(ValueError, match="channel must be 1, 2, or 3"):
        driver.get_voltage(channel=4)


def test_set_current_limit_invalid_channel(driver: SiglentSPD3303) -> None:
    with pytest.raises(ValueError, match="channel must be 1, 2, or 3"):
        driver.set_current_limit(0.1, channel=4)


def test_get_current_invalid_channel(driver: SiglentSPD3303) -> None:
    with pytest.raises(ValueError, match="channel must be 1, 2, or 3"):
        driver.get_current(channel=4)


def test_output_enable_invalid_channel(driver: SiglentSPD3303) -> None:
    with pytest.raises(ValueError, match="channel must be 1, 2, or 3"):
        driver.output_enable(True, channel=4)


def test_get_output_status_invalid_channel(driver: SiglentSPD3303) -> None:
    with pytest.raises(ValueError, match="channel must be 1, 2, or 3"):
        driver.get_output_status(channel=4)


def test_check_errors_raises_after_instrument_error(driver: SiglentSPD3303) -> None:
    # Bypass the driver once so the next error check sees a real instrument error queue entry.
    driver._visa.write("INSTRO:INVALID")

    with pytest.raises(RuntimeError, match="Siglent PSU reported error"):
        driver._check_errors()
