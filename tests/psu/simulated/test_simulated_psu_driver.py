"""Required integration tests for the simulated PSU driver."""

from __future__ import annotations

import socket

import pytest

from instro.lib.transports import VisaConfig
from instro.psu import PSUDriverBase
from instro.psu.drivers.simulated import SimulatedPSU
from instro.psu.scpi_sim_server import SimulatedPSU as SimulatedPSUSimulator
from instro.psu.scpi_sim_server import SimulatedPSUServer
from tests.psu.psu_driver_test_suite import PSUChannelConfig, PSUDriverTestSuite

# ============================================================================
# REAL HARDWARE TEMPLATE: EDIT THIS SECTION WHEN COPYING THIS FILE
#
# 1. Uncomment pytestmark so physical hardware tests are optional.
# 2. Replace CHANNELS with the real channel numbers and ranges.
# 3. Replace driver() with the concrete driver and bench resource.
# 4. Replace reset_before_each_test() if the driver is not SCPI/VISA-backed.
# 5. Rename TestSimulatedPSUDriver for the instrument under test.
# 6. Delete the simulation-only target setup at the bottom of the file.
# ============================================================================

# EDIT FOR REAL HARDWARE: uncomment this line.
# pytestmark = pytest.mark.hardware

# EDIT FOR REAL HARDWARE: set each physical output's channel number,
# programmable ranges, and acceptable readback tolerance.
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


@pytest.fixture(scope="module")
def driver(request: pytest.FixtureRequest, sim_target: "_SimulatedTarget") -> PSUDriverBase:
    # EDIT FOR REAL HARDWARE: instantiate the concrete driver here.
    # Example: psu_driver = KeysightE36100("USB0::...::INSTR")
    psu_driver = SimulatedPSU(
        VisaConfig(
            visa_resource=f"TCPIP0::{sim_target.host}::{sim_target.port}::SOCKET",
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
def reset_before_each_test(driver: PSUDriverBase) -> None:
    # EDIT FOR REAL HARDWARE: keep this for SCPI/VISA drivers, or replace this
    # fixture with the safest reset path for the instrument under test.
    driver._visa.write("*RST")  # type: ignore[attr-defined]


# EDIT FOR REAL HARDWARE: rename this class for the driver under test.
@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
class TestSimulatedPSUDriver(PSUDriverTestSuite):
    pass


@pytest.fixture(scope="module")
def invalid_channel() -> int:
    return max(channel.channel for channel in CHANNELS) + 1


# ============================================================================
# SIMULATION ONLY: DELETE THIS SECTION IN REAL HARDWARE TEST FILES
# ============================================================================


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
