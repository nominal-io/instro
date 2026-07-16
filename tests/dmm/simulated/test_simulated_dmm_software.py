"""Software tests for the simulated DMM driver."""

from collections.abc import Iterator
from unittest.mock import MagicMock, call, patch

import pytest

from instro.dmm.drivers.simulated import SimulatedDMM
from instro.dmm.types import MeasurementFunction
from instro.lib.transports import VisaConfig


@pytest.fixture
def sim_visa_cls() -> Iterator[MagicMock]:
    with patch("instro.dmm.drivers.simulated.VisaDriver", autospec=True) as cls:
        yield cls


@pytest.fixture
def sim_visa(sim_visa_cls: MagicMock) -> MagicMock:
    visa = sim_visa_cls.return_value
    visa.query.return_value = '0,"No error"'
    return visa


@pytest.fixture
def sim(sim_visa_cls: MagicMock) -> SimulatedDMM:
    return SimulatedDMM("TCPIP0::127.0.0.1::5026::SOCKET")


def test_simulated_init_builds_visa_driver_from_resource(sim_visa_cls: MagicMock) -> None:
    SimulatedDMM("TCPIP0::127.0.0.1::5026::SOCKET")

    sim_visa_cls.assert_called_once_with("TCPIP0::127.0.0.1::5026::SOCKET")


def test_simulated_init_accepts_prebuilt_connection_config(sim_visa_cls: MagicMock) -> None:
    config = VisaConfig(visa_resource="TCPIP0::127.0.0.1::5026::SOCKET")
    SimulatedDMM(config)

    sim_visa_cls.assert_called_once_with(config)


def test_simulated_open_close_delegate_to_visa(sim: SimulatedDMM, sim_visa: MagicMock) -> None:
    sim.open()
    sim.close()

    sim_visa.open.assert_called_once()
    sim_visa.close.assert_called_once()


@pytest.mark.parametrize(
    ("function", "name"),
    [
        (MeasurementFunction.DC_VOLTAGE, "VOLT:DC"),
        (MeasurementFunction.AC_VOLTAGE, "VOLT:AC"),
        (MeasurementFunction.DC_CURRENT, "CURR:DC"),
        (MeasurementFunction.AC_CURRENT, "CURR:AC"),
        (MeasurementFunction.TWO_WIRE_RESISTANCE, "RES"),
    ],
)
def test_simulated_set_measurement_function_writes_func(
    sim: SimulatedDMM, sim_visa: MagicMock, function: MeasurementFunction, name: str
) -> None:
    sim.set_measurement_function(function)

    sim_visa.write.assert_called_once_with(f'FUNC "{name}"')
    sim_visa.query.assert_called_once_with("SYST:ERR?")


def test_simulated_set_measurement_function_four_wire_unsupported(sim: SimulatedDMM) -> None:
    with pytest.raises(NotImplementedError, match="FOUR_WIRE_RESISTANCE"):
        sim.set_measurement_function(MeasurementFunction.FOUR_WIRE_RESISTANCE)


@pytest.mark.parametrize(
    ("method", "command"),
    [
        ("measure_dc_voltage", "MEAS:VOLT:DC?"),
        ("measure_ac_voltage", "MEAS:VOLT:AC?"),
        ("measure_dc_current", "MEAS:CURR:DC?"),
        ("measure_ac_current", "MEAS:CURR:AC?"),
        ("measure_resistance", "MEAS:RES?"),
    ],
)
def test_simulated_measure_queries_command(sim: SimulatedDMM, sim_visa: MagicMock, method: str, command: str) -> None:
    sim_visa.query.side_effect = ["1.234", '0,"No error"']

    assert getattr(sim, method)() == pytest.approx(1.234)
    assert sim_visa.query.call_args_list == [call(command), call("SYST:ERR?")]


def test_simulated_measure_raises_on_device_error(sim: SimulatedDMM, sim_visa: MagicMock) -> None:
    sim_visa.query.side_effect = ["1.234", '-113,"Undefined header"']

    with pytest.raises(RuntimeError, match="Simulated DMM reported error"):
        sim.measure_dc_voltage()


def test_simulated_optional_methods_raise_not_implemented(sim: SimulatedDMM) -> None:
    with pytest.raises(NotImplementedError):
        sim.set_digits(6)
    with pytest.raises(NotImplementedError):
        sim.set_dc_voltage_range(10.0)
    with pytest.raises(NotImplementedError):
        sim.measure_four_wire_resistance()
