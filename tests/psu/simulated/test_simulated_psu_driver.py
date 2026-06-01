"""Required integration tests for the simulated PSU driver."""

from __future__ import annotations

import socket
from dataclasses import dataclass

import pytest

from instro.lib.transports import VisaConfig
from instro.psu.drivers.simulated import SimulatedPSU
from instro.psu.scpi_sim_server import SimulatedPSU as SimulatedPSUSimulator
from instro.psu.scpi_sim_server import SimulatedPSUServer

# Uncomment with real hardware to make all tests optional
# pytestmark = pytest.mark.hardware


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


VISA_HOST = "127.0.0.1"
CHANNELS = [
    PSUChannelConfig(
        channel=1,
        voltage_range=(0.0, 60.0),
        current_range=(0.0, 10.0),
        voltage_readback_tolerance=0.15,
        current_readback_tolerance=0.01,
    ),
    PSUChannelConfig(
        channel=2,
        voltage_range=(0.0, 60.0),
        current_range=(0.0, 10.0),
        voltage_readback_tolerance=0.15,
        current_readback_tolerance=0.01,
    ),
]


def _invalid_channel() -> int:
    return max(channel.channel for channel in CHANNELS) + 1


@pytest.fixture(scope="module")
def driver(request: pytest.FixtureRequest, driver_config: VisaConfig) -> SimulatedPSU:
    psu_driver = SimulatedPSU(driver_config)
    try:
        psu_driver.open()
    except Exception:
        psu_driver.close()
        raise

    request.addfinalizer(psu_driver.close)
    return psu_driver


@pytest.fixture(autouse=True)
def reset_driver(driver: SimulatedPSU) -> None:
    driver._visa.write("*RST")


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_voltage_and_output_readback(driver: SimulatedPSU, channel_config: PSUChannelConfig) -> None:
    driver.set_current_limit(channel_config.programmed_current_limit(), channel=channel_config.channel)
    driver.set_voltage(channel_config.programmed_voltage(), channel=channel_config.channel)
    driver.output_enable(True, channel=channel_config.channel)

    assert driver.get_output_status(channel=channel_config.channel) is True
    assert driver.get_voltage(channel=channel_config.channel) == pytest.approx(
        channel_config.programmed_voltage(),
        abs=channel_config.voltage_readback_tolerance,
    )


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_current_readback(driver: SimulatedPSU, channel_config: PSUChannelConfig) -> None:
    driver.set_current_limit(channel_config.programmed_current_limit(), channel=channel_config.channel)
    driver.set_voltage(channel_config.programmed_voltage(), channel=channel_config.channel)

    assert driver.get_current(channel=channel_config.channel) == pytest.approx(
        0.0,
        abs=channel_config.current_readback_tolerance,
    )


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_output_disable_readback(driver: SimulatedPSU, channel_config: PSUChannelConfig) -> None:
    driver.set_current_limit(channel_config.programmed_current_limit(), channel=channel_config.channel)
    driver.set_voltage(channel_config.programmed_voltage(), channel=channel_config.channel)
    driver.output_enable(True, channel=channel_config.channel)
    driver.output_enable(False, channel=channel_config.channel)

    assert driver.get_output_status(channel=channel_config.channel) is False
    assert driver.get_voltage(channel=channel_config.channel) == pytest.approx(
        0.0,
        abs=channel_config.voltage_readback_tolerance,
    )


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_overvoltage_protection_round_trips(driver: SimulatedPSU, channel_config: PSUChannelConfig) -> None:
    driver.set_voltage(channel_config.low_programmed_voltage(), channel=channel_config.channel)
    driver.set_overvoltage_protection_level(channel_config.ovp_level(), channel=channel_config.channel)

    assert driver.get_overvoltage_protection_level(channel=channel_config.channel) == pytest.approx(
        channel_config.ovp_level()
    )

    driver.set_overvoltage_protection_enabled(True, channel=channel_config.channel)
    assert driver.get_overvoltage_protection_enabled(channel=channel_config.channel) is True

    driver.set_overvoltage_protection_enabled(False, channel=channel_config.channel)
    assert driver.get_overvoltage_protection_enabled(channel=channel_config.channel) is False


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_overcurrent_protection_round_trips(driver: SimulatedPSU, channel_config: PSUChannelConfig) -> None:
    driver.set_current_limit(channel_config.low_programmed_current_limit(), channel=channel_config.channel)
    driver.set_overcurrent_protection_level(channel_config.ocp_level(), channel=channel_config.channel)

    assert driver.get_overcurrent_protection_level(channel=channel_config.channel) == pytest.approx(
        channel_config.ocp_level()
    )

    driver.set_overcurrent_protection_enabled(True, channel=channel_config.channel)
    assert driver.get_overcurrent_protection_enabled(channel=channel_config.channel) is True

    driver.set_overcurrent_protection_enabled(False, channel=channel_config.channel)
    assert driver.get_overcurrent_protection_enabled(channel=channel_config.channel) is False


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_remote_sense_round_trips(driver: SimulatedPSU, channel_config: PSUChannelConfig) -> None:
    driver.set_remote_sense_enabled(True, channel=channel_config.channel)
    assert driver.get_remote_sense_enabled(channel=channel_config.channel) is True

    driver.set_remote_sense_enabled(False, channel=channel_config.channel)
    assert driver.get_remote_sense_enabled(channel=channel_config.channel) is False


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_voltage_above_overvoltage_protection_raises(
    driver: SimulatedPSU,
    channel_config: PSUChannelConfig,
) -> None:
    driver.set_overvoltage_protection_level(channel_config.low_ovp_level(), channel=channel_config.channel)

    with pytest.raises(RuntimeError, match="PV Above OVP"):
        driver.set_voltage(channel_config.programmed_voltage(), channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_current_limit_above_overcurrent_protection_raises(
    driver: SimulatedPSU,
    channel_config: PSUChannelConfig,
) -> None:
    driver.set_overcurrent_protection_level(channel_config.low_ocp_level(), channel=channel_config.channel)

    with pytest.raises(RuntimeError, match="PC Above OCP"):
        driver.set_current_limit(channel_config.programmed_current_limit(), channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_overvoltage_protection_below_programmed_voltage_raises(
    driver: SimulatedPSU,
    channel_config: PSUChannelConfig,
) -> None:
    driver.set_voltage(channel_config.programmed_voltage(), channel=channel_config.channel)

    with pytest.raises(RuntimeError, match="OVP Below PV"):
        driver.set_overvoltage_protection_level(channel_config.low_ovp_level(), channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_overcurrent_protection_below_programmed_current_raises(
    driver: SimulatedPSU,
    channel_config: PSUChannelConfig,
) -> None:
    driver.set_current_limit(channel_config.programmed_current_limit(), channel=channel_config.channel)

    with pytest.raises(RuntimeError, match="OCP Below PC"):
        driver.set_overcurrent_protection_level(channel_config.low_ocp_level(), channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_voltage_out_of_range_raises(driver: SimulatedPSU, channel_config: PSUChannelConfig) -> None:
    with pytest.raises(RuntimeError, match="Data out of range"):
        driver.set_voltage(channel_config.overrange_voltage(), channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_current_limit_out_of_range_raises(driver: SimulatedPSU, channel_config: PSUChannelConfig) -> None:
    with pytest.raises(RuntimeError, match="Data out of range"):
        driver.set_current_limit(channel_config.overrange_current(), channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_overvoltage_protection_out_of_range_raises(
    driver: SimulatedPSU,
    channel_config: PSUChannelConfig,
) -> None:
    with pytest.raises(RuntimeError, match="Data out of range"):
        driver.set_overvoltage_protection_level(channel_config.overrange_voltage(), channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_overcurrent_protection_out_of_range_raises(
    driver: SimulatedPSU,
    channel_config: PSUChannelConfig,
) -> None:
    with pytest.raises(RuntimeError, match="Data out of range"):
        driver.set_overcurrent_protection_level(channel_config.overrange_current(), channel=channel_config.channel)


def test_set_voltage_invalid_channel_raises(driver: SimulatedPSU) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.set_voltage(CHANNELS[0].programmed_voltage(), channel=_invalid_channel())


def test_get_voltage_invalid_channel_raises(driver: SimulatedPSU) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.get_voltage(channel=_invalid_channel())


def test_set_current_limit_invalid_channel_raises(driver: SimulatedPSU) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.set_current_limit(CHANNELS[0].programmed_current_limit(), channel=_invalid_channel())


def test_get_current_invalid_channel_raises(driver: SimulatedPSU) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.get_current(channel=_invalid_channel())


def test_output_enable_invalid_channel_raises(driver: SimulatedPSU) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.output_enable(True, channel=_invalid_channel())


def test_get_output_status_invalid_channel_raises(driver: SimulatedPSU) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.get_output_status(channel=_invalid_channel())


def test_set_overvoltage_protection_level_invalid_channel_raises(driver: SimulatedPSU) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.set_overvoltage_protection_level(CHANNELS[0].ovp_level(), channel=_invalid_channel())


def test_get_overvoltage_protection_level_invalid_channel_raises(driver: SimulatedPSU) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.get_overvoltage_protection_level(channel=_invalid_channel())


def test_set_overvoltage_protection_enabled_invalid_channel_raises(driver: SimulatedPSU) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.set_overvoltage_protection_enabled(True, channel=_invalid_channel())


def test_get_overvoltage_protection_enabled_invalid_channel_raises(driver: SimulatedPSU) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.get_overvoltage_protection_enabled(channel=_invalid_channel())


def test_set_overcurrent_protection_level_invalid_channel_raises(driver: SimulatedPSU) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.set_overcurrent_protection_level(CHANNELS[0].ocp_level(), channel=_invalid_channel())


def test_get_overcurrent_protection_level_invalid_channel_raises(driver: SimulatedPSU) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.get_overcurrent_protection_level(channel=_invalid_channel())


def test_set_overcurrent_protection_enabled_invalid_channel_raises(driver: SimulatedPSU) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.set_overcurrent_protection_enabled(True, channel=_invalid_channel())


def test_get_overcurrent_protection_enabled_invalid_channel_raises(driver: SimulatedPSU) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.get_overcurrent_protection_enabled(channel=_invalid_channel())


def test_set_remote_sense_enabled_invalid_channel_raises(driver: SimulatedPSU) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.set_remote_sense_enabled(True, channel=_invalid_channel())


def test_get_remote_sense_enabled_invalid_channel_raises(driver: SimulatedPSU) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.get_remote_sense_enabled(channel=_invalid_channel())


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_driver_recovers_after_simulator_error(driver: SimulatedPSU, channel_config: PSUChannelConfig) -> None:
    driver.set_voltage(channel_config.programmed_voltage(), channel=channel_config.channel)

    with pytest.raises(RuntimeError, match="OVP Below PV"):
        driver.set_overvoltage_protection_level(channel_config.low_ovp_level(), channel=channel_config.channel)

    driver.set_overvoltage_protection_level(channel_config.ovp_level(), channel=channel_config.channel)
    assert driver.get_overvoltage_protection_level(channel=channel_config.channel) == pytest.approx(
        channel_config.ovp_level()
    )


# Simulation-only target setup. Real hardware smoke tests do not need this
# section; provide the bench instrument resource directly in driver_config().


@pytest.fixture(scope="module")
def driver_config(sim_target: "_SimulatedTarget") -> VisaConfig:
    return sim_target.driver_config


@pytest.fixture(scope="module")
def sim_target(request: pytest.FixtureRequest) -> "_SimulatedTarget":
    target = _SimulatedTarget.start()
    request.addfinalizer(target.shutdown)
    return target


class _SimulatedTarget:
    def __init__(self, simulator: SimulatedPSUSimulator, server: SimulatedPSUServer, port: int) -> None:
        self.simulator = simulator
        self.server = server
        self.port = port

    @classmethod
    def start(cls) -> "_SimulatedTarget":
        port = _free_port()
        simulator = SimulatedPSUSimulator(num_channels=2)
        server = SimulatedPSUServer(simulator, host=VISA_HOST, port=port)
        server.start()
        return cls(simulator, server, port)

    @property
    def driver_config(self) -> VisaConfig:
        return VisaConfig(
            visa_resource=f"TCPIP0::{VISA_HOST}::{self.port}::SOCKET",
        )

    def shutdown(self) -> None:
        self.server.shutdown()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((VISA_HOST, 0))
        return int(sock.getsockname()[1])
