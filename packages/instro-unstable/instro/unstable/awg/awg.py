"""AWG instrument driver contract and Instro AWG interface."""

from __future__ import annotations

import abc
import logging
import threading
import time
from typing import Callable

from instro.unstable.awg.types import Channel, VoltageUnit, WaveformType
from instro.lib.instrument import Instrument, publish_command, publish_measurement
from instro.lib.publishers import Publisher
from instro.lib.types import Command, Measurement

logger = logging.getLogger(__name__)


class AWGDriverBase(abc.ABC):
    """Vendor AWG driver contract. Concrete drivers own their transport and lifecycle.

    All methods here apply to standard waveforms. Composite waveform
    support (modulation, sweep, burst, arb upload) is added in later milestones as
    optional method groups that raise NotImplementedError by default.

    ..._std_... - use with standard waveforms
    ..._arb_... - use with composite waveforms

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
    def set_std_waveform(self, channel: Channel, waveform: WaveformType) -> None:
        """Set the waveform function on channel."""

    @abc.abstractmethod
    def get_std_waveform(self, channel: Channel) -> WaveformType:
        """Get the current waveform function on channel."""

    @abc.abstractmethod
    def set_std_frequency(self, channel: Channel, frequency: float) -> None:
        """Set the output frequency (Hz) on channel."""

    @abc.abstractmethod
    def get_std_frequency(self, channel: Channel) -> float:
        """Get the output frequency (Hz) on channel."""

    @abc.abstractmethod
    def set_std_amplitude(self, channel: Channel, amplitude: float, unit: VoltageUnit) -> None:
        """Set the output amplitude on channel.

        Implementations must update the voltage unit to ``unit`` as part of this call.
        Callers do not need to call ``set_voltage_unit`` separately after this.
        """

    @abc.abstractmethod
    def get_std_amplitude(self, channel: Channel) -> tuple[float, VoltageUnit]:
        """Get the current output amplitude and voltage unit on channel."""

    @abc.abstractmethod
    def set_std_offset(self, channel: Channel, offset: float) -> None:
        """Set the DC offset (volts) on channel."""

    @abc.abstractmethod
    def get_std_offset(self, channel: Channel) -> float:
        """Get the DC offset (volts) on channel."""

    @abc.abstractmethod
    def output_enable(self, channel: Channel, enable: bool) -> None:
        """Enable or disable the output on channel."""

    @abc.abstractmethod
    def get_output_state(self, channel: Channel) -> bool:
        """Return True if the output on channel is enabled."""

    @abc.abstractmethod
    def set_std_output_load(self, channel: Channel, load: float | None) -> None:
        """Set the output load impedance; None means high-Z."""

    @abc.abstractmethod
    def get_std_output_load(self, channel: Channel) -> float | None:
        """Get the output load impedance; None means high-Z."""

    @abc.abstractmethod
    def set_phase(self, channel: Channel, phase_deg: float) -> None:
        """Set the phase (degrees) for a channel."""

    @abc.abstractmethod
    def get_phase(self, channel: Channel) -> float:
        """Get the current phase (degrees) for a channel."""

    def align_phase(self) -> None:
        """Sync the phase of both channels."""
        raise NotImplementedError(f"align_phase is not implemented for {type(self).__name__}")

    # --- Optional: standalone voltage unit ---

    def set_voltage_unit(self, channel: Channel, unit: VoltageUnit) -> None:
        """Set the voltage representation unit for a channel without changing the amplitude value.

        Optional — ``set_std_amplitude`` handles unit setting implicitly for combined changes.
        Implement this only if the instrument supports standalone unit changes.
        """
        raise NotImplementedError(f"set_voltage_unit is not implemented for {type(self).__name__}")

    def get_voltage_unit(self, channel: Channel) -> VoltageUnit:
        """Get the current voltage representation unit for a channel."""
        raise NotImplementedError(f"get_voltage_unit is not implemented for {type(self).__name__}")

    # --- Optional: high/low level (alternative to amplitude + offset) ---

    def set_high_level(self, channel: Channel, volts: float) -> None:
        """Set the high voltage level for a channel.

        Alternative to ``set_std_amplitude`` + ``set_std_offset``. Use the amplitude/offset
        pair as the primary interface; use this only when the instrument requires it.
        """
        raise NotImplementedError(f"set_high_level is not implemented for {type(self).__name__}")

    def set_low_level(self, channel: Channel, volts: float) -> None:
        """Set the low voltage level for a channel.

        Alternative to ``set_std_amplitude`` + ``set_std_offset``. Use the amplitude/offset
        pair as the primary interface; use this only when the instrument requires it.
        """
        raise NotImplementedError(f"set_low_level is not implemented for {type(self).__name__}")

    # --- Optional: waveform-specific ---

    def set_square_duty_cycle(self, channel: Channel, duty_pct: float) -> None:
        """Set the duty cycle (%) for a square waveform on channel."""
        raise NotImplementedError(f"set_square_duty_cycle is not implemented for {type(self).__name__}")

    def set_ramp_symmetry(self, channel: Channel, symmetry_pct: float) -> None:
        """Set the symmetry (%) for a ramp waveform on channel."""
        raise NotImplementedError(f"set_ramp_symmetry is not implemented for {type(self).__name__}")

    def set_pulse_width(self, channel: Channel, width_s: float) -> None:
        """Set the pulse width (seconds) for a pulse waveform on channel.

        Use ``set_std_frequency`` to set the repetition rate and ``set_pulse_width`` to set
        the high-duration. Duty cycle is derivable from these two and is not part of the surface.
        """
        raise NotImplementedError(f"set_pulse_width is not implemented for {type(self).__name__}")

    # --- Non-LTI / composite waveforms (add method groups here in later milestones) ---


_UNSET = object()  # sentinel for optional configure_std_channel params


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

    def channel(self, n: int) -> Channel:
        """Validate ``n`` is a valid channel number and return the corresponding Channel enum.

        Raises:
            ValueError: If ``n`` is outside the range 1–num_channels.
        """
        if n < 1 or n > self._num_channels:
            raise ValueError(f"Channel {n} is out of range for '{self.name}' (1–{self._num_channels})")
        return Channel(n)

    def check_errors(self) -> None:
        """Query the instrument error queue and raise on any non-zero error code."""
        with self._resource_lock:
            self._driver.check_errors()

    @publish_command
    def _execute_command(
        self,
        driver_method: Callable,
        channel: Channel,
        value: float | bool | str,
        channel_suffix: str,
        **kwargs,
    ) -> Command:
        """General-purpose command helper: call ``driver_method(channel, value)``, timestamp, and package.

        Covers the common two-argument driver pattern ``method(channel, value)``.
        Methods with extra parameters (e.g. ``set_std_amplitude``) handle their own
        driver calls directly.
        """
        with self._resource_lock:
            driver_method(channel, value)
            timestamp = time.time_ns()
        descriptor = f"ch{channel.value}.{channel_suffix}.cmd"
        return self._package_command(descriptor, value, timestamp, **kwargs)

    @publish_command
    def set_std_waveform(self, channel: Channel, waveform: WaveformType, **kwargs) -> Command:
        """Set the waveform type on channel."""
        with self._resource_lock:
            self._driver.set_std_waveform(channel=channel, waveform=waveform)
            timestamp = time.time_ns()
        descriptor = f"ch{channel.value}.waveform.cmd"
        return self._package_command(descriptor, waveform.value, timestamp, **kwargs)

    def set_std_frequency(self, channel: Channel, frequency_hz: float, **kwargs) -> Command:
        """Set the output frequency (Hz) on channel."""
        return self._execute_command(self._driver.set_std_frequency, channel, frequency_hz, "frequency", **kwargs)

    @publish_command
    def set_std_amplitude(self, channel: Channel, amplitude: float, unit: VoltageUnit, **kwargs) -> Command:
        """Set the output amplitude on channel."""
        with self._resource_lock:
            self._driver.set_std_amplitude(channel=channel, amplitude=amplitude, unit=unit)
            timestamp = time.time_ns()
        descriptor = f"ch{channel.value}.amplitude.cmd"
        return self._package_command(descriptor, amplitude, timestamp, **kwargs)

    def set_std_offset(self, channel: Channel, offset_v: float, **kwargs) -> Command:
        """Set the DC offset (volts) on channel."""
        return self._execute_command(self._driver.set_std_offset, channel, offset_v, "offset", **kwargs)

    def output_enable(self, channel: Channel, enable: bool, **kwargs) -> Command:
        """Enable or disable the output on channel."""
        return self._execute_command(self._driver.output_enable, channel, enable, "enabled", **kwargs)

    @publish_command
    def set_std_output_load(self, channel: Channel, load: float | None, **kwargs) -> Command:
        """Set the output load impedance; None means high-Z."""
        with self._resource_lock:
            self._driver.set_std_output_load(channel=channel, load=load)
            timestamp = time.time_ns()
        descriptor = f"ch{channel.value}.load.cmd"
        # _package_command requires float|str; represent high-Z as "INF"
        load_value = "INF" if load is None else load
        return self._package_command(descriptor, load_value, timestamp, **kwargs)

    def get_std_waveform(self, channel: Channel) -> WaveformType:
        """Read back the current waveform type on channel.

        Intentionally not decorated with ``@publish_measurement`` — WaveformType is
        non-numeric and cannot be represented as a float channel value.
        """
        with self._resource_lock:
            return self._driver.get_std_waveform(channel=channel)

    def get_std_amplitude(self, channel: Channel) -> tuple[float, VoltageUnit]:
        """Read back the current amplitude and voltage unit on channel.

        Intentionally not decorated with ``@publish_measurement`` — the return includes
        a non-numeric VoltageUnit. Publish the float component separately if needed.
        """
        with self._resource_lock:
            return self._driver.get_std_amplitude(channel=channel)

    @publish_measurement
    def get_std_frequency(self, channel: Channel, **kwargs) -> Measurement | None:
        """Read back the current output frequency (Hz) on channel."""
        with self._resource_lock:
            val = self._driver.get_std_frequency(channel=channel)
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

    @publish_measurement
    def get_phase(self, channel: Channel, **kwargs) -> Measurement | None:
        """Read back the current phase (degrees) on channel."""
        with self._resource_lock:
            val = self._driver.get_phase(channel=channel)
            timestamp = time.time_ns()
        descriptor = f"ch{channel.value}.phase"
        return self._package_measurement(descriptor, val, timestamp, **kwargs)

    @publish_measurement
    def get_std_offset(self, channel: Channel, **kwargs) -> Measurement | None:
        """Read back the DC offset (volts) on channel."""
        with self._resource_lock:
            val = self._driver.get_std_offset(channel=channel)
            timestamp = time.time_ns()
        descriptor = f"ch{channel.value}.offset"
        return self._package_measurement(descriptor, val, timestamp, **kwargs)

    @publish_measurement
    def get_std_output_load(self, channel: Channel, **kwargs) -> Measurement | None:
        """Read back the output load impedance on channel.

        High-Z is published as ``float('inf')`` so the state is always visible in the data stream.
        """
        with self._resource_lock:
            val = self._driver.get_std_output_load(channel=channel)
            timestamp = time.time_ns()
        load_float = float("inf") if val is None else val
        descriptor = f"ch{channel.value}.load"
        return self._package_measurement(descriptor, load_float, timestamp, **kwargs)

    def configure_std_channel(
        self,
        channel: Channel,
        waveform: WaveformType,
        frequency_hz: float,
        amplitude: float,
        unit: VoltageUnit,
        offset_v: float = 0.0,
        *,
        load: float | None = _UNSET,  # type: ignore[assignment]
        enable: bool | None = None,
        phase_deg: float | None = None,
        **kwargs,
    ) -> list[Command]:
        """Configure standard waveform parameters on channel in one call.

        Args:
            channel: Target output channel.
            waveform: Waveform function to output.
            frequency_hz: Output frequency in Hz.
            amplitude: Output amplitude in the given unit.
            unit: Voltage unit for the amplitude value.
            offset_v: DC offset in volts (default 0.0).
            load: Output load impedance in ohms, or None for high-Z. Omit to leave unchanged.
            enable: True to enable output, False to disable. Omit to leave unchanged.
            phase_deg: Phase in degrees. Omit to leave unchanged.
        """
        cmds: list[Command] = [
            self.set_std_waveform(channel, waveform, **kwargs),
            self.set_std_frequency(channel, frequency_hz, **kwargs),
            self.set_std_amplitude(channel, amplitude, unit, **kwargs),
            self.set_std_offset(channel, offset_v, **kwargs),
        ]
        if load is not _UNSET:
            cmds.append(self.set_std_output_load(channel, load, **kwargs))  # type: ignore[arg-type]
        if enable is not None:
            cmds.append(self.output_enable(channel, enable, **kwargs))
        if phase_deg is not None:
            cmds.append(self.set_phase(channel, phase_deg, **kwargs))
        return cmds

    @publish_command
    def set_voltage_unit(self, channel: Channel, unit: VoltageUnit, **kwargs) -> Command:
        """Set the voltage unit on channel."""
        with self._resource_lock:
            self._driver.set_voltage_unit(channel=channel, unit=unit)
            timestamp = time.time_ns()
        descriptor = f"ch{channel.value}.voltage_unit.cmd"
        return self._package_command(descriptor, unit.value, timestamp, **kwargs)

    def get_voltage_unit(self, channel: Channel) -> VoltageUnit:
        """Read back the current voltage unit on channel.

        Intentionally not decorated with ``@publish_measurement`` — VoltageUnit is
        non-numeric and cannot be represented as a float channel value.
        """
        with self._resource_lock:
            return self._driver.get_voltage_unit(channel=channel)

    def set_high_level(self, channel: Channel, volts: float, **kwargs) -> Command:
        """Set the high voltage level (volts) on channel."""
        return self._execute_command(self._driver.set_high_level, channel, volts, "high_level", **kwargs)

    def set_low_level(self, channel: Channel, volts: float, **kwargs) -> Command:
        """Set the low voltage level (volts) on channel."""
        return self._execute_command(self._driver.set_low_level, channel, volts, "low_level", **kwargs)

    def set_phase(self, channel: Channel, phase_deg: float, **kwargs) -> Command:
        """Set the phase (degrees) on channel."""
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

    def set_square_duty_cycle(self, channel: Channel, duty_pct: float, **kwargs) -> Command:
        """Set the duty cycle (%) for a square waveform on channel."""
        return self._execute_command(
            self._driver.set_square_duty_cycle, channel, duty_pct, "square.duty_cycle", **kwargs
        )

    def set_ramp_symmetry(self, channel: Channel, symmetry_pct: float, **kwargs) -> Command:
        """Set the symmetry (%) for a ramp waveform on channel."""
        return self._execute_command(self._driver.set_ramp_symmetry, channel, symmetry_pct, "ramp.symmetry", **kwargs)

    def set_pulse_width(self, channel: Channel, width_s: float, **kwargs) -> Command:
        """Set the pulse width (seconds) on channel."""
        return self._execute_command(self._driver.set_pulse_width, channel, width_s, "pulse.width", **kwargs)
