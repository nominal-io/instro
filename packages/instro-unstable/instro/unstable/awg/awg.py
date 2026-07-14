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

    @abc.abstractmethod
    def open(self) -> None:
        """Open the underlying transport."""

    @abc.abstractmethod
    def close(self) -> None:
        """Close the underlying transport."""

    @abc.abstractmethod
    def check_errors(self) -> None:
        """Query the instrument error queue and raise on error code."""

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
        """Set the high voltage level for a channel."""
        raise NotImplementedError(f"set_high_level is not implemented for {type(self).__name__}")

    def set_low_level(self, channel: int, volts: float) -> None:
        """Set the low voltage level for a channel."""
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


_UNSET = object()


class _ValidatedMethod(Enum):
    """Methods gated by InstroAWG._check_waveform_applicable, keyed here instead of by string name."""

    SET_STD_FREQUENCY = auto()
    SET_STD_AMPLITUDE = auto()
    SET_PHASE = auto()
    SET_SQUARE_DUTY_CYCLE = auto()
    SET_RAMP_SYMMETRY = auto()
    SET_PULSE_WIDTH = auto()


_WAVEFORM_APPLICABILITY: dict[_ValidatedMethod, frozenset[WaveformType]] = {
    _ValidatedMethod.SET_STD_FREQUENCY: frozenset(WaveformType) - {WaveformType.NOISE, WaveformType.DC},
    _ValidatedMethod.SET_STD_AMPLITUDE: frozenset(WaveformType) - {WaveformType.DC},
    _ValidatedMethod.SET_PHASE: frozenset(WaveformType) - {WaveformType.NOISE, WaveformType.DC},
    _ValidatedMethod.SET_SQUARE_DUTY_CYCLE: frozenset({WaveformType.SQUARE}),
    _ValidatedMethod.SET_RAMP_SYMMETRY: frozenset({WaveformType.RAMP}),
    _ValidatedMethod.SET_PULSE_WIDTH: frozenset({WaveformType.PULSE}),
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
        super().__init__(name, publishers=publishers, **kwargs)
        self._driver = driver
        self._num_channels = num_channels
        self._resource_lock = threading.Lock()
        self._channel_config: dict[int, AWGChannelConfig] = {}

    def _check_channel(self, channel: int) -> None:
        if not 1 <= channel <= self._num_channels:
            raise ValueError(f"channel {channel} out of range for '{self.name}' (1-{self._num_channels})")

    def _check_waveform_applicable(self, channel: int, method: _ValidatedMethod) -> None:
        self._check_channel(channel)
        with self._resource_lock:
            config = self._channel_config.get(channel)
        method_name = method.name.lower()
        if config is None:
            raise ValueError(f"set_std_waveform must be called for channel {channel} before {method_name}")
        if config.waveform not in _WAVEFORM_APPLICABILITY[method]:
            raise ValueError(f"{method_name} is not valid for channel {channel} configured as {config.waveform.value}")

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

    def check_errors(self) -> None:
        """Query the instrument error queue and raise on error code."""
        with self._resource_lock:
            self._driver.check_errors()

    @publish_command
    def _execute_command(
        self,
        driver_method: Callable,
        channel: int,
        value: float | bool | str,
        channel_suffix: str,
        **kwargs,
    ) -> Command:
        """General-purpose command helper: call ``driver_method(channel, value)``, timestamp, and package."""
        with self._resource_lock:
            driver_method(channel, value)
            timestamp = time.time_ns()
        descriptor = f"ch{channel}.{channel_suffix}.cmd"
        return self._package_command(descriptor, value, timestamp, **kwargs)

    @publish_command
    def set_std_waveform(self, channel: int, waveform: WaveformType, **kwargs) -> Command:
        """Set the waveform type on channel."""
        self._check_channel(channel)
        with self._resource_lock:
            self._driver.set_std_waveform(channel=channel, waveform=waveform)
            timestamp = time.time_ns()
            self._channel_config[channel] = AWGChannelConfig(waveform)
        descriptor = f"ch{channel}.waveform.cmd"
        return self._package_command(descriptor, waveform.value, timestamp, **kwargs)

    def set_std_frequency(self, channel: int, frequency_hz: float, **kwargs) -> Command:
        """Set the output frequency (Hz) on channel. Not valid for NOISE or DC."""
        self._check_waveform_applicable(channel, _ValidatedMethod.SET_STD_FREQUENCY)
        return self._execute_command(self._driver.set_std_frequency, channel, frequency_hz, "frequency", **kwargs)

    @publish_command
    def set_std_amplitude(self, channel: int, amplitude: float, unit: VoltageUnit, **kwargs) -> Command:
        """Set the output amplitude on channel. Not valid for DC."""
        self._check_waveform_applicable(channel, _ValidatedMethod.SET_STD_AMPLITUDE)
        with self._resource_lock:
            self._driver.set_std_amplitude(channel=channel, amplitude=amplitude, unit=unit)
            timestamp = time.time_ns()
        descriptor = f"ch{channel}.amplitude.cmd"
        return self._package_command(descriptor, amplitude, timestamp, **kwargs)

    def set_std_offset(self, channel: int, offset_v: float, **kwargs) -> Command:
        """Set the DC offset (volts) on channel."""
        self._check_channel(channel)
        return self._execute_command(self._driver.set_std_offset, channel, offset_v, "offset", **kwargs)

    def output_enable(self, channel: int, enable: bool, **kwargs) -> Command:
        """Enable or disable the output on channel."""
        self._check_channel(channel)
        return self._execute_command(self._driver.output_enable, channel, enable, "enabled", **kwargs)

    @publish_command
    def set_std_output_load(self, channel: int, load: float | None, **kwargs) -> Command:
        """Set the output load impedance; None means high-Z."""
        self._check_channel(channel)
        with self._resource_lock:
            self._driver.set_std_output_load(channel=channel, load=load)
            timestamp = time.time_ns()
        descriptor = f"ch{channel}.load.cmd"
        load_value = "INF" if load is None else load
        return self._package_command(descriptor, load_value, timestamp, **kwargs)

    def get_std_waveform(self, channel: int) -> WaveformType:
        """Read back the current waveform type on channel."""
        self._check_channel(channel)
        with self._resource_lock:
            return self._driver.get_std_waveform(channel=channel)

    def get_std_amplitude(self, channel: int) -> tuple[float, VoltageUnit]:
        """Read back the current amplitude and voltage unit on channel."""
        self._check_channel(channel)
        with self._resource_lock:
            return self._driver.get_std_amplitude(channel=channel)

    @publish_measurement
    def get_std_frequency(self, channel: int, **kwargs) -> Measurement | None:
        """Read back the current output frequency (Hz) on channel."""
        self._check_channel(channel)
        with self._resource_lock:
            val = self._driver.get_std_frequency(channel=channel)
            timestamp = time.time_ns()
        descriptor = f"ch{channel}.frequency"
        return self._package_measurement(descriptor, val, timestamp, **kwargs)

    @publish_measurement
    def get_output_state(self, channel: int, **kwargs) -> Measurement | None:
        """Read back whether the output is enabled on channel."""
        self._check_channel(channel)
        with self._resource_lock:
            val = self._driver.get_output_state(channel=channel)
            timestamp = time.time_ns()
        descriptor = f"ch{channel}.enabled"
        return self._package_measurement(descriptor, val, timestamp, **kwargs)

    @publish_measurement
    def get_phase(self, channel: int, **kwargs) -> Measurement | None:
        """Read back the current phase (degrees) on channel."""
        self._check_channel(channel)
        with self._resource_lock:
            val = self._driver.get_phase(channel=channel)
            timestamp = time.time_ns()
        descriptor = f"ch{channel}.phase"
        return self._package_measurement(descriptor, val, timestamp, **kwargs)

    @publish_measurement
    def get_std_offset(self, channel: int, **kwargs) -> Measurement | None:
        """Read back the DC offset (volts) on channel."""
        self._check_channel(channel)
        with self._resource_lock:
            val = self._driver.get_std_offset(channel=channel)
            timestamp = time.time_ns()
        descriptor = f"ch{channel}.offset"
        return self._package_measurement(descriptor, val, timestamp, **kwargs)

    @publish_measurement
    def get_std_output_load(self, channel: int, **kwargs) -> Measurement | None:
        """Read back the output load impedance on channel; high-Z is published as ``float('inf')``."""
        self._check_channel(channel)
        with self._resource_lock:
            val = self._driver.get_std_output_load(channel=channel)
            timestamp = time.time_ns()
        load_float = float("inf") if val is None else val
        descriptor = f"ch{channel}.load"
        return self._package_measurement(descriptor, load_float, timestamp, **kwargs)

    def configure_std_channel(
        self,
        channel: int,
        waveform: WaveformType,
        frequency_hz: float,
        amplitude: float,
        unit: VoltageUnit,
        offset_v: float = 0.0,
        *,
        load: float | None = _UNSET,
        enable: bool | None = None,
        phase_deg: float | None = None,
        **kwargs,
    ) -> list[Command]:
        """Configure standard waveform parameters on channel in one call."""
        cmds: list[Command] = [self.set_std_waveform(channel, waveform, **kwargs)]
        if waveform in _WAVEFORM_APPLICABILITY[_ValidatedMethod.SET_STD_FREQUENCY]:
            cmds.append(self.set_std_frequency(channel, frequency_hz, **kwargs))
        if waveform in _WAVEFORM_APPLICABILITY[_ValidatedMethod.SET_STD_AMPLITUDE]:
            cmds.append(self.set_std_amplitude(channel, amplitude, unit, **kwargs))
        cmds.append(self.set_std_offset(channel, offset_v, **kwargs))
        if load is not _UNSET:
            cmds.append(self.set_std_output_load(channel, load, **kwargs))
        if enable is not None:
            cmds.append(self.output_enable(channel, enable, **kwargs))
        if phase_deg is not None and waveform in _WAVEFORM_APPLICABILITY[_ValidatedMethod.SET_PHASE]:
            cmds.append(self.set_phase(channel, phase_deg, **kwargs))
        return cmds

    @publish_command
    def set_voltage_unit(self, channel: int, unit: VoltageUnit, **kwargs) -> Command:
        """Set the voltage unit on channel."""
        self._check_channel(channel)
        with self._resource_lock:
            self._driver.set_voltage_unit(channel=channel, unit=unit)
            timestamp = time.time_ns()
        descriptor = f"ch{channel}.voltage_unit.cmd"
        return self._package_command(descriptor, unit.value, timestamp, **kwargs)

    def get_voltage_unit(self, channel: int) -> VoltageUnit:
        """Read back the current voltage unit on channel."""
        self._check_channel(channel)
        with self._resource_lock:
            return self._driver.get_voltage_unit(channel=channel)

    def set_high_level(self, channel: int, volts: float, **kwargs) -> Command:
        """Set the high voltage level (volts) on channel."""
        self._check_channel(channel)
        return self._execute_command(self._driver.set_high_level, channel, volts, "high_level", **kwargs)

    def set_low_level(self, channel: int, volts: float, **kwargs) -> Command:
        """Set the low voltage level (volts) on channel."""
        self._check_channel(channel)
        return self._execute_command(self._driver.set_low_level, channel, volts, "low_level", **kwargs)

    def set_phase(self, channel: int, phase_deg: float, **kwargs) -> Command:
        """Set the phase (degrees) on channel. Not valid for NOISE or DC."""
        self._check_waveform_applicable(channel, _ValidatedMethod.SET_PHASE)
        return self._execute_command(self._driver.set_phase, channel, phase_deg, "phase", **kwargs)

    @publish_command
    def align_phase(self, **kwargs) -> Command:
        """Sync the phase of both channels."""
        with self._resource_lock:
            self._driver.align_phase()
            timestamp = time.time_ns()
        descriptor = "phase.align.cmd"
        return self._package_command(descriptor, "ALIGN", timestamp, **kwargs)

    # --- Waveform-specific ---

    def set_square_duty_cycle(self, channel: int, duty_pct: float, **kwargs) -> Command:
        """Set the duty cycle (%) for a square waveform on channel. SQUARE only."""
        self._check_waveform_applicable(channel, _ValidatedMethod.SET_SQUARE_DUTY_CYCLE)
        return self._execute_command(
            self._driver.set_square_duty_cycle, channel, duty_pct, "square.duty_cycle", **kwargs
        )

    def set_ramp_symmetry(self, channel: int, symmetry_pct: float, **kwargs) -> Command:
        """Set the symmetry (%) for a ramp waveform on channel. RAMP only."""
        self._check_waveform_applicable(channel, _ValidatedMethod.SET_RAMP_SYMMETRY)
        return self._execute_command(self._driver.set_ramp_symmetry, channel, symmetry_pct, "ramp.symmetry", **kwargs)

    def set_pulse_width(self, channel: int, width_s: float, **kwargs) -> Command:
        """Set the pulse width (seconds) on channel. PULSE only."""
        self._check_waveform_applicable(channel, _ValidatedMethod.SET_PULSE_WIDTH)
        return self._execute_command(self._driver.set_pulse_width, channel, width_s, "pulse.width", **kwargs)
