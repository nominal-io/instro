"""Unit tests for NIDAQ digital-port width validation against physical hardware."""

from unittest import mock

import pytest

pytest.importorskip("nidaqmx")

from instro.daq.drivers.ni import NIDAQDriver  # noqa: E402
from instro.daq.types import DigitalPortWidth, Logic  # noqa: E402

PHYSICAL_CHANNEL = "instro.daq.drivers.ni.nidaq.PhysicalChannel"
TASK = "instro.daq.drivers.ni.nidaq.nidaqmx.Task"


@pytest.mark.parametrize(
    ("width_attr", "configure"),
    [
        ("di_port_width", "configure_di_port_channel"),
        ("do_port_width", "configure_do_port_channel"),
    ],
)
def test_configure_port_rejects_width_mismatch(width_attr, configure):
    """A declared width wider than the physical port is rejected before the task is built."""
    with mock.patch(PHYSICAL_CHANNEL) as physical_channel:
        setattr(physical_channel.return_value, width_attr, 8)
        driver = NIDAQDriver(device_id="Dev1")
        with pytest.raises(ValueError, match="does not match the physical width"):
            getattr(driver, configure)("Dev1/port0", Logic.HIGH, DigitalPortWidth.WIDTH_16)


@pytest.mark.parametrize(
    ("width_attr", "configure", "channels_attr"),
    [
        ("di_port_width", "configure_di_port_channel", "di_channels"),
        ("do_port_width", "configure_do_port_channel", "do_channels"),
    ],
)
def test_configure_port_accepts_matching_width(width_attr, configure, channels_attr):
    """A declared width matching the physical port programs and registers the port."""
    with mock.patch(TASK), mock.patch(PHYSICAL_CHANNEL) as physical_channel:
        setattr(physical_channel.return_value, width_attr, 8)
        driver = NIDAQDriver(device_id="Dev1")
        getattr(driver, configure)("Dev1/port0", Logic.HIGH, DigitalPortWidth.WIDTH_8, alias="bus")
        assert "bus" in getattr(driver, channels_attr)
