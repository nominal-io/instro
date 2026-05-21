"""Data-acquisition (DAQ) instrument interface, driver contract, and helpers."""

import abc
import logging
import time
from types import MappingProxyType
from typing import Mapping

from instro.daq.scaling.scaling import Scaler
from instro.daq.types import (
    AnalogChannel,
    DAQChannel,
    DAQSamples,
    DAQTask,
    DigitalChannel,
    DigitalLineChannel,
    DigitalPortChannel,
    DigitalPortWidth,
    Direction,
    HWTimingConfig,
    Logic,
    RelayChannel,
    TerminalConfig,
)
from instro.utils import Instrument, Measurement
from instro.utils.instrument import publish_command, publish_measurement
from instro.utils.publishers.publisher import Publisher
from instro.utils.types import Command

logger = logging.getLogger(__name__)


class HWTimestamper:
    """Generates contiguous nanosecond timestamps for hardware-timed DAQ batches.

    Anchors the timeline to the wall clock exactly once via `seed()`, then advances
    purely by sample period on every subsequent `next()` call. This eliminates timestamp
    overlap when two reads return in rapid succession.
    """

    def __init__(self, last_timestamp: int):
        self._last_timestamp = last_timestamp

    @classmethod
    def seed(cls, t_wall: int, dt: int, length: int) -> tuple["HWTimestamper", list[int]]:
        """Create a timestamper anchored to the first batch's wall-clock read-return time.

        Args:
            t_wall: Wall-clock ns timestamp captured when the first read returned.
            dt: Sample period in nanoseconds.
            length: Number of samples in the first batch.

        Returns:
            A seeded HWTimestamper and the timestamps for the first batch.
        """
        t0 = t_wall - dt * (length - 1)
        timestamps = [t0 + i * dt for i in range(length)]
        return cls(timestamps[-1]), timestamps

    def next_batch(self, dt: int, length: int) -> list[int]:
        """Return timestamps for the next batch, continuing from the previous batch.

        Args:
            dt: Sample period in nanoseconds.
            length: Number of samples in this batch.

        Returns:
            list[int]: Nanosecond timestamps, one per sample.
        """
        t0 = self._last_timestamp + dt
        timestamps = [t0 + i * dt for i in range(length)]
        self._last_timestamp = timestamps[-1]
        return timestamps


class DAQDriverBase(abc.ABC):
    """Abstract base class for vendor DAQ drivers.

    Concrete drivers own their transport setup and translate category-level calls
    into vendor-specific commands. The base declares only the category contract;
    transport choice and lifecycle live in the concrete driver.

    Vendor drivers MUST NOT depend on `InstroDAQ`. All context the driver needs at
    any call site is passed through method arguments — typically via a `DAQTask`
    object. This keeps drivers independently constructible and unit-testable.

    Only `open` and `close` are abstract. Every other method has a default that
    raises `NotImplementedError` with a vendor-prefixed message. Drivers override
    what their hardware supports; absent capabilities surface as clear errors at
    call time rather than as silent type-check failures.
    """

    @abc.abstractmethod
    def open(self) -> None:
        """Establish connection to the device."""
        ...

    @abc.abstractmethod
    def close(self) -> None:
        """Disconnect from the device."""
        ...

    # -----------------------------------------------------------------------
    # Hardware-timed task lifecycle. All HW-timed work flows through these
    # methods, keyed by a `DAQTask` that carries channels, timing, and identity.
    # -----------------------------------------------------------------------

    def register_task(self, task: DAQTask) -> None:
        """Register a task with the driver.

        Called once per task when it is first created (before channels are added
        or timing is set). Single-engine vendors (one HW timing config per kind)
        should raise `NotImplementedError` on the second registration for the
        same `task.kind`.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support hardware-timed tasks.")

    def configure_ai_channel(self, task: DAQTask, channel: AnalogChannel) -> None:
        """Apply a per-channel analog-input configuration to the hardware."""
        raise NotImplementedError(f"{type(self).__name__} does not support analog input channel configuration.")

    def configure_ao_channel(self, task: DAQTask, channel: AnalogChannel) -> None:
        """Apply a per-channel analog-output configuration to the hardware."""
        raise NotImplementedError(f"{type(self).__name__} does not support analog output channel configuration.")

    def configure_di_line(self, task: DAQTask, channel: DigitalLineChannel) -> None:
        """Configure a single digital-input line."""
        raise NotImplementedError(f"{type(self).__name__} does not support digital input line configuration.")

    def configure_di_port(self, task: DAQTask, channel: DigitalPortChannel) -> None:
        """Configure a digital-input port (width-grouped lines)."""
        raise NotImplementedError(f"{type(self).__name__} does not support digital input port configuration.")

    def configure_do_line(self, task: DAQTask, channel: DigitalLineChannel) -> None:
        """Configure a single digital-output line."""
        raise NotImplementedError(f"{type(self).__name__} does not support digital output line configuration.")

    def configure_do_port(self, task: DAQTask, channel: DigitalPortChannel) -> None:
        """Configure a digital-output port (width-grouped lines)."""
        raise NotImplementedError(f"{type(self).__name__} does not support digital output port configuration.")

    def configure_timing(self, task: DAQTask) -> None:
        """Apply `task.timing_config` to the hardware.

        Called by `InstroDAQ` when the user sets HW timing on a task. Drivers
        may apply timing eagerly here or defer to `start_task`; the contract is
        only that subsequent `start_task` honor `task.timing_config`.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support hardware-timed acquisition.")

    def start_task(self, task: DAQTask) -> None:
        """Begin acquisition on a hardware-timed task."""
        raise NotImplementedError(f"{type(self).__name__} does not support hardware-timed acquisition.")

    def stop_task(self, task: DAQTask) -> None:
        """Halt acquisition on a hardware-timed task."""
        raise NotImplementedError(f"{type(self).__name__} does not support hardware-timed acquisition.")

    def read_task(self, task: DAQTask) -> list[DAQSamples]:
        """Software-timed read: initiate a fresh conversion and return one sample per channel.

        Returns a list of `DAQSamples`; each entry bundles channels that share a
        timebase. Most vendors return a single-element list (all channels share
        timestamps). Keysight-style hardware that timestamps each channel read
        separately may return one `DAQSamples` per channel. `channel_data` keys
        are channel aliases (no `{daq_name}.` prefix — the facade applies it).
        """
        raise NotImplementedError(f"{type(self).__name__} does not support software-timed analog reads.")

    def fetch_task(self, task: DAQTask) -> list[DAQSamples]:
        """Hardware-timed fetch: pull buffered samples from a running acquisition.

        Drivers de-interleave their vendor SDK response into per-channel data and
        expand any `HWTimestamper` batch into explicit per-sample nanosecond
        timestamps before returning. Returns a list of `DAQSamples`; see
        `read_task` for the multi-element rationale.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support hardware-timed buffered reads.")

    def is_running(self, task: DAQTask) -> bool:
        """Return whether the task is currently acquiring."""
        return False

    def get_actual_sample_rate(self, task: DAQTask) -> float | None:
        """Return the actual sample rate achieved by the hardware after `start_task`.

        Returns `None` if the driver does not support this query or the task has
        not been started.
        """
        return None

    def get_points_in_buffer(self, task: DAQTask) -> int:
        """Samples currently waiting to be read from the task's buffer."""
        return 0

    # -----------------------------------------------------------------------
    # Single-shot analog output (SW-timed).
    # -----------------------------------------------------------------------

    def write_analog_value(self, channel: AnalogChannel, value: float) -> None:
        """Write a value to an analog output channel."""
        raise NotImplementedError(f"{type(self).__name__} does not support analog output.")

    # -----------------------------------------------------------------------
    # Single-shot digital I/O (SW-timed). Line vs port split so drivers receive
    # typed channels and don't need to isinstance-dispatch internally.
    # -----------------------------------------------------------------------

    def read_digital_line(self, channel: DigitalLineChannel) -> int:
        """Read the current value (0 or 1) of a digital input line."""
        raise NotImplementedError(f"{type(self).__name__} does not support digital line reads.")

    def write_digital_line(self, channel: DigitalLineChannel, data: int) -> None:
        """Drive a digital output line to `data` (0 or 1)."""
        raise NotImplementedError(f"{type(self).__name__} does not support digital line writes.")

    def read_digital_port(self, channel: DigitalPortChannel) -> int:
        """Read the current bit pattern of a digital input port."""
        raise NotImplementedError(f"{type(self).__name__} does not support digital port reads.")

    def write_digital_port(self, channel: DigitalPortChannel, data: int) -> None:
        """Drive a digital output port to the bit pattern `data`."""
        raise NotImplementedError(f"{type(self).__name__} does not support digital port writes.")

    # -----------------------------------------------------------------------
    # Relays. `define_relay_channel` has a sensible generic default; vendors
    # override only if they need to attach extra fields.
    # -----------------------------------------------------------------------

    def define_relay_channel(
        self,
        physical_channel: str,
        alias: str | None = None,
    ) -> RelayChannel:
        """Construct a `RelayChannel`. Override only if the vendor needs extra fields."""
        alias = alias or physical_channel
        return RelayChannel(
            physical_channel=physical_channel,
            alias=alias,
            direction=Direction.OUTPUT,
        )

    def close_relay(self, channel: RelayChannel) -> None:
        """Close a relay (connect the circuit)."""
        raise NotImplementedError(f"{type(self).__name__} does not support relay control.")

    def open_relay(self, channel: RelayChannel) -> None:
        """Open a relay (disconnect the circuit)."""
        raise NotImplementedError(f"{type(self).__name__} does not support relay control.")


_DEFAULT_TASK_NAME = "default"


class InstroDAQ(Instrument):
    def __init__(
        self,
        name: str,
        driver: DAQDriverBase,
        publishers: list[Publisher] | None = None,
        **kwargs,
    ):
        """The main interface for using a DAQ device within the Nominal environment.

        Args:
            name (str): A name to give your DAQ that helps differentiate it from other DAQ instances.
                This is appended to the front of your channel name when using the Core Publisher.
            driver (DAQDriverBase): Driver instance for the specific DAQ vendor/model. Concrete
                drivers own their transport setup::

                    daq = InstroDAQ(
                        "myDAQ",
                        driver=Keysight34980A("USB0::0x0957::0x0507::MY44001757::INSTR"),
                    )

            publishers (list[Publisher] | None, optional): Publishers to send data to when executing methods. Defaults to None.
            **kwargs: Optional keyword arguments used as tags throughout the life of the instrument.
                These tags are applied to the Measurement and Command objects and can be utilized by publishers like
                NominalCorePublisher as added metadata.

                Special keyword arguments:
                    dataset_rid (str): If provided, automatically creates and adds a
                        NominalCorePublisher with the specified dataset RID. Assumes a Nominal
                        'default' credential is stored on disk.
        """
        super().__init__(name, connection_config=None, publishers=publishers, **kwargs)

        self._driver = driver

        self._channels: list[DAQChannel] = []
        self._tasks: dict[str, DAQTask] = {}

        self._analog_input_channels: dict[str, AnalogChannel] = {}
        self._analog_output_channels: dict[str, AnalogChannel] = {}
        self._digital_input_channels: dict[str, DigitalChannel] = {}
        self._digital_output_channels: dict[str, DigitalChannel] = {}
        self._relay_channels: dict[str, RelayChannel] = {}

        self._background_config.interval = (
            0  # DAQ reads block so set this to zero because they implicitly time the loop
        )

        # Per-task set of task names with a registered fetch daemon. Tracks
        # registrations to keep `_define_background_daemon` idempotent across
        # repeated start() calls; `add_background_daemon_function` on the base
        # class appends without dedup.
        self._daemons_registered: set[str] = set()

    # Need to ensure background interval never adds a wait for InstroDAQ
    @property
    def background_interval(self) -> float:
        """Get the background interval setting.

        Returns:
            float: The current background worker interval in seconds. For DAQ devices, this is always 0
            since DAQ reads block and implicitly time the loop.
        """
        return self._background_config.interval

    @background_interval.setter
    def background_interval(self, seconds: float):
        """Set the background interval rate.

        Note:
            This setter is a no-op for DAQ devices. The interval is automatically set to 0
            when the background worker is enabled, as DAQ reads block and implicitly time the loop.

        Args:
            seconds (float): Ignored for DAQ devices.
        """
        return

    @property
    def background_enable(self) -> bool:
        """Get the background worker enable state.

        Returns:
            bool: True if background worker is enabled, False otherwise.
        """
        return self._background_config.enabled

    @background_enable.setter
    def background_enable(self, enable: bool):
        """Enable or disable background worker.

        When enabled, the background daemon will continuously fetch data from the DAQ buffer.
        The interval is automatically set to 0 to let the fetch operation block, as DAQ reads
        implicitly time the loop based off samples_per_channel

        Args:
            enable (bool): True to enable background worker, False to disable.
        """
        if enable:
            # Never wait. Let fetch block
            self._background_config.interval = 0
        else:
            # Give background thread a big wait so as not to eat cycles
            self._background_config.interval = 1

        self._background_config.enabled = enable

    # ========  Public accessors  ===========

    @property
    def driver(self) -> DAQDriverBase:
        """Direct access to the underlying driver for vendor-specific operations."""
        return self._driver

    @property
    def tasks(self) -> Mapping[str, DAQTask]:
        """All configured DAQ tasks, keyed by name. Read-only view."""
        return MappingProxyType(self._tasks)

    @property
    def ai_channels(self) -> Mapping[str, AnalogChannel]:
        """Configured analog input channels, keyed by alias."""
        return MappingProxyType(self._analog_input_channels)

    @property
    def ao_channels(self) -> Mapping[str, AnalogChannel]:
        """Configured analog output channels, keyed by alias."""
        return MappingProxyType(self._analog_output_channels)

    @property
    def di_channels(self) -> Mapping[str, DigitalChannel]:
        """Configured digital input channels, keyed by alias."""
        return MappingProxyType(self._digital_input_channels)

    @property
    def do_channels(self) -> Mapping[str, DigitalChannel]:
        """Configured digital output channels, keyed by alias."""
        return MappingProxyType(self._digital_output_channels)

    @property
    def relays(self) -> Mapping[str, RelayChannel]:
        """Configured relay channels, keyed by alias."""
        return MappingProxyType(self._relay_channels)

    # ========  Task management  ===========

    def create_task(
        self,
        name: str,
        sample_rate: float | None = None,
        samples_per_channel: int | None = None,
    ) -> DAQTask:
        """Create a named hardware-timed task that channels can be added to.

        Tasks are kind-agnostic — a single task may hold any mix of analog and
        digital channels (input and output). For multi-task hardware, this is
        how you express independent timing groups. Single-engine vendors raise
        `NotImplementedError`from `register_task` on the second task; their hardware has one scan.

        Args:
            name: Unique task name.
            sample_rate: Optional HW sample rate (Hz). If `None`, the task is
                SW-timed until `configure_ai_sample_rate(task=...)` is called.
            samples_per_channel: Samples per channel per fetch. Defaults to 10%
                of `sample_rate`.
        """
        return self._create_task(name, sample_rate, samples_per_channel)

    def _create_task(
        self,
        name: str,
        sample_rate: float | None,
        samples_per_channel: int | None,
    ) -> DAQTask:
        """Construct a `DAQTask`, register it with the driver, and apply timing if provided."""
        if name in self._tasks:
            raise ValueError(f"Task '{name}' already exists on DAQ '{self.name}'.")
        timing = self._build_timing_config(sample_rate, samples_per_channel) if sample_rate is not None else None
        task = DAQTask(name=name, timing_config=timing)
        self._tasks[name] = task
        self._driver.register_task(task)
        if timing is not None:
            self._driver.configure_timing(task)
            if sample_rate is not None:
                self._channel_buffer_length = max(int(sample_rate * 10), self._channel_buffer_length)
        return task

    def _get_or_create_default_task(self) -> DAQTask:
        """Lazily create the implicit default task on first use."""
        if _DEFAULT_TASK_NAME not in self._tasks:
            task = DAQTask(name=_DEFAULT_TASK_NAME)
            self._tasks[_DEFAULT_TASK_NAME] = task
            self._driver.register_task(task)
        return self._tasks[_DEFAULT_TASK_NAME]

    def _resolve_task(self, task: "str | DAQTask | None") -> DAQTask:
        """Resolve a task reference: None=default, str=lookup, DAQTask=identity check."""
        if task is None:
            return self._get_or_create_default_task()
        if isinstance(task, DAQTask):
            if self._tasks.get(task.name) is not task:
                raise ValueError(f"Task '{task.name}' is not registered with this DAQ.")
            return task
        resolved = self._tasks.get(task)
        if resolved is None:
            raise ValueError(f"Task '{task}' is not configured on DAQ '{self.name}'.")
        return resolved

    @staticmethod
    def _build_timing_config(
        sample_rate: float,
        samples_per_channel: int | None,
    ) -> HWTimingConfig:
        """Build an `HWTimingConfig` from a sample rate, defaulting `samples_per_channel` to 10% of the rate."""
        if not samples_per_channel:
            samples_per_channel = int(sample_rate // 10) or 1
        return HWTimingConfig(
            sample_rate=sample_rate,
            sample_period=round(1e9 / sample_rate),
            samples_per_channel=samples_per_channel,
        )

    def open(self):
        """Establish connection to the device."""
        logger.info("Opening DAQ '%s'", self.name)
        super().open()
        self._driver.open()
        logger.info("Opened DAQ '%s'", self.name)

    def close(self):
        """Disconnect from the device."""
        logger.info("Closing DAQ '%s'", self.name)
        super().close()
        self._driver.close()
        logger.info("Closed DAQ '%s'", self.name)

    # ========  Analog Input  ===========

    def configure_analog_channel(
        self,
        direction: Direction,
        physical_channel: str,
        alias: str | None = None,
        range_min: float = -10.0,
        range_max: float = 10.0,
        scaler: Scaler | None = None,
        terminal_config: TerminalConfig | None = None,
        task: "str | DAQTask | None" = None,
    ):
        """Configure an analog input or output channel.

        Args:
            direction (Direction): The direction of the channel (INPUT or OUTPUT).
            physical_channel (str): The physical channel identifier (e.g., "ai0", "Dev1/ai0").
            alias (str | None, optional): A user-friendly name for the channel. If not provided,
                the physical_channel name is used as the alias. Defaults to None.
            range_min (float, optional): The minimum voltage range for the channel. Defaults to -10.0.
            range_max (float, optional): The maximum voltage range for the channel. Defaults to 10.0.
            scaler (Scaler, optional): A Scaler object responsible for scaling the data read by the DAQ channel. Defaults to None.
            terminal_config (TerminalConfig, optional): The terminal configuration for the channel. Defaults to None.
            task: Task to add this channel to. `None` (default) uses the implicit default task.

        Raises:
            ValueError: If direction is not INPUT or OUTPUT.
        """
        channel = AnalogChannel(
            physical_channel=physical_channel,
            alias=alias if alias else physical_channel,
            direction=direction,
            range_min=range_min,
            range_max=range_max,
            scaler=scaler,
            terminal_config=terminal_config,
        )

        self._channels.append(channel)
        resolved = self._resolve_task(task)
        resolved.channels.append(channel)

        match direction:
            case Direction.INPUT:
                self._analog_input_channels.update({channel.alias: channel})
                self._driver.configure_ai_channel(resolved, channel)
            case Direction.OUTPUT:
                self._analog_output_channels.update({channel.alias: channel})
                self._driver.configure_ao_channel(resolved, channel)
            case _:
                raise ValueError(
                    f"Unsupported analog channel direction: {direction}. Expected Direction.INPUT or Direction.OUTPUT."
                )
        logger.info("Configured analog channel on DAQ '%s'", self.name)

    def configure_ai_sample_rate(
        self,
        sample_rate: float,
        samples_per_channel: int | None = None,
        task: "str | DAQTask | None" = None,
        **kwargs,
    ):
        """Configures the device to use a hardware sample clock at the specified sample rate.

        Args:
            sample_rate (float): Sample Rate (Hz). Applies to all AI channels.
            samples_per_channel (int | None, optional): The number of samples to fetch per channel
            each time read_analog() is called. Defaults to 10% of the sample rate (e.g., for 1000 Hz,
            the default is 100 samples per channel).
            task: Task to configure timing on. `None` uses the implicit default task.
        """
        resolved = self._resolve_task(task)
        resolved.timing_config = self._build_timing_config(sample_rate, samples_per_channel)
        self._driver.configure_timing(resolved)
        # Set buffer length to 10 seconds or the default Instrument length, whichever is greater
        self._channel_buffer_length = max(int(sample_rate * 10), self._channel_buffer_length)
        logger.info("Configured AI hardware timing on DAQ '%s'", self.name)

    def start(self, task: "str | DAQTask | None" = None, **kwargs):
        """Start hardware-timed acquisition.

        Args:
            task: Task to start. `None` (default) starts every configured task
                that has a timing config. Pass a name or `DAQTask` object to
                start one specifically.

        In either case the instrument's background worker thread is ensured to
        be running — the worker is up whenever at least one task is running.
        """
        if task is None:
            for t in self._tasks.values():
                if t.timing_config is not None and not self._driver.is_running(t):
                    self._driver.start_task(t)
        else:
            resolved = self._lookup_task(task)
            self._driver.start_task(resolved)
        self._define_background_daemon()
        super().start()

    def stop(self, task: "str | DAQTask | None" = None, **kwargs):
        """Stop hardware-timed acquisition.

        Args:
            task: Task to stop. `None` (default) stops every running task.
                Pass a name or `DAQTask` object to stop one specifically.

        The instrument's background worker thread is brought down only when no
        tasks remain running. Targeted stops that leave other tasks running
        leave the worker alive.
        """
        if task is None:
            for t in self._tasks.values():
                if self._driver.is_running(t):
                    self._driver.stop_task(t)
        else:
            resolved = self._lookup_task(task)
            self._driver.stop_task(resolved)
        if not self._any_task_running():
            super().stop()

    def _any_task_running(self) -> bool:
        """Return whether any registered task is currently acquiring on the driver."""
        return any(self._driver.is_running(t) for t in self._tasks.values())

    def _lookup_task(self, task: "str | DAQTask") -> DAQTask:
        """Resolve a task ref to a registered DAQTask, with no kind constraint."""
        if isinstance(task, DAQTask):
            if self._tasks.get(task.name) is not task:
                raise ValueError(f"Task '{task.name}' is not registered with this DAQ.")
            return task
        resolved = self._tasks.get(task)
        if resolved is None:
            raise ValueError(f"Task '{task}' is not configured on DAQ '{self.name}'.")
        return resolved

    def read_analog(
        self,
        task: "str | DAQTask | None" = None,
        **kwargs,
    ) -> Measurement | list[Measurement]:
        """Read from analog input channels.

        Dispatches to either the hardware-timed buffer fetch or a software-timed conversion
        based on the DAQ's configuration. Each branch publishes its own Measurements via
        the underlying decorated method; this dispatcher does not publish.

        - Hardware-timed with background disabled: delegates to `_fetch_analog`.
        - Hardware-timed with background enabled: raises — the background daemon owns the
          buffer.
        - Software-timed: delegates to `_software_timed_read`.

        Returns a single Measurement when all configured channels share a timebase,
        otherwise a list of Measurements (one per timebase).
        """
        resolved = self._resolve_task(task)
        if resolved.timing_config is not None:
            if self._background_config.enabled:
                # TODO revisit with CON-793
                raise RuntimeError("Cannot read analog data while background acquisition daemon is running")
            return self._fetch_analog(resolved, **kwargs)
        return self._software_timed_read(resolved, **kwargs)

    @publish_measurement
    def _software_timed_read(self, task: DAQTask, **kwargs) -> Measurement | list[Measurement]:
        """Initiate a software-timed analog conversion and return the resulting Measurement(s)."""
        samples = self._driver.read_task(task)
        measurements = self._scale_analog_measurement([self._measurement_from_samples(s, **kwargs) for s in samples])
        return measurements[0] if len(measurements) == 1 else measurements

    @publish_measurement
    def _fetch_analog(self, task: DAQTask, **kwargs) -> Measurement | list[Measurement]:
        """Fetch buffered analog samples from a hardware-timed acquisition.

        Also publishes the current buffer occupancy via `get_points_in_buffer()` as a
        side-effect Measurement (each call is one telemetry sample on `{name}.buffer`).
        """
        samples = self._driver.fetch_task(task)
        measurements = self._scale_analog_measurement([self._measurement_from_samples(s, **kwargs) for s in samples])
        # HW-timed acquisition: also publish current buffer depth as telemetry.
        self.get_points_in_buffer(task=task)
        return measurements[0] if len(measurements) == 1 else measurements

    def _measurement_from_samples(self, samples: DAQSamples, **kwargs) -> Measurement:
        """Build a Measurement from one `DAQSamples`, applying `{name}.{alias}` naming."""
        channel_data = {f"{self.name}.{alias}": values for alias, values in samples.channel_data.items()}
        return Measurement(
            channel_data=channel_data,
            timestamps=samples.timestamps_ns,
            tags={**self.default_tags, **kwargs},
        )

    def _scale_analog_measurement(self, measurements: list[Measurement]) -> list[Measurement]:
        for measurement in measurements:
            for ch_name, ch_config in self._analog_input_channels.items():
                if ch_config.scaler:
                    ch_meas = measurement._get_channel(f"{self.name}.{ch_name}")
                    scaled_values = [
                        ch_config.scaler.scale(val) for val in ch_meas.channel_data[f"{self.name}.{ch_name}"]
                    ]
                    measurement.channel_data[f"{self.name}.{ch_name}"] = scaled_values
        return measurements

    @publish_command
    def write_analog_value(self, channel: str, value: float, **kwargs) -> Command:
        """Write a value to an analog output channel.

        Args:
            channel (str): The alias of the analog output channel to write to.
            value (float): The analog value to write.
            **kwargs: Optional keyword arguments used as tags. These tags are applied to the
                Command object and can be utilized by publishers like NominalCorePublisher
                as added metadata.

        Raises:
            KeyError: If the specified channel alias is not found in the configured analog output channels.
        """
        if (analog_channel := self._analog_output_channels.get(channel, None)) is None:
            raise KeyError(
                f"Analog output channel '{channel}' is not configured. "
                f"Configured analog output channels: {list(self._analog_output_channels.keys())}. "
                f"Call configure_analog_channel(Direction.OUTPUT, ...) first."
            )
        logger.debug("Sending DAQ write_analog_value command to '%s' for channel '%s'", self.name, channel)
        self._driver.write_analog_value(analog_channel, value)
        timestamp = time.time_ns()

        return self._package_command(f"{analog_channel.alias}.cmd", value, timestamp, **kwargs)

    def configure_digital_channel(
        self,
        direction: Direction,
        physical_channel: str,
        logic: Logic,
        logic_level: float | None = None,
        alias: str | None = None,
        port_width: DigitalPortWidth | None = None,
        task: "str | DAQTask | None" = None,
    ):
        """Configure a digital input or output channel.

        Args:
            direction (Direction): The direction of the channel (INPUT or OUTPUT).
            physical_channel (str): The physical channel identifier (e.g., "di0", "port0/line0").
            logic (Logic): The logic level type (HIGH or LOW).
            logic_level (float | None, optional): The voltage threshold for the logic level (if hardware supports customization).
                If None, the driver's default logic level is used. Defaults to None.
            alias (str | None, optional): A user-friendly name for the channel. If not provided,
                alias will be set to `physical_channel`. Defaults to None.
            port_width (DigitalPortWidth | None, optional): The width of the digital port in bits
                (8, 16, 32, or 64). Only used when port configuring the channel as a port rather than a line. Defaults to None.
            task: Task to add this channel to. `None` (default) uses the implicit default task.
        """
        alias = alias if alias else physical_channel
        channel: DigitalChannel
        if port_width is not None:
            channel = DigitalPortChannel(
                physical_channel=physical_channel,
                alias=alias,
                direction=direction,
                logic_level=logic_level,
                logic=logic,
                width=port_width,
            )
        else:
            channel = DigitalLineChannel(
                physical_channel=physical_channel,
                alias=alias,
                direction=direction,
                logic_level=logic_level,
                logic=logic,
            )

        self._channels.append(channel)
        resolved = self._resolve_task(task)
        resolved.channels.append(channel)

        match direction:
            case Direction.INPUT:
                self._digital_input_channels.update({channel.alias: channel})
                if isinstance(channel, DigitalLineChannel):
                    self._driver.configure_di_line(resolved, channel)
                else:
                    self._driver.configure_di_port(resolved, channel)
            case Direction.OUTPUT:
                self._digital_output_channels.update({channel.alias: channel})
                if isinstance(channel, DigitalLineChannel):
                    self._driver.configure_do_line(resolved, channel)
                else:
                    self._driver.configure_do_port(resolved, channel)
        logger.info("Configured digital channel on DAQ '%s'", self.name)

    @publish_command
    def write_digital_line(self, channel: str, data: int, **kwargs) -> Command:
        """Write a value to a digital output line.

        Args:
            channel (str): The alias of the digital output channel to write to.
            data (int): The digital value to write (typically 0 or 1 for a line).
            **kwargs: Optional keyword arguments used as tags. These tags are applied to the
                Command object and can be utilized by publishers like NominalCorePublisher
                as added metadata.

        Raises:
            KeyError: If the specified channel alias is not found in the configured digital output channels.

        Note:
            The written command is automatically published to all configured publishers.
        """
        if (digital_channel := self._digital_output_channels.get(channel, None)) is None:
            raise KeyError(
                f"Digital output channel '{channel}' is not configured. "
                f"Configured digital output channels: {list(self._digital_output_channels.keys())}. "
                f"Call configure_digital_channel(Direction.OUTPUT, ...) first."
            )
        if not isinstance(digital_channel, DigitalLineChannel):
            raise TypeError(f"Channel '{channel}' is configured as a port, not a line. Use write_digital_port.")
        logger.debug("Sending DAQ write_digital_line command to '%s' for channel '%s'", self.name, channel)
        self._driver.write_digital_line(digital_channel, data)
        timestamp = time.time_ns()

        if self.legacy_naming:
            # Legacy DAQ digital writes published as bare alias (no `{name}.` prefix, no `.cmd` suffix).
            channel_key = digital_channel.alias
        else:
            channel_key = f"{self.name}.{digital_channel.alias}.cmd"
        # Build the Command inline rather than via `_package_command` so the raw `int`
        # value is preserved on the wire. The base helper coerces non-float/non-str data
        # to `float`, which would silently turn `daq.write_digital_line(..., 1)` into
        # `1.0`. Same rationale as Modbus.write.
        return Command(
            channel_data={channel_key: data},
            timestamp=timestamp,
            tags={**self.default_tags, **kwargs},
        )

    @publish_measurement
    def read_digital_line(self, channel: str, **kwargs) -> Measurement:
        """Read a value from a digital input line.

        Args:
            channel (str): The alias of the digital input channel to read from.
            **kwargs: Optional keyword arguments used as tags. These tags are applied to the
                Measurement object and can be utilized by publishers like NominalCorePublisher
                as added metadata.

        Returns:
            Measurement: A Measurement object containing the digital value, channel name, and timestamp.
                The measurement is also automatically published to all configured publishers.

        Raises:
            KeyError: If the specified channel alias is not found in the configured digital input channels.
        """
        if (digital_channel := self._digital_input_channels.get(channel, None)) is None:
            raise KeyError(
                f"Digital input channel '{channel}' is not configured. "
                f"Configured digital input channels: {list(self._digital_input_channels.keys())}. "
                f"Call configure_digital_channel(Direction.INPUT, ...) first."
            )
        if not isinstance(digital_channel, DigitalLineChannel):
            raise TypeError(f"Channel '{channel}' is configured as a port, not a line. Use read_digital_port.")
        response = self._driver.read_digital_line(digital_channel)
        timestamp = time.time_ns()

        if self.legacy_naming:
            # Legacy DAQ digital reads published as bare alias (no `{name}.` prefix).
            return Measurement(
                channel_data={digital_channel.alias: [float(response)]},
                timestamps=[timestamp],
                tags={**self.default_tags, **kwargs},
            )
        return self._package_measurement(digital_channel.alias, response, timestamp, **kwargs)

    @publish_command
    def write_digital_port(self, channel: str, data: int, **kwargs) -> Command:
        """Write a value to a digital output port.

        Args:
            channel (str): The alias of the digital output channel to write to.
            data (int): The digital value to write to the port.
            **kwargs: Optional keyword arguments used as tags. These tags are applied to the
                Command object and can be utilized by publishers like NominalCorePublisher
                as added metadata.

        Raises:
            KeyError: If the specified channel alias is not found in the configured digital output channels.

        Note:
            The written command is automatically published to all configured publishers.
        """
        if (digital_channel := self._digital_output_channels.get(channel, None)) is None:
            raise KeyError(
                f"Digital output channel '{channel}' is not configured. "
                f"Configured digital output channels: {list(self._digital_output_channels.keys())}. "
                f"Call configure_digital_channel(Direction.OUTPUT, ...) first."
            )
        if not isinstance(digital_channel, DigitalPortChannel):
            raise TypeError(f"Channel '{channel}' is configured as a line, not a port. Use write_digital_line.")
        self._driver.write_digital_port(digital_channel, data)
        timestamp = time.time_ns()

        if self.legacy_naming:
            channel_key = digital_channel.alias
        else:
            channel_key = f"{self.name}.{digital_channel.alias}.cmd"
        # Inline construction preserves the raw `int` value (see write_digital_line for rationale).
        return Command(
            channel_data={channel_key: data},
            timestamp=timestamp,
            tags={**self.default_tags, **kwargs},
        )

    @publish_measurement
    def read_digital_port(self, channel: str, **kwargs) -> Measurement:
        """Read a value from a digital input port.

        Args:
            channel (str): The alias of the digital input channel to read from.
            **kwargs: Optional keyword arguments used as tags. These tags are applied to the
                Measurement object and can be utilized by publishers like NominalCorePublisher
                as added metadata.

        Returns:
            Measurement: A Measurement object containing the digital port value, channel name, and timestamp.
                The measurement is also automatically published to all configured publishers.

        Raises:
            KeyError: If the specified channel alias is not found in the configured digital input channels.
        """
        if (digital_channel := self._digital_input_channels.get(channel, None)) is None:
            raise KeyError(
                f"Digital input channel '{channel}' is not configured. "
                f"Configured digital input channels: {list(self._digital_input_channels.keys())}. "
                f"Call configure_digital_channel(Direction.INPUT, ...) first."
            )
        if not isinstance(digital_channel, DigitalPortChannel):
            raise TypeError(f"Channel '{channel}' is configured as a line, not a port. Use read_digital_line.")
        response = self._driver.read_digital_port(digital_channel)
        timestamp = time.time_ns()

        if self.legacy_naming:
            return Measurement(
                channel_data={digital_channel.alias: [float(response)]},
                timestamps=[timestamp],
                tags={**self.default_tags, **kwargs},
            )
        return self._package_measurement(digital_channel.alias, response, timestamp, **kwargs)

    def configure_relay_channel(
        self,
        physical_channel: str,
        alias: str | None = None,
    ):
        """Configure a relay channel.

        Args:
            physical_channel (str): The physical channel identifier (e.g., "3101" for slot 3, channel 101).
            alias (str | None, optional): A user-friendly name for the relay. If not provided,
                the physical_channel name is used as the alias. Defaults to None.
        """
        channel = self._driver.define_relay_channel(
            physical_channel=physical_channel,
            alias=alias,
        )
        self._relay_channels[channel.alias] = channel
        logger.info("Configured relay channel on DAQ '%s'", self.name)

    @publish_command
    def close_relay(self, channel: str, **kwargs) -> Command:
        """Close a relay (connect the circuit).

        Args:
            channel (str): The alias of the relay channel to close.
            **kwargs: Optional keyword arguments used as tags. These tags are applied to the
                Command object and can be utilized by publishers like NominalCorePublisher
                as added metadata.

        Raises:
            KeyError: If the specified channel alias is not found in the configured relay channels.
        """
        if (relay_channel := self._relay_channels.get(channel, None)) is None:
            raise KeyError(
                f"Relay channel '{channel}' is not configured. "
                f"Configured relay channels: {list(self._relay_channels.keys())}. "
                f"Call configure_relay_channel() first."
            )
        logger.debug("Sending DAQ close_relay command to '%s' for channel '%s'", self.name, channel)
        self._driver.close_relay(relay_channel)
        timestamp = time.time_ns()

        return self._package_command(f"{relay_channel.alias}.cmd", "CLOSED", timestamp, **kwargs)

    @publish_command
    def open_relay(self, channel: str, **kwargs) -> Command:
        """Open a relay (disconnect the circuit).

        Args:
            channel (str): The alias of the relay channel to open.
            **kwargs: Optional keyword arguments used as tags. These tags are applied to the
                Command object and can be utilized by publishers like NominalCorePublisher
                as added metadata.

        Raises:
            KeyError: If the specified channel alias is not found in the configured relay channels.
        """
        if (relay_channel := self._relay_channels.get(channel, None)) is None:
            raise KeyError(
                f"Relay channel '{channel}' is not configured. "
                f"Configured relay channels: {list(self._relay_channels.keys())}. "
                f"Call configure_relay_channel() first."
            )
        logger.debug("Sending DAQ open_relay command to '%s' for channel '%s'", self.name, channel)
        self._driver.open_relay(relay_channel)
        timestamp = time.time_ns()

        return self._package_command(f"{relay_channel.alias}.cmd", "OPEN", timestamp, **kwargs)

    def _define_background_daemon(self):
        """Register a background fetch daemon for every HW-timed task with channels.

        Iterates all configured tasks; for each task with a timing config and
        at least one channel that hasn't already been registered, adds a daemon
        that calls `_fetch_analog(task)`. Idempotent — per-task names are tracked
        in `_daemons_registered` because `add_background_daemon_function`
        appends without dedup.
        """
        for task in self._tasks.values():
            if task.name in self._daemons_registered:
                continue
            if task.timing_config is None or not task.channels:
                continue

            # Bind `task` via a default arg so each daemon closure captures its
            # own task by value rather than the loop variable by reference.
            def _daemon(t: DAQTask = task) -> None:
                self._fetch_analog(t)

            self.add_background_daemon_function(_daemon)
            self._daemons_registered.add(task.name)

    def get_actual_sample_rate(self, task: "str | DAQTask | None" = None) -> float | None:
        """Return the actual sample rate achieved by the hardware after start().

        Returns None if the driver does not support this query or if start() has not been called.
        """
        resolved = self._resolve_task(task)
        return self._driver.get_actual_sample_rate(resolved)

    @publish_measurement
    def get_points_in_buffer(self, task: "str | DAQTask | None" = None, **kwargs) -> Measurement:
        """Get the current number of points in the DAQ buffer.

        This is useful for monitoring hardware-timed acquisitions to see how many samples
        are waiting to be read from the buffer.

        Args:
            task: Task to query. `None` (default) queries the implicit default task.
            **kwargs: Optional keyword arguments used as tags. These tags are applied to the
                Measurement object and can be utilized by publishers like NominalCorePublisher
                as added metadata.

        Returns:
            Measurement: A Measurement object containing the buffer point count with channel name
            "{daq_name}.buffer" and a timestamp. The measurement is also automatically published
            to all configured publishers.
        """
        resolved = self._resolve_task(task)
        return self._package_measurement(
            "buffer",
            self._driver.get_points_in_buffer(resolved),
            time.time_ns(),
            **kwargs,
        )


class HWTimingException(Exception): ...
