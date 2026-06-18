import time
from dataclasses import dataclass
from itertools import count
from typing import Mapping

import nidaqmx
from nidaqmx.constants import AcquisitionType, LineGrouping
from nidaqmx.system import System as niSystem

from instro.daq import DAQDriverBase
from instro.daq.drivers import HWTimestamper
from instro.daq.types import (
    AnalogChannel,
    ChannelType,
    DAQChannel,
    DigitalChannel,
    DigitalLineChannel,
    DigitalPortChannel,
    DigitalPortWidth,
    Direction,
    HWTimingConfig,
    Logic,
    TerminalConfig,
)
from instro.lib import Measurement


@dataclass
class DAQmxData:
    data: list[list[float]]
    timestamp: int
    dt: int | None


_instance_ids = count()


class NIDAQDriver(DAQDriverBase):
    """NI-DAQmx DAQ driver."""

    def __init__(self, device_id: str):
        super().__init__()
        self._device_id = device_id
        # DAQmx task names must be unique per process, and multiple driver instances
        # may target the same device_id (one InstroDAQ per task), so suffix the names.
        self._task_prefix = f"{device_id}_{next(_instance_ids)}"
        self._tasks: dict[ChannelType, nidaqmx.Task] = {}
        self._ao_sw_tasks: dict[str, nidaqmx.Task] = {}

        self._actual_sample_period: int | None = None
        self._actual_sample_rate: float | None = None
        self._timestamper: HWTimestamper | None = None  # None until first hw-timed read

        # Per-line state for the shared DO/DI line tasks: NI writes/reads the whole task at once,
        # so we cache every line's value to address a single line.
        self._do_lines_state: dict[str, bool] = {}
        self._di_lines_state: dict[str, bool] = {}
        self._running_channel_types: set[ChannelType] = set()

        # Port channels get a dedicated task each: CHAN_FOR_ALL_LINES wants an int payload,
        # which can't share a task with the bool-list line payload. Keyed by channel alias.
        self._di_port_tasks: dict[str, nidaqmx.Task] = {}
        self._do_port_tasks: dict[str, nidaqmx.Task] = {}

    def open(self):
        """NI-DAQmx has no explicit connect — verifies the device is present in ``niSystem.local()``."""
        # Verify device exists
        system = niSystem.local()
        connected = [dev.name for dev in system.devices]
        if self._device_id not in connected:
            tools = "NI MAX or the NI Hardware Configuration Utility"
            if connected:
                detail = f"Connected devices: {connected}. Confirm the intended device's name in {tools}."
            else:
                detail = (
                    "No NI devices are connected; check the hardware connection and NI-DAQmx installation, "
                    f"then confirm the device appears in {tools}."
                )
            raise RuntimeError(f"Device {self._device_id} not found. {detail}")

    def close(self):
        """Close every DAQmx task this driver owns."""
        for task in self._tasks.values():
            self._close_task(task)
        for task in self._ao_sw_tasks.values():
            self._close_task(task)
        for task in (*self._di_port_tasks.values(), *self._do_port_tasks.values()):
            self._close_task(task)
        self._tasks.clear()
        self._ao_sw_tasks.clear()
        self._di_port_tasks.clear()
        self._do_port_tasks.clear()
        self._running_channel_types.clear()

    def _close_task(self, task: nidaqmx.Task):
        # Try to close and ignore any exceptions.
        try:
            task.close()
        except:
            pass

    def _get_task(self, channel_type: ChannelType) -> nidaqmx.Task:
        """Return (creating if missing) the DAQmx task for ``channel_type``."""
        task = self._tasks.get(channel_type, None)
        if not task:
            # Task does not yet exist for that channel_type
            task_name = f"{self._task_prefix}_{channel_type.value}"
            task = nidaqmx.Task(task_name)
            self._tasks[channel_type] = task
        return task

    @staticmethod
    def _get_terminal_config(
        terminal_config: TerminalConfig | None,
    ) -> nidaqmx.constants.TerminalConfiguration:
        """Match instro terminal configuration enum to nidaqmx terminal configuration enum."""
        match terminal_config:
            case None:
                return nidaqmx.constants.TerminalConfiguration.DEFAULT
            case TerminalConfig.DIFF:
                return nidaqmx.constants.TerminalConfiguration.DIFF
            case TerminalConfig.RSE:
                return nidaqmx.constants.TerminalConfiguration.RSE
            case TerminalConfig.NRSE:
                return nidaqmx.constants.TerminalConfiguration.NRSE
            case _:
                raise ValueError(
                    f"Invalid terminal configuration: {terminal_config}, must be one of {[cfg.name for cfg in TerminalConfig]}"
                )

    @staticmethod
    def _reject_channel_range_or_list(physical_channel: str):
        """Reject NI-DAQmx range/list syntax; one physical_channel maps to one channel."""
        if ":" in physical_channel or "," in physical_channel:
            raise ValueError(
                "physical_channel must name a single channel. NI-DAQmx range and list syntax "
                "(e.g. 'Dev1/ai0:3', 'Dev1/ai0,Dev1/ai2') is not supported; configure each channel "
                f"with its own call. Received {physical_channel}."
            )

    def configure_ai_channel(
        self,
        channel: AnalogChannel,
    ):
        """Configure a channel on the NI device."""
        self._reject_channel_range_or_list(channel.physical_channel)
        task = self._get_task(ChannelType.ANALOG_INPUT)
        terminal_config = self._get_terminal_config(channel.terminal_config)

        task.ai_channels.add_ai_voltage_chan(
            physical_channel=channel.physical_channel,
            min_val=channel.range_min,
            max_val=channel.range_max,
            terminal_config=terminal_config,
        )

        self._ai_channels[channel.alias] = channel

    def configure_ao_channel(self, channel: AnalogChannel):
        # Bypassing self._tasks in favor of our own task registry until hardware timed analog output is implemented.
        self._reject_channel_range_or_list(channel.physical_channel)

        task = self._ao_sw_tasks.get(channel.alias, None)
        if task:
            raise ValueError("Channel already exists and is configured")

        task_name = f"{self._task_prefix}_{channel.alias}"
        task = nidaqmx.Task(task_name)
        self._ao_sw_tasks[channel.alias] = task

        task.ao_channels.add_ao_voltage_chan(
            physical_channel=channel.physical_channel,
            min_val=channel.range_min,
            max_val=channel.range_max,
        )

        self._ao_channels[channel.alias] = channel

    def configure_ai_hw_timing(
        self,
        hw_timing_config: HWTimingConfig,
    ):
        """Configure hardware timing for the specified channels."""
        task = self._get_task(ChannelType.ANALOG_INPUT)

        # Set sample rate and samples per channel
        task.timing.cfg_samp_clk_timing(
            rate=hw_timing_config.sample_rate,
            samps_per_chan=hw_timing_config.samples_per_channel,
            sample_mode=AcquisitionType.CONTINUOUS,
        )

        self._ai_hw_timing_config = hw_timing_config

    def configure_di_line_channel(
        self,
        physical_channel: str,
        logic: Logic,
        logic_level: float | None = None,
        alias: str | None = None,
    ):
        """Parse ``DevN/portM/lineP``, add as CHAN_PER_LINE to the shared DI line task, and register the line."""
        channel = self._build_line_channel(physical_channel, Direction.INPUT, logic, logic_level, alias)
        task = self._get_task(ChannelType.DIGITAL_INPUT)
        self._di_lines_state[channel.alias] = False
        task.di_channels.add_di_chan(
            lines=channel.physical_channel,
            name_to_assign_to_lines=channel.alias,
            line_grouping=LineGrouping.CHAN_PER_LINE,
        )
        self._di_channels[channel.alias] = channel

    def configure_do_line_channel(
        self,
        physical_channel: str,
        logic: Logic,
        logic_level: float | None = None,
        alias: str | None = None,
    ):
        """Parse ``DevN/portM/lineP``, add as CHAN_PER_LINE to the shared DO line task, and register the line."""
        channel = self._build_line_channel(physical_channel, Direction.OUTPUT, logic, logic_level, alias)
        task = self._get_task(ChannelType.DIGITAL_OUTPUT)
        self._do_lines_state[channel.alias] = False
        task.do_channels.add_do_chan(
            lines=channel.physical_channel,
            name_to_assign_to_lines=channel.alias,
            line_grouping=LineGrouping.CHAN_PER_LINE,
        )
        self._do_channels[channel.alias] = channel

    def configure_di_port_channel(
        self,
        physical_channel: str,
        logic: Logic,
        port_width: DigitalPortWidth,
        logic_level: float | None = None,
        alias: str | None = None,
    ):
        """Parse ``DevN/portM``, add as CHAN_FOR_ALL_LINES to a dedicated DI task, and register the port."""
        channel = self._build_port_channel(physical_channel, Direction.INPUT, logic, port_width, logic_level, alias)
        task = nidaqmx.Task(f"{self._task_prefix}_di_port_{channel.alias}")
        task.di_channels.add_di_chan(
            lines=channel.physical_channel,
            name_to_assign_to_lines=channel.alias,
            line_grouping=LineGrouping.CHAN_FOR_ALL_LINES,
        )
        self._di_port_tasks[channel.alias] = task
        self._di_channels[channel.alias] = channel

    def configure_do_port_channel(
        self,
        physical_channel: str,
        logic: Logic,
        port_width: DigitalPortWidth,
        logic_level: float | None = None,
        alias: str | None = None,
    ):
        """Parse ``DevN/portM``, add as CHAN_FOR_ALL_LINES to a dedicated DO task, and register the port."""
        channel = self._build_port_channel(physical_channel, Direction.OUTPUT, logic, port_width, logic_level, alias)
        task = nidaqmx.Task(f"{self._task_prefix}_do_port_{channel.alias}")
        task.do_channels.add_do_chan(
            lines=channel.physical_channel,
            name_to_assign_to_lines=channel.alias,
            line_grouping=LineGrouping.CHAN_FOR_ALL_LINES,
        )
        self._do_port_tasks[channel.alias] = task
        self._do_channels[channel.alias] = channel

    def _build_line_channel(
        self,
        physical_channel: str,
        direction: Direction,
        logic: Logic,
        logic_level: float | None,
        alias: str | None,
    ) -> DigitalLineChannel:
        if "/line" not in physical_channel:
            raise ValueError(
                "physical_channel does not define the line within the channel to create a channel from. "
                "Define the physical channel as DevN/portM/lineP. ex 'Dev1/port2/line3'."
            )
        return DigitalLineChannel(
            physical_channel=physical_channel,
            alias=alias or physical_channel,
            direction=direction,
            logic_level=logic_level,  # type: ignore
            logic=logic,
        )

    def _build_port_channel(
        self,
        physical_channel: str,
        direction: Direction,
        logic: Logic,
        port_width: DigitalPortWidth,
        logic_level: float | None,
        alias: str | None,
    ) -> DigitalPortChannel:
        if "/line" in physical_channel:
            raise ValueError(
                f"port_width is set to {port_width} but physical_channel implies a line. "
                "Define the physical channel as DevN/portM. ex 'Dev1/port2'. "
                f"Received {physical_channel}."
            )
        return DigitalPortChannel(
            physical_channel=physical_channel,
            alias=alias or physical_channel,
            direction=direction,
            logic_level=logic_level,
            logic=logic,
            width=port_width,
        )

    def start(self, **kwargs):
        """Start the DAQ device for data acquisition."""
        channel_type: ChannelType | None = kwargs.get("channel_type", None)
        channel_types = [channel_type] if channel_type else list(self._tasks.keys())

        self._guard_already_running(channel_types)
        self._start_tasks(channel_types)

        if ChannelType.ANALOG_INPUT in channel_types:
            self._timestamper = None
            self._capture_sample_rate()

    def _guard_already_running(self, channel_types: list[ChannelType]):
        already_running = self._running_channel_types & set(channel_types)
        if already_running:
            running_text = ", ".join(str(channel_type) for channel_type in already_running)
            raise RuntimeError(f"Already running: {running_text}. Call stop() before starting again.")

    def _start_tasks(self, channel_types: list[ChannelType]):
        for channel_type in channel_types:
            task = self._tasks.get(channel_type)
            if not task:
                raise RuntimeError(
                    f"Task for channel type {channel_type} has not been configured. Configure prior to calling start."
                )
            task.start()
            self._running_channel_types.add(channel_type)

    def _capture_sample_rate(self):
        if self._ai_hw_timing_config is None:
            raise RuntimeError("configure_ai_sample_rate() must be called before starting the DAQ.")
        ai_task = self._tasks[ChannelType.ANALOG_INPUT]
        actual_rate = ai_task.timing.samp_clk_rate
        self._actual_sample_rate = actual_rate
        self._actual_sample_period = round(1e9 / actual_rate)
        requested_rate = self._ai_hw_timing_config.sample_rate
        if abs(actual_rate - requested_rate) / requested_rate > 0.1:
            print(
                f"Warning: Requested sample rate ({requested_rate}) "
                f"differs from actual hardware sample rate ({actual_rate}) by more than 10%."
            )

    def stop(self, **kwargs):
        """Stop the DAQ device."""
        if channel_type := kwargs.get("channel_type", None):
            task = self._tasks[channel_type]
            task.stop()
            self._running_channel_types.discard(channel_type)
        else:
            for task in self._tasks.values():
                task.stop()
            self._running_channel_types.clear()
        if not channel_type or channel_type == ChannelType.ANALOG_INPUT:
            self._timestamper = None

    def read_analog(
        self,
    ) -> DAQmxData:
        """Read from analog input channels."""
        task = self._tasks[ChannelType.ANALOG_INPUT]

        data = task.read(number_of_samples_per_channel=1)
        timestamp = time.time_ns()  # DAQmx does not do hardware timed samples, for the most part

        if isinstance(data[0], float):
            data = [data]

        return DAQmxData(data=data, timestamp=timestamp, dt=None)

    def fetch_analog(self) -> DAQmxData:
        if ChannelType.ANALOG_INPUT not in self._running_channel_types:
            raise RuntimeError("No active scan. Call start() before fetch_analog().")
        if self._ai_hw_timing_config is None:
            raise RuntimeError("configure_ai_sample_rate() must be called before fetching analog data.")
        task = self._tasks[ChannelType.ANALOG_INPUT]

        data = task.read(number_of_samples_per_channel=self._ai_hw_timing_config.samples_per_channel)
        timestamp = time.time_ns()  # DAQmx does not do hardware timed samples, for the most part

        if isinstance(data[0], float):
            data = [data]

        self.points_in_buffer = task.in_stream.avail_samp_per_chan
        return DAQmxData(data=data, timestamp=timestamp, dt=self._actual_sample_period)

    def get_actual_sample_rate(self) -> float | None:
        return self._actual_sample_rate

    def write_analog_value(self, channel: AnalogChannel, value: float):
        task = self._ao_sw_tasks[channel.alias]

        task.write(value)

    def write_digital_line(self, channel: DigitalChannel, data: int):
        """Write to digital output channels."""
        if not isinstance(channel, DigitalLineChannel):
            raise TypeError(
                f"write_digital_line expects a DigitalLineChannel, got {type(channel).__name__}. "
                "Use write_digital_port for whole-port writes."
            )
        task = self._tasks[ChannelType.DIGITAL_OUTPUT]

        if channel.logic is Logic.LOW:
            data = 1 - data

        self._do_lines_state[channel.alias] = bool(data)  # update state with new value

        task.write(list(self._do_lines_state.values()))  # Send everything

    def read_digital_line(self, channel: DigitalChannel) -> int:
        if not isinstance(channel, DigitalLineChannel):
            raise TypeError(
                f"read_digital_line expects a DigitalLineChannel, got {type(channel).__name__}. "
                "Use read_digital_port for whole-port reads."
            )
        task = self._tasks[ChannelType.DIGITAL_INPUT]

        response: bool | list[bool] = (
            task.read()
        )  # DAQmx returns a scalar for one channel or a list for multiple channels in the task.
        response = response if isinstance(response, list) else [response]

        # Update self._di_lines_state with the latest response values
        # Do not update with active high/low calculation
        for i, key in enumerate(self._di_lines_state.keys()):
            self._di_lines_state[key] = response[i]

        index = list(self._di_lines_state.keys()).index(channel.alias)

        data = not response[index] if channel.logic is Logic.LOW else response[index]
        return int(data)

    def write_digital_port(self, channel: DigitalChannel, data: int):
        """Drive a DO port as one N-bit integer; bit ``i`` controls line ``i``."""
        if not isinstance(channel, DigitalPortChannel):
            raise TypeError(
                f"write_digital_port expects a DigitalPortChannel, got {type(channel).__name__}. "
                "Use write_digital_line for single-bit writes."
            )
        if channel.logic is Logic.LOW:
            data ^= (1 << int(channel.width)) - 1
        self._do_port_tasks[channel.alias].write(int(data))

    def read_digital_port(self, channel: DigitalChannel) -> int:
        """Sample a DI port as one N-bit integer; bit ``i`` reflects line ``i``."""
        if not isinstance(channel, DigitalPortChannel):
            raise TypeError(
                f"read_digital_port expects a DigitalPortChannel, got {type(channel).__name__}. "
                "Use read_digital_line for single-bit reads."
            )
        data = int(self._di_port_tasks[channel.alias].read())
        if channel.logic is Logic.LOW:
            data ^= (1 << int(channel.width)) - 1
        return data

    def _read_to_measurements(
        self,
        response: DAQmxData,
        channel_list: Mapping[str, DAQChannel],
        daq_name: str,
        default_tags: dict[str, str],
        **kwargs,
    ) -> list[Measurement]:
        channel_data = dict(zip([f"{daq_name}.{key}" for key in channel_list.keys()], response.data))
        length = len(response.data[0])

        if response.dt:
            if self._timestamper is None:
                self._timestamper, timestamps = HWTimestamper.seed(response.timestamp, response.dt, length)
            else:
                timestamps = self._timestamper.next_batch(response.dt, length)
        else:
            timestamps = [response.timestamp]

        return [
            Measurement(
                channel_data=channel_data,
                timestamps=timestamps,
                tags={**default_tags, **(kwargs or {})},
            )
        ]
