"""Data-acquisition (DAQ) instrument interface, driver contract, and helpers."""

import abc
import logging
import time
from typing import Any, Mapping, Protocol

from instro.daq.scaling.scaling import Scaler
from instro.daq.types import (
    AnalogChannel,
    DAQChannel,
    DigitalChannel,
    DigitalPortWidth,
    Direction,
    HWTimingConfig,
    Logic,
    RelayChannel,
    TerminalConfig,
)
from instro.lib import Instrument, Measurement
from instro.lib.instrument import publish_command, publish_measurement
from instro.lib.publishers import Publisher
from instro.lib.types import Command

logger = logging.getLogger(__name__)


class APIInstroDAQ(Protocol):
    """Read-only view of InstroDAQ state exposed to drivers (timing + per-direction channel maps)."""

    @property
    def ai_hw_timing_configs(self) -> HWTimingConfig: ...

    @property
    def ai_sample_rate(self) -> float: ...

    @property
    def channels(self) -> list[DAQChannel]: ...

    @property
    def ai_channels(self) -> dict[str, AnalogChannel]: ...

    @property
    def ao_channels(self) -> dict[str, AnalogChannel]: ...

    @property
    def di_channels(self) -> dict[str, DigitalChannel]: ...

    @property
    def do_channels(self) -> dict[str, DigitalChannel]: ...


class HWTimestamper:
    """Contiguous nanosecond timestamps for hardware-timed DAQ batches.

    Anchors to the wall clock exactly once via ``seed()``, then advances by
    sample period on every ``next_batch()`` call — eliminates timestamp overlap
    when consecutive reads return in rapid succession.
    """

    def __init__(self, last_timestamp: int):
        self._last_timestamp = last_timestamp

    @classmethod
    def seed(cls, t_wall: int, dt: int, length: int) -> tuple["HWTimestamper", list[int]]:
        """Anchor the timeline at ``t_wall`` ns (read-return time of the first batch)."""
        t0 = t_wall - dt * (length - 1)
        timestamps = [t0 + i * dt for i in range(length)]
        return cls(timestamps[-1]), timestamps

    def next_batch(self, dt: int, length: int) -> list[int]:
        """Return ``length`` ns timestamps at ``dt`` spacing, continuing from the previous batch."""
        t0 = self._last_timestamp + dt
        timestamps = [t0 + i * dt for i in range(length)]
        self._last_timestamp = timestamps[-1]
        return timestamps


class DAQDriverBase(abc.ABC):
    """Vendor DAQ driver contract. Concrete drivers own their transport and lifecycle.

    The composed ``InstroDAQ`` installs an ``InstroDAQFacade`` (implements
    ``APIInstroDAQ``) onto ``self.hal`` so drivers can read back configured
    channels and timing without coupling to the instrument's internal state.
    """

    points_in_buffer: int
    hal: APIInstroDAQ

    @abc.abstractmethod
    def open(self):
        """Open the underlying transport (or verify the device is present, for handle-less SDKs)."""
        ...

    @abc.abstractmethod
    def close(self):
        """Close every task/handle owned by the driver. Idempotent."""
        ...

    @abc.abstractmethod
    def configure_ai_channel(
        self,
        channel: AnalogChannel,
    ):
        """Register an AI channel with the underlying driver (range, terminal mode, scaler — vendor-specific)."""
        ...

    def configure_ao_channel(
        self,
        channel: AnalogChannel,
    ):
        """Register an AO channel. Default is a no-op; override if the driver supports analog output."""
        ...

    @abc.abstractmethod
    def configure_ai_hw_timing(
        self,
        hw_timing_config: HWTimingConfig,
    ):
        """Configure hardware-timed AI sampling at ``hw_timing_config.sample_rate``.

        Called before ``start()`` whenever ``InstroDAQ.configure_ai_sample_rate()``
        is invoked. The driver should program the sample clock and any
        ``samples_per_channel`` buffer sizing the underlying SDK requires.
        """
        ...

    @abc.abstractmethod
    def define_digital_channel(
        self,
        direction: Direction,
        physical_channel: str,
        logic: Logic,
        logic_level: float | None = None,
        alias: str | None = None,
        port_width: DigitalPortWidth | None = None,
    ) -> DigitalChannel:
        """Parse a vendor-specific ``physical_channel`` string into a ``DigitalLineChannel`` or ``DigitalPortChannel``.

        ``port_width`` is supplied for port-mode channels; line-mode channels
        encode their bit position in ``physical_channel`` per vendor convention
        (e.g. ``"port0/line3"`` on NI, ``"5101/3"`` on Keysight 34980A,
        ``"AUXPORT0/1"`` on MCC).
        """
        ...

    @abc.abstractmethod
    def configure_do_channel(
        self,
        channel: DigitalChannel,
    ):
        """Register a DO channel (line or port) with the underlying driver."""
        ...

    @abc.abstractmethod
    def configure_di_channel(
        self,
        channel: DigitalChannel,
    ):
        """Register a DI channel (line or port) with the underlying driver."""
        ...

    @abc.abstractmethod
    def start(self, **kwargs):
        """Start hardware-timed acquisition.

        ``InstroDAQ`` passes ``channel_type=<ChannelType>`` when the user
        targets a specific task (e.g. on NI, where AI/AO/DI/DO each have their
        own DAQmx task). Drivers without that distinction can ignore it.
        """
        ...

    @abc.abstractmethod
    def stop(self, **kwargs):
        """Stop a running acquisition and release any scan buffers. ``channel_type`` mirrors :meth:`start`."""
        ...

    @abc.abstractmethod
    def read_analog(
        self,
    ) -> Any:
        """Software-timed read of every configured AI channel.

        Returns a vendor-specific payload that ``_read_to_measurements`` then
        unpacks into ``Measurement``s. ``response.dt`` should be ``None`` so
        the wrapper timestamps with wall-clock time.
        """
        ...

    @abc.abstractmethod
    def fetch_analog(
        self,
    ) -> Any:
        """Block until ``samples_per_channel`` new AI samples are available, then return them.

        Drivers should set ``self.points_in_buffer`` for buffer-depth
        telemetry and return ``dt`` (ns per sample) so the wrapper can
        build contiguous timestamps via ``HWTimestamper``.
        """
        ...

    def get_actual_sample_rate(self) -> float | None:
        """Actual hardware sample rate achieved after ``start()``.

        Default returns ``None`` (driver doesn't know or hasn't started).
        Override on drivers whose SDK reports the effective rate (NI, MCC,
        LabJack T-series all do).
        """
        return None

    def write_analog_value(self, channel: AnalogChannel, value: float):
        """Write ``value`` to AO ``channel``. Override if the driver supports analog output."""
        raise NotImplementedError("Analog Output has not been configured for this driver")

    @abc.abstractmethod
    def write_digital_line(self, channel: DigitalChannel, data: int):
        """Drive a single DO line. ``data`` is 0 or 1 (active-low ``channel.logic`` is handled in the driver)."""
        ...

    @abc.abstractmethod
    def read_digital_line(self, channel: DigitalChannel) -> int:
        """Sample a single DI line. Returns 0 or 1 after applying ``channel.logic``."""
        ...

    @abc.abstractmethod
    def write_digital_port(self, channel: DigitalChannel, data: int):
        """Drive a multi-line DO port. ``data`` is an N-bit integer; bit ``i`` controls line ``i``."""
        ...

    @abc.abstractmethod
    def read_digital_port(self, channel: DigitalChannel) -> int:
        """Sample a multi-line DI port. Returns an N-bit integer; bit ``i`` reflects line ``i``."""
        ...

    def define_relay_channel(
        self,
        physical_channel: str,
        alias: str | None = None,
    ) -> RelayChannel:
        """Build a ``RelayChannel`` for ``physical_channel`` (e.g. ``"3101"`` = slot 3 / channel 101).

        Default implementation suits the Keysight 34980A's slot/channel
        addressing; override if the driver needs different parsing.
        """
        alias = alias or physical_channel
        return RelayChannel(
            physical_channel=physical_channel,
            alias=alias,
            direction=Direction.OUTPUT,  # Relay control is treated as an output command
        )

    def close_relay(self, channel: RelayChannel):
        """Close the relay (connect the circuit). Override if the driver supports relays."""
        raise NotImplementedError("Relay control has not been configured for this driver")

    def open_relay(self, channel: RelayChannel):
        """Open the relay (disconnect the circuit). Override if the driver supports relays."""
        raise NotImplementedError("Relay control has not been configured for this driver")

    @abc.abstractmethod
    def _read_to_measurements(
        self,
        response: Any,
        channel_list: Mapping[str, DAQChannel],
        daq_name: str,
        default_tags: dict[str, str],
        **kwargs,
    ) -> list[Measurement]:
        """Unpack a vendor-specific ``response`` from :meth:`read_analog` / :meth:`fetch_analog` into Measurements.

        One Measurement per timebase cluster — for vendors where every AI
        channel shares a clock, that's a single entry; for the Keysight 34980A
        (per-channel timestamps in the scan reply) it's one Measurement per
        channel. The wrapper publishes whatever this returns.
        """
        ...


class InstroDAQ(Instrument):
    def __init__(
        self,
        name: str,
        driver: DAQDriverBase,
        publishers: list[Publisher] | None = None,
        **kwargs,
    ):
        """Initialize an InstroDAQ.

        Args:
            name: Channel-name prefix for published data.
            driver: Concrete DAQ driver; owns its own transport::

                daq = InstroDAQ(
                    "myDAQ",
                    driver=Keysight34980A("USB0::0x0957::0x0507::MY44001757::INSTR"),
                )

            publishers: Publishers that receive emitted Measurement/Command data.
            **kwargs: Default tags applied to every emitted Measurement/Command.
                Pass ``dataset_rid="<rid>"`` to auto-create a NominalCorePublisher
                (uses the on-disk 'default' Nominal credential).
        """
        super().__init__(name, publishers=publishers, **kwargs)

        self._driver = driver
        self._driver.hal = InstroDAQFacade(self)

        self._channels: list[DAQChannel] = []

        self._analog_input_channels: dict[str, AnalogChannel] = {}
        self._analog_output_channels: dict[str, AnalogChannel] = {}
        self._digital_input_channels: dict[str, DigitalChannel] = {}
        self._digital_output_channels: dict[str, DigitalChannel] = {}
        self._relay_channels: dict[str, RelayChannel] = {}

        self._ai_hw_timing_config: HWTimingConfig | None = None
        self._ao_hw_timing_config: HWTimingConfig | None = None
        self._di_hw_timing_config: HWTimingConfig | None = None
        self._do_hw_timing_config: HWTimingConfig | None = None

        self._background_config.interval = (
            0  # DAQ reads block so set this to zero because they implicitly time the loop
        )

    # Need to ensure background interval never adds a wait for InstroDAQ
    @property
    def background_interval(self) -> float:
        """Always 0 for DAQ: blocking reads implicitly time the daemon loop via ``samples_per_channel``."""
        return self._background_config.interval

    @background_interval.setter
    def background_interval(self, seconds: float):
        """No-op for DAQ — the interval is fixed at 0 while the daemon is enabled."""
        return

    @property
    def background_enable(self) -> bool:
        """Whether the background daemon is enabled."""
        return self._background_config.enabled

    @background_enable.setter
    def background_enable(self, enable: bool):
        """Enable/disable the background daemon.

        When enabled, the daemon continuously fetches the DAQ buffer; the interval
        is set to 0 so the blocking fetch implicitly times the loop. When
        disabled, the interval is bumped to 1 s so the loop doesn't burn cycles.
        """
        if enable:
            # Never wait. Let fetch block
            self._background_config.interval = 0
        else:
            # Give background thread a big wait so as not to eat cycles
            self._background_config.interval = 1

        self._background_config.enabled = enable

    def open(self):
        """Open the underlying driver."""
        logger.info("Opening DAQ '%s'", self.name)
        self._driver.open()
        logger.info("Opened DAQ '%s'", self.name)

    def close(self):
        """Close the underlying driver and stop the daemon."""
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
    ):
        """Configure an analog channel.

        Args:
            direction: ``INPUT`` or ``OUTPUT``.
            physical_channel: Vendor-specific channel id (e.g. ``"ai0"`` or ``"Dev1/ai0"``).
            alias: Friendly name; defaults to ``physical_channel``.
            range_min: Lower voltage range (volts).
            range_max: Upper voltage range (volts).
            scaler: Optional ``Scaler`` applied to AI samples after read.
            terminal_config: Terminal wiring (RSE / NRSE / DIFF) for the channel.
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

        match direction:
            case Direction.INPUT:
                self._analog_input_channels.update({channel.alias: channel})
                self._driver.configure_ai_channel(channel)
            case Direction.OUTPUT:
                self._analog_output_channels.update({channel.alias: channel})
                self._driver.configure_ao_channel(channel)
            case _:
                raise ValueError(
                    f"Unsupported analog channel direction: {direction}. Expected Direction.INPUT or Direction.OUTPUT."
                )
        logger.info("Configured analog channel on DAQ '%s'", self.name)

    def configure_ai_sample_rate(
        self,
        sample_rate: float,
        samples_per_channel: int | None = None,
        **kwargs,
    ):
        """Configure the hardware sample clock for AI channels.

        Args:
            sample_rate: Sample rate (Hz). Applies to all AI channels.
            samples_per_channel: Samples per channel per ``read_analog()`` call;
                defaults to 10 % of ``sample_rate`` (e.g. 100 at 1 kHz).
        """
        if not samples_per_channel:
            samples_per_channel = int(sample_rate // 10)

        self._ai_hw_timing_config = HWTimingConfig(
            sample_rate=sample_rate,
            sample_period=round(1e9 / sample_rate),
            samples_per_channel=samples_per_channel,
        )

        self._driver.configure_ai_hw_timing(
            hw_timing_config=self._ai_hw_timing_config,
        )

        # Set buffer length to 10 seconds or the default Instrument length, whichever is greater
        self._channel_buffer_length = max(int(sample_rate * 10), self._channel_buffer_length)
        logger.info("Configured AI hardware timing on DAQ '%s'", self.name)

    def start(self, **kwargs):
        """Start hardware-timed acquisition.

        Args:
            **kwargs: ``channel_type`` (NI only) selects which DAQmx task to start.
        """
        # DAQmx allows starting different channel_types independently.
        channel_type = kwargs.get("channel_type", None)

        # TODO
        # Need to evaluate spinning up a different worker per channel type, but this
        # gets weird with different devices. DAQmx's channel types are their own things
        # whereas labjack is all one timing engine. Tricky architecture.
        # Baselining ai sample rate as the rate right now, which will break as soon as
        # we add other channel type capabilities that are hardware timed.

        self._driver.start(channel_type=channel_type)
        self._define_background_daemon()

        super().start()

    def stop(self, **kwargs):
        """Stop the DAQ device."""
        super().stop()
        channel_type = kwargs.get("channel_type", None)
        self._driver.stop(channel_type=channel_type, **kwargs)

    def read_analog(
        self,
        **kwargs,
    ) -> Measurement | list[Measurement]:
        """Dispatch a hardware-timed buffer fetch or a software-timed conversion based on configuration.

        Each branch publishes its own Measurements; this dispatcher does not.
        Hardware-timed with the background daemon running raises — the daemon owns the buffer.
        Returns a single Measurement when channels share a timebase, otherwise one Measurement per timebase cluster.
        """
        if self._ai_hw_timing_config:
            if not self._background_config.enabled:
                return self._fetch_analog(**kwargs)
            # Background daemon running. The user can't pull from the buffer mid-flight.
            # TODO revisit with INSTRO-149 issue ticket.
            raise RuntimeError("Cannot read analog data while background acquisition daemon is running")

        return self._software_timed_read(**kwargs)

    @publish_measurement
    def _software_timed_read(self, **kwargs) -> Measurement | list[Measurement]:
        """Initiate a software-timed analog conversion and return the resulting Measurement(s)."""
        response = self._driver.read_analog()
        measurements = self._driver._read_to_measurements(
            response=response,
            channel_list=self._analog_input_channels,
            daq_name=self.name,
            default_tags=self.default_tags,
            **kwargs,
        )
        measurements = self._scale_analog_measurement(measurements)
        return measurements[0] if len(measurements) == 1 else measurements

    @publish_measurement
    def _fetch_analog(self, **kwargs) -> Measurement | list[Measurement]:
        """Fetch buffered samples from a hardware-timed acquisition; also publishes buffer depth on ``{name}.buffer``."""
        if not self._ai_hw_timing_config:
            raise RuntimeError(
                "Cannot fetch analog data without hardware timing configured. "
                "Call configure_ai_sample_rate() before starting a hardware-timed acquisition."
            )

        response = self._driver.fetch_analog()
        measurements = self._driver._read_to_measurements(
            response=response,
            channel_list=self._analog_input_channels,
            daq_name=self.name,
            default_tags=self.default_tags,
            **kwargs,
        )
        measurements = self._scale_analog_measurement(measurements)

        # HW-timed acquisition: also publish current buffer depth as telemetry.
        self.get_points_in_buffer()

        return measurements[0] if len(measurements) == 1 else measurements

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
        """Write ``value`` (volts) to AO ``channel`` (alias). Raises ``KeyError`` if ``channel`` isn't configured."""
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
    ):
        """Configure a digital channel.

        Args:
            direction: ``INPUT`` or ``OUTPUT``.
            physical_channel: Vendor-specific id (e.g. ``"di0"`` or ``"port0/line0"``).
            logic: Active-``HIGH`` or active-``LOW``.
            logic_level: Voltage threshold (volts); the driver default is used when ``None``.
            alias: Friendly name; defaults to ``physical_channel``.
            port_width: Port width in bits (8/16/32/64) when treating the channel as a port rather than a line.
        """
        channel = self._driver.define_digital_channel(
            direction=direction,
            physical_channel=physical_channel,
            logic=logic,
            logic_level=logic_level,
            alias=alias,
            port_width=port_width,
        )

        self._channels.append(channel)

        match direction:
            case Direction.INPUT:
                self._digital_input_channels.update({channel.alias: channel})
                self._driver.configure_di_channel(channel)
            case Direction.OUTPUT:
                self._digital_output_channels.update({channel.alias: channel})
                self._driver.configure_do_channel(channel)
        logger.info("Configured digital channel on DAQ '%s'", self.name)

    @publish_command
    def write_digital_line(self, channel: str, data: int, **kwargs) -> Command:
        """Write 0/1 to DO line ``channel`` (alias). Raises ``KeyError`` if ``channel`` isn't configured."""
        if (digital_channel := self._digital_output_channels.get(channel, None)) is None:
            raise KeyError(
                f"Digital output channel '{channel}' is not configured. "
                f"Configured digital output channels: {list(self._digital_output_channels.keys())}. "
                f"Call configure_digital_channel(Direction.OUTPUT, ...) first."
            )
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
        """Read DI line ``channel`` (alias). Raises ``KeyError`` if ``channel`` isn't configured."""
        if (digital_channel := self._digital_input_channels.get(channel, None)) is None:
            raise KeyError(
                f"Digital input channel '{channel}' is not configured. "
                f"Configured digital input channels: {list(self._digital_input_channels.keys())}. "
                f"Call configure_digital_channel(Direction.INPUT, ...) first."
            )
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
        """Write ``data`` to DO port ``channel`` (alias). Raises ``KeyError`` if ``channel`` isn't configured."""
        if (digital_channel := self._digital_output_channels.get(channel, None)) is None:
            raise KeyError(
                f"Digital output channel '{channel}' is not configured. "
                f"Configured digital output channels: {list(self._digital_output_channels.keys())}. "
                f"Call configure_digital_channel(Direction.OUTPUT, ...) first."
            )
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
        """Read DI port ``channel`` (alias). Raises ``KeyError`` if ``channel`` isn't configured."""
        if (digital_channel := self._digital_input_channels.get(channel, None)) is None:
            raise KeyError(
                f"Digital input channel '{channel}' is not configured. "
                f"Configured digital input channels: {list(self._digital_input_channels.keys())}. "
                f"Call configure_digital_channel(Direction.INPUT, ...) first."
            )
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
        """Configure a relay channel (``physical_channel`` e.g. ``"3101"`` = slot 3 / channel 101)."""
        channel = self._driver.define_relay_channel(
            physical_channel=physical_channel,
            alias=alias,
        )
        self._relay_channels[channel.alias] = channel
        logger.info("Configured relay channel on DAQ '%s'", self.name)

    @publish_command
    def close_relay(self, channel: str, **kwargs) -> Command:
        """Close relay ``channel`` (alias) — connects the circuit."""
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
        """Open relay ``channel`` (alias) — disconnects the circuit."""
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
        """Register ``_fetch_analog`` as the daemon function when AI channels exist."""
        if self._analog_input_channels:
            self.add_background_daemon_function(self._fetch_analog)

    def get_actual_sample_rate(self) -> float | None:
        """Hardware's actual sample rate after ``start()``; ``None`` if unsupported or not started."""
        return self._driver.get_actual_sample_rate()

    @publish_measurement
    def get_points_in_buffer(self, **kwargs) -> Measurement:
        """Publish the current DAQ buffer depth on channel ``{name}.buffer``."""
        return self._package_measurement("buffer", self._driver.points_in_buffer, time.time_ns(), **kwargs)


class HWTimingException(Exception): ...


class InstroDAQFacade:
    """Read-only view of an ``InstroDAQ`` exposed to drivers (implements ``APIInstroDAQ``)."""

    # Implements APIInstroDAQ
    def __init__(self, nominal_daq: InstroDAQ):
        self._nominal_daq = nominal_daq

    @property
    def ai_hw_timing_configs(self) -> HWTimingConfig:
        """AI hardware-timing config. Raises ``ValueError`` if ``configure_ai_sample_rate`` was not called."""
        if config := self._nominal_daq._ai_hw_timing_config:
            return config
        raise ValueError(
            "Hardware timing has not been configured for analog input channels. Call configure_ai_sample_rate() first."
        )

    @property
    def ai_sample_rate(self) -> float:
        """AI sample rate (Hz). Raises ``ValueError`` if hardware timing isn't configured."""
        return self.ai_hw_timing_configs.sample_rate

    @property
    def channels(self) -> list[DAQChannel]:
        return self._nominal_daq._channels

    @property
    def ai_channels(self) -> dict[str, AnalogChannel]:
        return self._nominal_daq._analog_input_channels

    @property
    def ao_channels(self) -> dict[str, AnalogChannel]:
        return self._nominal_daq._analog_output_channels

    @property
    def di_channels(self) -> dict[str, DigitalChannel]:
        return self._nominal_daq._digital_input_channels

    @property
    def do_channels(self) -> dict[str, DigitalChannel]:
        return self._nominal_daq._digital_output_channels
