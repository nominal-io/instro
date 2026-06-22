"""Data-acquisition (DAQ) instrument interface, driver contract, and helpers."""

import abc
import logging
import time
from types import MappingProxyType
from typing import Any, Mapping

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
from instro.lib import Instrument, InstrumentNotOpenError, Measurement
from instro.lib.instrument import publish_command, publish_measurement
from instro.lib.publishers import Publisher
from instro.lib.types import Command

logger = logging.getLogger(__name__)


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
    """Vendor DAQ driver contract.

    The driver is the single source of truth for configured channels and
    timing config, held in private dicts/slots that ``__init__`` initializes so
    every concrete driver has the same shape; subclasses call
    ``super().__init__()`` and then populate those privates inside their own
    ``configure_*`` methods (``self._ai_channels[channel.alias] = channel``,
    ``self._ai_hw_timing_config = hw_timing_config``, etc.). Read-only
    ``@property`` accessors hand back frozen snapshots so the state can't be
    mutated from outside the ``configure_*`` path; ``InstroDAQ`` exposes the
    same snapshots for user introspection — it does not keep its own copies.
    """

    points_in_buffer: int

    _ai_channels: dict[str, AnalogChannel]
    _ao_channels: dict[str, AnalogChannel]
    _di_channels: dict[str, DigitalChannel]
    _do_channels: dict[str, DigitalChannel]
    _relay_channels: dict[str, RelayChannel]

    _ai_hw_timing_config: HWTimingConfig | None
    _ao_hw_timing_config: HWTimingConfig | None
    _di_hw_timing_config: HWTimingConfig | None
    _do_hw_timing_config: HWTimingConfig | None

    def __init__(self) -> None:
        self.points_in_buffer = 0

        self._ai_channels = {}
        self._ao_channels = {}
        self._di_channels = {}
        self._do_channels = {}
        self._relay_channels = {}

        self._ai_hw_timing_config = None
        self._ao_hw_timing_config = None
        self._di_hw_timing_config = None
        self._do_hw_timing_config = None

    @property
    def channels(self) -> tuple[DAQChannel, ...]:
        """Frozen snapshot of all configured AI/AO/DI/DO channels (excludes relays)."""
        return (
            *self._ai_channels.values(),
            *self._ao_channels.values(),
            *self._di_channels.values(),
            *self._do_channels.values(),
        )

    @property
    def ai_channels(self) -> Mapping[str, AnalogChannel]:
        """Frozen snapshot of configured AI channels, keyed by alias."""
        return MappingProxyType(dict(self._ai_channels))

    @property
    def ao_channels(self) -> Mapping[str, AnalogChannel]:
        """Frozen snapshot of configured AO channels, keyed by alias."""
        return MappingProxyType(dict(self._ao_channels))

    @property
    def di_channels(self) -> Mapping[str, DigitalChannel]:
        """Frozen snapshot of configured DI channels, keyed by alias."""
        return MappingProxyType(dict(self._di_channels))

    @property
    def do_channels(self) -> Mapping[str, DigitalChannel]:
        """Frozen snapshot of configured DO channels, keyed by alias."""
        return MappingProxyType(dict(self._do_channels))

    @property
    def relay_channels(self) -> Mapping[str, RelayChannel]:
        """Frozen snapshot of configured relay channels, keyed by alias."""
        return MappingProxyType(dict(self._relay_channels))

    @property
    def ai_hw_timing_config(self) -> HWTimingConfig | None:
        return self._ai_hw_timing_config

    @property
    def ao_hw_timing_config(self) -> HWTimingConfig | None:
        return self._ao_hw_timing_config

    @property
    def di_hw_timing_config(self) -> HWTimingConfig | None:
        return self._di_hw_timing_config

    @property
    def do_hw_timing_config(self) -> HWTimingConfig | None:
        return self._do_hw_timing_config

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
        """Register an AO channel. Override if the driver supports analog output."""
        raise NotImplementedError("Analog Output has not been configured for this driver")

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
    def configure_di_line_channel(
        self,
        physical_channel: str,
        logic: Logic,
        logic_level: float | None = None,
        alias: str | None = None,
    ):
        """Parse, program, and register a DI line channel."""
        ...

    @abc.abstractmethod
    def configure_do_line_channel(
        self,
        physical_channel: str,
        logic: Logic,
        logic_level: float | None = None,
        alias: str | None = None,
    ):
        """Parse, program, and register a DO line channel."""
        ...

    def configure_di_port_channel(
        self,
        physical_channel: str,
        logic: Logic,
        port_width: DigitalPortWidth,
        logic_level: float | None = None,
        alias: str | None = None,
    ):
        """Parse, program, and register a DI port channel. Override if the driver supports port-mode digital input."""
        raise NotImplementedError("Digital Input port mode has not been configured for this driver")

    def configure_do_port_channel(
        self,
        physical_channel: str,
        logic: Logic,
        port_width: DigitalPortWidth,
        logic_level: float | None = None,
        alias: str | None = None,
    ):
        """Parse, program, and register a DO port channel. Override if the driver supports port-mode digital output."""
        raise NotImplementedError("Digital Output port mode has not been configured for this driver")

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
        addressing; override if the driver needs different parsing. Overrides
        must also record the resulting channel on ``self._relay_channels``.
        """
        alias = alias or physical_channel
        channel = RelayChannel(
            physical_channel=physical_channel,
            alias=alias,
            direction=Direction.OUTPUT,  # Relay control is treated as an output command
        )
        self._relay_channels[channel.alias] = channel
        return channel

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
        self._is_open = False

        self._background_config.interval = (
            0  # DAQ reads block so set this to zero because they implicitly time the loop
        )

    @property
    def driver(self) -> DAQDriverBase:
        """The underlying vendor driver. Source of truth for all channel/timing state."""
        return self._driver

    @property
    def channels(self) -> tuple[DAQChannel, ...]:
        """Frozen snapshot of all configured AI/AO/DI/DO channels (excludes relays)."""
        return self._driver.channels

    @property
    def ai_channels(self) -> Mapping[str, AnalogChannel]:
        """Frozen snapshot of configured AI channels, keyed by alias."""
        return self._driver.ai_channels

    @property
    def ao_channels(self) -> Mapping[str, AnalogChannel]:
        """Frozen snapshot of configured AO channels, keyed by alias."""
        return self._driver.ao_channels

    @property
    def di_channels(self) -> Mapping[str, DigitalChannel]:
        """Frozen snapshot of configured DI channels, keyed by alias."""
        return self._driver.di_channels

    @property
    def do_channels(self) -> Mapping[str, DigitalChannel]:
        """Frozen snapshot of configured DO channels, keyed by alias."""
        return self._driver.do_channels

    @property
    def relay_channels(self) -> Mapping[str, RelayChannel]:
        """Frozen snapshot of configured relay channels, keyed by alias."""
        return self._driver.relay_channels

    @property
    def ai_hw_timing_config(self) -> HWTimingConfig | None:
        return self._driver.ai_hw_timing_config

    @property
    def ao_hw_timing_config(self) -> HWTimingConfig | None:
        return self._driver.ao_hw_timing_config

    @property
    def di_hw_timing_config(self) -> HWTimingConfig | None:
        return self._driver.di_hw_timing_config

    @property
    def do_hw_timing_config(self) -> HWTimingConfig | None:
        return self._driver.do_hw_timing_config

    # Need to ensure background interval never adds a wait for InstroDAQ
    @property
    def background_interval(self) -> float:
        """Always 0 for DAQ: blocking reads implicitly time the daemon loop via ``samples_per_channel``."""
        return self._background_config.interval

    @background_interval.setter
    def background_interval(self, seconds: float):
        """No-op for DAQ — the interval is fixed at 0 so the blocking fetch implicitly times the loop."""
        return

    def _require_open(self) -> None:
        """Guard device I/O: raise if a method is called before ``open()``."""
        if not self._is_open:
            raise InstrumentNotOpenError(f"InstroDAQ '{self.name}' is not open. Call open() first.")

    def open(self):
        """Open the underlying driver."""
        logger.info("Opening DAQ '%s'", self.name)
        self._driver.open()
        self._is_open = True
        logger.info("Opened DAQ '%s'", self.name)

    def close(self):
        """Run full teardown unconditionally: daemon, then publishers, then the driver, which owns its idempotency."""
        logger.info("Closing DAQ '%s'", self.name)
        super().close()
        self._driver.close()
        self._is_open = False
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
        self._require_open()
        channel = AnalogChannel(
            physical_channel=physical_channel,
            alias=alias if alias else physical_channel,
            direction=direction,
            range_min=range_min,
            range_max=range_max,
            scaler=scaler,
            terminal_config=terminal_config,
        )

        match direction:
            case Direction.INPUT:
                self._driver.configure_ai_channel(channel)
            case Direction.OUTPUT:
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
        self._require_open()
        if not samples_per_channel:
            samples_per_channel = max(1, int(sample_rate // 10))

        hw_timing_config = HWTimingConfig(
            sample_rate=sample_rate,
            sample_period=round(1e9 / sample_rate),
            samples_per_channel=samples_per_channel,
        )

        self._driver.configure_ai_hw_timing(hw_timing_config=hw_timing_config)

        # Set buffer length to 10 seconds or the default Instrument length, whichever is greater
        self._channel_buffer_length = max(int(sample_rate * 10), self._channel_buffer_length)
        logger.info("Configured AI hardware timing on DAQ '%s'", self.name)

    def start(self, background: bool = True, **kwargs):
        """Start hardware-timed acquisition.

        Args:
            background: When True (default), spin the daemon thread to continuously
                fetch the buffer. When False, begin hardware acquisition only and
                fetch the buffer yourself by calling ``read_analog()``.
            **kwargs: ``channel_type`` (NI only) selects which DAQmx task to start.
        """
        self._require_open()
        # DAQmx allows starting different channel_types independently.
        channel_type = kwargs.get("channel_type", None)

        # TODO
        # Need to evaluate spinning up a different daemon per channel type, but this
        # gets weird with different devices. DAQmx's channel types are their own things
        # whereas labjack is all one timing engine. Tricky architecture.
        # Baselining ai sample rate as the rate right now, which will break as soon as
        # we add other channel type capabilities that are hardware timed.

        self._driver.start(channel_type=channel_type)

        if background:
            self._define_background_daemon()
            super().start()

    def stop(self, **kwargs):
        """Stop hardware acquisition and the background daemon; tolerant teardown when not open."""
        super().stop()
        # Skip the device stop when not open: some drivers' stop() issues a transport
        # command (e.g. Keysight's ABORt) that raises if the session isn't open. close()
        # routes through here, so this gate keeps close-before-open from raising.
        if not self._is_open:
            return
        channel_type = kwargs.pop("channel_type", None)
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
        self._require_open()
        if self.ai_hw_timing_config:
            if not (self._background_thread and self._background_thread.is_alive()):
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
            channel_list=self.ai_channels,
            daq_name=self.name,
            default_tags=self.default_tags,
            **kwargs,
        )
        measurements = self._scale_analog_measurement(measurements)
        return measurements[0] if len(measurements) == 1 else measurements

    @publish_measurement
    def _fetch_analog(self, **kwargs) -> Measurement | list[Measurement]:
        """Fetch buffered samples from a hardware-timed acquisition; also publishes buffer depth on ``{name}.buffer``."""
        if not self.ai_hw_timing_config:
            raise RuntimeError(
                "Cannot fetch analog data without hardware timing configured. "
                "Call configure_ai_sample_rate() before starting a hardware-timed acquisition."
            )

        response = self._driver.fetch_analog()
        measurements = self._driver._read_to_measurements(
            response=response,
            channel_list=self.ai_channels,
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
            for ch_name, ch_config in self.ai_channels.items():
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
        self._require_open()
        if (analog_channel := self.ao_channels.get(channel, None)) is None:
            raise KeyError(
                f"Analog output channel '{channel}' is not configured. "
                f"Configured analog output channels: {list(self.ao_channels.keys())}. "
                f"Call configure_analog_channel(Direction.OUTPUT, ...) first."
            )
        logger.debug("Sending DAQ write_analog_value command to '%s' for channel '%s'", self.name, channel)
        self._driver.write_analog_value(analog_channel, value)
        timestamp = time.time_ns()

        return self._package_command(f"{analog_channel.alias}.cmd", value, timestamp, **kwargs)

    def configure_digital_line(
        self,
        direction: Direction,
        physical_channel: str,
        logic: Logic,
        logic_level: float | None = None,
        alias: str | None = None,
    ):
        """Configure a digital line channel.

        Args:
            direction: ``INPUT`` or ``OUTPUT``.
            physical_channel: Vendor-specific line id (e.g. ``"port0/line3"`` on NI, ``"5101/3"`` on Keysight, ``"FIO0"`` on LabJack).
            logic: Active-``HIGH`` or active-``LOW``.
            logic_level: Voltage threshold (volts); the driver default is used when ``None``.
            alias: Friendly name; defaults to ``physical_channel``.
        """
        self._require_open()
        match direction:
            case Direction.INPUT:
                self._driver.configure_di_line_channel(
                    physical_channel=physical_channel,
                    logic=logic,
                    logic_level=logic_level,
                    alias=alias,
                )
            case Direction.OUTPUT:
                self._driver.configure_do_line_channel(
                    physical_channel=physical_channel,
                    logic=logic,
                    logic_level=logic_level,
                    alias=alias,
                )
        logger.info("Configured digital line channel on DAQ '%s'", self.name)

    def configure_digital_port(
        self,
        direction: Direction,
        physical_channel: str,
        logic: Logic,
        port_width: DigitalPortWidth,
        logic_level: float | None = None,
        alias: str | None = None,
    ):
        """Configure a digital port channel.

        Args:
            direction: ``INPUT`` or ``OUTPUT``.
            physical_channel: Vendor-specific port id (e.g. ``"port0"`` on NI, ``"5101"`` on Keysight, ``"AUXPORT0"`` on MCC).
            logic: Active-``HIGH`` or active-``LOW``.
            port_width: Port width in bits (8/16/32/64).
            logic_level: Voltage threshold (volts); the driver default is used when ``None``.
            alias: Friendly name; defaults to ``physical_channel``.
        """
        self._require_open()
        match direction:
            case Direction.INPUT:
                self._driver.configure_di_port_channel(
                    physical_channel=physical_channel,
                    logic=logic,
                    port_width=port_width,
                    logic_level=logic_level,
                    alias=alias,
                )
            case Direction.OUTPUT:
                self._driver.configure_do_port_channel(
                    physical_channel=physical_channel,
                    logic=logic,
                    port_width=port_width,
                    logic_level=logic_level,
                    alias=alias,
                )
        logger.info("Configured digital port channel on DAQ '%s'", self.name)

    @publish_command
    def write_digital_line(self, channel: str, data: int, **kwargs) -> Command:
        """Write 0/1 to DO line ``channel`` (alias). Raises ``KeyError`` if ``channel`` isn't configured."""
        self._require_open()
        if (digital_channel := self.do_channels.get(channel, None)) is None:
            raise KeyError(
                f"Digital output channel '{channel}' is not configured. "
                f"Configured digital output channels: {list(self.do_channels.keys())}. "
                f"Call configure_digital_line(Direction.OUTPUT, ...) first."
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
        self._require_open()
        if (digital_channel := self.di_channels.get(channel, None)) is None:
            raise KeyError(
                f"Digital input channel '{channel}' is not configured. "
                f"Configured digital input channels: {list(self.di_channels.keys())}. "
                f"Call configure_digital_line(Direction.INPUT, ...) first."
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
        self._require_open()
        if (digital_channel := self.do_channels.get(channel, None)) is None:
            raise KeyError(
                f"Digital output channel '{channel}' is not configured. "
                f"Configured digital output channels: {list(self.do_channels.keys())}. "
                f"Call configure_digital_port(Direction.OUTPUT, ...) first."
            )
        if (width := getattr(digital_channel, "width", None)) is not None:
            max_value = (1 << int(width)) - 1
            if not 0 <= data <= max_value:
                raise ValueError(
                    f"Value {data} does not fit the {int(width)}-bit port '{channel}'; "
                    f"valid range is 0 to {max_value} (0x{max_value:X})."
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
        self._require_open()
        if (digital_channel := self.di_channels.get(channel, None)) is None:
            raise KeyError(
                f"Digital input channel '{channel}' is not configured. "
                f"Configured digital input channels: {list(self.di_channels.keys())}. "
                f"Call configure_digital_port(Direction.INPUT, ...) first."
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
        self._require_open()
        self._driver.define_relay_channel(
            physical_channel=physical_channel,
            alias=alias,
        )
        logger.info("Configured relay channel on DAQ '%s'", self.name)

    @publish_command
    def close_relay(self, channel: str, **kwargs) -> Command:
        """Close relay ``channel`` (alias) — connects the circuit."""
        self._require_open()
        if (relay_channel := self.relay_channels.get(channel, None)) is None:
            raise KeyError(
                f"Relay channel '{channel}' is not configured. "
                f"Configured relay channels: {list(self.relay_channels.keys())}. "
                f"Call configure_relay_channel() first."
            )
        logger.debug("Sending DAQ close_relay command to '%s' for channel '%s'", self.name, channel)
        self._driver.close_relay(relay_channel)
        timestamp = time.time_ns()

        return self._package_command(f"{relay_channel.alias}.cmd", "CLOSED", timestamp, **kwargs)

    @publish_command
    def open_relay(self, channel: str, **kwargs) -> Command:
        """Open relay ``channel`` (alias) — disconnects the circuit."""
        self._require_open()
        if (relay_channel := self.relay_channels.get(channel, None)) is None:
            raise KeyError(
                f"Relay channel '{channel}' is not configured. "
                f"Configured relay channels: {list(self.relay_channels.keys())}. "
                f"Call configure_relay_channel() first."
            )
        logger.debug("Sending DAQ open_relay command to '%s' for channel '%s'", self.name, channel)
        self._driver.open_relay(relay_channel)
        timestamp = time.time_ns()

        return self._package_command(f"{relay_channel.alias}.cmd", "OPEN", timestamp, **kwargs)

    def _define_background_daemon(self):
        """Register ``_fetch_analog`` as the daemon function when AI channels exist."""
        already_registered = any(method == self._fetch_analog for method, _, _ in self._background_methods)
        if self.ai_channels and not already_registered:
            self.add_background_daemon_function(self._fetch_analog)

    def get_actual_sample_rate(self) -> float | None:
        """Hardware's actual sample rate after ``start()``; ``None`` if unsupported or not started."""
        return self._driver.get_actual_sample_rate()

    @publish_measurement
    def get_points_in_buffer(self, **kwargs) -> Measurement:
        """Publish the current DAQ buffer depth on channel ``{name}.buffer``."""
        self._require_open()
        return self._package_measurement("buffer", self._driver.points_in_buffer, time.time_ns(), **kwargs)


class HWTimingException(Exception): ...
