"""AWG instrument driver contract and Instro AWG interface."""

from __future__ import annotations

import abc
import logging
import threading
import time
from enum import Enum, auto
from typing import Callable

from instro.lib.instrument import Instrument, publish_command, publish_measurement
from instro.lib.publishers import Publisher
from instro.lib.types import Command, Measurement
from instro.unstable.awg.types import AWGChannelConfig, VoltageUnit, WaveformType

logger = logging.getLogger(__name__)


class AWGDriverBase(abc.ABC):
    """Vendor AWG driver contract. Concrete drivers own their transport and lifecycle."""

    # drivers whose instrument supports pulse phase override
    supports_pulse_phase: bool = False

    @abc.abstractmethod
    def open(self) -> None:
        """Open the underlying transport."""

    @abc.abstractmethod
    def close(self) -> None:
        """Close the underlying transport."""

    @abc.abstractmethod
    def check_errors(self) -> None:
        """Drain the instrument error queue; raise if any error is pending."""

    # --- Standard periodic waveforms ---

    @abc.abstractmethod
    def set_std_waveform(self, channel: int, waveform: WaveformType) -> None:
        """Set the waveform function on channel."""

    @abc.abstractmethod
    def get_std_waveform(self, channel: int) -> WaveformType:
        """Get the current waveform function on channel."""

    @abc.abstractmethod
    def set_std_frequency(self, channel: int, frequency: float) -> None:
        """Set the output frequency (Hz) on channel."""

    @abc.abstractmethod
    def get_std_frequency(self, channel: int) -> float:
        """Get the output frequency (Hz) on channel."""

    @abc.abstractmethod
    def set_std_amplitude(self, channel: int, amplitude: float, unit: VoltageUnit) -> None:
        """Set the output amplitude on channel."""

    @abc.abstractmethod
    def get_std_amplitude(self, channel: int) -> tuple[float, VoltageUnit]:
        """Get the current output amplitude and voltage unit on channel."""

    @abc.abstractmethod
    def set_std_offset(self, channel: int, offset: float) -> None:
        """Set the DC offset (volts) on channel."""

    @abc.abstractmethod
    def get_std_offset(self, channel: int) -> float:
        """Get the DC offset (volts) on channel."""

    @abc.abstractmethod
    def output_enable(self, channel: int, enable: bool) -> None:
        """Enable or disable the output on channel."""

    @abc.abstractmethod
    def get_output_state(self, channel: int) -> bool:
        """Return True if the output on channel is enabled."""

    @abc.abstractmethod
    def set_std_output_load(self, channel: int, load: float | None) -> None:
        """Set the output load impedance; None means high-Z."""

    @abc.abstractmethod
    def get_std_output_load(self, channel: int) -> float | None:
        """Get the output load impedance; None means high-Z."""

    @abc.abstractmethod
    def set_phase(self, channel: int, phase_deg: float) -> None:
        """Set the phase (degrees) for a channel."""

    @abc.abstractmethod
    def get_phase(self, channel: int) -> float:
        """Get the current phase (degrees) for a channel."""

    def align_phase(self) -> None:
        """Sync the phase of both channels."""
        raise NotImplementedError(f"align_phase is not implemented for {type(self).__name__}")

    # --- Optional: standalone voltage unit ---

    def set_voltage_unit(self, channel: int, unit: VoltageUnit) -> None:
        """Set the voltage representation unit for a channel without changing the amplitude value."""
        raise NotImplementedError(f"set_voltage_unit is not implemented for {type(self).__name__}")

    def get_voltage_unit(self, channel: int) -> VoltageUnit:
        """Get the current voltage representation unit for a channel."""
        raise NotImplementedError(f"get_voltage_unit is not implemented for {type(self).__name__}")

    # --- Optional: high/low level (alternative to amplitude + offset) ---

    def set_high_level(self, channel: int, volts: float) -> None:
        """Set the high voltage level for a channel; not valid for DC."""
        raise NotImplementedError(f"set_high_level is not implemented for {type(self).__name__}")

    def set_low_level(self, channel: int, volts: float) -> None:
        """Set the low voltage level for a channel; not valid for DC."""
        raise NotImplementedError(f"set_low_level is not implemented for {type(self).__name__}")

    # --- Optional: waveform-specific ---

    def set_square_duty_cycle(self, channel: int, duty_pct: float) -> None:
        """Set the duty cycle (%) for a square waveform on channel."""
        raise NotImplementedError(f"set_square_duty_cycle is not implemented for {type(self).__name__}")

    def set_ramp_symmetry(self, channel: int, symmetry_pct: float) -> None:
        """Set the symmetry (%) for a ramp waveform on channel."""
        raise NotImplementedError(f"set_ramp_symmetry is not implemented for {type(self).__name__}")

    def set_pulse_width(self, channel: int, width_s: float) -> None:
        """Set the pulse width (seconds) for a pulse waveform on channel."""
        raise NotImplementedError(f"set_pulse_width is not implemented for {type(self).__name__}")

    def set_pulse_delay(self, channel: int, delay_s: float) -> None:
        """Set the pulse lead delay (seconds) for a pulse waveform on channel."""
        raise NotImplementedError(f"set_pulse_delay is not implemented for {type(self).__name__}")


class _ValidatedMethod(Enum):
    """Methods gated by InstroAWG._check_waveform_applicable, keyed here instead of by string name."""

    SET_STD_FREQUENCY = auto()
    SET_STD_AMPLITUDE = auto()
    SET_HIGH_LEVEL = auto()
    SET_LOW_LEVEL = auto()
    SET_PHASE = auto()
    SET_SQUARE_DUTY_CYCLE = auto()
    SET_RAMP_SYMMETRY = auto()
    SET_PULSE_WIDTH = auto()
    SET_PULSE_DELAY = auto()


_WAVEFORM_APPLICABILITY: dict[_ValidatedMethod, frozenset[WaveformType]] = {
    _ValidatedMethod.SET_STD_FREQUENCY: frozenset(WaveformType) - {WaveformType.NOISE, WaveformType.DC},
    _ValidatedMethod.SET_STD_AMPLITUDE: frozenset(WaveformType) - {WaveformType.DC},
    _ValidatedMethod.SET_HIGH_LEVEL: frozenset(WaveformType) - {WaveformType.DC},
    _ValidatedMethod.SET_LOW_LEVEL: frozenset(WaveformType) - {WaveformType.DC},
    _ValidatedMethod.SET_PHASE: frozenset(WaveformType) - {WaveformType.NOISE, WaveformType.DC},
    _ValidatedMethod.SET_SQUARE_DUTY_CYCLE: frozenset({WaveformType.SQUARE}),
    _ValidatedMethod.SET_RAMP_SYMMETRY: frozenset({WaveformType.RAMP}),
    _ValidatedMethod.SET_PULSE_WIDTH: frozenset({WaveformType.PULSE}),
    _ValidatedMethod.SET_PULSE_DELAY: frozenset({WaveformType.PULSE}),
}


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
        self._channel_config: dict[int, AWGChannelConfig] = {}

        self._define_background_daemon()

    def start(self) -> None:
        """Start the background daemon; raises unless ``set_std_waveform`` was called for every channel."""
        with self._resource_lock:
            unconfigured = [ch for ch in range(1, self._num_channels + 1) if ch not in self._channel_config]
        if unconfigured:
            channels = ", ".join(str(ch) for ch in unconfigured)
            raise ValueError(
                f"set_std_waveform must be called for channel(s) {channels} before starting background collection"
            )

        super().start()

    def _define_background_daemon(self) -> None:
        """Register per-channel output-state polling; other readbacks are opt-in via add_background_daemon_function."""
        for channel in range(1, self._num_channels + 1):
            self.add_background_daemon_function(self.get_output_state, channel=channel)

    def _check_channel(self, channel: int) -> None:
        if not 1 <= channel <= self._num_channels:
            raise ValueError(f"channel {channel} out of range for '{self.name}' (1-{self._num_channels})")

    def _check_waveform_applicable(self, channel: int, method: _ValidatedMethod) -> None:
        """Validate ``method`` against the channel's configured waveform. Caller must hold ``_resource_lock``."""
        self._check_channel(channel)
        config = self._channel_config.get(channel)
        method_name = method.name.lower()
        if config is None:
            raise ValueError(f"set_std_waveform must be called for channel {channel} before {method_name}")
        if config.waveform not in _WAVEFORM_APPLICABILITY[method]:
            raise ValueError(f"{method_name} is not valid for channel {channel} configured as {config.waveform.value}")
        if (
            method is _ValidatedMethod.SET_PHASE
            and config.waveform is WaveformType.PULSE
            and not self._driver.supports_pulse_phase
        ):
            raise ValueError(
                f"set_phase is not supported for PULSE by {type(self._driver).__name__}; "
                "use set_pulse_delay if the instrument exposes a pulse delay"
            )

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
        validate: _ValidatedMethod | None = None,
        /,
        **kwargs,
    ) -> Command:
        """Validate and run ``driver_method(channel, value)``."""
        with self._resource_lock:
            if validate is not None:
                self._check_waveform_applicable(channel, validate)
            driver_method(channel, value)
            timestamp = time.time_ns()
            self._check_errors()
        descriptor = f"ch{channel}.{channel_suffix}.cmd"
        return self._package_command(descriptor, value, timestamp, **kwargs)

    @publish_command
    def set_std_waveform(self, channel: int, waveform: WaveformType, **kwargs) -> Command:
        """Set the waveform type on channel."""
        if not isinstance(waveform, WaveformType):
            raise TypeError(f"waveform must be a WaveformType, got {type(waveform).__name__}")
        self._check_channel(channel)
        with self._resource_lock:
            self._driver.set_std_waveform(channel=channel, waveform=waveform)
            timestamp = time.time_ns()
            self._check_errors()
            config = self._channel_config.get(channel)
            if config is None:
                self._channel_config[channel] = AWGChannelConfig(waveform)
            else:
                config.waveform = waveform
        descriptor = f"ch{channel}.waveform.cmd"
        return self._package_command(descriptor, waveform.value, timestamp, **kwargs)

    @publish_command
    def set_std_frequency(self, channel: int, frequency_hz: float, **kwargs) -> Command:
        """Set the output frequency (Hz) on channel. Not valid for NOISE or DC."""
        with self._resource_lock:
            self._check_waveform_applicable(channel, _ValidatedMethod.SET_STD_FREQUENCY)
            self._driver.set_std_frequency(channel=channel, frequency=frequency_hz)
            timestamp = time.time_ns()
            self._check_errors()
            self._channel_config[channel].frequency_hz = frequency_hz
        descriptor = f"ch{channel}.frequency.cmd"
        return self._package_command(descriptor, frequency_hz, timestamp, **kwargs)

    @publish_command
    def set_std_amplitude(self, channel: int, amplitude: float, unit: VoltageUnit, **kwargs) -> Command:
        """Set the output amplitude on channel. Not valid for DC; the unit ships as a ``unit`` tag."""
        if not isinstance(unit, VoltageUnit):
            raise TypeError(f"unit must be a VoltageUnit, got {type(unit).__name__}")
        with self._resource_lock:
            self._check_waveform_applicable(channel, _ValidatedMethod.SET_STD_AMPLITUDE)
            self._driver.set_std_amplitude(channel=channel, amplitude=amplitude, unit=unit)
            timestamp = time.time_ns()
            self._check_errors()
            self._channel_config[channel].voltage_unit = unit
        descriptor = f"ch{channel}.amplitude.cmd"
        # A tag, not a second channel: NominalConnect drops any Command whose channel_data holds a string.
        return self._package_command(descriptor, amplitude, timestamp, unit=unit.value, **kwargs)

    def set_std_offset(self, channel: int, offset_v: float, **kwargs) -> Command:
        """Set the DC offset (volts) on channel."""
        self._check_channel(channel)
        return self._execute_command(self._driver.set_std_offset, channel, offset_v, "offset", **kwargs)

    @publish_command
    def output_enable(self, channel: int, enable: bool, **kwargs) -> Command:
        """Enable or disable the output on channel."""
        self._check_channel(channel)
        with self._resource_lock:
            self._driver.output_enable(channel=channel, enable=enable)
            timestamp = time.time_ns()
            self._check_errors()
            config = self._channel_config.get(channel)
            if config is not None:
                config.output_enabled = enable
        descriptor = f"ch{channel}.enabled.cmd"
        return self._package_command(descriptor, enable, timestamp, **kwargs)

    @publish_command
    def set_std_output_load(self, channel: int, load: float | None, **kwargs) -> Command:
        """Set the output load impedance; None means high-Z."""
        self._check_channel(channel)
        with self._resource_lock:
            self._driver.set_std_output_load(channel=channel, load=load)
            timestamp = time.time_ns()
            self._check_errors()
        descriptor = f"ch{channel}.load.cmd"
        load_value = float("inf") if load is None else load
        return self._package_command(descriptor, load_value, timestamp, **kwargs)

    def get_std_waveform(self, channel: int) -> WaveformType:
        """Read back the current waveform type on channel."""
        self._check_channel(channel)
        with self._resource_lock:
            waveform = self._driver.get_std_waveform(channel=channel)
            self._check_errors()
        return waveform

    def get_std_amplitude(self, channel: int) -> tuple[float, VoltageUnit]:
        """Read back the current amplitude and voltage unit on channel."""
        self._check_channel(channel)
        with self._resource_lock:
            amplitude = self._driver.get_std_amplitude(channel=channel)
            self._check_errors()
        return amplitude

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

    def get_std_frequency(self, channel: int, **kwargs) -> Measurement | None:
        """Read back the current output frequency (Hz) on channel."""
        self._check_channel(channel)
        return self._execute_measurement(self._driver.get_std_frequency, channel, "frequency", **kwargs)

    def get_output_state(self, channel: int, **kwargs) -> Measurement | None:
        """Read back whether the output is enabled on channel."""
        self._check_channel(channel)
        return self._execute_measurement(self._driver.get_output_state, channel, "enabled", **kwargs)

    def get_phase(self, channel: int, **kwargs) -> Measurement | None:
        """Read back the current phase (degrees) on channel."""
        self._check_channel(channel)
        return self._execute_measurement(self._driver.get_phase, channel, "phase", **kwargs)

    def get_std_offset(self, channel: int, **kwargs) -> Measurement | None:
        """Read back the DC offset (volts) on channel."""
        self._check_channel(channel)
        return self._execute_measurement(self._driver.get_std_offset, channel, "offset", **kwargs)

    @publish_measurement
    def get_std_output_load(self, channel: int, **kwargs) -> Measurement | None:
        """Read back the output load impedance on channel; high-Z is published as ``float('inf')``."""
        self._check_channel(channel)
        with self._resource_lock:
            val = self._driver.get_std_output_load(channel=channel)
            timestamp = time.time_ns()
            self._check_errors()
        load_float = float("inf") if val is None else val
        descriptor = f"ch{channel}.load"
        return self._package_measurement(descriptor, load_float, timestamp, **kwargs)

    @publish_command
    def set_voltage_unit(self, channel: int, unit: VoltageUnit, **kwargs) -> Command:
        """Set the voltage unit on channel."""
        if not isinstance(unit, VoltageUnit):
            raise TypeError(f"unit must be a VoltageUnit, got {type(unit).__name__}")
        self._check_channel(channel)
        with self._resource_lock:
            self._driver.set_voltage_unit(channel=channel, unit=unit)
            timestamp = time.time_ns()
            self._check_errors()
            config = self._channel_config.get(channel)
            if config is not None:
                config.voltage_unit = unit
        descriptor = f"ch{channel}.voltage_unit.cmd"
        return self._package_command(descriptor, unit.value, timestamp, **kwargs)

    def get_voltage_unit(self, channel: int) -> VoltageUnit:
        """Read back the current voltage unit on channel."""
        self._check_channel(channel)
        with self._resource_lock:
            unit = self._driver.get_voltage_unit(channel=channel)
            self._check_errors()
        return unit

    def set_high_level(self, channel: int, volts: float, **kwargs) -> Command:
        """Set the high voltage level (volts) on channel. Not valid for DC."""
        return self._execute_command(
            self._driver.set_high_level,
            channel,
            volts,
            "high_level",
            _ValidatedMethod.SET_HIGH_LEVEL,
            **kwargs,
        )

    def set_low_level(self, channel: int, volts: float, **kwargs) -> Command:
        """Set the low voltage level (volts) on channel. Not valid for DC."""
        return self._execute_command(
            self._driver.set_low_level, channel, volts, "low_level", _ValidatedMethod.SET_LOW_LEVEL, **kwargs
        )

    def set_phase(self, channel: int, phase_deg: float, **kwargs) -> Command:
        """Set the phase (degrees) on channel. Not valid for NOISE or DC; PULSE only if the driver supports it."""
        return self._execute_command(
            self._driver.set_phase, channel, phase_deg, "phase", _ValidatedMethod.SET_PHASE, **kwargs
        )

    @publish_command
    def align_phase(self, **kwargs) -> Command:
        """Sync the phase of both channels."""
        with self._resource_lock:
            self._driver.align_phase()
            timestamp = time.time_ns()
            self._check_errors()
        descriptor = "phase.align.cmd"
        return self._package_command(descriptor, "ALIGN", timestamp, **kwargs)

    # --- Waveform-specific ---

    def set_square_duty_cycle(self, channel: int, duty_pct: float, **kwargs) -> Command:
        """Set the duty cycle (%) for a square waveform on channel. SQUARE only."""
        return self._execute_command(
            self._driver.set_square_duty_cycle,
            channel,
            duty_pct,
            "square.duty_cycle",
            _ValidatedMethod.SET_SQUARE_DUTY_CYCLE,
            **kwargs,
        )

    def set_ramp_symmetry(self, channel: int, symmetry_pct: float, **kwargs) -> Command:
        """Set the symmetry (%) for a ramp waveform on channel. RAMP only."""
        return self._execute_command(
            self._driver.set_ramp_symmetry,
            channel,
            symmetry_pct,
            "ramp.symmetry",
            _ValidatedMethod.SET_RAMP_SYMMETRY,
            **kwargs,
        )

    def set_pulse_width(self, channel: int, width_s: float, **kwargs) -> Command:
        """Set the pulse width (seconds) on channel. PULSE only."""
        return self._execute_command(
            self._driver.set_pulse_width,
            channel,
            width_s,
            "pulse.width",
            _ValidatedMethod.SET_PULSE_WIDTH,
            **kwargs,
        )

    def set_pulse_delay(self, channel: int, delay_s: float, **kwargs) -> Command:
        """Set the pulse lead delay (seconds) on channel. PULSE only."""
        return self._execute_command(
            self._driver.set_pulse_delay,
            channel,
            delay_s,
            "pulse.delay",
            _ValidatedMethod.SET_PULSE_DELAY,
            **kwargs,
        )
