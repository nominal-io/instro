"""Required end-to-end tests for the simulated PSU driver."""

from __future__ import annotations

import socket
from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from instro.lib.exceptions import FeatureNotSupportedError
from instro.lib.transports import VisaConfig
from instro.psu.drivers.simulated import SimulatedPSU
from instro.psu.scpi_sim_server import SimulatedPSU as SimulatedPSUSimulator
from instro.psu.scpi_sim_server import SimulatedPSUServer

# When copying this file for real hardware, uncomment the marker below so the
# tests only run when pytest is explicitly invoked with `-m hardware`.
# pytestmark = pytest.mark.hardware


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


CHANNELS = [
    ChannelConfig(
        channel=1,
        voltage_range=(0.0, 60.0),
        current_range=(0.0, 10.0),
        voltage_readback_tolerance=0.15,
        current_readback_tolerance=0.01,
    ),
    ChannelConfig(
        channel=2,
        voltage_range=(0.0, 60.0),
        current_range=(0.0, 10.0),
        voltage_readback_tolerance=0.15,
        current_readback_tolerance=0.01,
    ),
]


@pytest.fixture(scope="module")
def driver(sim_target: "_SimulatedTarget") -> Iterator[SimulatedPSU]:
    psu_driver = SimulatedPSU(
        VisaConfig(
            visa_resource=f"TCPIP0::{sim_target.host}::{sim_target.port}::SOCKET",
        )
    )
    try:
        psu_driver.open()
        yield psu_driver
    except Exception:
        psu_driver.close()
        raise
    finally:
        psu_driver.close()


@pytest.fixture(autouse=True)
def reset_before_each_test(driver: SimulatedPSU) -> None:
    driver._visa.write("*RST")


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_voltage(driver: SimulatedPSU, channel_config: ChannelConfig) -> None:
    driver.set_current_limit(channel_config.programmed_current_limit(), channel=channel_config.channel)
    driver.set_voltage(channel_config.programmed_voltage(), channel=channel_config.channel)
    driver.output_enable(True, channel=channel_config.channel)

    assert driver.get_voltage(channel=channel_config.channel) == pytest.approx(
        channel_config.programmed_voltage(),
        abs=channel_config.voltage_readback_tolerance,
    )


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_voltage(driver: SimulatedPSU, channel_config: ChannelConfig) -> None:
    driver.set_current_limit(channel_config.programmed_current_limit(), channel=channel_config.channel)
    driver.set_voltage(channel_config.programmed_voltage(), channel=channel_config.channel)
    driver.output_enable(True, channel=channel_config.channel)

    voltage = driver.get_voltage(channel=channel_config.channel)

    assert voltage == pytest.approx(
        channel_config.programmed_voltage(),
        abs=channel_config.voltage_readback_tolerance,
    )


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_current_limit(driver: SimulatedPSU, channel_config: ChannelConfig) -> None:
    driver.set_current_limit(channel_config.programmed_current_limit(), channel=channel_config.channel)
    driver.set_voltage(channel_config.programmed_voltage(), channel=channel_config.channel)

    assert driver.get_current(channel=channel_config.channel) == pytest.approx(
        0.0,
        abs=channel_config.current_readback_tolerance,
    )


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_current(driver: SimulatedPSU, channel_config: ChannelConfig) -> None:
    driver.set_current_limit(channel_config.programmed_current_limit(), channel=channel_config.channel)
    driver.set_voltage(channel_config.programmed_voltage(), channel=channel_config.channel)

    current = driver.get_current(channel=channel_config.channel)

    assert current == pytest.approx(
        0.0,
        abs=channel_config.current_readback_tolerance,
    )


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_output_enable(driver: SimulatedPSU, channel_config: ChannelConfig) -> None:
    driver.output_enable(True, channel=channel_config.channel)
    assert driver.get_output_status(channel=channel_config.channel) is True

    driver.output_enable(False, channel=channel_config.channel)
    assert driver.get_output_status(channel=channel_config.channel) is False


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_output_status(driver: SimulatedPSU, channel_config: ChannelConfig) -> None:
    assert driver.get_output_status(channel=channel_config.channel) is False

    driver.output_enable(True, channel=channel_config.channel)

    assert driver.get_output_status(channel=channel_config.channel) is True


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_overvoltage_protection_level(driver: SimulatedPSU, channel_config: ChannelConfig) -> None:
    driver.set_overvoltage_protection_level(channel_config.ovp_level(), channel=channel_config.channel)

    assert driver.get_overvoltage_protection_level(channel=channel_config.channel) == pytest.approx(
        channel_config.ovp_level()
    )


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_overvoltage_protection_level(driver: SimulatedPSU, channel_config: ChannelConfig) -> None:
    driver.set_overvoltage_protection_level(channel_config.ovp_level(), channel=channel_config.channel)

    level = driver.get_overvoltage_protection_level(channel=channel_config.channel)

    assert level == pytest.approx(channel_config.ovp_level())


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_overvoltage_protection_enabled(driver: SimulatedPSU, channel_config: ChannelConfig) -> None:
    driver.set_overvoltage_protection_enabled(True, channel=channel_config.channel)
    assert driver.get_overvoltage_protection_enabled(channel=channel_config.channel) is True

    driver.set_overvoltage_protection_enabled(False, channel=channel_config.channel)
    assert driver.get_overvoltage_protection_enabled(channel=channel_config.channel) is False


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_overvoltage_protection_enabled(driver: SimulatedPSU, channel_config: ChannelConfig) -> None:
    assert driver.get_overvoltage_protection_enabled(channel=channel_config.channel) is False

    driver.set_overvoltage_protection_enabled(True, channel=channel_config.channel)

    assert driver.get_overvoltage_protection_enabled(channel=channel_config.channel) is True


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_overvoltage_protection_delay_unsupported(
    driver: SimulatedPSU,
    channel_config: ChannelConfig,
) -> None:
    with pytest.raises(FeatureNotSupportedError, match="set_overvoltage_protection_delay is not supported"):
        driver.set_overvoltage_protection_delay(0.1, channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_overvoltage_protection_delay_unsupported(
    driver: SimulatedPSU,
    channel_config: ChannelConfig,
) -> None:
    with pytest.raises(FeatureNotSupportedError, match="get_overvoltage_protection_delay is not supported"):
        driver.get_overvoltage_protection_delay(channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_overcurrent_protection_level(driver: SimulatedPSU, channel_config: ChannelConfig) -> None:
    driver.set_overcurrent_protection_level(channel_config.ocp_level(), channel=channel_config.channel)

    assert driver.get_overcurrent_protection_level(channel=channel_config.channel) == pytest.approx(
        channel_config.ocp_level()
    )


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_overcurrent_protection_level(driver: SimulatedPSU, channel_config: ChannelConfig) -> None:
    driver.set_overcurrent_protection_level(channel_config.ocp_level(), channel=channel_config.channel)

    level = driver.get_overcurrent_protection_level(channel=channel_config.channel)

    assert level == pytest.approx(channel_config.ocp_level())


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_overcurrent_protection_enabled(driver: SimulatedPSU, channel_config: ChannelConfig) -> None:
    driver.set_overcurrent_protection_enabled(True, channel=channel_config.channel)
    assert driver.get_overcurrent_protection_enabled(channel=channel_config.channel) is True

    driver.set_overcurrent_protection_enabled(False, channel=channel_config.channel)
    assert driver.get_overcurrent_protection_enabled(channel=channel_config.channel) is False


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_overcurrent_protection_enabled(driver: SimulatedPSU, channel_config: ChannelConfig) -> None:
    assert driver.get_overcurrent_protection_enabled(channel=channel_config.channel) is False

    driver.set_overcurrent_protection_enabled(True, channel=channel_config.channel)

    assert driver.get_overcurrent_protection_enabled(channel=channel_config.channel) is True


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_remote_sense_enabled(driver: SimulatedPSU, channel_config: ChannelConfig) -> None:
    driver.set_remote_sense_enabled(True, channel=channel_config.channel)
    assert driver.get_remote_sense_enabled(channel=channel_config.channel) is True

    driver.set_remote_sense_enabled(False, channel=channel_config.channel)
    assert driver.get_remote_sense_enabled(channel=channel_config.channel) is False


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_remote_sense_enabled(driver: SimulatedPSU, channel_config: ChannelConfig) -> None:
    assert driver.get_remote_sense_enabled(channel=channel_config.channel) is False

    driver.set_remote_sense_enabled(True, channel=channel_config.channel)

    assert driver.get_remote_sense_enabled(channel=channel_config.channel) is True


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_voltage_above_overvoltage_protection_raises(
    driver: SimulatedPSU,
    channel_config: ChannelConfig,
) -> None:
    driver.set_overvoltage_protection_level(channel_config.low_ovp_level(), channel=channel_config.channel)

    with pytest.raises(RuntimeError, match="PV Above OVP"):
        driver.set_voltage(channel_config.programmed_voltage(), channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_current_limit_above_overcurrent_protection_raises(
    driver: SimulatedPSU,
    channel_config: ChannelConfig,
) -> None:
    driver.set_overcurrent_protection_level(channel_config.low_ocp_level(), channel=channel_config.channel)

    with pytest.raises(RuntimeError, match="PC Above OCP"):
        driver.set_current_limit(channel_config.programmed_current_limit(), channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_overvoltage_protection_below_programmed_voltage_raises(
    driver: SimulatedPSU,
    channel_config: ChannelConfig,
) -> None:
    driver.set_voltage(channel_config.programmed_voltage(), channel=channel_config.channel)

    with pytest.raises(RuntimeError, match="OVP Below PV"):
        driver.set_overvoltage_protection_level(channel_config.low_ovp_level(), channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_overcurrent_protection_below_programmed_current_raises(
    driver: SimulatedPSU,
    channel_config: ChannelConfig,
) -> None:
    driver.set_current_limit(channel_config.programmed_current_limit(), channel=channel_config.channel)

    with pytest.raises(RuntimeError, match="OCP Below PC"):
        driver.set_overcurrent_protection_level(channel_config.low_ocp_level(), channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_voltage_out_of_range_raises(driver: SimulatedPSU, channel_config: ChannelConfig) -> None:
    with pytest.raises(RuntimeError, match="Data out of range"):
        driver.set_voltage(channel_config.overrange_voltage(), channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_current_limit_out_of_range_raises(driver: SimulatedPSU, channel_config: ChannelConfig) -> None:
    with pytest.raises(RuntimeError, match="Data out of range"):
        driver.set_current_limit(channel_config.overrange_current(), channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_overvoltage_protection_out_of_range_raises(
    driver: SimulatedPSU,
    channel_config: ChannelConfig,
) -> None:
    with pytest.raises(RuntimeError, match="Data out of range"):
        driver.set_overvoltage_protection_level(channel_config.overrange_voltage(), channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_overcurrent_protection_out_of_range_raises(
    driver: SimulatedPSU,
    channel_config: ChannelConfig,
) -> None:
    with pytest.raises(RuntimeError, match="Data out of range"):
        driver.set_overcurrent_protection_level(channel_config.overrange_current(), channel=channel_config.channel)


@pytest.fixture(scope="module")
def invalid_channel() -> int:
    return max(channel.channel for channel in CHANNELS) + 1


def test_set_voltage_invalid_channel(driver: SimulatedPSU, invalid_channel: int) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.set_voltage(CHANNELS[0].programmed_voltage(), channel=invalid_channel)


def test_get_voltage_invalid_channel(driver: SimulatedPSU, invalid_channel: int) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.get_voltage(channel=invalid_channel)


def test_set_current_limit_invalid_channel(
    driver: SimulatedPSU,
    invalid_channel: int,
) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.set_current_limit(CHANNELS[0].programmed_current_limit(), channel=invalid_channel)


def test_get_current_invalid_channel(driver: SimulatedPSU, invalid_channel: int) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.get_current(channel=invalid_channel)


def test_output_enable_invalid_channel(driver: SimulatedPSU, invalid_channel: int) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.output_enable(True, channel=invalid_channel)


def test_get_output_status_invalid_channel(driver: SimulatedPSU, invalid_channel: int) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.get_output_status(channel=invalid_channel)


def test_set_overvoltage_protection_level_invalid_channel(
    driver: SimulatedPSU,
    invalid_channel: int,
) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.set_overvoltage_protection_level(CHANNELS[0].ovp_level(), channel=invalid_channel)


def test_get_overvoltage_protection_level_invalid_channel(driver: SimulatedPSU, invalid_channel: int) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.get_overvoltage_protection_level(channel=invalid_channel)


def test_set_overvoltage_protection_enabled_invalid_channel(driver: SimulatedPSU, invalid_channel: int) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.set_overvoltage_protection_enabled(True, channel=invalid_channel)


def test_get_overvoltage_protection_enabled_invalid_channel(driver: SimulatedPSU, invalid_channel: int) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.get_overvoltage_protection_enabled(channel=invalid_channel)


def test_set_overcurrent_protection_level_invalid_channel(
    driver: SimulatedPSU,
    invalid_channel: int,
) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.set_overcurrent_protection_level(CHANNELS[0].ocp_level(), channel=invalid_channel)


def test_get_overcurrent_protection_level_invalid_channel(driver: SimulatedPSU, invalid_channel: int) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.get_overcurrent_protection_level(channel=invalid_channel)


def test_set_overcurrent_protection_enabled_invalid_channel(driver: SimulatedPSU, invalid_channel: int) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.set_overcurrent_protection_enabled(True, channel=invalid_channel)


def test_get_overcurrent_protection_enabled_invalid_channel(driver: SimulatedPSU, invalid_channel: int) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.get_overcurrent_protection_enabled(channel=invalid_channel)


def test_set_remote_sense_enabled_invalid_channel(driver: SimulatedPSU, invalid_channel: int) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.set_remote_sense_enabled(True, channel=invalid_channel)


def test_get_remote_sense_enabled_invalid_channel(driver: SimulatedPSU, invalid_channel: int) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.get_remote_sense_enabled(channel=invalid_channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_driver_recovers_after_simulator_error(driver: SimulatedPSU, channel_config: ChannelConfig) -> None:
    driver.set_voltage(channel_config.programmed_voltage(), channel=channel_config.channel)

    with pytest.raises(RuntimeError, match="OVP Below PV"):
        driver.set_overvoltage_protection_level(channel_config.low_ovp_level(), channel=channel_config.channel)

    driver.set_overvoltage_protection_level(channel_config.ovp_level(), channel=channel_config.channel)
    assert driver.get_overvoltage_protection_level(channel=channel_config.channel) == pytest.approx(
        channel_config.ovp_level()
    )


@pytest.fixture(scope="module")
def sim_target(request: pytest.FixtureRequest) -> "_SimulatedTarget":
    target = _SimulatedTarget.start()
    request.addfinalizer(target.shutdown)
    return target


class _SimulatedTarget:
    def __init__(self, simulator: SimulatedPSUSimulator, server: SimulatedPSUServer, host: str, port: int) -> None:
        self.simulator = simulator
        self.server = server
        self.host = host
        self.port = port

    @classmethod
    def start(cls) -> "_SimulatedTarget":
        host = "127.0.0.1"
        port = _free_port(host)
        simulator = SimulatedPSUSimulator(num_channels=2)
        server = SimulatedPSUServer(simulator, host=host, port=port)
        server.start()
        return cls(simulator, server, host, port)

    def shutdown(self) -> None:
        self.server.shutdown()


def _free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])
