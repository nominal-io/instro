"""AWG instrument driver contract and Instro AWG interface."""

from __future__ import annotations

import abc
import logging
import threading
import time
from dataclasses import fields
from typing import Callable

from instro.lib.instrument import Instrument, publish_command, publish_measurement
from instro.lib.publishers import Publisher
from instro.lib.types import Command, Measurement
from instro.unstable.awg.types import (
    AmplitudeMeasurementUnit,
    Arbitrary,
    ModulationType,
    Pulse,
    Sawtooth,
    Sine,
    Square,
    StaticValue,
    Triangle,
    Waveform,
    convert_amplitude,
)

logger = logging.getLogger(__name__)


class AWGDriverBase(abc.ABC):
    """Vendor AWG driver contract. Concrete drivers own their transport and lifecycle."""

    @abc.abstractmethod
    def open(self) -> None:
        """Open the underlying transport."""

    @abc.abstractmethod
    def close(self) -> None:
        """Close the underlying transport."""

    @abc.abstractmethod
    def check_errors(self) -> None:
        """Drain the instrument error queue; raise if any error is pending."""

    @abc.abstractmethod
    def set_waveform(self, channel: int, waveform: Waveform) -> None:
        """Program channel with the waveform definition; raise ValueError if the definition is unsupported."""

    @abc.abstractmethod
    def get_waveform(self, channel: int) -> Waveform:
        """Get the current waveform on channel; drivers may return the last-programmed definition if not readable."""

    @abc.abstractmethod
    def set_amplitude(self, channel: int, amplitude: float, unit: AmplitudeMeasurementUnit) -> None:
        """Set the output amplitude on channel."""

    @abc.abstractmethod
    def get_amplitude(self, channel: int) -> tuple[float, AmplitudeMeasurementUnit]:
        """Get the current output amplitude and voltage unit on channel."""

    @abc.abstractmethod
    def set_offset(self, channel: int, offset: float) -> None:
        """Set the DC offset (volts) on channel."""

    @abc.abstractmethod
    def get_offset(self, channel: int) -> float:
        """Get the DC offset (volts) on channel."""

    @abc.abstractmethod
    def output_enable(self, channel: int, enable: bool) -> None:
        """Enable or disable the output on channel."""

    @abc.abstractmethod
    def get_output_state(self, channel: int) -> bool:
        """Return True if the output on channel is enabled."""

    def set_output_load(self, channel: int, load: float | None) -> None:
        """Set the output load impedance; None means high-Z."""
        raise NotImplementedError(f"set_output_load is not implemented for {type(self).__name__}")

    def get_output_load(self, channel: int) -> float | None:
        """Get the output load impedance; None means high-Z."""
        raise NotImplementedError(f"get_output_load is not implemented for {type(self).__name__}")

    def align_phase(self) -> None:
        """Sync the phase of all channels."""
        raise NotImplementedError(f"align_phase is not implemented for {type(self).__name__}")

    def modulate(self, channel: int, mod_type: ModulationType, shape: Waveform, magnitude: float) -> None:
        """Modulate channel's carrier with modulator shape.

        magnitude by mod_type: AM=depth, FM=frequency deviation, PM=phase deviation, ASK=2nd
        amplitude (shape ignored), FSK=hop frequency (shape ignored).
        """
        raise NotImplementedError(f"modulate is not implemented for {type(self).__name__}")

    def disable_modulation(self, channel: int) -> None:
        """Disable modulation on the given channel, if enabled, and reset modulation state."""
        raise NotImplementedError(f"disable_modulation is not implemented for {type(self).__name__}")


_PUBLISHED_NAMES: dict[type, str] = {
    Sine: "SINE",
    Square: "SQUARE",
    Sawtooth: "SAWTOOTH",
    Triangle: "TRIANGLE",
    Pulse: "PULSE",
    Arbitrary: "ARBITRARY",
    StaticValue: "STATICVALUE",
}


def _waveform_tags(waveform: Waveform) -> dict[str, str]:
    """Shape parameters as publish tags; Arbitrary samples are summarized, never published."""
    if isinstance(waveform, Arbitrary):
        return {
            "num_samples": str(len(waveform.samples)),
            "sample_rate_hz": str(waveform.sample_rate_hz),
        }
    return {f.name: str(getattr(waveform, f.name)) for f in fields(waveform)}


def _waveform_param_channels(waveform: Waveform) -> dict[str, float]:
    """Numeric shape parameters, one publishable channel each; Arbitrary samples are summarized."""
    if isinstance(waveform, Arbitrary):
        return {"sample_rate_hz": waveform.sample_rate_hz, "num_samples": float(len(waveform.samples))}
    return {f.name: float(getattr(waveform, f.name)) for f in fields(waveform)}


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
        """Initialize an InstroAWG."""
        if num_channels < 1:
            raise ValueError(f"num_channels must be at least 1, got {num_channels}")
        super().__init__(name, publishers=publishers, **kwargs)
        self._driver = driver
        self._num_channels = num_channels
        self._resource_lock = threading.Lock()
        self._channel_waveforms: dict[int, Waveform] = {}

        self._define_background_daemon()

    def _define_background_daemon(self) -> None:
        """Register per-channel output-state polling; other readbacks are opt-in via add_background_daemon_function."""
        for channel in range(1, self._num_channels + 1):
            self.add_background_daemon_function(self.get_output_state, channel=channel)

    def _check_channel(self, channel: int) -> None:
        if not 1 <= channel <= self._num_channels:
            raise ValueError(f"channel {channel} out of range for '{self.name}' (1-{self._num_channels})")

    def _check_errors(self) -> None:
        """Raise if the driver's error queue holds anything. Caller must hold ``_resource_lock``."""
        self._driver.check_errors()

    @publish_command
    def _execute_command(
        self,
        driver_method: Callable,
        channel: int,
        value: float | bool | str,
        channel_suffix: str,
        /,
        **kwargs,
    ) -> Command:
        """Run ``driver_method(channel, value)``, check errors, and package."""
        with self._resource_lock:
            driver_method(channel, value)
            timestamp = time.time_ns()
            self._check_errors()
        descriptor = f"ch{channel}.{channel_suffix}.cmd"
        return self._package_command(descriptor, value, timestamp, **kwargs)

    @publish_measurement
    def _execute_measurement(
        self,
        driver_method: Callable,
        channel: int,
        channel_suffix: str,
        /,
        **kwargs,
    ) -> Measurement | None:
        """Readback helper: call ``driver_method(channel=channel)`` and package; positional-only params avoid tag collisions."""
        with self._resource_lock:
            val = driver_method(channel=channel)
            timestamp = time.time_ns()
            self._check_errors()
        descriptor = f"ch{channel}.{channel_suffix}"
        return self._package_measurement(descriptor, val, timestamp, **kwargs)

    def start(self) -> None:
        """Start the background daemon; raises unless ``set_waveform`` was called for at least one channel."""
        with self._resource_lock:
            configured = bool(self._channel_waveforms)
        if not configured:
            raise ValueError(
                "set_waveform must be called for at least one channel before starting background collection"
            )

        super().start()

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
    def set_waveform(self, channel: int, waveform: Waveform, **kwargs) -> Command:
        """Program channel with the waveform definition; numeric shape parameters publish as companion channels."""
        if type(waveform) not in _PUBLISHED_NAMES:
            raise TypeError(f"waveform must be a Waveform definition, got {type(waveform).__name__}")
        self._check_channel(channel)
        with self._resource_lock:
            self._driver.set_waveform(channel=channel, waveform=waveform)
            timestamp = time.time_ns()
            self._check_errors()
            self._channel_waveforms[channel] = waveform
        published_name = _PUBLISHED_NAMES[type(waveform)]
        params = _waveform_param_channels(waveform)
        if params:
            companion_tags = {**self.default_tags, "waveform": published_name, **kwargs}
            self.publish(
                Command(
                    channel_data={f"{self.name}.ch{channel}.{param}.cmd": value for param, value in params.items()},
                    timestamp=timestamp,
                    tags=companion_tags,
                )
            )
        descriptor = f"ch{channel}.waveform.cmd"
        tags = {**_waveform_tags(waveform), **kwargs}
        return self._package_command(descriptor, published_name, timestamp, **tags)

    def get_waveform(self, channel: int) -> Waveform:
        """Read back the current waveform definition on channel."""
        self._check_channel(channel)
        with self._resource_lock:
            waveform = self._driver.get_waveform(channel=channel)
            self._check_errors()
        return waveform

    @publish_command
    def set_amplitude(self, channel: int, amplitude: float, unit: AmplitudeMeasurementUnit, **kwargs) -> Command:
        """Set the output amplitude on channel; the unit ships as a ``unit`` tag."""
        if not isinstance(unit, AmplitudeMeasurementUnit):
            raise TypeError(f"unit must be an AmplitudeMeasurementUnit, got {type(unit).__name__}")
        self._check_channel(channel)
        with self._resource_lock:
            self._driver.set_amplitude(channel=channel, amplitude=amplitude, unit=unit)
            timestamp = time.time_ns()
            self._check_errors()
        descriptor = f"ch{channel}.amplitude.cmd"
        return self._package_command(descriptor, amplitude, timestamp, unit=unit.value, **kwargs)

    def get_amplitude(self, channel: int) -> tuple[float, AmplitudeMeasurementUnit]:
        """Read back the current amplitude and its measurement unit on channel."""
        self._check_channel(channel)
        with self._resource_lock:
            amplitude = self._driver.get_amplitude(channel=channel)
            self._check_errors()
        return amplitude

    def convert_amplitude(
        self,
        channel: int,
        amplitude: float,
        from_unit: AmplitudeMeasurementUnit,
        to_unit: AmplitudeMeasurementUnit,
        impedance_ohms: float | None = None,
    ) -> float:
        """Convert an amplitude value between units using channel's configured waveform.

        DBM conversions need a load impedance; if ``impedance_ohms`` isn't given, channel's
        output load is used instead. Raises ValueError if neither is available.
        """
        if not isinstance(from_unit, AmplitudeMeasurementUnit):
            raise TypeError(f"from_unit must be an AmplitudeMeasurementUnit, got {type(from_unit).__name__}")
        if not isinstance(to_unit, AmplitudeMeasurementUnit):
            raise TypeError(f"to_unit must be an AmplitudeMeasurementUnit, got {type(to_unit).__name__}")
        self._check_channel(channel)
        with self._resource_lock:
            waveform = self._channel_waveforms.get(channel)
            if waveform is None:
                raise ValueError(f"channel {channel} has no waveform configured; call set_waveform first")
            if impedance_ohms is None and AmplitudeMeasurementUnit.DBM in (from_unit, to_unit):
                try:
                    impedance_ohms = self._driver.get_output_load(channel=channel)
                except NotImplementedError:
                    impedance_ohms = None
                else:
                    self._check_errors()
                if impedance_ohms is None:
                    raise ValueError(f"channel {channel} has no known output load; pass impedance_ohms explicitly")
        return convert_amplitude(amplitude, from_unit, to_unit, waveform, impedance_ohms=impedance_ohms)

    def set_offset(self, channel: int, offset_v: float, **kwargs) -> Command:
        """Set the DC offset (volts) on channel."""
        self._check_channel(channel)
        return self._execute_command(self._driver.set_offset, channel, offset_v, "offset", **kwargs)

    def output_enable(self, channel: int, enable: bool, **kwargs) -> Command:
        """Enable or disable the output on channel."""
        self._check_channel(channel)
        return self._execute_command(self._driver.output_enable, channel, enable, "enabled", **kwargs)

    @publish_command
    def set_output_load(self, channel: int, load: float | None, **kwargs) -> Command:
        """Set the output load impedance; None means high-Z."""
        self._check_channel(channel)
        with self._resource_lock:
            self._driver.set_output_load(channel=channel, load=load)
            timestamp = time.time_ns()
            self._check_errors()
        descriptor = f"ch{channel}.load.cmd"
        load_value = float("inf") if load is None else load
        return self._package_command(descriptor, load_value, timestamp, **kwargs)

    @publish_command
    def align_phase(self, **kwargs) -> Command:
        """Sync the phase of all channels."""
        with self._resource_lock:
            self._driver.align_phase()
            timestamp = time.time_ns()
            self._check_errors()
        descriptor = "phase.align.cmd"
        return self._package_command(descriptor, "ALIGN", timestamp, **kwargs)

    def get_offset(self, channel: int, **kwargs) -> Measurement | None:
        """Read back the DC offset (volts) on channel."""
        self._check_channel(channel)
        return self._execute_measurement(self._driver.get_offset, channel, "offset", **kwargs)

    def get_output_state(self, channel: int, **kwargs) -> Measurement | None:
        """Read back whether the output is enabled on channel."""
        self._check_channel(channel)
        return self._execute_measurement(self._driver.get_output_state, channel, "enabled", **kwargs)

    @publish_measurement
    def get_output_load(self, channel: int, **kwargs) -> Measurement | None:
        """Read back the output load impedance on channel; high-Z is published as ``float('inf')``."""
        self._check_channel(channel)
        with self._resource_lock:
            val = self._driver.get_output_load(channel=channel)
            timestamp = time.time_ns()
            self._check_errors()
        load_float = float("inf") if val is None else val
        descriptor = f"ch{channel}.load"
        return self._package_measurement(descriptor, load_float, timestamp, **kwargs)

    @publish_command
    def modulate(self, channel: int, mod_type: ModulationType, shape: Waveform, magnitude: float, **kwargs) -> Command:
        """Modulate channel's carrier with modulator shape.

        magnitude by mod_type: AM=depth, FM=frequency deviation, PM=phase deviation, ASK=2nd
        amplitude (shape ignored), FSK=hop frequency (shape ignored).
        """
        if not isinstance(mod_type, ModulationType):
            raise TypeError(f"mod_type must be a ModulationType, got {type(mod_type).__name__}")
        self._check_channel(channel)
        with self._resource_lock:
            self._driver.modulate(channel=channel, mod_type=mod_type, shape=shape, magnitude=magnitude)
            timestamp = time.time_ns()
            self._check_errors()
        descriptor = f"ch{channel}.modulation.cmd"
        return self._package_command(descriptor, magnitude, timestamp, mod_type=mod_type.value, **kwargs)

    @publish_command
    def disable_modulation(self, channel: int, **kwargs) -> Command:
        """Disable modulation on the given channel, if enabled, and reset modulation state."""
        self._check_channel(channel)
        with self._resource_lock:
            self._driver.disable_modulation(channel=channel)
            timestamp = time.time_ns()
            self._check_errors()
        descriptor = f"ch{channel}.modulation.cmd"
        return self._package_command(descriptor, "OFF", timestamp, **kwargs)
