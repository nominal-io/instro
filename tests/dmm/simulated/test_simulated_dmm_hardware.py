"""Full-transport tests for the simulated DMM driver."""

from __future__ import annotations

import pytest

from instro.dmm import InstroDMM, MeasurementFunction
from instro.dmm.drivers.simulated import SimulatedDMM
from instro.dmm.scpi_sim_server import (
    DEFAULT_STIMULUS,
    FUNCTION_AC_CURRENT,
    FUNCTION_AC_VOLTAGE,
    FUNCTION_DC_CURRENT,
    FUNCTION_DC_VOLTAGE,
    FUNCTION_RESISTANCE,
    SimulatedDMMServer,
)
from instro.dmm.scpi_sim_server import SimulatedDMM as SimulatedDMMSimulator
from instro.lib.transports import VisaConfig

# SIMULATED HARDWARE TEST TEMPLATE:
#
# Copy this file into tests/dmm/<vendor>/test_<driver>_hardware.py, uncomment
# pytestmark, set VISA_ADDRESS to the bench instrument address, and instantiate
# the real driver in driver(). Keep reset_before_each_test() for SCPI/VISA
# instruments that accept *RST; replace it for hardware with a different reset
# path. Delete sim_target, _SimulatedTarget, and simulator imports because real
# hardware tests do not need to launch the local SCPI simulator.
# pytestmark = pytest.mark.hardware

VISA_ADDRESS = "TCPIP0::127.0.0.1::5026::SOCKET"


@pytest.fixture(scope="module")
def driver(request: pytest.FixtureRequest, sim_target: "_SimulatedTarget") -> SimulatedDMM:
    dmm_driver = SimulatedDMM(
        VisaConfig(
            visa_resource=sim_target.visa_address,
        )
    )
    try:
        dmm_driver.open()
    except Exception:
        dmm_driver.close()
        raise

    request.addfinalizer(dmm_driver.close)
    return dmm_driver


@pytest.fixture(autouse=True)
def reset_before_each_test(driver: SimulatedDMM) -> None:
    driver._visa.write("*RST")


@pytest.mark.parametrize(
    "function",
    [
        MeasurementFunction.DC_VOLTAGE,
        MeasurementFunction.AC_VOLTAGE,
        MeasurementFunction.DC_CURRENT,
        MeasurementFunction.AC_CURRENT,
        MeasurementFunction.TWO_WIRE_RESISTANCE,
    ],
)
def test_set_measurement_function(driver: SimulatedDMM, function: MeasurementFunction) -> None:
    driver.set_measurement_function(function)


def test_set_measurement_function_four_wire_unsupported(driver: SimulatedDMM) -> None:
    with pytest.raises(NotImplementedError):
        driver.set_measurement_function(MeasurementFunction.FOUR_WIRE_RESISTANCE)


@pytest.mark.parametrize(
    ("method", "function"),
    [
        ("measure_dc_voltage", FUNCTION_DC_VOLTAGE),
        ("measure_ac_voltage", FUNCTION_AC_VOLTAGE),
        ("measure_dc_current", FUNCTION_DC_CURRENT),
        ("measure_ac_current", FUNCTION_AC_CURRENT),
        ("measure_resistance", FUNCTION_RESISTANCE),
    ],
)
def test_measure_returns_stimulus(driver: SimulatedDMM, method: str, function: str) -> None:
    value = getattr(driver, method)()

    assert value == pytest.approx(DEFAULT_STIMULUS[function], rel=0.05)


def test_measure_tracks_stimulus_change(
    driver: SimulatedDMM,
    sim_target: "_SimulatedTarget",
) -> None:
    original = sim_target.simulator.stimulus[FUNCTION_RESISTANCE]
    try:
        sim_target.simulator.set_stimulus(FUNCTION_RESISTANCE, 4700.0)
        assert driver.measure_resistance() == pytest.approx(4700.0, rel=0.05)
    finally:
        sim_target.simulator.set_stimulus(FUNCTION_RESISTANCE, original)


def test_device_error_raises(driver: SimulatedDMM) -> None:
    driver._visa.write(":BOGUS:HEADER")

    with pytest.raises(RuntimeError, match="Simulated DMM reported error"):
        driver.measure_dc_voltage()

    # The failed transaction popped the queued error; the next one succeeds.
    assert driver.measure_dc_voltage() == pytest.approx(DEFAULT_STIMULUS[FUNCTION_DC_VOLTAGE], rel=0.05)


def test_instro_dmm_reads_through_full_stack() -> None:
    # Dedicated server: the sim server handles one client at a time, and the
    # module-scoped driver fixture keeps its connection to sim_target open.
    target = _SimulatedTarget.start()
    try:
        with InstroDMM(name="sim_dmm", driver=SimulatedDMM(target.visa_address)) as dmm:
            for function, key in [
                (MeasurementFunction.DC_VOLTAGE, FUNCTION_DC_VOLTAGE),
                (MeasurementFunction.AC_CURRENT, FUNCTION_AC_CURRENT),
                (MeasurementFunction.TWO_WIRE_RESISTANCE, FUNCTION_RESISTANCE),
            ]:
                dmm.set_measurement_function(function)
                reading = dmm.read()
                assert reading.latest == pytest.approx(DEFAULT_STIMULUS[key], rel=0.05)
    finally:
        target.shutdown()


@pytest.fixture(scope="module")
def sim_target(request: pytest.FixtureRequest) -> "_SimulatedTarget":
    target = _SimulatedTarget.start()
    request.addfinalizer(target.shutdown)
    return target


class _SimulatedTarget:
    def __init__(self, simulator: SimulatedDMMSimulator, server: SimulatedDMMServer, visa_address: str) -> None:
        self.simulator = simulator
        self.server = server
        self.visa_address = visa_address

    @classmethod
    def start(cls) -> "_SimulatedTarget":
        simulator = SimulatedDMMSimulator()
        # Bind an ephemeral port to avoid EADDRINUSE collisions on shared CI runners.
        server = SimulatedDMMServer(simulator, host="127.0.0.1", port=0)
        server.start()
        visa_address = f"TCPIP0::127.0.0.1::{server.port}::SOCKET"
        return cls(simulator, server, visa_address)

    def shutdown(self) -> None:
        self.server.shutdown()
