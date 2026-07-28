import sys
from unittest.mock import MagicMock, patch

import pytest

from instro.daq.drivers.arduino_firmata import ArduinoFirmata
from instro.daq.types import AnalogChannel, Direction, HWTimingConfig, Logic
from instro.lib.types import Measurement


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
    assert len(result) == 1
    timestamp, values = result[0]
    assert isinstance(timestamp, int)
    assert values == {"voltage": 0.5}


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
    assert len(result) == 1
    timestamp, values = result[0]
    assert isinstance(timestamp, int)
    assert values == {"voltage": 0.0}


def test_read_analog_input_before_first_report_raises(arduino_driver, mock_board):
    channel = AnalogChannel(
        physical_channel="A0",
        alias="voltage",
        direction=Direction.INPUT,
        range_min=0.0,
        range_max=5.0,
        scaler=None,
    )
    arduino_driver.configure_ai_channel(channel)

    with pytest.raises(RuntimeError, match="voltage"):
        arduino_driver.read_analog()


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


def test_read_digital_line_before_first_report_raises(arduino_driver, mock_board):
    arduino_driver.configure_di_line_channel(
        physical_channel="D13",
        logic=Logic.HIGH,
        alias="button",
    )
    channel = arduino_driver._di_channels["button"]

    with pytest.raises(RuntimeError, match="button"):
        arduino_driver.read_digital_line(channel)


def test_close(arduino_driver, mock_board):
    arduino_driver.close()
    mock_board.exit.assert_called_once()
    assert not arduino_driver._pins


def test_close_stops_iterator_thread(mock_board):
    mock_pyfirmata2 = MagicMock()
    mock_pyfirmata2.Arduino.return_value = mock_board
    with patch.dict(sys.modules, {"pyfirmata2": mock_pyfirmata2}):
        driver = ArduinoFirmata("/dev/ttyACM0")
        with patch("time.sleep"):
            driver.open()
        mock_iterator = mock_pyfirmata2.util.Iterator.return_value

        driver.close()

        mock_iterator.stop.assert_called_once()
        mock_iterator.join.assert_called_once_with(timeout=1.0)


def test_close_resets_latest_values_so_reopen_starts_clean(arduino_driver, mock_board):
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
    callback = mock_pin.register_callback.call_args[0][0]
    callback(0.5)
    arduino_driver.read_analog()  # sanity: data is present before close()

    arduino_driver.close()

    assert arduino_driver._latest_values == {}
    with pytest.raises(RuntimeError):
        arduino_driver.read_analog()


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


# ---------------------------------------------------------------------------
# _read_to_measurements
# ---------------------------------------------------------------------------

_VOLTAGE_CHANNEL = AnalogChannel(
    physical_channel="A0",
    alias="voltage",
    direction=Direction.INPUT,
    range_min=0.0,
    range_max=5.0,
    scaler=None,
)


def test_read_to_measurements_single_point(arduino_driver):
    response = [(1_000_000, {"voltage": 0.5})]
    result = arduino_driver._read_to_measurements(
        response=response,
        channel_list={"voltage": _VOLTAGE_CHANNEL},
        daq_name="arduino",
        default_tags={},
    )
    assert len(result) == 1
    m = result[0]
    assert m.timestamps == [1_000_000]
    assert len(m.channel_data["arduino.voltage"]) == 1
    assert abs(m.channel_data["arduino.voltage"][0] - 2.5) < 0.001  # 0.5 * 5.0V


def test_read_to_measurements_multi_point(arduino_driver):
    response = [(1000, {"voltage": 0.0}), (2000, {"voltage": 0.5}), (3000, {"voltage": 1.0})]
    result = arduino_driver._read_to_measurements(
        response=response,
        channel_list={"voltage": _VOLTAGE_CHANNEL},
        daq_name="arduino",
        default_tags={},
    )
    assert len(result) == 1
    m = result[0]
    assert m.timestamps == [1000, 2000, 3000]
    assert len(m.channel_data["arduino.voltage"]) == 3
    assert abs(m.channel_data["arduino.voltage"][0] - 0.0) < 0.001
    assert abs(m.channel_data["arduino.voltage"][1] - 2.5) < 0.001
    assert abs(m.channel_data["arduino.voltage"][2] - 5.0) < 0.001


# ---------------------------------------------------------------------------
# InstroDAQ analog read routing
# ---------------------------------------------------------------------------


@pytest.fixture
def daq(arduino_driver):
    from instro.daq import InstroDAQ

    d = InstroDAQ(name="arduino", driver=arduino_driver)
    d._is_open = True
    return d


def test_daq_read_analog_no_start(daq, arduino_driver):
    """No start(): routes to _software_timed_read -> driver.read_analog() -> one Measurement."""
    daq.configure_analog_channel(
        direction=Direction.INPUT, physical_channel="A0", alias="voltage", range_min=0.0, range_max=5.0
    )
    arduino_driver._on_analog_callback("voltage", 0.6)
    result = daq.read_analog()
    assert isinstance(result, Measurement)
    assert len(result.timestamps) == 1
    assert abs(result.channel_data["arduino.voltage"][0] - 3.0) < 0.001  # 0.6 * 5.0V


def test_daq_read_analog_daemon_running_raises(daq, arduino_driver):
    """Daemon running: read_analog() raises RuntimeError."""
    daq.configure_analog_channel(
        direction=Direction.INPUT, physical_channel="A0", alias="voltage", range_min=0.0, range_max=5.0
    )
    arduino_driver._ai_hw_timing_config = HWTimingConfig(
        sample_rate=100.0, sample_period=10_000_000, samples_per_channel=1
    )
    daq._background_thread = MagicMock()
    daq._background_thread.is_alive.return_value = True
    with pytest.raises(RuntimeError, match="background acquisition daemon"):
        daq.read_analog()


def test_daq_read_analog_start_no_background(daq, arduino_driver):
    """start(background=False): routes to _fetch_analog -> driver.fetch_analog() -> one Measurement."""
    daq.configure_analog_channel(
        direction=Direction.INPUT, physical_channel="A0", alias="voltage", range_min=0.0, range_max=5.0
    )
    daq.start(background=False)
    arduino_driver._on_analog_callback("voltage", 0.4)  # populates queue now that _expected_ai_channels is set
    result = daq.read_analog()
    assert isinstance(result, Measurement)
    assert len(result.timestamps) == 1
    assert abs(result.channel_data["arduino.voltage"][0] - 2.0) < 0.001  # 0.4 * 5.0V
