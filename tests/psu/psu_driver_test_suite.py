"""Reusable PSU driver end-to-end test suite."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from instro.psu import PSUDriverBase


@dataclass(frozen=True)
class PSUChannelConfig:
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

    def low_programmed_voltage(self) -> float:
        return self.relative_range_value(self.voltage_range, 0.05)

    def programmed_current_limit(self) -> float:
        return self.relative_range_value(self.current_range, 0.10)

    def low_programmed_current_limit(self) -> float:
        return self.relative_range_value(self.current_range, 0.05)

    def ovp_level(self) -> float:
        return self.relative_range_value(self.voltage_range, 0.20)

    def low_ovp_level(self) -> float:
        return self.relative_range_value(self.voltage_range, 0.05)

    def ocp_level(self) -> float:
        return self.relative_range_value(self.current_range, 0.20)

    def low_ocp_level(self) -> float:
        return self.relative_range_value(self.current_range, 0.05)

    def overrange_voltage(self) -> float:
        return self.voltage_range[1] + 1.0

    def overrange_current(self) -> float:
        return self.current_range[1] + 1.0


class PSUDriverTestSuite:
    def test_voltage_and_output_readback(self, driver: PSUDriverBase, channel_config: PSUChannelConfig) -> None:
        driver.set_current_limit(channel_config.programmed_current_limit(), channel=channel_config.channel)
        driver.set_voltage(channel_config.programmed_voltage(), channel=channel_config.channel)
        driver.output_enable(True, channel=channel_config.channel)

        assert driver.get_output_status(channel=channel_config.channel) is True
        assert driver.get_voltage(channel=channel_config.channel) == pytest.approx(
            channel_config.programmed_voltage(),
            abs=channel_config.voltage_readback_tolerance,
        )

    def test_current_readback(self, driver: PSUDriverBase, channel_config: PSUChannelConfig) -> None:
        driver.set_current_limit(channel_config.programmed_current_limit(), channel=channel_config.channel)
        driver.set_voltage(channel_config.programmed_voltage(), channel=channel_config.channel)

        assert driver.get_current(channel=channel_config.channel) == pytest.approx(
            0.0,
            abs=channel_config.current_readback_tolerance,
        )

    def test_output_disable_readback(self, driver: PSUDriverBase, channel_config: PSUChannelConfig) -> None:
        driver.set_current_limit(channel_config.programmed_current_limit(), channel=channel_config.channel)
        driver.set_voltage(channel_config.programmed_voltage(), channel=channel_config.channel)
        driver.output_enable(True, channel=channel_config.channel)
        driver.output_enable(False, channel=channel_config.channel)

        assert driver.get_output_status(channel=channel_config.channel) is False
        assert driver.get_voltage(channel=channel_config.channel) == pytest.approx(
            0.0,
            abs=channel_config.voltage_readback_tolerance,
        )

    def test_overvoltage_protection_round_trips(
        self,
        driver: PSUDriverBase,
        channel_config: PSUChannelConfig,
    ) -> None:
        driver.set_voltage(channel_config.low_programmed_voltage(), channel=channel_config.channel)
        driver.set_overvoltage_protection_level(channel_config.ovp_level(), channel=channel_config.channel)

        assert driver.get_overvoltage_protection_level(channel=channel_config.channel) == pytest.approx(
            channel_config.ovp_level()
        )

        driver.set_overvoltage_protection_enabled(True, channel=channel_config.channel)
        assert driver.get_overvoltage_protection_enabled(channel=channel_config.channel) is True

        driver.set_overvoltage_protection_enabled(False, channel=channel_config.channel)
        assert driver.get_overvoltage_protection_enabled(channel=channel_config.channel) is False

    def test_overcurrent_protection_round_trips(
        self,
        driver: PSUDriverBase,
        channel_config: PSUChannelConfig,
    ) -> None:
        driver.set_current_limit(channel_config.low_programmed_current_limit(), channel=channel_config.channel)
        driver.set_overcurrent_protection_level(channel_config.ocp_level(), channel=channel_config.channel)

        assert driver.get_overcurrent_protection_level(channel=channel_config.channel) == pytest.approx(
            channel_config.ocp_level()
        )

        driver.set_overcurrent_protection_enabled(True, channel=channel_config.channel)
        assert driver.get_overcurrent_protection_enabled(channel=channel_config.channel) is True

        driver.set_overcurrent_protection_enabled(False, channel=channel_config.channel)
        assert driver.get_overcurrent_protection_enabled(channel=channel_config.channel) is False

    def test_remote_sense_round_trips(self, driver: PSUDriverBase, channel_config: PSUChannelConfig) -> None:
        driver.set_remote_sense_enabled(True, channel=channel_config.channel)
        assert driver.get_remote_sense_enabled(channel=channel_config.channel) is True

        driver.set_remote_sense_enabled(False, channel=channel_config.channel)
        assert driver.get_remote_sense_enabled(channel=channel_config.channel) is False

    def test_set_voltage_above_overvoltage_protection_raises(
        self,
        driver: PSUDriverBase,
        channel_config: PSUChannelConfig,
    ) -> None:
        driver.set_overvoltage_protection_level(channel_config.low_ovp_level(), channel=channel_config.channel)

        with pytest.raises(RuntimeError, match="PV Above OVP"):
            driver.set_voltage(channel_config.programmed_voltage(), channel=channel_config.channel)

    def test_set_current_limit_above_overcurrent_protection_raises(
        self,
        driver: PSUDriverBase,
        channel_config: PSUChannelConfig,
    ) -> None:
        driver.set_overcurrent_protection_level(channel_config.low_ocp_level(), channel=channel_config.channel)

        with pytest.raises(RuntimeError, match="PC Above OCP"):
            driver.set_current_limit(channel_config.programmed_current_limit(), channel=channel_config.channel)

    def test_set_overvoltage_protection_below_programmed_voltage_raises(
        self,
        driver: PSUDriverBase,
        channel_config: PSUChannelConfig,
    ) -> None:
        driver.set_voltage(channel_config.programmed_voltage(), channel=channel_config.channel)

        with pytest.raises(RuntimeError, match="OVP Below PV"):
            driver.set_overvoltage_protection_level(channel_config.low_ovp_level(), channel=channel_config.channel)

    def test_set_overcurrent_protection_below_programmed_current_raises(
        self,
        driver: PSUDriverBase,
        channel_config: PSUChannelConfig,
    ) -> None:
        driver.set_current_limit(channel_config.programmed_current_limit(), channel=channel_config.channel)

        with pytest.raises(RuntimeError, match="OCP Below PC"):
            driver.set_overcurrent_protection_level(channel_config.low_ocp_level(), channel=channel_config.channel)

    def test_set_voltage_out_of_range_raises(
        self,
        driver: PSUDriverBase,
        channel_config: PSUChannelConfig,
    ) -> None:
        with pytest.raises(RuntimeError, match="Data out of range"):
            driver.set_voltage(channel_config.overrange_voltage(), channel=channel_config.channel)

    def test_set_current_limit_out_of_range_raises(
        self,
        driver: PSUDriverBase,
        channel_config: PSUChannelConfig,
    ) -> None:
        with pytest.raises(RuntimeError, match="Data out of range"):
            driver.set_current_limit(channel_config.overrange_current(), channel=channel_config.channel)

    def test_set_overvoltage_protection_out_of_range_raises(
        self,
        driver: PSUDriverBase,
        channel_config: PSUChannelConfig,
    ) -> None:
        with pytest.raises(RuntimeError, match="Data out of range"):
            driver.set_overvoltage_protection_level(channel_config.overrange_voltage(), channel=channel_config.channel)

    def test_set_overcurrent_protection_out_of_range_raises(
        self,
        driver: PSUDriverBase,
        channel_config: PSUChannelConfig,
    ) -> None:
        with pytest.raises(RuntimeError, match="Data out of range"):
            driver.set_overcurrent_protection_level(channel_config.overrange_current(), channel=channel_config.channel)

    def test_set_voltage_invalid_channel_raises(
        self,
        driver: PSUDriverBase,
        channel_config: PSUChannelConfig,
        invalid_channel: int,
    ) -> None:
        with pytest.raises(RuntimeError, match="Header suffix out of range"):
            driver.set_voltage(channel_config.programmed_voltage(), channel=invalid_channel)

    def test_get_voltage_invalid_channel_raises(
        self,
        driver: PSUDriverBase,
        channel_config: PSUChannelConfig,
        invalid_channel: int,
    ) -> None:
        assert channel_config.channel != invalid_channel
        with pytest.raises(RuntimeError, match="Header suffix out of range"):
            driver.get_voltage(channel=invalid_channel)

    def test_set_current_limit_invalid_channel_raises(
        self,
        driver: PSUDriverBase,
        channel_config: PSUChannelConfig,
        invalid_channel: int,
    ) -> None:
        with pytest.raises(RuntimeError, match="Header suffix out of range"):
            driver.set_current_limit(channel_config.programmed_current_limit(), channel=invalid_channel)

    def test_get_current_invalid_channel_raises(
        self,
        driver: PSUDriverBase,
        channel_config: PSUChannelConfig,
        invalid_channel: int,
    ) -> None:
        assert channel_config.channel != invalid_channel
        with pytest.raises(RuntimeError, match="Header suffix out of range"):
            driver.get_current(channel=invalid_channel)

    def test_output_enable_invalid_channel_raises(
        self,
        driver: PSUDriverBase,
        channel_config: PSUChannelConfig,
        invalid_channel: int,
    ) -> None:
        assert channel_config.channel != invalid_channel
        with pytest.raises(RuntimeError, match="Header suffix out of range"):
            driver.output_enable(True, channel=invalid_channel)

    def test_get_output_status_invalid_channel_raises(
        self,
        driver: PSUDriverBase,
        channel_config: PSUChannelConfig,
        invalid_channel: int,
    ) -> None:
        assert channel_config.channel != invalid_channel
        with pytest.raises(RuntimeError, match="Header suffix out of range"):
            driver.get_output_status(channel=invalid_channel)

    def test_set_overvoltage_protection_level_invalid_channel_raises(
        self,
        driver: PSUDriverBase,
        channel_config: PSUChannelConfig,
        invalid_channel: int,
    ) -> None:
        with pytest.raises(RuntimeError, match="Header suffix out of range"):
            driver.set_overvoltage_protection_level(channel_config.ovp_level(), channel=invalid_channel)

    def test_get_overvoltage_protection_level_invalid_channel_raises(
        self,
        driver: PSUDriverBase,
        channel_config: PSUChannelConfig,
        invalid_channel: int,
    ) -> None:
        assert channel_config.channel != invalid_channel
        with pytest.raises(RuntimeError, match="Header suffix out of range"):
            driver.get_overvoltage_protection_level(channel=invalid_channel)

    def test_set_overvoltage_protection_enabled_invalid_channel_raises(
        self,
        driver: PSUDriverBase,
        channel_config: PSUChannelConfig,
        invalid_channel: int,
    ) -> None:
        assert channel_config.channel != invalid_channel
        with pytest.raises(RuntimeError, match="Header suffix out of range"):
            driver.set_overvoltage_protection_enabled(True, channel=invalid_channel)

    def test_get_overvoltage_protection_enabled_invalid_channel_raises(
        self,
        driver: PSUDriverBase,
        channel_config: PSUChannelConfig,
        invalid_channel: int,
    ) -> None:
        assert channel_config.channel != invalid_channel
        with pytest.raises(RuntimeError, match="Header suffix out of range"):
            driver.get_overvoltage_protection_enabled(channel=invalid_channel)

    def test_set_overcurrent_protection_level_invalid_channel_raises(
        self,
        driver: PSUDriverBase,
        channel_config: PSUChannelConfig,
        invalid_channel: int,
    ) -> None:
        with pytest.raises(RuntimeError, match="Header suffix out of range"):
            driver.set_overcurrent_protection_level(channel_config.ocp_level(), channel=invalid_channel)

    def test_get_overcurrent_protection_level_invalid_channel_raises(
        self,
        driver: PSUDriverBase,
        channel_config: PSUChannelConfig,
        invalid_channel: int,
    ) -> None:
        assert channel_config.channel != invalid_channel
        with pytest.raises(RuntimeError, match="Header suffix out of range"):
            driver.get_overcurrent_protection_level(channel=invalid_channel)

    def test_set_overcurrent_protection_enabled_invalid_channel_raises(
        self,
        driver: PSUDriverBase,
        channel_config: PSUChannelConfig,
        invalid_channel: int,
    ) -> None:
        assert channel_config.channel != invalid_channel
        with pytest.raises(RuntimeError, match="Header suffix out of range"):
            driver.set_overcurrent_protection_enabled(True, channel=invalid_channel)

    def test_get_overcurrent_protection_enabled_invalid_channel_raises(
        self,
        driver: PSUDriverBase,
        channel_config: PSUChannelConfig,
        invalid_channel: int,
    ) -> None:
        assert channel_config.channel != invalid_channel
        with pytest.raises(RuntimeError, match="Header suffix out of range"):
            driver.get_overcurrent_protection_enabled(channel=invalid_channel)

    def test_set_remote_sense_enabled_invalid_channel_raises(
        self,
        driver: PSUDriverBase,
        channel_config: PSUChannelConfig,
        invalid_channel: int,
    ) -> None:
        assert channel_config.channel != invalid_channel
        with pytest.raises(RuntimeError, match="Header suffix out of range"):
            driver.set_remote_sense_enabled(True, channel=invalid_channel)

    def test_get_remote_sense_enabled_invalid_channel_raises(
        self,
        driver: PSUDriverBase,
        channel_config: PSUChannelConfig,
        invalid_channel: int,
    ) -> None:
        assert channel_config.channel != invalid_channel
        with pytest.raises(RuntimeError, match="Header suffix out of range"):
            driver.get_remote_sense_enabled(channel=invalid_channel)

    def test_driver_recovers_after_simulator_error(
        self,
        driver: PSUDriverBase,
        channel_config: PSUChannelConfig,
    ) -> None:
        driver.set_voltage(channel_config.programmed_voltage(), channel=channel_config.channel)

        with pytest.raises(RuntimeError, match="OVP Below PV"):
            driver.set_overvoltage_protection_level(channel_config.low_ovp_level(), channel=channel_config.channel)

        driver.set_overvoltage_protection_level(channel_config.ovp_level(), channel=channel_config.channel)
        assert driver.get_overvoltage_protection_level(channel=channel_config.channel) == pytest.approx(
            channel_config.ovp_level()
        )
