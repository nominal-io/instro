"""Full-transport tests for the simulated E-Load driver."""

from __future__ import annotations

import pytest

from instro.eload.drivers.simulated import SimulatedELoad
from instro.eload.scpi_sim_server import SimulatedELoad as SimulatedELoadSimulator
from instro.eload.scpi_sim_server import SimulatedELoadServer
from instro.eload.types import LoadMode, SlewRateDirection
from instro.lib.transports import VisaConfig

# SIMULATED HARDWARE TEST TEMPLATE:
#
# Copy this file into tests/eload/<vendor>/test_<driver>_hardware.py, uncomment
# pytestmark, set VISA_ADDRESS to the bench instrument address, and instantiate
# the real driver in driver(). Keep reset_before_each_test() for SCPI/VISA
# instruments that accept *RST; replace it for hardware with a different reset
# path. Delete sim_target, _SimulatedTarget, and simulator imports because real
# hardware tests do not need to launch the local SCPI simulator.
# pytestmark = pytest.mark.hardware

VISA_ADDRESS = "TCPIP0::127.0.0.1::5026::SOCKET"

# The default simulated source is 12 V behind 0.5 ohm.
SOURCE_VOLTAGE = 12.0
SOURCE_RESISTANCE = 0.5


@pytest.fixture(scope="module")
def driver(request: pytest.FixtureRequest, sim_target: "_SimulatedTarget") -> SimulatedELoad:
    eload_driver = SimulatedELoad(
        VisaConfig(
            visa_resource=sim_target.visa_address,
        )
    )
    try:
        eload_driver.open()
    except Exception:
        eload_driver.close()
        raise

    request.addfinalizer(eload_driver.close)
    return eload_driver


@pytest.fixture(autouse=True)
def reset_before_each_test(driver: SimulatedELoad) -> None:
    driver._visa.write("*RST")


@pytest.mark.parametrize(
    ("channel", "current"),
    [
        (1, 1.0),
        (2, 2.0),
    ],
)
def test_set_mode_and_level_cc(driver: SimulatedELoad, channel: int, current: float) -> None:
    driver.set_mode(LoadMode.CC, channel=channel)
    driver.set_level(LoadMode.CC, current, channel=channel, curr_limit=None)
    driver.output_enable(True, channel=channel)

    assert driver.get_current(channel=channel) == pytest.approx(current, rel=0.05)
    expected_voltage = SOURCE_VOLTAGE - current * SOURCE_RESISTANCE
    assert driver.get_voltage(channel=channel) == pytest.approx(expected_voltage, rel=0.05)


@pytest.mark.parametrize(
    ("channel", "voltage"),
    [
        (1, 6.0),
        (2, 8.0),
    ],
)
def test_set_mode_and_level_cv(driver: SimulatedELoad, channel: int, voltage: float) -> None:
    driver.set_mode(LoadMode.CV, channel=channel)
    driver.set_level(LoadMode.CV, voltage, channel=channel, curr_limit=None)
    driver.output_enable(True, channel=channel)

    assert driver.get_voltage(channel=channel) == pytest.approx(voltage, rel=0.05)
    expected_current = (SOURCE_VOLTAGE - voltage) / SOURCE_RESISTANCE
    assert driver.get_current(channel=channel) == pytest.approx(expected_current, rel=0.05)


@pytest.mark.parametrize(
    ("channel", "voltage", "curr_limit"),
    [
        (1, 6.0, 4.0),
        (2, 8.0, 2.0),
    ],
)
def test_set_level_cv_current_limit_clamps(
    driver: SimulatedELoad,
    channel: int,
    voltage: float,
    curr_limit: float,
) -> None:
    driver.set_mode(LoadMode.CV, channel=channel)
    driver.set_level(LoadMode.CV, voltage, channel=channel, curr_limit=curr_limit)
    driver.output_enable(True, channel=channel)

    assert driver.get_current(channel=channel) == pytest.approx(curr_limit, rel=0.05)
    expected_voltage = SOURCE_VOLTAGE - curr_limit * SOURCE_RESISTANCE
    assert driver.get_voltage(channel=channel) == pytest.approx(expected_voltage, rel=0.05)


@pytest.mark.parametrize(
    ("channel", "power"),
    [
        (1, 30.0),
        (2, 60.0),
    ],
)
def test_set_mode_and_level_cp(driver: SimulatedELoad, channel: int, power: float) -> None:
    driver.set_mode(LoadMode.CP, channel=channel)
    driver.set_level(LoadMode.CP, power, channel=channel, curr_limit=None)
    driver.output_enable(True, channel=channel)

    measured_power = driver.get_power(channel=channel)

    assert measured_power == pytest.approx(power, rel=0.05)


@pytest.mark.parametrize(
    ("channel", "resistance"),
    [
        (1, 10.0),
        (2, 20.0),
    ],
)
def test_set_mode_and_level_cr(driver: SimulatedELoad, channel: int, resistance: float) -> None:
    driver.set_mode(LoadMode.CR, channel=channel)
    driver.set_level(LoadMode.CR, resistance, channel=channel, curr_limit=None)
    driver.output_enable(True, channel=channel)

    expected_current = SOURCE_VOLTAGE / (SOURCE_RESISTANCE + resistance)
    assert driver.get_current(channel=channel) == pytest.approx(expected_current, rel=0.05)


@pytest.mark.parametrize(
    ("mode", "value"),
    [
        (LoadMode.CC, 10.0),
        (LoadMode.CV, 20.0),
        (LoadMode.CP, 100.0),
        (LoadMode.CR, 500.0),
    ],
)
def test_set_range(driver: SimulatedELoad, mode: LoadMode, value: float) -> None:
    driver.set_range(mode, value, channel=1)


def test_set_range_rejects_level_above_range(driver: SimulatedELoad) -> None:
    driver.set_mode(LoadMode.CC, channel=1)
    driver.set_range(LoadMode.CC, 10.0, channel=1)

    with pytest.raises(RuntimeError, match="Data out of range"):
        driver.set_level(LoadMode.CC, 15.0, channel=1, curr_limit=None)


@pytest.mark.parametrize(
    ("direction", "rate"),
    [
        (SlewRateDirection.RISE, 0.5),
        (SlewRateDirection.FALL, 0.25),
        (SlewRateDirection.BOTH, 1.0),
    ],
)
def test_set_slewrate(driver: SimulatedELoad, direction: SlewRateDirection, rate: float) -> None:
    driver.set_slewrate(direction, rate, channel=1)


def test_set_slewrate_out_of_range_raises(driver: SimulatedELoad) -> None:
    with pytest.raises(RuntimeError, match="Data out of range"):
        driver.set_slewrate(SlewRateDirection.BOTH, 100.0, channel=1)


@pytest.mark.parametrize("channel", [1, 2])
def test_output_enable_gates_current_draw(driver: SimulatedELoad, channel: int) -> None:
    driver.set_mode(LoadMode.CC, channel=channel)
    driver.set_level(LoadMode.CC, 1.0, channel=channel, curr_limit=None)

    assert driver.get_current(channel=channel) == pytest.approx(0.0, abs=0.01)

    driver.output_enable(True, channel=channel)
    assert driver.get_current(channel=channel) == pytest.approx(1.0, rel=0.05)

    driver.output_enable(False, channel=channel)
    assert driver.get_current(channel=channel) == pytest.approx(0.0, abs=0.01)


@pytest.mark.parametrize("channel", [1, 2])
def test_short_output(driver: SimulatedELoad, channel: int) -> None:
    driver.output_enable(True, channel=channel)
    driver.short_output(True, channel=channel)

    expected_current = SOURCE_VOLTAGE / SOURCE_RESISTANCE
    assert driver.get_current(channel=channel) == pytest.approx(expected_current, rel=0.05)
    assert driver.get_voltage(channel=channel) == pytest.approx(0.0, abs=0.01)

    driver.short_output(False, channel=channel)
    assert driver.get_voltage(channel=channel) == pytest.approx(SOURCE_VOLTAGE, rel=0.05)


def test_input_off_reads_open_circuit_source_voltage(driver: SimulatedELoad) -> None:
    assert driver.get_voltage(channel=1) == pytest.approx(SOURCE_VOLTAGE, rel=0.05)
    assert driver.get_current(channel=1) == pytest.approx(0.0, abs=0.01)


@pytest.mark.parametrize(
    ("mode", "value"),
    [
        (LoadMode.CC, 31.0),
        (LoadMode.CV, 151.0),
        (LoadMode.CP, 301.0),
        (LoadMode.CR, 10001.0),
    ],
)
def test_set_level_out_of_range_raises(driver: SimulatedELoad, mode: LoadMode, value: float) -> None:
    with pytest.raises(RuntimeError, match="Data out of range"):
        driver.set_level(mode, value, channel=1, curr_limit=None)


@pytest.mark.parametrize("invalid_channel", [3])
def test_set_mode_invalid_channel(driver: SimulatedELoad, invalid_channel: int) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.set_mode(LoadMode.CC, channel=invalid_channel)


@pytest.mark.parametrize("invalid_channel", [3])
def test_set_level_invalid_channel(driver: SimulatedELoad, invalid_channel: int) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.set_level(LoadMode.CC, 1.0, channel=invalid_channel, curr_limit=None)


@pytest.mark.parametrize("invalid_channel", [3])
def test_output_enable_invalid_channel(driver: SimulatedELoad, invalid_channel: int) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.output_enable(True, channel=invalid_channel)


@pytest.mark.parametrize("invalid_channel", [3])
def test_short_output_invalid_channel(driver: SimulatedELoad, invalid_channel: int) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.short_output(True, channel=invalid_channel)


@pytest.mark.parametrize("invalid_channel", [3])
def test_get_current_invalid_channel(driver: SimulatedELoad, invalid_channel: int) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.get_current(channel=invalid_channel)


@pytest.mark.parametrize("invalid_channel", [3])
def test_get_voltage_invalid_channel(driver: SimulatedELoad, invalid_channel: int) -> None:
    with pytest.raises(RuntimeError, match="Header suffix out of range"):
        driver.get_voltage(channel=invalid_channel)


def test_driver_recovers_after_simulator_error(driver: SimulatedELoad) -> None:
    with pytest.raises(RuntimeError, match="Data out of range"):
        driver.set_level(LoadMode.CC, 31.0, channel=1, curr_limit=None)

    driver.set_level(LoadMode.CC, 1.0, channel=1, curr_limit=None)
    driver.output_enable(True, channel=1)
    assert driver.get_current(channel=1) == pytest.approx(1.0, rel=0.05)


@pytest.fixture(scope="module")
def sim_target(request: pytest.FixtureRequest) -> "_SimulatedTarget":
    target = _SimulatedTarget.start()
    request.addfinalizer(target.shutdown)
    return target


class _SimulatedTarget:
    def __init__(
        self,
        simulator: SimulatedELoadSimulator,
        server: SimulatedELoadServer,
        visa_address: str,
    ) -> None:
        self.simulator = simulator
        self.server = server
        self.visa_address = visa_address

    @classmethod
    def start(cls) -> "_SimulatedTarget":
        simulator = SimulatedELoadSimulator(num_channels=2)
        # Bind an ephemeral port to avoid EADDRINUSE collisions on shared CI runners.
        server = SimulatedELoadServer(simulator, host="127.0.0.1", port=0)
        server.start()
        visa_address = f"TCPIP0::127.0.0.1::{server.port}::SOCKET"
        return cls(simulator, server, visa_address)

    def shutdown(self) -> None:
        self.server.shutdown()
