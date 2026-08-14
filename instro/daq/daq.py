"""Data-acquisition (DAQ) instrument interface, driver contract, and helpers."""

import abc
import logging
import math
import time
from enum import Enum
from types import MappingProxyType
from typing import Any, ClassVar, Mapping, TypeVar

from instro.daq.scaling.scaling import Scaler
from instro.daq.scaling.thermocouple import TC_TYPE, TC_UNIT
from instro.daq.types import (
    AnalogChannel,
    AnalogChannelUnion,
    AnalogCurrentChannel,
    AnalogThermocoupleChannel,
    AnalogVoltageChannel,
    CJCSource,
    DAQChannel,
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
from instro.lib import InstroError, Instrument, InstrumentNotOpenError, Measurement
from instro.lib.instrument import publish_command, publish_measurement
from instro.lib.publishers import Publisher
from instro.lib.types import Command

logger = logging.getLogger(__name__)

_E = TypeVar("_E", bound=Enum)


def _coerce_enum(value: str | _E, enum_cls: type[_E], param: str) -> _E:
    """Convert ``value`` (name or member) to an ``enum_cls`` member, raising a uniform ValueError on a bad value."""
    try:
        return enum_cls(value)
    except ValueError:
        valid = ", ".join(str(member.value) for member in enum_cls)
        raise ValueError(f"{param} '{value}' is not valid; choose one of {valid}.") from None


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

    _ai_channels: dict[str, AnalogChannelUnion]
    _ao_channels: dict[str, AnalogChannelUnion]
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
    def ai_channels(self) -> Mapping[str, AnalogChannelUnion]:
        """Frozen snapshot of configured AI channels, keyed by alias."""
        return MappingProxyType(dict(self._ai_channels))

    @property
    def ao_channels(self) -> Mapping[str, AnalogChannelUnion]:
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

    def configure_ai_voltage_channel(self, channel: AnalogVoltageChannel):
        """Register an AI voltage channel. Override if the driver supports analog voltage input."""
        raise NotImplementedError("Analog voltage input has not been configured for this driver")

    def configure_ao_voltage_channel(self, channel: AnalogVoltageChannel):
        """Register an AO voltage channel. Override if the driver supports analog voltage output."""
        raise NotImplementedError("Analog voltage output has not been configured for this driver")

    def configure_ai_current_channel(self, channel: AnalogCurrentChannel):
        """Register an AI current channel. Override if the driver supports analog current input."""
        raise NotImplementedError("Analog current input has not been configured for this driver")

    def configure_ao_current_channel(self, channel: AnalogCurrentChannel):
        """Register an AO current channel. Override if the driver supports analog current output."""
        raise NotImplementedError("Analog current output has not been configured for this driver")

    def configure_ai_thermocouple_channel(self, channel: AnalogThermocoupleChannel):
        """Register an AI thermocouple channel. Override if the driver supports thermocouple input."""
        raise NotImplementedError("Thermocouple input has not been configured for this driver")

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

    def write_analog_value(self, channel: AnalogChannelUnion, value: float):
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


def _channel_kind(channel: DAQChannel) -> str:
    """Short kind label (e.g. ``voltage_input``) describing an already-configured channel for error messages."""
    direction = channel.direction.value.lower()
    match channel:
        case AnalogThermocoupleChannel():
            return "thermocouple_input"
        case AnalogVoltageChannel():
            return f"voltage_{direction}"
        case AnalogCurrentChannel():
            return f"current_{direction}"
        case DigitalChannel():
            return f"digital_{direction}"
        case AnalogChannel():
            return f"analog_{direction}"
        case _:
            return f"channel_{direction}"


class InstroDAQ(Instrument):
    # Software-timed polling rate used by start() when no AI timing was configured.
    DEFAULT_SW_SAMPLE_RATE: ClassVar[float] = 1.0

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
        self._is_sw_timing_configured = False
        self._running = False

    @property
    def driver(self) -> DAQDriverBase:
        """The underlying vendor driver. Source of truth for all channel/timing state."""
        return self._driver

    @property
    def channels(self) -> tuple[DAQChannel, ...]:
        """Frozen snapshot of all configured AI/AO/DI/DO channels (excludes relays)."""
        return self._driver.channels

    @property
    def ai_channels(self) -> Mapping[str, AnalogChannelUnion]:
        """Frozen snapshot of configured AI channels, keyed by alias."""
        return self._driver.ai_channels

    @property
    def ao_channels(self) -> Mapping[str, AnalogChannelUnion]:
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

    @property
    def is_hw_timing_configured(self) -> bool:
        """True once ``configure_ai_hw_sample_rate()`` has programmed the AI sample clock."""
        return self.ai_hw_timing_config is not None

    @property
    def is_sw_timing_configured(self) -> bool:
        """True once ``configure_ai_sw_sample_rate()`` has set a software-timed polling rate."""
        return self._is_sw_timing_configured

    # Need to ensure background interval never adds a wait for hardware-timed InstroDAQ
    @property
    def background_interval(self) -> float:
        """Daemon loop period (s); 0 unless ``configure_ai_sw_sample_rate()`` set a software-timed rate."""
        return self._background_config.interval

    @background_interval.setter
    def background_interval(self, seconds: float):
        """No-op for DAQ — set the loop period with ``configure_ai_sw_sample_rate()`` instead."""
        return

    def _require_open(self) -> None:
        """Guard device I/O: raise if a method is called before ``open()``."""
        if not self._is_open:
            raise InstrumentNotOpenError(f"InstroDAQ '{self.name}' is not open. Call open() first.")

    def _reject_duplicate_channel(self, alias: str) -> None:
        """Raise if ``alias`` is already configured on the driver, so a channel can't be silently reconfigured."""
        for existing in self._driver.channels:
            if existing.alias == alias:
                raise ValueError(
                    f"channel '{alias}' is already configured "
                    f"({_channel_kind(existing)} on {existing.physical_channel}); remove it before reconfiguring."
                )

    def _verify_not_running(self, alias: str) -> None:
        """Raise if a channel is configured while acquisition is running; the user must ``stop()`` first."""
        if self._running:
            raise RuntimeError(f"cannot configure channel '{alias}' while '{self.name}' is running; call stop() first.")

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

    # ========  Voltage Channels  ===========

    def configure_voltage_input(
        self,
        physical_channel: str,
        *,
        alias: str | None = None,
        range_min: float = -10.0,
        range_max: float = 10.0,
        scaler: Scaler | None = None,
        terminal_config: str | TerminalConfig | None = None,
    ):
        """Configure an analog voltage input channel.

        Args:
            physical_channel: Vendor-specific channel id (e.g. ``"ai0"`` or ``"Dev1/ai0"``).
            alias: Friendly name; defaults to ``physical_channel``.
            range_min: Lower voltage range (volts).
            range_max: Upper voltage range (volts).
            scaler: Optional ``Scaler`` applied to AI samples after read.
            terminal_config: Terminal wiring (RSE / NRSE / DIFF) for the channel.
        """
        self._require_open()
        if terminal_config is not None:
            terminal_config = _coerce_enum(terminal_config, TerminalConfig, "terminal_config")
        alias = alias if alias else physical_channel
        # Channel validation
        self._reject_duplicate_channel(alias)
        self._verify_not_running(alias)
        channel = AnalogVoltageChannel(
            physical_channel=physical_channel,
            alias=alias,
            direction=Direction.INPUT,
            range_min=range_min,
            range_max=range_max,
            scaler=scaler,
            terminal_config=terminal_config,
        )
        self._driver.configure_ai_voltage_channel(channel)
        logger.info("Configured voltage input channel on DAQ '%s'", self.name)

    def configure_voltage_output(
        self,
        physical_channel: str,
        *,
        alias: str | None = None,
        range_min: float = -10.0,
        range_max: float = 10.0,
        scaler: Scaler | None = None,
    ):
        """Configure an analog voltage output channel.

        Args:
            physical_channel: Vendor-specific channel id (e.g. ``"ao0"`` or ``"Dev1/ao0"``).
            alias: Friendly name; defaults to ``physical_channel``.
            range_min: Lower voltage range (volts).
            range_max: Upper voltage range (volts).
            scaler: Optional ``Scaler`` for the channel.
        """
        self._require_open()
        alias = alias if alias else physical_channel
        # Channel validation
        self._reject_duplicate_channel(alias)
        self._verify_not_running(alias)
        channel = AnalogVoltageChannel(
            physical_channel=physical_channel,
            alias=alias,
            direction=Direction.OUTPUT,
            range_min=range_min,
            range_max=range_max,
            scaler=scaler,
        )
        self._driver.configure_ao_voltage_channel(channel)
        logger.info("Configured voltage output channel on DAQ '%s'", self.name)

    # ========  Current Channels  ===========

    def configure_current_input(
        self,
        physical_channel: str,
        *,
        alias: str | None = None,
        range_min: float = 0.0,
        range_max: float = 0.02,
        scaler: Scaler | None = None,
    ):
        """Configure an analog current input channel.

        Args:
            physical_channel: Vendor-specific channel id (e.g. ``"ai0"`` or ``"Dev1/ai0"``).
            alias: Friendly name; defaults to ``physical_channel``.
            range_min: Lower current range (amps).
            range_max: Upper current range (amps).
            scaler: Optional ``Scaler`` applied to AI samples after read.
        """
        self._require_open()
        alias = alias if alias else physical_channel
        # Channel validation
        self._reject_duplicate_channel(alias)
        self._verify_not_running(alias)
        channel = AnalogCurrentChannel(
            physical_channel=physical_channel,
            alias=alias,
            direction=Direction.INPUT,
            range_min=range_min,
            range_max=range_max,
            scaler=scaler,
        )
        self._driver.configure_ai_current_channel(channel)
        logger.info("Configured current input channel on DAQ '%s'", self.name)

    def configure_current_output(
        self,
        physical_channel: str,
        *,
        alias: str | None = None,
        range_min: float = 0.0,
        range_max: float = 0.02,
        scaler: Scaler | None = None,
    ):
        """Configure an analog current output channel.

        Args:
            physical_channel: Vendor-specific channel id (e.g. ``"ao0"`` or ``"Dev1/ao0"``).
            alias: Friendly name; defaults to ``physical_channel``.
            range_min: Lower current range (amps).
            range_max: Upper current range (amps).
            scaler: Optional ``Scaler`` for the channel.
        """
        self._require_open()
        alias = alias if alias else physical_channel
        # Channel validation
        self._reject_duplicate_channel(alias)
        self._verify_not_running(alias)
        channel = AnalogCurrentChannel(
            physical_channel=physical_channel,
            alias=alias,
            direction=Direction.OUTPUT,
            range_min=range_min,
            range_max=range_max,
            scaler=scaler,
        )
        self._driver.configure_ao_current_channel(channel)
        logger.info("Configured current output channel on DAQ '%s'", self.name)

    # ========  Thermocouple Channels  ===========

    def configure_thermocouple_input(
        self,
        physical_channel: str,
        tc_type: str | TC_TYPE,
        *,
        unit: str | TC_UNIT,
        alias: str | None = None,
        range_min: float = 0.0,
        range_max: float = 100.0,
        scaler: Scaler | None = None,
        cjc_source: str | CJCSource = CJCSource.INTERNAL,
        cjc_temp: float | None = None,
        cjc_channel: str | None = None,
        tc_input_scaler: Scaler | None = None,
    ):
        """Configure a thermocouple input channel.

        Args:
            physical_channel: Vendor-specific channel id (e.g. ``"ai0"`` or ``"Dev1/ai0"``).
            tc_type: Thermocouple type — one of B, E, J, K, N, R, S, T.
            unit: Temperature unit for ``range_min``/``range_max``, ``cjc_temp``, and returned readings.
            alias: Friendly name; defaults to ``physical_channel``.
            range_min: Lower temperature range (in ``unit``).
            range_max: Upper temperature range (in ``unit``).
            scaler: Optional ``Scaler`` applied to AI samples after read.
            cjc_source: Cold-junction compensation source (internal / constant / channel).
            cjc_temp: Cold-junction temperature when ``cjc_source`` is ``CONSTANT``, expressed in ``unit``.
            cjc_channel: Channel supplying cold-junction temperature when ``cjc_source`` is ``CHANNEL``.
            tc_input_scaler: Volts-domain scaler applied before temperature conversion if amplifier was used (LabJack only).
        """
        self._require_open()
        tc_type = _coerce_enum(tc_type, TC_TYPE, "tc_type")
        cjc_source = _coerce_enum(cjc_source, CJCSource, "cjc_source")
        unit = _coerce_enum(unit, TC_UNIT, "unit")
        alias = alias if alias else physical_channel
        # Channel validation
        self._reject_duplicate_channel(alias)
        self._verify_not_running(alias)
        channel = AnalogThermocoupleChannel(
            physical_channel=physical_channel,
            alias=alias,
            direction=Direction.INPUT,
            range_min=range_min,
            range_max=range_max,
            scaler=scaler,
            tc_type=tc_type,
            cjc_source=cjc_source,
            cjc_temp=cjc_temp,
            cjc_channel=cjc_channel,
            unit=unit,
            tc_input_scaler=tc_input_scaler,
        )
        self._driver.configure_ai_thermocouple_channel(channel)
        logger.info("Configured thermocouple input channel on DAQ '%s'", self.name)

    # ========  Digital Channels  ===========

    def configure_digital_input(
        self,
        physical_channel: str,
        *,
        logic: str | Logic = Logic.HIGH,
        logic_level: float | None = None,
        alias: str | None = None,
    ):
        """Configure a digital input line channel.

        Args:
            physical_channel: Vendor-specific line id (e.g. ``"port0/line3"`` on NI, ``"FIO0"`` on LabJack).
            logic: Active-``HIGH`` or active-``LOW``.
            logic_level: Voltage threshold (volts); the driver default is used when ``None``.
            alias: Friendly name; defaults to ``physical_channel``.
        """
        self._require_open()
        logic = _coerce_enum(logic, Logic, "logic")
        alias = alias if alias else physical_channel
        # Channel validation
        self._reject_duplicate_channel(alias)
        self._verify_not_running(alias)
        self._driver.configure_di_line_channel(
            physical_channel=physical_channel,
            logic=logic,
            logic_level=logic_level,
            alias=alias,
        )
        logger.info("Configured digital input channel on DAQ '%s'", self.name)

    def configure_digital_output(
        self,
        physical_channel: str,
        *,
        logic: str | Logic = Logic.HIGH,
        logic_level: float | None = None,
        alias: str | None = None,
    ):
        """Configure a digital output line channel.

        Args:
            physical_channel: Vendor-specific line id (e.g. ``"port0/line3"`` on NI, ``"FIO0"`` on LabJack).
            logic: Active-``HIGH`` or active-``LOW``.
            logic_level: Voltage threshold (volts); the driver default is used when ``None``.
            alias: Friendly name; defaults to ``physical_channel``.
        """
        self._require_open()
        logic = _coerce_enum(logic, Logic, "logic")
        alias = alias if alias else physical_channel
        # Channel validation
        self._reject_duplicate_channel(alias)
        self._verify_not_running(alias)
        self._driver.configure_do_line_channel(
            physical_channel=physical_channel,
            logic=logic,
            logic_level=logic_level,
            alias=alias,
        )
        logger.info("Configured digital output channel on DAQ '%s'", self.name)

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
        self.configure_ai_hw_sample_rate(
            sample_rate=sample_rate,
            samples_per_channel=samples_per_channel,
        )

    def configure_ai_hw_sample_rate(
        self,
        sample_rate: float,
        samples_per_channel: int | None = None,
    ):
        """Configure the hardware sample clock for AI channels.

        Args:
            sample_rate: Sample rate (Hz). Applies to all AI channels.
            samples_per_channel: Samples per channel per ``read_analog()`` call;
                defaults to 10 % of ``sample_rate`` (e.g. 100 at 1 kHz).
        """
        self._require_open()
        if self.is_sw_timing_configured:
            raise TimingConfigException(
                f"DAQ '{self.name}' is already configured for software timing. "
                "Hardware and software timing are mutually exclusive; build a separate InstroDAQ instead."
            )
        if not samples_per_channel:
            samples_per_channel = max(1, int(sample_rate // 10))

        hw_timing_config = HWTimingConfig(
            sample_rate=sample_rate,
            sample_period=round(1e9 / sample_rate),
            samples_per_channel=samples_per_channel,
        )

        self._driver.configure_ai_hw_timing(hw_timing_config=hw_timing_config)
        self._background_config.interval = (
            0  # DAQ reads block so set this to zero because they implicitly time the loop
        )

        # Set buffer length to 10 seconds or the default Instrument length, whichever is greater
        self._channel_buffer_length = max(int(sample_rate * 10), self._channel_buffer_length)
        logger.info("Configured AI hardware timing on DAQ '%s'", self.name)

    def configure_ai_sw_sample_rate(
        self,
        sample_rate: float,
    ):
        """Configure the software-timed polling rate for AI channels.

        Args:
            sample_rate: Rate (Hz) at which the background daemon polls AI channels.
        """
        self._require_open()
        if self.is_hw_timing_configured:
            raise TimingConfigException(
                f"DAQ '{self.name}' is already configured for hardware timing. "
                "Hardware and software timing are mutually exclusive; build a separate InstroDAQ instead."
            )
        if sample_rate <= 0:
            raise ValueError(f"Software-timed sample rate must be greater than 0 Hz, got {sample_rate}.")

        # Software timing needs a background loop period.
        self._background_config.interval = 1 / sample_rate
        self._is_sw_timing_configured = True

        # Set buffer length to 10 seconds or the default Instrument length, whichever is greater
        self._channel_buffer_length = max(int(sample_rate * 10), self._channel_buffer_length)
        logger.info("Configured AI software timing on DAQ '%s' at %s Hz", self.name, sample_rate)

    def start(self, background: bool = True, **kwargs):
        """Start acquisition: hardware-timed, or the software-timed daemon when SW timing is configured.

        With no AI timing configured, ``background=True`` falls back to software timing at
        1 Hz.

        Args:
            background: When True (default), spin the daemon thread to continuously
                fetch the buffer. When False, begin hardware acquisition only and
                fetch the buffer yourself by calling ``read_analog()``. Software-timed
                acquisition requires True — the daemon is what does the timing — so False
                logs an error and starts nothing.
            **kwargs: ``channel_type`` (NI only) selects which DAQmx task to start.
        """
        self._require_open()
        if not self.is_hw_timing_configured and not self.is_sw_timing_configured:
            if not background:
                # Nothing would pace the reads, so start nothing
                logger.error(
                    "Calling start(background=False) without AI timing configured is unnecessary. "
                    "Call read_analog() directly instead."
                )
                return

            # If no timing configured and start called, resort to sw timed daemon at default rate
            self.configure_ai_sw_sample_rate(sample_rate=self.DEFAULT_SW_SAMPLE_RATE)

        if self.is_sw_timing_configured:
            if not background:
                # The background daemon is the software clock, so start nothing
                logger.error(
                    "Calling start(background=False) with SW AI timing configured is a no-op because the "
                    "background daemon paces software reads. Call start(background=True) to start continuous "
                    "software-timed acquisition, or read_analog() directly instead."
                )
                return

            self._define_background_daemon()
            super().start()
            self._running = True
            return

        # DAQmx allows starting different channel_types independently.
        channel_type = kwargs.get("channel_type", None)

        # TODO
        # Need to evaluate spinning up a different daemon per channel type, but this
        # gets weird with different devices. DAQmx's channel types are their own things
        # whereas labjack is all one timing engine. Tricky architecture.
        # Baselining ai sample rate as the rate right now, which will break as soon as
        # we add other channel type capabilities that are hardware timed.

        self._driver.start(channel_type=channel_type)
        self._running = True

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
        # Software-timed acquisition never started the device, so there is nothing to stop.
        if self.is_sw_timing_configured:
            self._running = False
            return
        channel_type = kwargs.pop("channel_type", None)
        self._driver.stop(channel_type=channel_type, **kwargs)
        self._running = False

    def read_analog(
        self,
        **kwargs,
    ) -> Measurement | list[Measurement]:
        """Dispatch a hardware-timed buffer fetch or a software-timed conversion based on configuration.

        Each branch publishes its own Measurements; this dispatcher does not.
        Either timing mode with the background daemon running raises — the daemon owns the reads.
        Returns a single Measurement when channels share a timebase, otherwise one Measurement per timebase cluster.
        """
        self._require_open()
        if self._background_thread and self._background_thread.is_alive():
            # Background daemon running. The user can't pull from the buffer mid-flight.
            # TODO revisit with INSTRO-149 issue ticket.
            raise RuntimeError("Cannot read analog data while background acquisition daemon is running")

        if self.is_hw_timing_configured:
            measurements = self._fetch_analog_hw_timed(**kwargs)

        else:
            measurements = self._software_timed_read(**kwargs)

        return measurements[0] if len(measurements) == 1 else measurements

    @publish_measurement
    def _software_timed_read(self, **kwargs) -> list[Measurement]:
        """Initiate a software-timed analog conversion and return the resulting Measurements."""
        response = self._driver.read_analog()
        measurements = self._driver._read_to_measurements(
            response=response,
            channel_list=self.ai_channels,
            daq_name=self.name,
            default_tags=self.default_tags,
            **kwargs,
        )

        return self._scale_analog_measurement(measurements)

    @publish_measurement
    def _fetch_analog_hw_timed(self, **kwargs) -> list[Measurement]:
        """Fetch buffered samples as a list; also publish buffer depth on ``{name}.buffer``."""
        if not self.is_hw_timing_configured:
            raise RuntimeError(
                "Cannot fetch analog data without hardware timing configured. "
                "Call configure_ai_hw_sample_rate() before starting a hardware-timed acquisition."
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

        return measurements

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

    def read(self, channel: str, **kwargs) -> Measurement:
        """Read one AI/DI channel by alias; returns its Measurement."""
        if channel is None:
            raise ValueError("read() requires a channel alias; use read_batch() to read all configured inputs.")
        return self.read_batch([channel], **kwargs)[channel]

    def read_batch(
        self,
        channels: list[str] | None = None,
        **kwargs,
    ) -> dict[str, Measurement]:
        """Read AI/DI channels by alias (``None`` = all inputs); returns ``{alias: Measurement}``, analog via ``read_analog`` (or the daemon buffer while it runs), digital per line/port."""
        self._require_open()
        ai, di = self.ai_channels, self.di_channels
        daemon_running = bool(self._background_thread and self._background_thread.is_alive())

        if channels is None:
            aliases = [*ai, *di]
        elif unknown := [a for a in channels if a not in ai and a not in di]:
            raise KeyError(f"Input channel(s) {unknown} not configured. Configured input channels: {[*ai, *di]}.")
        else:
            aliases = list(channels)

        analog_aliases = [alias for alias in aliases if alias in ai]
        digital_aliases = [alias for alias in aliases if alias in di]

        # Analog pass
        analog: dict[str, Measurement] = {}
        if analog_aliases and not daemon_running:
            batch = self.read_analog(**kwargs)
            by_key = {key: m for m in (batch if isinstance(batch, list) else [batch]) for key in m.channel_data}
            for alias in analog_aliases:
                key = f"{self.name}.{alias}"
                source = by_key.get(key)
                if source is None:
                    raise KeyError(
                        f"read_analog() returned no data for analog channel '{alias}' (key '{key}'). "
                        f"Channels returned: {sorted(by_key)}."
                    )
                analog[alias] = Measurement({key: source.channel_data[key]}, source.timestamps, source.tags)
        elif analog_aliases:
            # Just use the get_channel defaults here. Directly call get_channel for more customization
            analog = {alias: self.get_channel(alias) for alias in analog_aliases}

        # Digital pass
        # NOTE: Planning on ripping out port support
        # TODO: Handle digital read when background daemon is running (when continuos di feature lands)
        digital = {
            alias: self.read_digital_port(alias, **kwargs)
            if isinstance(di[alias], DigitalPortChannel)
            else self.read_digital_line(alias, **kwargs)
            for alias in digital_aliases
        }

        measurements = analog | digital
        return {alias: measurements[alias] for alias in aliases}

    def write(self, channel: str, value: float | int | bool, **kwargs) -> Command:
        """Write ``value`` to one AO/DO channel by alias; returns its Command."""
        if channel is None:
            raise ValueError("write() requires a channel alias; use write_batch() to write several channels.")
        return self.write_batch([channel], [value], **kwargs)[0]

    def write_batch(
        self,
        channels: list[str],
        values: list[float | int | bool],
        continue_on_failed_write: bool = False,
        **kwargs,
    ) -> list[Command]:
        """Write ``values[i]`` to output ``channels[i]`` (alias); ``continue_on_failed_write`` logs and skips failed writes instead of raising."""
        if not channels:
            raise ValueError("write_batch() requires at least one channel alias.")
        self._require_open()
        ao, do = self.ao_channels, self.do_channels
        channel_list = list(channels)
        value_list = list(values)
        if len(channel_list) != len(value_list):
            raise ValueError(
                f"write_batch() got {len(channel_list)} channels but {len(value_list)} values; lengths must match."
            )
        # Validate up front so a bad alias or value can't leave earlier channels already written to hardware.
        self._validate_inputs_for_channels(channel_list, value_list, ao, do)

        commands: list[Command] = []
        for channel, value in zip(channel_list, value_list):
            try:
                if channel in ao:
                    command = self.write_analog_value(channel, value, **kwargs)
                # NOTE: Planning on ripping out port support
                elif isinstance(do[channel], DigitalPortChannel):
                    command = self.write_digital_port(channel, int(value), **kwargs)
                else:
                    command = self.write_digital_line(channel, int(value), **kwargs)
            # Non-failed write errors should always raise (relating to interpreter health)
            except (MemoryError, RecursionError):
                raise
            # Catch errors relating to a failed write
            except Exception as e:
                logger.warning("%s -> failed: %s", channel, e)
                if not continue_on_failed_write:
                    raise RuntimeError(f"write_batch() failed writing to channel '{channel}': {e}") from e
                continue
            logger.debug("%s -> succeeded", channel)
            commands.append(command)

        return commands

    def _validate_inputs_for_channels(
        self,
        channels: list[str],
        values: list[float | int | bool],
        ao: Mapping[str, AnalogChannelUnion],
        do: Mapping[str, DigitalChannel],
    ) -> None:
        """Validate every value against its target channel type before anything is written to hardware."""
        # Every alias must be registered on the driver as an AO or DO channel.
        if unknown := [alias for alias in channels if alias not in ao and alias not in do]:
            raise KeyError(f"Output channel(s) {unknown} not configured. Configured output channels: {[*ao, *do]}.")
        # Reject duplicate aliases: writing the same channel twice with last-wins is ambiguous intent.
        if len(set(channels)) != len(channels):
            duplicates = sorted({alias for alias in channels if channels.count(alias) > 1})
            raise ValueError(f"Duplicate output channel(s) {duplicates}; each channel may appear only once.")
        for alias, value in zip(channels, values):
            if alias in ao:
                # Analog outputs require a real, finite number; bool is excluded despite being an int subclass.
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError(f"Analog output '{alias}' requires a finite number, got {value!r}.")
                # Vendor SDKs silently clip or error on out-of-range values, so enforce the configured range here.
                if not ao[alias].range_min <= value <= ao[alias].range_max:
                    raise ValueError(
                        f"Analog output '{alias}' value {value!r} is outside the configured range "
                        f"[{ao[alias].range_min}, {ao[alias].range_max}]."
                    )
            elif isinstance(do[alias], DigitalPortChannel):
                # Digital ports take a raw integer; bool is a subclass of int, so it must be excluded explicitly.
                if isinstance(value, bool) or not isinstance(value, int):
                    raise ValueError(f"Digital port '{alias}' requires an integer value, got {value!r}.")
            # Digital lines accept only 0 or 1, in bool, int, or float form.
            elif not isinstance(value, (bool, int, float)) or value not in (0, 1):
                raise ValueError(f"Digital line '{alias}' requires 0 or 1 (as float, int, or bool), got {value!r}.")

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
        """Register the fetch matching the configured timing mode when AI channels exist."""
        fetch = self._software_timed_read if self.is_sw_timing_configured else self._fetch_analog_hw_timed
        already_registered = any(method == fetch for method, _, _ in self._background_methods)
        if self.ai_channels and not already_registered:
            self.add_background_daemon_function(fetch)

    def get_actual_sample_rate(self) -> float | None:
        """Hardware's actual sample rate after ``start()``; ``None`` if unsupported or not started."""
        return self._driver.get_actual_sample_rate()

    @publish_measurement
    def get_points_in_buffer(self, **kwargs) -> Measurement:
        """Publish the current DAQ buffer depth on channel ``{name}.buffer``."""
        self._require_open()
        return self._package_measurement("buffer", self._driver.points_in_buffer, time.time_ns(), **kwargs)


class HWTimingException(InstroError): ...


class TimingConfigException(InstroError): ...
