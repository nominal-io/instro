"""Software tests for the simulated PSU driver."""

from collections.abc import Iterator
from unittest.mock import MagicMock, call, patch

import pytest

from instro.lib.exceptions import FeatureNotSupportedError
from instro.lib.transports import VisaConfig
from instro.psu.drivers.simulated import SimulatedPSU


@pytest.fixture
def sim_visa_cls() -> Iterator[MagicMock]:
    with patch("instro.psu.drivers.simulated.VisaDriver", autospec=True) as cls:
        yield cls


@pytest.fixture
def sim_visa(sim_visa_cls: MagicMock) -> MagicMock:
    visa = sim_visa_cls.return_value
    visa.query.return_value = '0,"No error"'
    return visa


@pytest.fixture
def sim(sim_visa_cls: MagicMock) -> SimulatedPSU:
    return SimulatedPSU("TCPIP0::127.0.0.1::5025::SOCKET")


def test_simulated_init_builds_visa_driver_from_resource(sim_visa_cls: MagicMock) -> None:
    SimulatedPSU("TCPIP0::127.0.0.1::5025::SOCKET")

    sim_visa_cls.assert_called_once_with("TCPIP0::127.0.0.1::5025::SOCKET")


def test_simulated_init_accepts_prebuilt_connection_config(sim_visa_cls: MagicMock) -> None:
    config = VisaConfig(visa_resource="TCPIP0::127.0.0.1::5025::SOCKET")
    SimulatedPSU(config)

    sim_visa_cls.assert_called_once_with(config)


def test_simulated_open_close_delegate_to_visa(sim: SimulatedPSU, sim_visa: MagicMock) -> None:
    sim.open()
    sim.close()

    sim_visa.open.assert_called_once()
    sim_visa.close.assert_called_once()


def test_simulated_set_voltage_writes_channel_command(sim: SimulatedPSU, sim_visa: MagicMock) -> None:
    sim.set_voltage(5.0, channel=2)

    sim_visa.write.assert_called_once_with(":SOUR2:VOLT 5.000")
    sim_visa.query.assert_called_once_with(":SYST:ERR?")


def test_simulated_get_voltage_queries_channel_command(sim: SimulatedPSU, sim_visa: MagicMock) -> None:
    sim_visa.query.side_effect = ["1.234", '0,"No error"']

    assert sim.get_voltage(channel=2) == pytest.approx(1.234)
    assert sim_visa.query.call_args_list == [call(":MEAS2:VOLT?"), call(":SYST:ERR?")]


def test_simulated_set_current_limit_writes_channel_command(sim: SimulatedPSU, sim_visa: MagicMock) -> None:
    sim.set_current_limit(0.5, channel=2)

    sim_visa.write.assert_called_once_with(":SOUR2:CURR 0.500")
    sim_visa.query.assert_called_once_with(":SYST:ERR?")


def test_simulated_get_current_queries_channel_command(sim: SimulatedPSU, sim_visa: MagicMock) -> None:
    sim_visa.query.side_effect = ["0.250", '0,"No error"']

    assert sim.get_current(channel=1) == pytest.approx(0.25)
    assert sim_visa.query.call_args_list == [call(":MEAS1:CURR?"), call(":SYST:ERR?")]


def test_simulated_output_enable_writes_on_and_off(sim: SimulatedPSU, sim_visa: MagicMock) -> None:
    sim.output_enable(True, channel=2)
    sim.output_enable(False, channel=2)

    assert sim_visa.write.call_args_list == [call(":OUTP2:STAT ON"), call(":OUTP2:STAT OFF")]
    assert sim_visa.query.call_args_list == [call(":SYST:ERR?"), call(":SYST:ERR?")]


def test_simulated_get_output_status_parses_state(sim: SimulatedPSU, sim_visa: MagicMock) -> None:
    sim_visa.query.side_effect = ["1", '0,"No error"']
    assert sim.get_output_status(channel=1) is True

    sim_visa.query.side_effect = ["0", '0,"No error"']
    assert sim.get_output_status(channel=1) is False


def test_simulated_set_overvoltage_protection_level_writes_channel_command(
    sim: SimulatedPSU,
    sim_visa: MagicMock,
) -> None:
    sim.set_overvoltage_protection_level(12.0, channel=2)

    sim_visa.write.assert_called_once_with(":SOUR2:VOLT:PROT 12.000")
    sim_visa.query.assert_called_once_with(":SYST:ERR?")


def test_simulated_get_overvoltage_protection_level_queries_channel_command(
    sim: SimulatedPSU,
    sim_visa: MagicMock,
) -> None:
    sim_visa.query.side_effect = ["12.000", '0,"No error"']

    assert sim.get_overvoltage_protection_level(channel=2) == pytest.approx(12.0)
    assert sim_visa.query.call_args_list == [call(":SOUR2:VOLT:PROT:LEV?"), call(":SYST:ERR?")]


def test_simulated_set_overvoltage_protection_enabled_writes_state(
    sim: SimulatedPSU,
    sim_visa: MagicMock,
) -> None:
    sim.set_overvoltage_protection_enabled(True, channel=2)
    sim.set_overvoltage_protection_enabled(False, channel=2)

    assert sim_visa.write.call_args_list == [
        call(":SOUR2:VOLT:PROT:STAT ON"),
        call(":SOUR2:VOLT:PROT:STAT OFF"),
    ]


def test_simulated_get_overvoltage_protection_enabled_parses_state(
    sim: SimulatedPSU,
    sim_visa: MagicMock,
) -> None:
    sim_visa.query.side_effect = ["1", '0,"No error"']
    assert sim.get_overvoltage_protection_enabled(channel=1) is True

    sim_visa.query.side_effect = ["0", '0,"No error"']
    assert sim.get_overvoltage_protection_enabled(channel=1) is False


def test_simulated_set_overvoltage_protection_delay_unsupported(
    sim: SimulatedPSU,
    sim_visa: MagicMock,
) -> None:
    with pytest.raises(FeatureNotSupportedError, match="set_overvoltage_protection_delay is not supported"):
        sim.set_overvoltage_protection_delay(0.25, channel=1)

    sim_visa.write.assert_not_called()
    sim_visa.query.assert_not_called()


def test_simulated_get_overvoltage_protection_delay_unsupported(
    sim: SimulatedPSU,
    sim_visa: MagicMock,
) -> None:
    with pytest.raises(FeatureNotSupportedError, match="get_overvoltage_protection_delay is not supported"):
        sim.get_overvoltage_protection_delay(channel=1)

    sim_visa.write.assert_not_called()
    sim_visa.query.assert_not_called()


def test_simulated_set_overcurrent_protection_level_writes_channel_command(
    sim: SimulatedPSU,
    sim_visa: MagicMock,
) -> None:
    sim.set_overcurrent_protection_level(2.0, channel=2)

    sim_visa.write.assert_called_once_with(":SOUR2:CURR:PROT 2.000")
    sim_visa.query.assert_called_once_with(":SYST:ERR?")


def test_simulated_get_overcurrent_protection_level_queries_channel_command(
    sim: SimulatedPSU,
    sim_visa: MagicMock,
) -> None:
    sim_visa.query.side_effect = ["2.000", '0,"No error"']

    assert sim.get_overcurrent_protection_level(channel=2) == pytest.approx(2.0)
    assert sim_visa.query.call_args_list == [call(":SOUR2:CURR:PROT:LEV?"), call(":SYST:ERR?")]


def test_simulated_set_overcurrent_protection_enabled_writes_state(
    sim: SimulatedPSU,
    sim_visa: MagicMock,
) -> None:
    sim.set_overcurrent_protection_enabled(True, channel=2)
    sim.set_overcurrent_protection_enabled(False, channel=2)

    assert sim_visa.write.call_args_list == [
        call(":SOUR2:CURR:PROT:STAT ON"),
        call(":SOUR2:CURR:PROT:STAT OFF"),
    ]


def test_simulated_get_overcurrent_protection_enabled_parses_state(
    sim: SimulatedPSU,
    sim_visa: MagicMock,
) -> None:
    sim_visa.query.side_effect = ["1", '0,"No error"']
    assert sim.get_overcurrent_protection_enabled(channel=1) is True

    sim_visa.query.side_effect = ["0", '0,"No error"']
    assert sim.get_overcurrent_protection_enabled(channel=1) is False


def test_simulated_set_remote_sense_enabled_writes_state(sim: SimulatedPSU, sim_visa: MagicMock) -> None:
    sim.set_remote_sense_enabled(True, channel=2)
    sim.set_remote_sense_enabled(False, channel=2)

    assert sim_visa.write.call_args_list == [call(":SYST2:SENS REM"), call(":SYST2:SENS LOC")]


def test_simulated_get_remote_sense_enabled_parses_state(sim: SimulatedPSU, sim_visa: MagicMock) -> None:
    sim_visa.query.side_effect = ["REM", '0,"No error"']
    assert sim.get_remote_sense_enabled(channel=1) is True

    sim_visa.query.side_effect = ["LOC", '0,"No error"']
    assert sim.get_remote_sense_enabled(channel=1) is False


def test_simulated_check_errors_accepts_unsigned_zero(sim: SimulatedPSU, sim_visa: MagicMock) -> None:
    sim_visa.query.return_value = '0,"No error"'

    sim.set_voltage(1.0, channel=1)

    sim_visa.query.assert_called_once_with(":SYST:ERR?")


def test_simulated_check_errors_raises_on_nonzero(sim: SimulatedPSU, sim_visa: MagicMock) -> None:
    sim_visa.query.return_value = '-100,"Command error"'

    with pytest.raises(RuntimeError, match="Simulated PSU reported error"):
        sim.set_voltage(1.0, channel=1)
