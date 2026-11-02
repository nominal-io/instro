"""Virtual DAQ driver: channels simulated in-process, with no transport, SDK, or hardware."""

from __future__ import annotations

import math
import random
import time
from collections.abc import Mapping
from dataclasses import dataclass

from instro.daq import DAQDriverBase, HWTimingException
from instro.daq.types import (
    AnalogChannelUnion,
    AnalogCurrentChannel,
    AnalogThermocoupleChannel,
    AnalogVoltageChannel,
    DAQChannel,
    DigitalChannel,
    DigitalLineChannel,
    DigitalPortChannel,
    DigitalPortWidth,
    Direction,
    HWTimingConfig,
    Logic,
    RelayChannel,
)
from instro.lib.types import Measurement

_SINE_PERIOD_S = 10.0
_SINE_AMPLITUDE = 0.25  # fraction of the channel's range span
_NOISE = 0.001  # standard deviation, as a fraction of the channel's range span


@dataclass
class VirtualDAQData:
    """Samples from one `VirtualDAQ` read or fetch.

    Attributes:
        data (dict[str, list[float]]): Samples per AI channel alias, in the channel's units.
        timestamps (list[int]): Sample times in ns since the Unix epoch, shared by every channel.
    """

    data: dict[str, list[float]]
    timestamps: list[int]


class VirtualDAQ(DAQDriverBase):
    """DAQ simulated in-process: no transport, SDK, or hardware.

    Any physical channel name is accepted. Analog inputs read a slow sine (10 s period) centered
    in the channel's configured range and spanning half of it, plus noise, or a constant set in
    ``ai_values``; readings are clipped to the range. Hardware-timed acquisition paces itself to
    the configured sample rate, so each fetch blocks until its samples would have been acquired.
    Outputs are stored, and an input reads back the last value written to the output with the
    same physical channel (0 before any write), so a digital output and input on one line form a
    loopback. Relays open and close in ``closed_relays``.
    """

    def __init__(self, ai_values: Mapping[str, float] | None = None) -> None:
        """Create a virtual DAQ.

        Args:
            ai_values (Mapping[str, float] | None): A constant reading, before noise, for each listed
                analog input physical channel, in the channel's units (volts, amperes, or the
                thermocouple's unit). Channels left out read the sine.

        Example::

            from instro.daq import InstroDAQ
            from instro.daq.drivers import VirtualDAQ

            daq = InstroDAQ(name="daq", driver=VirtualDAQ({"ai1": 3.3}))
            with daq:
                daq.configure_voltage_input("ai0", range_min=0, range_max=5)
                daq.configure_voltage_input("ai1", range_min=0, range_max=5)
                daq.configure_digital_output("port0/line0", alias="do0")
                daq.configure_digital_input("port0/line0", alias="di0")

                print(daq.read("ai0").latest)  # between 1.25 and 3.75 V
                print(daq.read("ai1").latest)  # about 3.3 V
                daq.write("do0", 1)
                print(daq.read("di0").latest)  # 1.0, read back from do0
        """
        super().__init__()
        self.ai_values: dict[str, float] = dict(ai_values or {})
        self.outputs: dict[str, float] = {}
        self.closed_relays: set[str] = set()
        self._start_ns: int | None = None
        self._next_sample = 0

    def open(self) -> None:
        """Nothing to connect to."""

    def close(self) -> None:
        """Stop any acquisition; there is nothing to disconnect."""
        self.stop()

    def start(self, **kwargs) -> None:
        """Start hardware-timed acquisition; sample times count from now at the configured rate."""
        if self._ai_hw_timing_config is None:
            raise HWTimingException("No hardware timing configured. Call configure_ai_hw_sample_rate() first.")
        self._start_ns = time.time_ns()
        self._next_sample = 0

    def stop(self, **kwargs) -> None:
        """Stop hardware-timed acquisition."""
        self._start_ns = None
        self.points_in_buffer = 0

    def configure_ai_voltage_channel(self, channel: AnalogVoltageChannel) -> None:
        self._ai_channels[channel.alias] = channel

    def configure_ao_voltage_channel(self, channel: AnalogVoltageChannel) -> None:
        self._ao_channels[channel.alias] = channel

    def configure_ai_current_channel(self, channel: AnalogCurrentChannel) -> None:
        self._ai_channels[channel.alias] = channel

    def configure_ao_current_channel(self, channel: AnalogCurrentChannel) -> None:
        self._ao_channels[channel.alias] = channel

    def configure_ai_thermocouple_channel(self, channel: AnalogThermocoupleChannel) -> None:
        self._ai_channels[channel.alias] = channel

    def configure_di_line_channel(
        self,
        physical_channel: str,
        logic: Logic,
        logic_level: float | None = None,
        alias: str | None = None,
    ) -> None:
        channel = DigitalLineChannel(
            physical_channel=physical_channel,
            alias=alias or physical_channel,
            direction=Direction.INPUT,
            logic_level=logic_level,
            logic=logic,
        )
        self._di_channels[channel.alias] = channel

    def configure_do_line_channel(
        self,
        physical_channel: str,
        logic: Logic,
        logic_level: float | None = None,
        alias: str | None = None,
    ) -> None:
        channel = DigitalLineChannel(
            physical_channel=physical_channel,
            alias=alias or physical_channel,
            direction=Direction.OUTPUT,
            logic_level=logic_level,
            logic=logic,
        )
        self._do_channels[channel.alias] = channel

    def configure_di_port_channel(
        self,
        physical_channel: str,
        logic: Logic,
        port_width: DigitalPortWidth,
        logic_level: float | None = None,
        alias: str | None = None,
    ) -> None:
        channel = DigitalPortChannel(
            physical_channel=physical_channel,
            alias=alias or physical_channel,
            direction=Direction.INPUT,
            logic_level=logic_level,
            logic=logic,
            width=port_width,
        )
        self._di_channels[channel.alias] = channel

    def configure_do_port_channel(
        self,
        physical_channel: str,
        logic: Logic,
        port_width: DigitalPortWidth,
        logic_level: float | None = None,
        alias: str | None = None,
    ) -> None:
        channel = DigitalPortChannel(
            physical_channel=physical_channel,
            alias=alias or physical_channel,
            direction=Direction.OUTPUT,
            logic_level=logic_level,
            logic=logic,
            width=port_width,
        )
        self._do_channels[channel.alias] = channel

    def configure_ai_hw_timing(self, hw_timing_config: HWTimingConfig) -> None:
        self._ai_hw_timing_config = hw_timing_config

    def get_actual_sample_rate(self) -> float | None:
        """The configured sample rate (Hz) while acquisition runs, else ``None``."""
        if self._start_ns is None or self._ai_hw_timing_config is None:
            return None
        return self._ai_hw_timing_config.sample_rate

    def read_analog(self) -> VirtualDAQData:
        """Sample every AI channel once, now."""
        timestamp = time.time_ns()
        return VirtualDAQData(
            data={alias: [self._sample(channel, timestamp)] for alias, channel in self._ai_channels.items()},
            timestamps=[timestamp],
        )

    def fetch_analog(self) -> VirtualDAQData:
        """Block until the next ``samples_per_channel`` samples are due, then return them."""
        timing = self._ai_hw_timing_config
        if self._start_ns is None or timing is None:
            raise RuntimeError("No acquisition running. Call start() before fetch_analog().")
        first = self._next_sample
        self._next_sample += timing.samples_per_channel
        timestamps = [self._start_ns + i * timing.sample_period for i in range(first, self._next_sample)]

        wait_s = (timestamps[-1] - time.time_ns()) / 1e9
        if wait_s > 0:
            time.sleep(wait_s)
        due = (time.time_ns() - self._start_ns) // timing.sample_period + 1
        self.points_in_buffer = max(0, due - self._next_sample) * len(self._ai_channels)

        return VirtualDAQData(
            data={
                alias: [self._sample(channel, t) for t in timestamps] for alias, channel in self._ai_channels.items()
            },
            timestamps=timestamps,
        )

    def write_analog_value(self, channel: AnalogChannelUnion, value: float) -> None:
        self.outputs[channel.physical_channel] = value

    def write_digital_line(self, channel: DigitalChannel, data: int) -> None:
        self.outputs[channel.physical_channel] = 1 - data if channel.logic is Logic.LOW else data

    def read_digital_line(self, channel: DigitalChannel) -> int:
        level = int(self.outputs.get(channel.physical_channel, 0))
        return 1 - level if channel.logic is Logic.LOW else level

    def write_digital_port(self, channel: DigitalChannel, data: int) -> None:
        self.outputs[channel.physical_channel] = data ^ _port_mask(channel)

    def read_digital_port(self, channel: DigitalChannel) -> int:
        return int(self.outputs.get(channel.physical_channel, 0)) ^ _port_mask(channel)

    def close_relay(self, channel: RelayChannel) -> None:
        self.closed_relays.add(channel.physical_channel)

    def open_relay(self, channel: RelayChannel) -> None:
        self.closed_relays.discard(channel.physical_channel)

    def _read_to_measurements(
        self,
        response: VirtualDAQData,
        channel_list: Mapping[str, DAQChannel],
        daq_name: str,
        default_tags: dict[str, str],
        **kwargs,
    ) -> list[Measurement]:
        channel_data: dict[str, list[float] | list[str]] = {
            f"{daq_name}.{alias}": response.data[alias] for alias in channel_list
        }
        return [Measurement(channel_data=channel_data, timestamps=response.timestamps, tags={**default_tags, **kwargs})]

    def _sample(self, channel: AnalogChannelUnion, timestamp: int) -> float:
        low, high = channel.range_min, channel.range_max
        span = high - low
        value = self.ai_values.get(channel.physical_channel)
        if value is None:
            phase = 2 * math.pi * (timestamp / 1e9) / _SINE_PERIOD_S
            value = (low + high) / 2 + _SINE_AMPLITUDE * span * math.sin(phase)
        return min(max(random.gauss(value, _NOISE * span), low), high)


def _port_mask(channel: DigitalChannel) -> int:
    """All-ones over an active-low port's width, so XOR inverts it; 0 for active-high."""
    if channel.logic is Logic.HIGH or not isinstance(channel, DigitalPortChannel):
        return 0
    return (1 << int(channel.width)) - 1
