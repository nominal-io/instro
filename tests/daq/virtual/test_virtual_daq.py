"""Tests for the transport-free VirtualDAQ driver."""

from __future__ import annotations

import pytest

from instro.daq import InstroDAQ
from instro.daq.drivers import VirtualDAQ
from instro.daq.types import DigitalPortWidth, Direction, Logic
from instro.lib import Measurement


def test_sw_timed_read_uses_ai_values_and_stays_in_range() -> None:
    daq = InstroDAQ(name="daq", driver=VirtualDAQ({"ai1": 3.3}))
    with daq:
        daq.configure_voltage_input("ai0", range_min=0, range_max=5)
        daq.configure_voltage_input("ai1", range_min=0, range_max=5)
        daq.configure_current_input("ai2", range_min=0.004, range_max=0.02)

        reads = daq.read_batch()

        assert list(daq.ai_channels) == ["ai0", "ai1", "ai2"]
        assert 1.2 < reads["ai0"].latest < 3.8
        assert reads["ai1"].latest == pytest.approx(3.3, abs=0.05)
        assert 0.004 <= reads["ai2"].latest <= 0.02


def test_hw_timed_fetch_follows_the_sample_clock() -> None:
    daq = InstroDAQ(name="daq", driver=VirtualDAQ())
    with daq:
        daq.configure_voltage_input("ai0")
        daq.configure_voltage_input("ai1")
        daq.configure_ai_hw_sample_rate(1000, samples_per_channel=10)
        daq.start(background=False)

        first, second = daq.read_analog(), daq.read_analog()
        daq.stop()

    assert isinstance(first, Measurement) and isinstance(second, Measurement)
    assert set(first.channel_data) == {"daq.ai0", "daq.ai1"}
    assert all(len(samples) == 10 for samples in first.channel_data.values())
    timestamps = first.timestamps + second.timestamps
    assert {b - a for a, b in zip(timestamps, timestamps[1:])} == {1_000_000}


def test_inputs_read_back_outputs_on_the_same_physical_channel() -> None:
    daq = InstroDAQ(name="daq", driver=VirtualDAQ())
    with daq:
        daq.configure_digital_output("port0/line0", alias="do0")
        daq.configure_digital_input("port0/line0", alias="di0")
        daq.configure_digital_input("port0/line0", alias="di0_inverted", logic=Logic.LOW)
        daq.configure_digital_port(Direction.OUTPUT, "port1", Logic.LOW, DigitalPortWidth.WIDTH_8, alias="do_port")
        daq.configure_digital_port(Direction.INPUT, "port1", Logic.HIGH, DigitalPortWidth.WIDTH_8, alias="di_port")
        daq.configure_relay_channel("k1", alias="relay")

        assert daq.read("di0").latest == 0
        daq.write_batch(["do0", "do_port"], [1, 0x0F])
        assert daq.read("di0").latest == 1
        assert daq.read("di0_inverted").latest == 0
        assert daq.read("di_port").latest == 0xF0

        daq.close_relay("relay")
        assert daq.driver.closed_relays == {"k1"}
        daq.open_relay("relay")
        assert daq.driver.closed_relays == set()
