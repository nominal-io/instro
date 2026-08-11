"""Unit tests for InstroMotorController over a stub driver."""

from unittest.mock import MagicMock

import pytest

from instro.lib.publishers import Publisher
from instro.lib.types import Command, Measurement
from instro.unstable.motorcontroller import (
    InstroMotorController,
    MotorControllerDriverBase,
    MotorTelemetry,
)


class _StubDriver(MotorControllerDriverBase):
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.telemetry: MotorTelemetry = {}

    def open(self) -> None:
        self.calls.append(("open",))

    def close(self) -> None:
        self.calls.append(("close",))

    def stop(self) -> None:
        self.calls.append(("stop",))

    def get_telemetry(self) -> MotorTelemetry:
        return self.telemetry

    def set_current(self, amps: float) -> None:
        self.calls.append(("set_current", amps))


@pytest.fixture
def publisher() -> MagicMock:
    return MagicMock(spec=Publisher)


@pytest.fixture
def driver() -> _StubDriver:
    return _StubDriver()


@pytest.fixture
def motor(driver: _StubDriver, publisher: MagicMock) -> InstroMotorController:
    return InstroMotorController("m", driver=driver, publishers=[publisher])


def _published(publisher: MagicMock) -> list:
    return [c.args[0] for c in publisher.publish.call_args_list]


def test_open_close_delegate_to_driver(motor: InstroMotorController, driver: _StubDriver) -> None:
    motor.open()
    motor.close()
    assert ("open",) in driver.calls
    assert ("close",) in driver.calls


@pytest.mark.parametrize(
    ("call", "channel", "value", "driver_call"),
    [
        (lambda m: m.stop_motor(), "m.stop.cmd", 1.0, ("stop",)),
        (lambda m: m.set_current(2.5), "m.current.cmd", 2.5, ("set_current", 2.5)),
    ],
)
def test_commands_delegate_and_publish(
    motor: InstroMotorController,
    driver: _StubDriver,
    publisher: MagicMock,
    call,
    channel: str,
    value: float,
    driver_call: tuple,
) -> None:
    call(motor)

    assert driver.calls[-1] == driver_call
    command = _published(publisher)[-1]
    assert isinstance(command, Command)
    assert command.channel_data == {channel: value}


def test_unsupported_command_raises_and_publishes_nothing(motor: InstroMotorController, publisher: MagicMock) -> None:
    with pytest.raises(NotImplementedError, match="position control"):
        motor.set_position(90.0)
    publisher.publish.assert_not_called()


def test_get_telemetry_publishes_reported_fields(
    motor: InstroMotorController, driver: _StubDriver, publisher: MagicMock
) -> None:
    driver.telemetry = {"velocity": 1_500.0, "bus_voltage": 24.0}

    measurement = motor.get_telemetry()

    assert isinstance(measurement, Measurement)
    assert measurement.channel_data == {"m.velocity": [1_500.0], "m.bus_voltage": [24.0]}
    assert isinstance(_published(publisher)[-1], Measurement)


def test_get_telemetry_empty_returns_none_and_publishes_nothing(
    motor: InstroMotorController, publisher: MagicMock
) -> None:
    assert motor.get_telemetry() is None
    publisher.publish.assert_not_called()


def test_close_stops_background_daemon(motor: InstroMotorController) -> None:
    motor.open()
    motor.start()
    daemon = motor._background_thread
    assert daemon is not None and daemon.is_alive()

    # Regression: a HAL method named stop() would shadow Instrument.stop() and leave the daemon running.
    motor.close()
    assert not daemon.is_alive()
