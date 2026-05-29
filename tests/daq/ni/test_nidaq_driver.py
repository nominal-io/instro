from unittest.mock import patch

import pytest

from instro.daq.drivers.ni import NIDAQDriver
from instro.daq.types import (
    AnalogChannel,
    DigitalLineChannel,
    DigitalPortChannel,
    DigitalPortWidth,
    Direction,
    Logic,
)


def _ai_channel(physical_channel: str) -> AnalogChannel:
    return AnalogChannel(
        physical_channel=physical_channel,
        alias="ai_0",
        direction=Direction.INPUT,
        range_max=5.0,
        range_min=0.0,
        scaler=None,
    )


def _ao_channel(physical_channel: str) -> AnalogChannel:
    return AnalogChannel(
        physical_channel=physical_channel,
        alias="ao_0",
        direction=Direction.OUTPUT,
        range_max=5.0,
        range_min=0.0,
        scaler=None,
    )


def _line_channel(physical_channel: str, direction: Direction) -> DigitalLineChannel:
    return DigitalLineChannel(
        physical_channel=physical_channel,
        alias="line_0",
        direction=direction,
        logic_level=5.0,
        logic=Logic.HIGH,
    )


@patch("instro.daq.drivers.ni.nidaq.nidaqmx")
def test_configure_ai_channel_passes_physical_channel_verbatim(mock_nidaqmx):
    driver = NIDAQDriver(device_id="Dev1")
    driver.configure_ai_channel(_ai_channel("Dev1/ai0"))

    task = mock_nidaqmx.Task.return_value
    _, kwargs = task.ai_channels.add_ai_voltage_chan.call_args
    assert kwargs["physical_channel"] == "Dev1/ai0"


@patch("instro.daq.drivers.ni.nidaq.nidaqmx")
def test_configure_ao_channel_passes_physical_channel_verbatim(mock_nidaqmx):
    driver = NIDAQDriver(device_id="Dev1")
    driver.configure_ao_channel(_ao_channel("Dev1/ao0"))

    task = mock_nidaqmx.Task.return_value
    _, kwargs = task.ao_channels.add_ao_voltage_chan.call_args
    assert kwargs["physical_channel"] == "Dev1/ao0"


@patch("instro.daq.drivers.ni.nidaq.nidaqmx")
def test_configure_do_channel_passes_lines_verbatim(mock_nidaqmx):
    driver = NIDAQDriver(device_id="Dev1")
    driver.configure_do_channel(_line_channel("Dev1/port0/line0", Direction.OUTPUT))

    task = mock_nidaqmx.Task.return_value
    _, kwargs = task.do_channels.add_do_chan.call_args
    assert kwargs["lines"] == "Dev1/port0/line0"


@patch("instro.daq.drivers.ni.nidaq.nidaqmx")
def test_configure_di_channel_passes_lines_verbatim(mock_nidaqmx):
    driver = NIDAQDriver(device_id="Dev1")
    driver.configure_di_channel(_line_channel("Dev1/port0/line0", Direction.INPUT))

    task = mock_nidaqmx.Task.return_value
    _, kwargs = task.di_channels.add_di_chan.call_args
    assert kwargs["lines"] == "Dev1/port0/line0"


def test_define_digital_channel_port_accepts_full_name():
    driver = NIDAQDriver(device_id="Dev1")
    channel = driver.define_digital_channel(
        direction=Direction.OUTPUT,
        physical_channel="Dev1/port0",
        logic=Logic.HIGH,
        port_width=DigitalPortWidth.WIDTH_8,
    )
    assert isinstance(channel, DigitalPortChannel)
    assert channel.physical_channel == "Dev1/port0"


def test_define_digital_channel_line_accepts_full_name():
    driver = NIDAQDriver(device_id="Dev1")
    channel = driver.define_digital_channel(
        direction=Direction.OUTPUT,
        physical_channel="Dev1/port0/line0",
        logic=Logic.HIGH,
    )
    assert isinstance(channel, DigitalLineChannel)
    assert channel.physical_channel == "Dev1/port0/line0"


def test_define_digital_channel_port_width_with_line_raises():
    driver = NIDAQDriver(device_id="Dev1")
    with pytest.raises(ValueError, match="implies a line"):
        driver.define_digital_channel(
            direction=Direction.OUTPUT,
            physical_channel="Dev1/port0/line0",
            logic=Logic.HIGH,
            port_width=DigitalPortWidth.WIDTH_8,
        )
