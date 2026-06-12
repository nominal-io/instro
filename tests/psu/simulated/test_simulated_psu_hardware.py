"""Full-transport tests for the simulated PSU driver."""

from __future__ import annotations

import pytest

from instro.lib.exceptions import FeatureNotSupportedError
from instro.lib.transports import VisaConfig
from instro.psu.drivers.simulated import SimulatedPSU
from instro.psu.scpi_sim_server import SimulatedPSU as SimulatedPSUSimulator
from instro.psu.scpi_sim_server import SimulatedPSUServer

# SIMULATED HARDWARE TEST TEMPLATE:
#
# Copy this file into tests/psu/<vendor>/test_<driver>_hardware.py, uncomment
# pytestmark, set VISA_ADDRESS to the bench instrument address, and instantiate
# the real driver in driver(). Keep reset_before_each_test() for SCPI/VISA
# instruments that accept *RST; replace it for hardware with a different reset
# path. Delete sim_target, _SimulatedTarget, and simulator imports because real
# hardware tests do not need to launch the local SCPI simulator.
# pytestmark = pytest.mark.hardware

VISA_ADDRESS = "TCPIP0::127.0.0.1::5025::SOCKET"


@pytest.fixture(scope="module")
def driver(request: pytest.FixtureRequest, sim_target: "_SimulatedTarget") -> SimulatedPSU:
    psu_driver = SimulatedPSU(
        VisaConfig(
            visa_resource=sim_target.visa_address,
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
def reset_before_each_test(driver: SimulatedPSU) -> None:
    driver._visa.write("*RST")


@pytest.mark.parametrize(
    ("channel", "current_limit", "voltage"),
    [
        (1, 1.0, 6.0),
        (2, 1.5, 7.5),
    ],
)
def test_set_voltage(driver: SimulatedPSU, channel: int, current_limit: float, voltage: float) -> None:
    driver.set_current_limit(current_limit, channel=channel)
    driver.set_voltage(voltage, channel=channel)
    driver.output_enable(True, channel=channel)

    assert driver.get_voltage(channel=channel) == pytest.approx(voltage, abs=0.15)


@pytest.mark.parametrize(
    ("channel", "current_limit", "voltage"),
    [
        (1, 1.0, 6.0),
        (2, 1.5, 7.5),
    ],
)
def test_get_voltage(driver: SimulatedPSU, channel: int, current_limit: float, voltage: float) -> None:
    driver.set_current_limit(current_limit, channel=channel)
    driver.set_voltage(voltage, channel=channel)
    driver.output_enable(True, channel=channel)

    measured_voltage = driver.get_voltage(channel=channel)

    assert measured_voltage == pytest.approx(voltage, abs=0.15)


@pytest.mark.parametrize(
    ("channel", "current_limit", "voltage"),
    [
        (1, 1.0, 6.0),
        (2, 1.5, 7.5),
    ],
)
def test_set_current_limit(driver: SimulatedPSU, channel: int, current_limit: float, voltage: float) -> None:
    driver.set_current_limit(current_limit, channel=channel)
    driver.set_voltage(voltage, channel=channel)

    assert driver.get_current(channel=channel) == pytest.approx(0.0, abs=0.01)


@pytest.mark.parametrize(
    ("channel", "current_limit", "voltage"),
    [
        (1, 1.0, 6.0),
        (2, 1.5, 7.5),
    ],
)
def test_get_current(driver: SimulatedPSU, channel: int, current_limit: float, voltage: float) -> None:
    driver.set_current_limit(current_limit, channel=channel)
    driver.set_voltage(voltage, channel=channel)

    current = driver.get_current(channel=channel)

    assert current == pytest.approx(0.0, abs=0.01)


@pytest.mark.parametrize(
    ("channel", "enabled", "disabled"),
    [
        (1, True, False),
        (2, True, False),
    ],
)
def test_output_enable(driver: SimulatedPSU, channel: int, enabled: bool, disabled: bool) -> None:
    driver.output_enable(enabled, channel=channel)
    assert driver.get_output_status(channel=channel) is True

    driver.output_enable(disabled, channel=channel)
    assert driver.get_output_status(channel=channel) is False


@pytest.mark.parametrize(
    ("channel", "enabled"),
    [
        (1, True),
        (2, True),
    ],
)
def test_get_output_status(driver: SimulatedPSU, channel: int, enabled: bool) -> None:
    assert driver.get_output_status(channel=channel) is False

    driver.output_enable(enabled, channel=channel)

    assert driver.get_output_status(channel=channel) is True


@pytest.mark.parametrize(
    ("channel", "ovp_level"),
    [
        (1, 12.0),
        (2, 13.0),
    ],
)
def test_set_overvoltage_protection_level(driver: SimulatedPSU, channel: int, ovp_level: float) -> None:
    driver.set_overvoltage_protection_level(ovp_level, channel=channel)

    assert driver.get_overvoltage_protection_level(channel=channel) == pytest.approx(ovp_level)


@pytest.mark.parametrize(
    ("channel", "ovp_level"),
    [
        (1, 12.0),
        (2, 13.0),
    ],
)
def test_get_overvoltage_protection_level(driver: SimulatedPSU, channel: int, ovp_level: float) -> None:
    driver.set_overvoltage_protection_level(ovp_level, channel=channel)

    level = driver.get_overvoltage_protection_level(channel=channel)

    assert level == pytest.approx(ovp_level)


@pytest.mark.parametrize(
    ("channel", "enabled", "disabled"),
    [
        (1, True, False),
        (2, True, False),
    ],
)
def test_set_overvoltage_protection_enabled(driver: SimulatedPSU, channel: int, enabled: bool, disabled: bool) -> None:
    driver.set_overvoltage_protection_enabled(enabled, channel=channel)
    assert driver.get_overvoltage_protection_enabled(channel=channel) is True

    driver.set_overvoltage_protection_enabled(disabled, channel=channel)
    assert driver.get_overvoltage_protection_enabled(channel=channel) is False


@pytest.mark.parametrize(
    ("channel", "enabled"),
    [
        (1, True),
        (2, True),
    ],
)
def test_get_overvoltage_protection_enabled(driver: SimulatedPSU, channel: int, enabled: bool) -> None:
    assert driver.get_overvoltage_protection_enabled(channel=channel) is False

    driver.set_overvoltage_protection_enabled(enabled, channel=channel)

    assert driver.get_overvoltage_protection_enabled(channel=channel) is True


@pytest.mark.parametrize(
    ("channel", "delay"),
    [
        (1, 0.1),
        (2, 0.2),
    ],
)
def test_set_overvoltage_protection_delay_unsupported(driver: SimulatedPSU, channel: int, delay: float) -> None:
    with pytest.raises(FeatureNotSupportedError, match="set_overvoltage_protection_delay is not supported"):
        driver.set_overvoltage_protection_delay(delay, channel=channel)


@pytest.mark.parametrize("channel", [1, 2])
def test_get_overvoltage_protection_delay_unsupported(driver: SimulatedPSU, channel: int) -> None:
    with pytest.raises(FeatureNotSupportedError, match="get_overvoltage_protection_delay is not supported"):
        driver.get_overvoltage_protection_delay(channel=channel)


@pytest.mark.parametrize(
    ("channel", "ocp_level"),
    [
        (1, 2.0),
        (2, 2.5),
    ],
)
def test_set_overcurrent_protection_level(driver: SimulatedPSU, channel: int, ocp_level: float) -> None:
    driver.set_overcurrent_protection_level(ocp_level, channel=channel)

    assert driver.get_overcurrent_protection_level(channel=channel) == pytest.approx(ocp_level)


@pytest.mark.parametrize(
    ("channel", "ocp_level"),
    [
        (1, 2.0),
        (2, 2.5),
    ],
)
def test_get_overcurrent_protection_level(driver: SimulatedPSU, channel: int, ocp_level: float) -> None:
    driver.set_overcurrent_protection_level(ocp_level, channel=channel)

    level = driver.get_overcurrent_protection_level(channel=channel)

    assert level == pytest.approx(ocp_level)


@pytest.mark.parametrize(
    ("channel", "enabled", "disabled"),
    [
        (1, True, False),
        (2, True, False),
    ],
)
def test_set_overcurrent_protection_enabled(driver: SimulatedPSU, channel: int, enabled: bool, disabled: bool) -> None:
    driver.set_overcurrent_protection_enabled(enabled, channel=channel)
    assert driver.get_overcurrent_protection_enabled(channel=channel) is True

    driver.set_overcurrent_protection_enabled(disabled, channel=channel)
    assert driver.get_overcurrent_protection_enabled(channel=channel) is False


@pytest.mark.parametrize(
    ("channel", "enabled"),
    [
        (1, True),
        (2, True),
    ],
)
def test_get_overcurrent_protection_enabled(driver: SimulatedPSU, channel: int, enabled: bool) -> None:
    assert driver.get_overcurrent_protection_enabled(channel=channel) is False

    driver.set_overcurrent_protection_enabled(enabled, channel=channel)

    assert driver.get_overcurrent_protection_enabled(channel=channel) is True


@pytest.mark.parametrize(
    ("channel", "enabled", "disabled"),
    [
        (1, True, False),
        (2, True, False),
    ],
)
def test_set_remote_sense_enabled(driver: SimulatedPSU, channel: int, enabled: bool, disabled: bool) -> None:
    driver.set_remote_sense_enabled(enabled, channel=channel)
    assert driver.get_remote_sense_enabled(channel=channel) is True

    driver.set_remote_sense_enabled(disabled, channel=channel)
    assert driver.get_remote_sense_enabled(channel=channel) is False


@pytest.mark.parametrize(
    ("channel", "enabled"),
    [
        (1, True),
        (2, True),
    ],
)
def test_get_remote_sense_enabled(driver: SimulatedPSU, channel: int, enabled: bool) -> None:
    assert driver.get_remote_sense_enabled(channel=channel) is False

    driver.set_remote_sense_enabled(enabled, channel=channel)

    assert driver.get_remote_sense_enabled(channel=channel) is True


@pytest.mark.parametrize(
    ("channel", "ovp_level", "voltage"),
    [
        (1, 3.0, 6.0),
        (2, 4.0, 7.5),
    ],
)
def test_set_voltage_above_overvoltage_protection_raises(
    driver: SimulatedPSU,
    channel: int,
    ovp_level: float,
    voltage: float,
) -> None:
    driver.set_overvoltage_protection_level(ovp_level, channel=channel)

    with pytest.raises(RuntimeError, match="PV Above OVP"):
        driver.set_voltage(voltage, channel=channel)


@pytest.mark.parametrize(
    ("channel", "ocp_level", "current_limit"),
    [
        (1, 0.5, 1.0),
        (2, 0.75, 1.5),
    ],
)
def test_set_current_limit_above_overcurrent_protection_raises(
    driver: SimulatedPSU,
    channel: int,
    ocp_level: float,
    current_limit: float,
) -> None:
    driver.set_overcurrent_protection_level(ocp_level, channel=channel)

    with pytest.raises(RuntimeError, match="PC Above OCP"):
        driver.set_current_limit(current_limit, channel=channel)


@pytest.mark.parametrize(
    ("channel", "voltage", "ovp_level"),
    [
        (1, 6.0, 3.0),
        (2, 7.5, 4.0),
    ],
)
def test_set_overvoltage_protection_below_programmed_voltage_raises(
    driver: SimulatedPSU,
    channel: int,
    voltage: float,
    ovp_level: float,
) -> None:
    driver.set_voltage(voltage, channel=channel)

    with pytest.raises(RuntimeError, match="OVP Below PV"):
        driver.set_overvoltage_protection_level(ovp_level, channel=channel)


@pytest.mark.parametrize(
    ("channel", "current_limit", "ocp_level"),
    [
        (1, 1.0, 0.5),
        (2, 1.5, 0.75),
    ],
)
def test_set_overcurrent_protection_below_programmed_current_raises(
    driver: SimulatedPSU,
    channel: int,
    current_limit: float,
    ocp_level: float,
) -> None:
    driver.set_current_limit(current_limit, channel=channel)

    with pytest.raises(RuntimeError, match="OCP Below PC"):
        driver.set_overcurrent_protection_level(ocp_level, channel=channel)


@pytest.mark.parametrize(
    ("channel", "voltage"),
    [
        (1, 61.0),
        (2, 61.0),
    ],
)
def test_set_voltage_out_of_range_raises(driver: SimulatedPSU, channel: int, voltage: float) -> None:
    with pytest.raises(RuntimeError, match="Data out of range"):
        driver.set_voltage(voltage, channel=channel)


@pytest.mark.parametrize(
    ("channel", "current_limit"),
    [
        (1, 11.0),
        (2, 11.0),
    ],
)
def test_set_current_limit_out_of_range_raises(driver: SimulatedPSU, channel: int, current_limit: float) -> None:
    with pytest.raises(RuntimeError, match="Data out of range"):
        driver.set_current_limit(current_limit, channel=channel)


@pytest.mark.parametrize(
    ("channel", "ovp_level"),
    [
        (1, 61.0),
        (2, 61.0),
    ],
)
def test_set_overvoltage_protection_out_of_range_raises(
    driver: SimulatedPSU,
    channel: int,
    ovp_level: float,
) -> None:
    with pytest.raises(RuntimeError, match="Data out of range"):
        driver.set_overvoltage_protection_level(ovp_level, channel=channel)


@pytest.mark.parametrize(
    ("channel", "ocp_level"),
    [
        (1, 11.0),
        (2, 11.0),
    ],
)
def test_set_overcurrent_protection_out_of_range_raises(
    driver: SimulatedPSU,
    channel: int,
    ocp_level: float,
) -> None:
    with pytest.raises(RuntimeError, match="Data out of range"):
        driver.set_overcurrent_protection_level(ocp_level, channel=channel)


@pytest.mark.parametrize(("invalid_channel", "voltage"), [(3, 6.0)])
def test_set_voltage_invalid_channel(driver: SimulatedPSU, invalid_channel: int, voltage: float) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.set_voltage(voltage, channel=invalid_channel)


@pytest.mark.parametrize("invalid_channel", [3])
def test_get_voltage_invalid_channel(driver: SimulatedPSU, invalid_channel: int) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.get_voltage(channel=invalid_channel)


@pytest.mark.parametrize(("invalid_channel", "current_limit"), [(3, 1.0)])
def test_set_current_limit_invalid_channel(driver: SimulatedPSU, invalid_channel: int, current_limit: float) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.set_current_limit(current_limit, channel=invalid_channel)


@pytest.mark.parametrize("invalid_channel", [3])
def test_get_current_invalid_channel(driver: SimulatedPSU, invalid_channel: int) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.get_current(channel=invalid_channel)


@pytest.mark.parametrize(("invalid_channel", "enabled"), [(3, True)])
def test_output_enable_invalid_channel(driver: SimulatedPSU, invalid_channel: int, enabled: bool) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.output_enable(enabled, channel=invalid_channel)


@pytest.mark.parametrize("invalid_channel", [3])
def test_get_output_status_invalid_channel(driver: SimulatedPSU, invalid_channel: int) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.get_output_status(channel=invalid_channel)


@pytest.mark.parametrize(("invalid_channel", "ovp_level"), [(3, 12.0)])
def test_set_overvoltage_protection_level_invalid_channel(
    driver: SimulatedPSU,
    invalid_channel: int,
    ovp_level: float,
) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.set_overvoltage_protection_level(ovp_level, channel=invalid_channel)


@pytest.mark.parametrize("invalid_channel", [3])
def test_get_overvoltage_protection_level_invalid_channel(driver: SimulatedPSU, invalid_channel: int) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.get_overvoltage_protection_level(channel=invalid_channel)


@pytest.mark.parametrize(("invalid_channel", "enabled"), [(3, True)])
def test_set_overvoltage_protection_enabled_invalid_channel(
    driver: SimulatedPSU,
    invalid_channel: int,
    enabled: bool,
) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.set_overvoltage_protection_enabled(enabled, channel=invalid_channel)


@pytest.mark.parametrize("invalid_channel", [3])
def test_get_overvoltage_protection_enabled_invalid_channel(driver: SimulatedPSU, invalid_channel: int) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.get_overvoltage_protection_enabled(channel=invalid_channel)


@pytest.mark.parametrize(("invalid_channel", "ocp_level"), [(3, 2.0)])
def test_set_overcurrent_protection_level_invalid_channel(
    driver: SimulatedPSU,
    invalid_channel: int,
    ocp_level: float,
) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.set_overcurrent_protection_level(ocp_level, channel=invalid_channel)


@pytest.mark.parametrize("invalid_channel", [3])
def test_get_overcurrent_protection_level_invalid_channel(driver: SimulatedPSU, invalid_channel: int) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.get_overcurrent_protection_level(channel=invalid_channel)


@pytest.mark.parametrize(("invalid_channel", "enabled"), [(3, True)])
def test_set_overcurrent_protection_enabled_invalid_channel(
    driver: SimulatedPSU,
    invalid_channel: int,
    enabled: bool,
) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.set_overcurrent_protection_enabled(enabled, channel=invalid_channel)


@pytest.mark.parametrize("invalid_channel", [3])
def test_get_overcurrent_protection_enabled_invalid_channel(driver: SimulatedPSU, invalid_channel: int) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.get_overcurrent_protection_enabled(channel=invalid_channel)


@pytest.mark.parametrize(("invalid_channel", "enabled"), [(3, True)])
def test_set_remote_sense_enabled_invalid_channel(driver: SimulatedPSU, invalid_channel: int, enabled: bool) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.set_remote_sense_enabled(enabled, channel=invalid_channel)


@pytest.mark.parametrize("invalid_channel", [3])
def test_get_remote_sense_enabled_invalid_channel(driver: SimulatedPSU, invalid_channel: int) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.get_remote_sense_enabled(channel=invalid_channel)


@pytest.mark.parametrize(
    ("channel", "voltage", "low_ovp_level", "ovp_level"),
    [
        (1, 6.0, 3.0, 12.0),
        (2, 7.5, 4.0, 13.0),
    ],
)
def test_driver_recovers_after_simulator_error(
    driver: SimulatedPSU,
    channel: int,
    voltage: float,
    low_ovp_level: float,
    ovp_level: float,
) -> None:
    driver.set_voltage(voltage, channel=channel)

    with pytest.raises(RuntimeError, match="OVP Below PV"):
        driver.set_overvoltage_protection_level(low_ovp_level, channel=channel)

    driver.set_overvoltage_protection_level(ovp_level, channel=channel)
    assert driver.get_overvoltage_protection_level(channel=channel) == pytest.approx(ovp_level)


@pytest.fixture(scope="module")
def sim_target(request: pytest.FixtureRequest) -> "_SimulatedTarget":
    target = _SimulatedTarget.start()
    request.addfinalizer(target.shutdown)
    return target


class _SimulatedTarget:
    def __init__(self, simulator: SimulatedPSUSimulator, server: SimulatedPSUServer, visa_address: str) -> None:
        self.simulator = simulator
        self.server = server
        self.visa_address = visa_address

    @classmethod
    def start(cls) -> "_SimulatedTarget":
        simulator = SimulatedPSUSimulator(num_channels=2)
        # Bind an ephemeral port to avoid EADDRINUSE collisions on shared CI runners.
        server = SimulatedPSUServer(simulator, host="127.0.0.1", port=0)
        server.start()
        visa_address = f"TCPIP0::127.0.0.1::{server.port}::SOCKET"
        return cls(simulator, server, visa_address)

    def shutdown(self) -> None:
        self.server.shutdown()
