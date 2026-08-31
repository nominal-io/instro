"""AWG instrument driver contract and Instro AWG interface."""

from __future__ import annotations

import abc
import logging
import threading
import time
from dataclasses import fields
from enum import Enum
from pathlib import Path
from typing import Callable

from instro.lib.config import load_config
from instro.lib.instrument import Instrument, publish_command, publish_measurement
from instro.lib.publishers import Publisher
from instro.lib.types import Command, Measurement
from instro.unstable.awg.config import AWGConfig, build_waveform, resolve_awg_from_config
from instro.unstable.awg.types import (
    AmplitudeMeasurementUnit,
    Arbitrary,
    BurstTriggerSource,
    BurstType,
    GatePolarity,
    ModulationType,
    Pulse,
    Sawtooth,
    Sine,
    Square,
    StaticValue,
    SweepTriggerSource,
    SweepType,
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
    def set_waveform(self, channel: int, waveform: Waveform) -> None:
        """Program channel with the waveform definition."""

    @abc.abstractmethod
    def get_waveform(self, channel: int) -> Waveform:
        """Get the current waveform on channel."""

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

    def set_modulation(self, channel: int, mod_type: ModulationType, shape: Waveform, magnitude: float) -> None:
        """Configure channel's carrier modulation with modulator shape."""
        raise NotImplementedError(f"set_modulation is not implemented for {type(self).__name__}")

    def modulation_enable(self, channel: int, enable: bool) -> None:
        """Enable or disable modulation on the given channel."""
        raise NotImplementedError(f"modulation_enable is not implemented for {type(self).__name__}")

    def get_modulation_type(self, channel: int) -> ModulationType:
        """Get the modulation type currently active on channel."""
        raise NotImplementedError(f"get_modulation_type is not implemented for {type(self).__name__}")

    def get_modulation_state(self, channel: int) -> bool:
        """Get the modulation enabled state currently active on channel."""
        raise NotImplementedError(f"get_modulation_state is not implemented for {type(self).__name__}")

    def set_burst(self, channel: int, burst_type: BurstType) -> None:
        """Configure channel's burst type."""
        raise NotImplementedError(f"set_burst is not implemented for {type(self).__name__}")

    def burst_enable(self, channel: int, enable: bool) -> None:
        """Enable or disable burst mode on the given channel."""
        raise NotImplementedError(f"burst_enable is not implemented for {type(self).__name__}")

    def get_burst_type(self, channel: int) -> BurstType:
        """Get the burst type currently active on channel."""
        raise NotImplementedError(f"get_burst_type is not implemented for {type(self).__name__}")

    def get_burst_state(self, channel: int) -> bool:
        """Return True if burst mode is enabled on channel."""
        raise NotImplementedError(f"get_burst_state is not implemented for {type(self).__name__}")

    def set_burst_trigger(self, channel: int, source: BurstTriggerSource) -> None:
        """Set the burst trigger source on channel."""
        raise NotImplementedError(f"set_burst_trigger is not implemented for {type(self).__name__}")

    def get_burst_trigger(self, channel: int) -> BurstTriggerSource:
        """Get the burst trigger source on channel."""
        raise NotImplementedError(f"get_burst_trigger is not implemented for {type(self).__name__}")

    def fire_burst_trigger(self, channel: int) -> None:
        """Fire a burst trigger on channel now; the trigger source must already be MANUAL."""
        raise NotImplementedError(f"fire_burst_trigger is not implemented for {type(self).__name__}")

    def set_burst_delay(self, channel: int, delay_s: float) -> None:
        """Set the burst trigger delay (seconds) on channel."""
        raise NotImplementedError(f"set_burst_delay is not implemented for {type(self).__name__}")

    def get_burst_delay(self, channel: int) -> float:
        """Get the burst trigger delay (seconds) on channel."""
        raise NotImplementedError(f"get_burst_delay is not implemented for {type(self).__name__}")

    def set_burst_gate_polarity(self, channel: int, gate_polarity: GatePolarity) -> None:
        """Set the gate polarity for GATED bursts on channel."""
        raise NotImplementedError(f"set_burst_gate_polarity is not implemented for {type(self).__name__}")

    def get_burst_gate_polarity(self, channel: int) -> GatePolarity:
        """Get the gate polarity for GATED bursts on channel."""
        raise NotImplementedError(f"get_burst_gate_polarity is not implemented for {type(self).__name__}")

    def set_burst_ncycles(self, channel: int, n_cycles: int) -> None:
        """Set the number of cycles per trigger for NCYCLE bursts on channel."""
        raise NotImplementedError(f"set_burst_ncycles is not implemented for {type(self).__name__}")

    def get_burst_ncycles(self, channel: int) -> int:
        """Get the number of cycles per trigger for NCYCLE bursts on channel."""
        raise NotImplementedError(f"get_burst_ncycles is not implemented for {type(self).__name__}")

    def set_burst_period(self, channel: int, period: float) -> None:
        """Set the internal burst period (seconds) on channel."""
        raise NotImplementedError(f"set_burst_period is not implemented for {type(self).__name__}")

    def get_burst_period(self, channel: int) -> float:
        """Get the internal burst period (seconds) on channel."""
        raise NotImplementedError(f"get_burst_period is not implemented for {type(self).__name__}")

    def set_sweep(self, channel: int, sweep_type: SweepType) -> None:
        """Configure the sweep type on channel."""
        raise NotImplementedError(f"set_sweep is not implemented for {type(self).__name__}")

    def get_sweep_type(self, channel: int) -> SweepType:
        """Get the sweep type currently configured on channel."""
        raise NotImplementedError(f"get_sweep_type is not implemented for {type(self).__name__}")

    def sweep_enable(self, channel: int, enable: bool) -> None:
        """Enable or disable sweep mode on channel."""
        raise NotImplementedError(f"sweep_enable is not implemented for {type(self).__name__}")

    def get_sweep_state(self, channel: int) -> bool:
        """Return True if sweep mode is enabled on channel."""
        raise NotImplementedError(f"get_sweep_state is not implemented for {type(self).__name__}")

    def set_sweep_trigger(self, channel: int, source: SweepTriggerSource) -> None:
        """Set the sweep trigger source on channel."""
        raise NotImplementedError(f"set_sweep_trigger is not implemented for {type(self).__name__}")

    def get_sweep_trigger(self, channel: int) -> SweepTriggerSource:
        """Get the sweep trigger source on channel."""
        raise NotImplementedError(f"get_sweep_trigger is not implemented for {type(self).__name__}")

    def set_sweep_start_freq(self, channel: int, frequency_hz: float) -> None:
        """Set the sweep start frequency (Hz) on channel."""
        raise NotImplementedError(f"set_sweep_start_freq is not implemented for {type(self).__name__}")

    def get_sweep_start_freq(self, channel: int) -> float:
        """Get the sweep start frequency (Hz) on channel."""
        raise NotImplementedError(f"get_sweep_start_freq is not implemented for {type(self).__name__}")

    def set_sweep_end_freq(self, channel: int, frequency_hz: float) -> None:
        """Set the sweep end frequency (Hz) on channel."""
        raise NotImplementedError(f"set_sweep_end_freq is not implemented for {type(self).__name__}")

    def get_sweep_end_freq(self, channel: int) -> float:
        """Get the sweep end frequency (Hz) on channel."""
        raise NotImplementedError(f"get_sweep_end_freq is not implemented for {type(self).__name__}")

    def set_sweep_time(self, channel: int, sweep_time: float) -> None:
        """Set the sweep time (seconds) on channel."""
        raise NotImplementedError(f"set_sweep_time is not implemented for {type(self).__name__}")

    def get_sweep_time(self, channel: int) -> float:
        """Get the sweep time (seconds) on channel."""
        raise NotImplementedError(f"get_sweep_time is not implemented for {type(self).__name__}")

    def set_sweep_start_hold_time(self, channel: int, hold_time: float) -> None:
        """Set the sweep start hold time (seconds) on channel."""
        raise NotImplementedError(f"set_sweep_start_hold_time is not implemented for {type(self).__name__}")

    def set_sweep_stop_hold_time(self, channel: int, hold_time: float) -> None:
        """Set the sweep stop hold time (seconds) on channel."""
        raise NotImplementedError(f"set_sweep_stop_hold_time is not implemented for {type(self).__name__}")

    def get_sweep_start_hold_time(self, channel: int) -> float:
        """Get the sweep start hold time (seconds) on channel."""
        raise NotImplementedError(f"get_sweep_start_hold_time is not implemented for {type(self).__name__}")

    def get_sweep_stop_hold_time(self, channel: int) -> float:
        """Get the sweep stop hold time (seconds) on channel."""
        raise NotImplementedError(f"get_sweep_stop_hold_time is not implemented for {type(self).__name__}")

    def set_sweep_return_time(self, channel: int, return_time: float) -> None:
        """Set the sweep return time (seconds) on channel."""
        raise NotImplementedError(f"set_sweep_return_time is not implemented for {type(self).__name__}")

    def get_sweep_return_time(self, channel: int) -> float:
        """Get the sweep return time (seconds) on channel."""
        raise NotImplementedError(f"get_sweep_return_time is not implemented for {type(self).__name__}")

    def fire_sweep_trigger(self, channel: int) -> None:
        """Fire a sweep trigger on channel now; the trigger source must already be MANUAL."""
        raise NotImplementedError(f"fire_sweep_trigger is not implemented for {type(self).__name__}")


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
            "sample_rate_sas": str(waveform.sample_rate_sas),
        }
    return {f.name: str(getattr(waveform, f.name)) for f in fields(waveform)}


def _waveform_param_channels(waveform: Waveform) -> dict[str, float]:
    """Numeric shape parameters, one publishable channel each; Arbitrary samples are summarized."""
    if isinstance(waveform, Arbitrary):
        return {"sample_rate_sas": waveform.sample_rate_sas, "num_samples": float(len(waveform.samples))}
    return {f.name: float(getattr(waveform, f.name)) for f in fields(waveform)}


class InstroAWG(Instrument):
    """AWG instrument. Methods return Measurement/Command for publishing."""

    def __init__(
        self,
        name: str | None = None,
        driver: AWGDriverBase | None = None,
        num_channels: int | None = None,
        publishers: list[Publisher] | None = None,
        config: AWGConfig | dict | Path | str | None = None,
        autostart: bool = False,
        **kwargs,
    ):
        """Initialize an InstroAWG.

        Provide either ``config`` or ``driver``/``num_channels`` together, not both.

        Args:
            name: Channel-name prefix for published data. Falls back to
                ``config.device.name`` when ``config`` is given.
            driver: Concrete AWG driver; owns its own transport::

                awg = InstroAWG(
                    name="main",
                    driver=RigolDG1022Z("USB0::0x1AB1::0x0642::DG1ZA000000000::INSTR"),
                    num_channels=2,
                )

            num_channels: Number of output channels on this AWG.
            publishers: Publishers that receive emitted Measurement/Command data.
                Combined with any publishers declared in ``config``.
            config: An ``AWGConfig``, a dict, or a path to a JSON config file. Its
                ``channels`` block is applied through the public setters on ``open()``.
            autostart: When True, open the connection and start background polling.
                Requires ``config``, since polling cannot start without a configured channel.
            **kwargs: Default tags applied to every emitted Measurement/Command.
                Pass ``dataset_rid="<rid>"`` to auto-create a NominalCorePublisher
                (uses the on-disk 'default' Nominal credential).
        """
        poll_interval: float | None = None
        resolved_config: AWGConfig | None = None
        config_publishers: list[Publisher] = []
        if config is not None:
            if driver is not None or num_channels is not None:
                raise ValueError(
                    "InstroAWG(config=...) cannot be combined with driver/num_channels; "
                    "use one construction style or the other."
                )
            resolved_config = load_config(config, AWGConfig)
            resolved_name, driver, num_channels, config_publishers, poll_interval = resolve_awg_from_config(
                resolved_config
            )
            publishers = [*(publishers or []), *config_publishers] or None
            if name is None:
                name = resolved_name
        elif name is None or driver is None or num_channels is None:
            raise ValueError("InstroAWG requires either config=..., or name, driver, and num_channels together.")

        if autostart and resolved_config is None:
            raise ValueError(
                "autostart=True requires config=...; background polling cannot start without a configured channel."
            )

        if num_channels < 1:
            raise ValueError(f"num_channels must be at least 1, got {num_channels}")
        super().__init__(name, publishers=publishers, **kwargs)
        self._driver = driver
        self._num_channels = num_channels
        self._config = resolved_config
        self._channel_config_applied = False
        self._resource_lock = threading.Lock()
        self._channel_waveforms: dict[int, Waveform] = {}

        self._define_background_daemon()

        if poll_interval is not None:
            self.background_interval = poll_interval

        if autostart:
            try:
                self.open()
                self.start()
            except Exception:
                self._driver.close()
                for publisher in config_publishers:
                    publisher.close()
                raise

    def _define_background_daemon(self) -> None:
        """Define the background daemon to read output state for each channel and publish it."""
        for channel in range(1, self._num_channels + 1):
            self.add_background_daemon_function(self.get_output_state, channel=channel)

    def _check_channel(self, channel: int) -> None:
        if not 1 <= channel <= self._num_channels:
            raise ValueError(f"channel {channel} out of range for '{self.name}' (1-{self._num_channels})")

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
        with self._resource_lock:
            driver_method(channel, value)
            timestamp = time.time_ns()
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
        with self._resource_lock:
            val = driver_method(channel=channel)
            timestamp = time.time_ns()
        val = val.value if isinstance(val, Enum) else val
        descriptor = f"ch{channel}.{channel_suffix}"
        return self._package_measurement(descriptor, val, timestamp, **kwargs)

    def start(self) -> None:
        """Start the background daemon."""
        with self._resource_lock:
            configured = bool(self._channel_waveforms)
        if not configured:
            raise ValueError(
                "set_waveform must be called for at least one channel before starting background collection"
            )

        super().start()

    def open(self) -> None:
        """Open the underlying driver and apply any configured channel state."""
        logger.info("Opening AWG '%s'", self.name)
        self._driver.open()
        try:
            self._apply_channel_config()
        except Exception:
            self._channel_config_applied = False
            self._driver.close()
            raise
        logger.info("Opened AWG '%s'", self.name)

    def _apply_channel_config(self) -> None:
        """Apply the config's ``channels`` block through the public setters, once per open."""
        if self._config is None or self._channel_config_applied:
            return
        for channel_key, channel_config in self._config.channels.items():
            channel = int(channel_key)
            self.set_waveform(channel, build_waveform(channel_config.waveform))
            if channel_config.amplitude is not None:
                self.set_amplitude(channel, channel_config.amplitude.value, channel_config.amplitude.unit)
            if channel_config.offset is not None:
                self.set_offset(channel, channel_config.offset)
        self._channel_config_applied = True

    def close(self) -> None:
        """Close the underlying driver."""
        logger.info("Closing AWG '%s'", self.name)
        super().close()
        self._driver.close()
        self._channel_config_applied = False
        logger.info("Closed AWG '%s'", self.name)

    @publish_command
    def set_waveform(self, channel: int, waveform: Waveform, **kwargs) -> Command:
        """Program channel with a waveform."""
        if type(waveform) not in _PUBLISHED_NAMES:
            raise TypeError(f"waveform must be a Waveform definition, got {type(waveform).__name__}")
        self._check_channel(channel)
        with self._resource_lock:
            self._driver.set_waveform(channel=channel, waveform=waveform)
            timestamp = time.time_ns()
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
        return waveform

    @publish_command
    def set_amplitude(self, channel: int, amplitude: float, unit: AmplitudeMeasurementUnit, **kwargs) -> Command:
        """Set the output amplitude on channel."""
        if not isinstance(unit, AmplitudeMeasurementUnit):
            raise TypeError(f"unit must be an AmplitudeMeasurementUnit, got {type(unit).__name__}")
        self._check_channel(channel)
        with self._resource_lock:
            self._driver.set_amplitude(channel=channel, amplitude=amplitude, unit=unit)
            timestamp = time.time_ns()
        descriptor = f"ch{channel}.amplitude.cmd"
        return self._package_command(descriptor, amplitude, timestamp, unit=unit.value, **kwargs)

    def get_amplitude(self, channel: int) -> tuple[float, AmplitudeMeasurementUnit]:
        """Read back the current amplitude and its measurement unit on channel."""
        self._check_channel(channel)
        with self._resource_lock:
            amplitude = self._driver.get_amplitude(channel=channel)
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
        descriptor = f"ch{channel}.load.cmd"
        load_value = float("inf") if load is None else load
        return self._package_command(descriptor, load_value, timestamp, **kwargs)

    @publish_command
    def align_phase(self, **kwargs) -> Command:
        """Sync the phase of all channels."""
        with self._resource_lock:
            self._driver.align_phase()
            timestamp = time.time_ns()
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
        """Read back the output load impedance on channel."""
        self._check_channel(channel)
        with self._resource_lock:
            val = self._driver.get_output_load(channel=channel)
            timestamp = time.time_ns()
        load_float = float("inf") if val is None else val
        descriptor = f"ch{channel}.load"
        return self._package_measurement(descriptor, load_float, timestamp, **kwargs)

    @publish_command
    def set_modulation(
        self, channel: int, mod_type: ModulationType, shape: Waveform, magnitude: float, **kwargs
    ) -> Command:
        """Configure channel's carrier modulation with modulator shape.

        NOTE: magnitude varies by mod_type:
        AM: depth,
        FM: frequency deviation,
        PM: phase deviation,
        ASK: 2nd amplitude,
        FSK: hop frequency.
        """
        if not isinstance(mod_type, ModulationType):
            raise TypeError(f"mod_type must be a ModulationType, got {type(mod_type).__name__}")
        self._check_channel(channel)
        with self._resource_lock:
            self._driver.set_modulation(channel=channel, mod_type=mod_type, shape=shape, magnitude=magnitude)
            timestamp = time.time_ns()
        descriptor = f"ch{channel}.modulation.cmd"
        return self._package_command(descriptor, magnitude, timestamp, mod_type=mod_type.value, **kwargs)

    @publish_command
    def modulation_enable(self, channel: int, enable: bool, **kwargs) -> Command:
        """Enable or disable modulation on the given channel."""
        self._check_channel(channel)
        with self._resource_lock:
            self._driver.modulation_enable(channel=channel, enable=enable)
            timestamp = time.time_ns()
        descriptor = f"ch{channel}.modulation_enabled.cmd"
        return self._package_command(descriptor, enable, timestamp, **kwargs)

    def get_modulation_type(self, channel: int, **kwargs) -> Measurement | None:
        """Read back the modulation type currently active on channel."""
        self._check_channel(channel)
        return self._execute_measurement(self._driver.get_modulation_type, channel, "modulation_type", **kwargs)

    def get_modulation_state(self, channel: int, **kwargs) -> Measurement | None:
        """Read back whether modulation is enabled on channel."""
        self._check_channel(channel)
        return self._execute_measurement(self._driver.get_modulation_state, channel, "modulation_enabled", **kwargs)

    @publish_command
    def set_burst(self, channel: int, burst_type: BurstType, **kwargs) -> Command:
        """Configure channel's burst type."""
        if not isinstance(burst_type, BurstType):
            raise TypeError(f"burst_type must be a BurstType, got {type(burst_type).__name__}")
        self._check_channel(channel)
        with self._resource_lock:
            self._driver.set_burst(channel=channel, burst_type=burst_type)
            timestamp = time.time_ns()
        descriptor = f"ch{channel}.burst.cmd"
        return self._package_command(descriptor, burst_type.value, timestamp, **kwargs)

    def burst_enable(self, channel: int, enable: bool, **kwargs) -> Command:
        """Enable or disable burst mode on the given channel."""
        self._check_channel(channel)
        return self._execute_command(self._driver.burst_enable, channel, enable, "burst_enabled", **kwargs)

    def get_burst_type(self, channel: int, **kwargs) -> Measurement | None:
        """Read back the burst type currently active on channel."""
        self._check_channel(channel)
        return self._execute_measurement(self._driver.get_burst_type, channel, "burst_type", **kwargs)

    def get_burst_state(self, channel: int, **kwargs) -> Measurement | None:
        """Read back whether burst mode is enabled on channel."""
        self._check_channel(channel)
        return self._execute_measurement(self._driver.get_burst_state, channel, "burst_enabled", **kwargs)

    @publish_command
    def set_burst_trigger(self, channel: int, source: BurstTriggerSource, **kwargs) -> Command:
        """Set the burst trigger source on channel."""
        if not isinstance(source, BurstTriggerSource):
            raise TypeError(f"source must be a BurstTriggerSource, got {type(source).__name__}")
        self._check_channel(channel)
        with self._resource_lock:
            self._driver.set_burst_trigger(channel=channel, source=source)
            timestamp = time.time_ns()
        descriptor = f"ch{channel}.burst_trigger.cmd"
        return self._package_command(descriptor, source.value, timestamp, **kwargs)

    def get_burst_trigger(self, channel: int, **kwargs) -> Measurement | None:
        """Read back the burst trigger source on channel."""
        self._check_channel(channel)
        return self._execute_measurement(self._driver.get_burst_trigger, channel, "burst_trigger", **kwargs)

    @publish_command
    def fire_burst_trigger(self, channel: int, **kwargs) -> Command:
        """Fire a burst trigger on channel now; the trigger source must already be MANUAL."""
        self._check_channel(channel)
        with self._resource_lock:
            self._driver.fire_burst_trigger(channel=channel)
            timestamp = time.time_ns()
        descriptor = f"ch{channel}.burst_trigger_forced.cmd"
        return self._package_command(descriptor, BurstTriggerSource.MANUAL.value, timestamp, **kwargs)

    def set_burst_delay(self, channel: int, delay_s: float, **kwargs) -> Command:
        """Set the burst trigger delay (seconds) on channel."""
        self._check_channel(channel)
        return self._execute_command(self._driver.set_burst_delay, channel, delay_s, "burst_delay", **kwargs)

    def get_burst_delay(self, channel: int, **kwargs) -> Measurement | None:
        """Read back the burst trigger delay (seconds) on channel."""
        self._check_channel(channel)
        return self._execute_measurement(self._driver.get_burst_delay, channel, "burst_delay", **kwargs)

    @publish_command
    def set_burst_gate_polarity(self, channel: int, gate_polarity: GatePolarity, **kwargs) -> Command:
        """Set the gate polarity for GATED bursts on channel."""
        if not isinstance(gate_polarity, GatePolarity):
            raise TypeError(f"gate_polarity must be a GatePolarity, got {type(gate_polarity).__name__}")
        self._check_channel(channel)
        with self._resource_lock:
            self._driver.set_burst_gate_polarity(channel=channel, gate_polarity=gate_polarity)
            timestamp = time.time_ns()
        descriptor = f"ch{channel}.burst_gate_polarity.cmd"
        return self._package_command(descriptor, gate_polarity.value, timestamp, **kwargs)

    def get_burst_gate_polarity(self, channel: int, **kwargs) -> Measurement | None:
        """Read back the gate polarity for GATED bursts on channel."""
        self._check_channel(channel)
        return self._execute_measurement(self._driver.get_burst_gate_polarity, channel, "burst_gate_polarity", **kwargs)

    def set_burst_ncycles(self, channel: int, n_cycles: int, **kwargs) -> Command:
        """Set the number of cycles per trigger for NCYCLE bursts on channel."""
        self._check_channel(channel)
        return self._execute_command(self._driver.set_burst_ncycles, channel, n_cycles, "burst_ncycles", **kwargs)

    def get_burst_ncycles(self, channel: int, **kwargs) -> Measurement | None:
        """Read back the number of cycles per trigger for NCYCLE bursts on channel."""
        self._check_channel(channel)
        return self._execute_measurement(self._driver.get_burst_ncycles, channel, "burst_ncycles", **kwargs)

    def set_burst_period(self, channel: int, period: float, **kwargs) -> Command:
        """Set the internal burst period (seconds) on channel."""
        self._check_channel(channel)
        return self._execute_command(self._driver.set_burst_period, channel, period, "burst_period", **kwargs)

    def get_burst_period(self, channel: int, **kwargs) -> Measurement | None:
        """Read back the internal burst period (seconds) on channel."""
        self._check_channel(channel)
        return self._execute_measurement(self._driver.get_burst_period, channel, "burst_period", **kwargs)

    @publish_command
    def set_sweep(self, channel: int, sweep_type: SweepType, **kwargs) -> Command:
        """Configure the sweep type on channel."""
        if not isinstance(sweep_type, SweepType):
            raise TypeError(f"sweep_type must be a SweepType, got {type(sweep_type).__name__}")
        self._check_channel(channel)
        with self._resource_lock:
            self._driver.set_sweep(channel=channel, sweep_type=sweep_type)
            timestamp = time.time_ns()
        descriptor = f"ch{channel}.sweep.cmd"
        return self._package_command(descriptor, sweep_type.value, timestamp, **kwargs)

    def get_sweep_type(self, channel: int, **kwargs) -> Measurement | None:
        """Read back the sweep type currently configured on channel."""
        self._check_channel(channel)
        return self._execute_measurement(self._driver.get_sweep_type, channel, "sweep_type", **kwargs)

    def sweep_enable(self, channel: int, enable: bool, **kwargs) -> Command:
        """Enable or disable sweep mode on channel."""
        self._check_channel(channel)
        return self._execute_command(self._driver.sweep_enable, channel, enable, "sweep_enabled", **kwargs)

    def get_sweep_state(self, channel: int, **kwargs) -> Measurement | None:
        """Read back whether sweep mode is enabled on channel."""
        self._check_channel(channel)
        return self._execute_measurement(self._driver.get_sweep_state, channel, "sweep_enabled", **kwargs)

    @publish_command
    def set_sweep_trigger(self, channel: int, source: SweepTriggerSource, **kwargs) -> Command:
        """Set the sweep trigger source on channel."""
        if not isinstance(source, SweepTriggerSource):
            raise TypeError(f"source must be a SweepTriggerSource, got {type(source).__name__}")
        self._check_channel(channel)
        with self._resource_lock:
            self._driver.set_sweep_trigger(channel=channel, source=source)
            timestamp = time.time_ns()
        descriptor = f"ch{channel}.sweep_trigger.cmd"
        return self._package_command(descriptor, source.value, timestamp, **kwargs)

    def get_sweep_trigger(self, channel: int, **kwargs) -> Measurement | None:
        """Read back the sweep trigger source on channel."""
        self._check_channel(channel)
        return self._execute_measurement(self._driver.get_sweep_trigger, channel, "sweep_trigger", **kwargs)

    def set_sweep_start_freq(self, channel: int, frequency_hz: float, **kwargs) -> Command:
        """Set the sweep start frequency (Hz) on channel."""
        self._check_channel(channel)
        return self._execute_command(
            self._driver.set_sweep_start_freq, channel, frequency_hz, "sweep_start_freq", **kwargs
        )

    def get_sweep_start_freq(self, channel: int, **kwargs) -> Measurement | None:
        """Read back the sweep start frequency (Hz) on channel."""
        self._check_channel(channel)
        return self._execute_measurement(self._driver.get_sweep_start_freq, channel, "sweep_start_freq", **kwargs)

    def set_sweep_end_freq(self, channel: int, frequency_hz: float, **kwargs) -> Command:
        """Set the sweep end frequency (Hz) on channel."""
        self._check_channel(channel)
        return self._execute_command(self._driver.set_sweep_end_freq, channel, frequency_hz, "sweep_end_freq", **kwargs)

    def get_sweep_end_freq(self, channel: int, **kwargs) -> Measurement | None:
        """Read back the sweep end frequency (Hz) on channel."""
        self._check_channel(channel)
        return self._execute_measurement(self._driver.get_sweep_end_freq, channel, "sweep_end_freq", **kwargs)

    def set_sweep_time(self, channel: int, sweep_time: float, **kwargs) -> Command:
        """Set the sweep time (seconds) on channel."""
        self._check_channel(channel)
        return self._execute_command(self._driver.set_sweep_time, channel, sweep_time, "sweep_time", **kwargs)

    def get_sweep_time(self, channel: int, **kwargs) -> Measurement | None:
        """Read back the sweep time (seconds) on channel."""
        self._check_channel(channel)
        return self._execute_measurement(self._driver.get_sweep_time, channel, "sweep_time", **kwargs)

    def set_sweep_start_hold_time(self, channel: int, hold_time: float, **kwargs) -> Command:
        """Set the sweep start hold time (seconds) on channel."""
        self._check_channel(channel)
        return self._execute_command(
            self._driver.set_sweep_start_hold_time, channel, hold_time, "sweep_start_hold_time", **kwargs
        )

    def get_sweep_start_hold_time(self, channel: int, **kwargs) -> Measurement | None:
        """Read back the sweep start hold time (seconds) on channel."""
        self._check_channel(channel)
        return self._execute_measurement(
            self._driver.get_sweep_start_hold_time, channel, "sweep_start_hold_time", **kwargs
        )

    def set_sweep_stop_hold_time(self, channel: int, hold_time: float, **kwargs) -> Command:
        """Set the sweep stop hold time (seconds) on channel."""
        self._check_channel(channel)
        return self._execute_command(
            self._driver.set_sweep_stop_hold_time, channel, hold_time, "sweep_stop_hold_time", **kwargs
        )

    def get_sweep_stop_hold_time(self, channel: int, **kwargs) -> Measurement | None:
        """Read back the sweep stop hold time (seconds) on channel."""
        self._check_channel(channel)
        return self._execute_measurement(
            self._driver.get_sweep_stop_hold_time, channel, "sweep_stop_hold_time", **kwargs
        )

    def set_sweep_return_time(self, channel: int, return_time: float, **kwargs) -> Command:
        """Set the sweep return time (seconds) on channel."""
        self._check_channel(channel)
        return self._execute_command(
            self._driver.set_sweep_return_time, channel, return_time, "sweep_return_time", **kwargs
        )

    def get_sweep_return_time(self, channel: int, **kwargs) -> Measurement | None:
        """Read back the sweep return time (seconds) on channel."""
        self._check_channel(channel)
        return self._execute_measurement(self._driver.get_sweep_return_time, channel, "sweep_return_time", **kwargs)

    @publish_command
    def fire_sweep_trigger(self, channel: int, **kwargs) -> Command:
        """Fire a sweep trigger on channel now; the trigger source must already be MANUAL."""
        self._check_channel(channel)
        with self._resource_lock:
            self._driver.fire_sweep_trigger(channel=channel)
            timestamp = time.time_ns()
        descriptor = f"ch{channel}.sweep_trigger_forced.cmd"
        return self._package_command(descriptor, SweepTriggerSource.MANUAL.value, timestamp, **kwargs)
