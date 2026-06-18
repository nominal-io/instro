import sys
from unittest.mock import MagicMock, patch

import pytest

from instro.daq.drivers.arduino_firmata import ArduinoFirmata
from instro.daq.types import AnalogChannel, Direction, HWTimingConfig, Logic


@pytest.fixture
def mock_board():
    return MagicMock()


@pytest.fixture
def arduino_driver(mock_board):
    mock_pyfirmata2 = MagicMock()
    mock_pyfirmata2.Arduino.return_value = mock_board
    with patch.dict(sys.modules, {"pyfirmata2": mock_pyfirmata2}):
        driver = ArduinoFirmata("/dev/ttyACM0")
        with patch("time.sleep"):
            driver.open()
        yield driver


def test_configure_ai_channel(arduino_driver, mock_board):
    channel = AnalogChannel(
        physical_channel="A0",
        alias="voltage",
        direction=Direction.INPUT,
        range_min=0.0,
        range_max=5.0,
        scaler=None,
    )
    arduino_driver.configure_ai_channel(channel)

    mock_board.get_pin.assert_called_once_with("a:0:i")
    assert arduino_driver._ai_channels["voltage"] == channel


def test_read_analog_input(arduino_driver, mock_board):
    channel = AnalogChannel(
        physical_channel="A0",
        alias="voltage",
        direction=Direction.INPUT,
        range_min=0.0,
        range_max=5.0,
        scaler=None,
    )
    arduino_driver.configure_ai_channel(channel)
    mock_pin = mock_board.get_pin.return_value

    # get the registered callback
    callback = mock_pin.register_callback.call_args[0][0]
    callback(0.5)
    result = arduino_driver.read_analog()
    assert result == {"voltage": 0.5}


def test_read_analog_input_none(arduino_driver, mock_board):
    channel = AnalogChannel(
        physical_channel="A0",
        alias="voltage",
        direction=Direction.INPUT,
        range_min=0.0,
        range_max=5.0,
        scaler=None,
    )
    arduino_driver.configure_ai_channel(channel)
    mock_pin = mock_board.get_pin.return_value

    # get the registered callback
    callback = mock_pin.register_callback.call_args[0][0]
    callback(None)
    result = arduino_driver.read_analog()
    assert result == {"voltage": 0.0}


def test_write_digital_line(arduino_driver, mock_board):
    arduino_driver.configure_do_line_channel(
        physical_channel="D13",
        logic=Logic.HIGH,
        alias="led",
    )
    channel = arduino_driver._do_channels["led"]
    mock_pin = mock_board.get_pin.return_value

    arduino_driver.write_digital_line(channel, 1)
    mock_pin.write.assert_called_once_with(1)


def test_read_digital_line(arduino_driver, mock_board):
    arduino_driver.configure_di_line_channel(
        physical_channel="D13",
        logic=Logic.HIGH,
        alias="button",
    )

    # get callback registered on pin
    mock_pin = mock_board.get_pin.return_value
    callback = mock_pin.register_callback.call_args[0][0]

    # sim pyfirmata2 pushing a value
    callback(1)

    channel = arduino_driver._di_channels["button"]
    result = arduino_driver.read_digital_line(channel)
    assert result == 1


def test_close(arduino_driver, mock_board):
    arduino_driver.close()
    mock_board.exit.assert_called_once()
    assert not arduino_driver._pins


def test_configure_ai_hw_timing_raises(arduino_driver):
    with pytest.raises(NotImplementedError):
        arduino_driver.configure_ai_hw_timing(hw_timing_config=HWTimingConfig(30.0, 1, 40))


def test_set_sampling_rate_before_open():
    driver = ArduinoFirmata("/dev/ttyACM0")
    driver.set_sampling_rate(100)
    assert driver._sampling_interval_ms == 10
    assert driver._board is None


def test_set_sampling_rate_after_open_calls_board(arduino_driver, mock_board):
    arduino_driver.set_sampling_rate(50)
    assert arduino_driver._sampling_interval_ms == 20
    mock_board.setSamplingInterval.assert_called_with(20)
