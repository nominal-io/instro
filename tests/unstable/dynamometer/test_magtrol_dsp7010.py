from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest

from instro.lib import InstroError
from instro.lib.publishers import Publisher
from instro.lib.transports.visa import VisaConfig
from instro.lib.types import Command, Measurement
from instro.unstable.dynamometer.drivers import MagtrolDSP7010, TorqueUnit

# DSP7012 flag, ch1 ramp-up complete, ch2 speed alarm
_STATUS_WORD = (1 << 5) | (1 << 24) | (1 << 20)


@pytest.fixture
def visa_cls() -> Iterator[MagicMock]:
    with patch("instro.unstable.dynamometer.drivers.magtrol_dsp7010.VisaDriver", autospec=True) as cls:
        # Set commands answer with an empty line.
        cls.return_value.query.return_value = "\r"
        yield cls


@pytest.fixture
def visa(visa_cls: MagicMock) -> MagicMock:
    return visa_cls.return_value


@pytest.fixture
def dyno(visa_cls: MagicMock) -> MagtrolDSP7010:
    return MagtrolDSP7010("ASRL3::INSTR", name="dyno")


def _sent(visa: MagicMock) -> list[str]:
    return [c.args[0] for c in visa.query.call_args_list]


def test_init_accepts_str_or_config(visa_cls: MagicMock) -> None:
    MagtrolDSP7010("GPIB0::9::INSTR")
    config = VisaConfig(visa_resource="ASRL3::INSTR")
    MagtrolDSP7010(config)
    assert [c.args[0] for c in visa_cls.call_args_list] == ["GPIB0::9::INSTR", config]


def test_open_close_delegate(dyno: MagtrolDSP7010, visa: MagicMock) -> None:
    dyno.open()
    visa.open.assert_called_once_with()
    dyno.close()
    visa.close.assert_called_once_with()


def test_close_stops_daemon_before_transport_and_always_closes_it(visa_cls: MagicMock) -> None:
    publisher = MagicMock(spec=Publisher)
    publisher.close.side_effect = RuntimeError("publisher failed")
    dyno = MagtrolDSP7010("ASRL3::INSTR", publishers=[publisher])
    visa = visa_cls.return_value
    order: list[str] = []
    visa.close.side_effect = lambda: order.append("transport")

    with patch.object(dyno, "stop", side_effect=lambda: order.append("daemon")):
        with pytest.raises(RuntimeError, match="publisher failed"):
            dyno.close()

    assert order == ["daemon", "transport"]


def test_identify(dyno: MagtrolDSP7010, visa: MagicMock) -> None:
    visa.query.return_value = "DSP7012 D0 05\r"
    assert dyno.identify() == "DSP7012 D0 05"
    visa.query.assert_called_once_with("*IDN?")


@pytest.mark.parametrize("reply", ["COMAND ERROR", "COMMAND ERROR"])
def test_command_error_raises(dyno: MagtrolDSP7010, visa: MagicMock, reply: str) -> None:
    visa.query.return_value = reply
    with pytest.raises(InstroError, match="rejected 'OD1'"):
        dyno.read_data(1)


def test_read_data(dyno: MagtrolDSP7010, visa: MagicMock) -> None:
    visa.query.return_value = "S 1725T22.60R"

    reading = dyno.read_data(2)

    visa.query.assert_called_once_with("OD2")
    assert reading.channel_data == {
        "dyno.ch2.speed": [1725.0],
        "dyno.ch2.torque": [22.6],
        "dyno.ch2.alarm": [0.0],
        "dyno.ch2.direction": [1.0],
    }


def test_read_data_negative_torque_ccw(dyno: MagtrolDSP7010, visa: MagicMock) -> None:
    visa.query.return_value = "S0T-1.25L"

    reading = dyno.read_data(1)

    assert reading.channel_data["dyno.ch1.torque"] == [-1.25]
    assert reading.channel_data["dyno.ch1.direction"] == [-1.0]


def test_read_data_alarm_has_no_direction(dyno: MagtrolDSP7010, visa: MagicMock) -> None:
    visa.query.return_value = "S 300T0.50A"

    reading = dyno.read_data(1)

    assert reading.channel_data["dyno.ch1.alarm"] == [1.0]
    assert "dyno.ch1.direction" not in reading.channel_data


def test_read_data_power_needs_units(dyno: MagtrolDSP7010, visa: MagicMock) -> None:
    visa.query.side_effect = ["S 3000T2.000R", "\r", "S 3000T2.000R"]
    assert "dyno.ch1.power" not in dyno.read_data(1).channel_data

    dyno.set_torque_units(1, TorqueUnit.N_M)

    assert _sent(visa) == ["OD1", "UR1,8"]
    assert dyno.read_data(1).channel_data["dyno.ch1.power"] == [pytest.approx(2.0 * 3000 * 2 * 3.141592653589793 / 60)]


@pytest.mark.parametrize("reply", ["garbage", "S 100T.R"])
def test_read_data_bad_reply(dyno: MagtrolDSP7010, visa: MagicMock, reply: str) -> None:
    visa.query.return_value = reply
    with pytest.raises(InstroError, match="unparseable"):
        dyno.read_data(1)


def test_reopen_forgets_torque_units(dyno: MagtrolDSP7010, visa: MagicMock) -> None:
    dyno.set_torque_units(1, TorqueUnit.N_M)
    dyno.close()
    dyno.open()
    visa.query.return_value = "S 3000T2.000R"

    assert "dyno.ch1.power" not in dyno.read_data(1).channel_data


def test_get_status(dyno: MagtrolDSP7010, visa: MagicMock) -> None:
    visa.query.return_value = f"{_STATUS_WORD:08x}"

    status = dyno.get_status().channel_data

    visa.query.assert_called_once_with("STAT1")
    assert status["dyno.status.word"] == [float(_STATUS_WORD)]
    assert status["dyno.status.dsp7012"] == [1.0]
    assert status["dyno.ch2.alarm.speed"] == [1.0]
    assert status["dyno.ch1.ramp_up_complete"] == [1.0]
    assert status["dyno.ch1.alarm.speed"] == [0.0]
    assert status["dyno.alarm.global_power"] == [0.0]


def test_get_status_dsp7011_has_no_ch2(dyno: MagtrolDSP7010, visa: MagicMock) -> None:
    visa.query.return_value = "8000cc5f\r"  # captured from a DSP7011

    status = dyno.get_status().channel_data

    assert status["dyno.status.dsp7012"] == [0.0]
    assert status["dyno.ch1.alarm.external"] == [1.0]
    assert status["dyno.ch1.alarm.water"] == [1.0]
    assert status["dyno.ch1.alarm.speed"] == [0.0]
    assert not any(key.startswith("dyno.ch2.") for key in status)


def test_get_status_bad_reply(dyno: MagtrolDSP7010, visa: MagicMock) -> None:
    visa.query.return_value = "0x20"
    with pytest.raises(InstroError, match="STAT1"):
        dyno.get_status()


@pytest.mark.parametrize(
    ("call", "wire"),
    [
        (lambda d: d.set_max_speed(1, 3600), ["F1,3600.0"]),
        (lambda d: d.set_speed(1, 1500), ["N1,1500.0"]),
        (lambda d: d.set_torque(2, 2.5), ["Q2,2.500"]),
        (lambda d: d.set_open_loop_output(1, 25), ["I1,25.00"]),
        (lambda d: d.brake_off(2), ["I2", "N2", "Q2"]),
        (lambda d: d.tare(1), ["TS1"]),
        (lambda d: d.set_alarm_limits(1, 4000, 5, 1.5), ["ALS1,4000.0", "ALT1,5.000", "ALP1,1.500"]),
        (lambda d: d.set_alarms_enabled(1, True), ["ALL1,1"]),
        (lambda d: d.clear_alarms(), ["CLR"]),
    ],
)
def test_set_commands(dyno: MagtrolDSP7010, visa: MagicMock, call, wire: list[str]) -> None:
    call(dyno)

    assert _sent(visa) == wire


def test_publishes_numbers_only(visa_cls: MagicMock) -> None:
    # Connect drops string channels.
    publisher = MagicMock(spec=Publisher)
    dyno = MagtrolDSP7010("ASRL3::INSTR", name="dyno", publishers=[publisher])
    visa = visa_cls.return_value

    dyno.set_torque_units(1, TorqueUnit.N_M)
    dyno.set_torque(1, 2.5)
    dyno.brake_off(1)
    visa.query.return_value = "S 300T0.50A"
    dyno.read_data(1)
    visa.query.return_value = f"{_STATUS_WORD:08x}"
    dyno.get_status()

    published = [c.args[0] for c in publisher.publish.call_args_list]
    values: list[object] = []
    for data in published:
        if isinstance(data, Measurement):
            values.extend(v for channel in data.channel_data.values() for v in channel)
        else:
            assert isinstance(data, Command)
            values.extend(data.channel_data.values())
    assert values
    assert all(isinstance(v, float) for v in values)
    assert published[1].channel_data == {"dyno.ch1.torque_setpoint.cmd": 2.5, "dyno.ch1.brake.cmd": 1.0}


def test_bad_args_rejected_before_write(dyno: MagtrolDSP7010, visa: MagicMock) -> None:
    with pytest.raises(ValueError, match="channel"):
        dyno.set_speed(3, 100)
    with pytest.raises(ValueError, match="percent"):
        dyno.set_open_loop_output(1, 100.0)
    with pytest.raises(ValueError, match="torque"):
        dyno.set_torque(1, float("nan"))
    with pytest.raises(ValueError, match="max_power_kw"):
        dyno.set_alarm_limits(1, 4000, 5, -1)
    visa.query.assert_not_called()


def test_rejected_write_raises_for_that_command(dyno: MagtrolDSP7010, visa: MagicMock) -> None:
    visa.query.return_value = "COMMAND ERROR\r"
    with pytest.raises(InstroError, match="rejected 'UR1,8'"):
        dyno.set_torque_units(1, TorqueUnit.N_M)


def test_unexpected_write_reply_raises(dyno: MagtrolDSP7010, visa: MagicMock) -> None:
    visa.query.return_value = "S     0T0.016R\r"
    with pytest.raises(InstroError, match="unexpected reply"):
        dyno.tare(1)
