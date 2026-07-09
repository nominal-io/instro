"""Software tests for the simulated E-Load driver."""

from collections.abc import Iterator
from unittest.mock import MagicMock, call, patch

import pytest

from instro.eload.drivers.simulated import SimulatedELoad
from instro.eload.types import LoadMode, SlewRateDirection
from instro.lib.transports import VisaConfig


@pytest.fixture
def sim_visa_cls() -> Iterator[MagicMock]:
    with patch("instro.eload.drivers.simulated.VisaDriver", autospec=True) as cls:
        yield cls


@pytest.fixture
def sim_visa(sim_visa_cls: MagicMock) -> MagicMock:
    visa = sim_visa_cls.return_value
    visa.query.return_value = '0,"No error"'
    return visa


@pytest.fixture
def sim(sim_visa_cls: MagicMock) -> SimulatedELoad:
    return SimulatedELoad("TCPIP0::127.0.0.1::5026::SOCKET")


def test_simulated_init_builds_visa_driver_from_resource(sim_visa_cls: MagicMock) -> None:
    SimulatedELoad("TCPIP0::127.0.0.1::5026::SOCKET")

    sim_visa_cls.assert_called_once_with("TCPIP0::127.0.0.1::5026::SOCKET")


def test_simulated_init_accepts_prebuilt_connection_config(sim_visa_cls: MagicMock) -> None:
    config = VisaConfig(visa_resource="TCPIP0::127.0.0.1::5026::SOCKET")
    SimulatedELoad(config)

    sim_visa_cls.assert_called_once_with(config)


def test_simulated_open_close_delegate_to_visa(sim: SimulatedELoad, sim_visa: MagicMock) -> None:
    sim.open()
    sim.close()

    sim_visa.open.assert_called_once()
    sim_visa.close.assert_called_once()


@pytest.mark.parametrize(
    ("mode", "token"),
    [
        (LoadMode.CC, "CURR"),
        (LoadMode.CV, "VOLT"),
        (LoadMode.CP, "POW"),
        (LoadMode.CR, "RES"),
    ],
)
def test_simulated_set_mode_writes_function_command(
    sim: SimulatedELoad,
    sim_visa: MagicMock,
    mode: LoadMode,
    token: str,
) -> None:
    sim.set_mode(mode, channel=2)

    sim_visa.write.assert_called_once_with(f":SOUR2:FUNC {token}")
    sim_visa.query.assert_called_once_with(":SYST:ERR?")


@pytest.mark.parametrize(
    ("mode", "value", "command"),
    [
        (LoadMode.CC, 1.5, ":SOUR2:CURR 1.500"),
        (LoadMode.CV, 5.0, ":SOUR2:VOLT 5.000"),
        (LoadMode.CP, 60.0, ":SOUR2:POW 60.000"),
        (LoadMode.CR, 100.0, ":SOUR2:RES 100.000"),
    ],
)
def test_simulated_set_level_writes_mode_command(
    sim: SimulatedELoad,
    sim_visa: MagicMock,
    mode: LoadMode,
    value: float,
    command: str,
) -> None:
    sim.set_level(mode, value, channel=2, curr_limit=None)

    sim_visa.write.assert_called_once_with(command)
    sim_visa.query.assert_called_once_with(":SYST:ERR?")


def test_simulated_set_level_cv_with_current_limit_writes_both(sim: SimulatedELoad, sim_visa: MagicMock) -> None:
    sim.set_level(LoadMode.CV, 5.0, channel=1, curr_limit=2.0)

    assert sim_visa.write.call_args_list == [call(":SOUR1:VOLT 5.000"), call(":SOUR1:CURR:LIM 2.000")]
    sim_visa.query.assert_called_once_with(":SYST:ERR?")


def test_simulated_set_level_non_cv_ignores_current_limit(sim: SimulatedELoad, sim_visa: MagicMock) -> None:
    sim.set_level(LoadMode.CC, 1.0, channel=1, curr_limit=2.0)

    sim_visa.write.assert_called_once_with(":SOUR1:CURR 1.000")


@pytest.mark.parametrize(
    ("mode", "value", "command"),
    [
        (LoadMode.CC, 10.0, ":SOUR1:CURR:RANG 10.000"),
        (LoadMode.CV, 15.0, ":SOUR1:VOLT:RANG 15.000"),
        (LoadMode.CP, 100.0, ":SOUR1:POW:RANG 100.000"),
        (LoadMode.CR, 500.0, ":SOUR1:RES:RANG 500.000"),
    ],
)
def test_simulated_set_range_writes_mode_command(
    sim: SimulatedELoad,
    sim_visa: MagicMock,
    mode: LoadMode,
    value: float,
    command: str,
) -> None:
    sim.set_range(mode, value, channel=1)

    sim_visa.write.assert_called_once_with(command)
    sim_visa.query.assert_called_once_with(":SYST:ERR?")


@pytest.mark.parametrize(
    ("direction", "command"),
    [
        (SlewRateDirection.RISE, ":SOUR2:CURR:SLEW:RISE 0.500"),
        (SlewRateDirection.FALL, ":SOUR2:CURR:SLEW:FALL 0.500"),
        (SlewRateDirection.BOTH, ":SOUR2:CURR:SLEW 0.500"),
    ],
)
def test_simulated_set_slewrate_writes_direction_command(
    sim: SimulatedELoad,
    sim_visa: MagicMock,
    direction: SlewRateDirection,
    command: str,
) -> None:
    sim.set_slewrate(direction, 0.5, channel=2)

    sim_visa.write.assert_called_once_with(command)
    sim_visa.query.assert_called_once_with(":SYST:ERR?")


def test_simulated_output_enable_writes_on_and_off(sim: SimulatedELoad, sim_visa: MagicMock) -> None:
    sim.output_enable(True, channel=2)
    sim.output_enable(False, channel=2)

    assert sim_visa.write.call_args_list == [call(":INP2:STAT ON"), call(":INP2:STAT OFF")]
    assert sim_visa.query.call_args_list == [call(":SYST:ERR?"), call(":SYST:ERR?")]


def test_simulated_short_output_writes_on_and_off(sim: SimulatedELoad, sim_visa: MagicMock) -> None:
    sim.short_output(True, channel=1)
    sim.short_output(False, channel=1)

    assert sim_visa.write.call_args_list == [call(":INP1:SHOR ON"), call(":INP1:SHOR OFF")]


def test_simulated_get_current_queries_channel_command(sim: SimulatedELoad, sim_visa: MagicMock) -> None:
    sim_visa.query.side_effect = ["0.250", '0,"No error"']

    assert sim.get_current(channel=1) == pytest.approx(0.25)
    assert sim_visa.query.call_args_list == [call(":MEAS1:CURR?"), call(":SYST:ERR?")]


def test_simulated_get_voltage_queries_channel_command(sim: SimulatedELoad, sim_visa: MagicMock) -> None:
    sim_visa.query.side_effect = ["1.234", '0,"No error"']

    assert sim.get_voltage(channel=2) == pytest.approx(1.234)
    assert sim_visa.query.call_args_list == [call(":MEAS2:VOLT?"), call(":SYST:ERR?")]


def test_simulated_get_power_queries_channel_command(sim: SimulatedELoad, sim_visa: MagicMock) -> None:
    sim_visa.query.side_effect = ["12.5", '0,"No error"']

    assert sim.get_power(channel=1) == pytest.approx(12.5)
    assert sim_visa.query.call_args_list == [call(":MEAS1:POW?"), call(":SYST:ERR?")]


def test_simulated_check_errors_accepts_unsigned_zero(sim: SimulatedELoad, sim_visa: MagicMock) -> None:
    sim_visa.query.return_value = '0,"No error"'

    sim.set_level(LoadMode.CC, 1.0, channel=1, curr_limit=None)

    sim_visa.query.assert_called_once_with(":SYST:ERR?")


def test_simulated_check_errors_raises_on_nonzero(sim: SimulatedELoad, sim_visa: MagicMock) -> None:
    sim_visa.query.return_value = '-222,"Data out of range"'

    with pytest.raises(RuntimeError, match="Simulated E-Load reported error"):
        sim.set_level(LoadMode.CC, 1.0, channel=1, curr_limit=None)
