"""AWG instrument driver contract and Instro AWG interface."""

from __future__ import annotations

import abc
import logging
import threading
import time

from instro.awg.types import Channel, VoltageUnit, WaveformType
from instro.lib.instrument import Instrument, publish_command, publish_measurement
from instro.lib.publishers import Publisher
from instro.lib.types import Command, Measurement

logger = logging.getLogger(__name__)


class AWGDriverBase(abc.ABC):
    """Vendor AWG driver contract. Concrete drivers own their transport and lifecycle.

    All methods here apply to standard periodic (LTI) waveforms. Non-LTI signal
    support (modulation, sweep, burst, arb upload) is added in later milestones as
    optional method groups that raise NotImplementedError by default.
    """

    @abc.abstractmethod
    def open(self) -> None:
        """Open the underlying transport."""

    @abc.abstractmethod
    def close(self) -> None:
        """Close the underlying transport. Idempotent."""

    @abc.abstractmethod
    def check_errors(self) -> None:
        """Query the instrument error queue and raise on non-zero error code."""

    # --- Standard periodic waveforms ---

    @abc.abstractmethod
    def set_waveform(self, channel: Channel, waveform: WaveformType) -> None:
        """Set the waveform function on channel."""

    @abc.abstractmethod
    def get_waveform(self, channel: Channel) -> WaveformType:
        """Get the current waveform function on channel."""

    @abc.abstractmethod
    def set_frequency(self, channel: Channel, frequency: float) -> None:
        """Set the output frequency (Hz) on channel."""

    @abc.abstractmethod
    def get_frequency(self, channel: Channel) -> float:
        """Get the output frequency (Hz) on channel."""

    @abc.abstractmethod
    def set_amplitude(self, channel: Channel, amplitude: float, unit: VoltageUnit) -> None:
        """Set the output amplitude on channel."""

    @abc.abstractmethod
    def set_offset(self, channel: Channel, offset: float) -> None:
        """Set the DC offset (volts) on channel."""

    @abc.abstractmethod
    def get_offset(self, channel: Channel) -> float:
        """Get the DC offset (volts) on channel."""

    @abc.abstractmethod
    def output_enable(self, channel: Channel, enable: bool) -> None:
        """Enable or disable the output on channel."""

    @abc.abstractmethod
    def get_output_state(self, channel: Channel) -> bool:
        """Return True if the output on channel is enabled."""

    @abc.abstractmethod
    def set_output_load(self, channel: Channel, load: float | None) -> None:
        """Set the output load impedance; None means high-Z."""

    @abc.abstractmethod
    def get_output_load(self, channel: Channel) -> float | None:
        """Get the output load impedance; None means high-Z."""

    # --- Non-LTI / composite waveforms (add method groups here in later milestones) ---


class InstroAWG(Instrument):
    """AWG instrument. Methods return Measurement/Command for publishing."""

    def __init__(
        self,
        name: str,
        driver: AWGDriverBase,
        num_channels: int,
        publishers: list[Publisher] | None = None,
        **kwargs,
    ):
        """Initialize an InstroAWG.

        Args:
            name: Channel-name prefix for published data.
            driver: Concrete AWG driver; owns its own transport.
            num_channels: Number of output channels on this instrument.
            publishers: Publishers that receive emitted Measurement/Command data.
            **kwargs: Default tags applied to every emitted Measurement/Command.
        """
        super().__init__(name, publishers=publishers, **kwargs)
        self._driver = driver
        self._num_channels = num_channels
        self._resource_lock = threading.Lock()

    @property
    def driver(self) -> AWGDriverBase:
        """The underlying vendor driver."""
        return self._driver

    def open(self) -> None:
        """Open the underlying driver."""
        logger.info("Opening AWG '%s'", self.name)
        self._driver.open()
        logger.info("Opened AWG '%s'", self.name)

    def close(self) -> None:
        """Close the underlying driver."""
        logger.info("Closing AWG '%s'", self.name)
        super().close()
        self._driver.close()
        logger.info("Closed AWG '%s'", self.name)

    @publish_command
    def set_waveform(self, channel: Channel, waveform: WaveformType, **kwargs) -> Command:
        """Set the waveform type on channel."""
        with self._resource_lock:
            self._driver.set_waveform(channel=channel, waveform=waveform)
            timestamp = time.time_ns()
        descriptor = f"ch{channel.value}.waveform.cmd"
        return self._package_command(descriptor, waveform.value, timestamp, **kwargs)

    @publish_command
    def set_frequency(self, channel: Channel, frequency_hz: float, **kwargs) -> Command:
        """Set the output frequency (Hz) on channel."""
        with self._resource_lock:
            self._driver.set_frequency(channel=channel, frequency=frequency_hz)
            timestamp = time.time_ns()
        descriptor = f"ch{channel.value}.frequency.cmd"
        return self._package_command(descriptor, frequency_hz, timestamp, **kwargs)

    @publish_command
    def set_amplitude(self, channel: Channel, amplitude: float, unit: VoltageUnit, **kwargs) -> Command:
        """Set the output amplitude on channel."""
        with self._resource_lock:
            self._driver.set_amplitude(channel=channel, amplitude=amplitude, unit=unit)
            timestamp = time.time_ns()
        descriptor = f"ch{channel.value}.amplitude.cmd"
        return self._package_command(descriptor, amplitude, timestamp, **kwargs)

    @publish_command
    def set_offset(self, channel: Channel, offset_v: float, **kwargs) -> Command:
        """Set the DC offset (volts) on channel."""
        with self._resource_lock:
            self._driver.set_offset(channel=channel, offset=offset_v)
            timestamp = time.time_ns()
        descriptor = f"ch{channel.value}.offset.cmd"
        return self._package_command(descriptor, offset_v, timestamp, **kwargs)

    @publish_command
    def output_enable(self, channel: Channel, enable: bool, **kwargs) -> Command:
        """Enable or disable the output on channel."""
        with self._resource_lock:
            self._driver.output_enable(channel=channel, enable=enable)
            timestamp = time.time_ns()
        descriptor = f"ch{channel.value}.enabled.cmd"
        return self._package_command(descriptor, enable, timestamp, **kwargs)

    @publish_command
    def set_output_load(self, channel: Channel, load: float | None, **kwargs) -> Command:
        """Set the output load impedance; None means high-Z."""
        with self._resource_lock:
            self._driver.set_output_load(channel=channel, load=load)
            timestamp = time.time_ns()
        descriptor = f"ch{channel.value}.load.cmd"
        # _package_command requires float|str; represent high-Z as "INF"
        load_value = "INF" if load is None else load
        return self._package_command(descriptor, load_value, timestamp, **kwargs)

    def get_waveform(self, channel: Channel) -> WaveformType:
        """Read back the current waveform type on channel.

        Not published as a Measurement — WaveformType is not numeric.
        """
        with self._resource_lock:
            return self._driver.get_waveform(channel=channel)

    @publish_measurement
    def get_frequency(self, channel: Channel, **kwargs) -> Measurement | None:
        """Read back the current output frequency (Hz) on channel."""
        with self._resource_lock:
            val = self._driver.get_frequency(channel=channel)
            timestamp = time.time_ns()
        descriptor = f"ch{channel.value}.frequency"
        return self._package_measurement(descriptor, val, timestamp, **kwargs)

    @publish_measurement
    def get_output_state(self, channel: Channel, **kwargs) -> Measurement | None:
        """Read back whether the output is enabled on channel."""
        with self._resource_lock:
            val = self._driver.get_output_state(channel=channel)
            timestamp = time.time_ns()
        descriptor = f"ch{channel.value}.enabled"
        return self._package_measurement(descriptor, val, timestamp, **kwargs)

    def configure_channel(
        self,
        channel: Channel,
        waveform: WaveformType,
        frequency_hz: float,
        amplitude: float,
        unit: VoltageUnit,
        offset_v: float = 0.0,
        **kwargs,
    ) -> list[Command]:
        """Configure all standard waveform parameters on channel in one call."""
        return [
            self.set_waveform(channel, waveform, **kwargs),
            self.set_frequency(channel, frequency_hz, **kwargs),
            self.set_amplitude(channel, amplitude, unit, **kwargs),
            self.set_offset(channel, offset_v, **kwargs),
        ]
