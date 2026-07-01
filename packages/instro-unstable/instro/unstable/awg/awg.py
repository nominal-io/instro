"""AWG instrument driver contract and Instro AWG interface."""

from __future__ import annotations

import abc
import logging
import threading
import time
from typing import Callable

from instro.lib.instrument import Instrument, publish_command, publish_measurement
from instro.lib.publishers import Publisher
from instro.lib.types import Command, Measurement
from instro.unstable.awg.types import VoltageUnit, WaveformType

logger = logging.getLogger(__name__)


class AWGDriverBase(abc.ABC):
    """Vendor AWG driver contract. Concrete drivers own their transport and lifecycle.

    All methods here apply to standard waveforms. Solely composite/arbitrary waveforms are
    delineated by __arb__ in the method name.
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
    
    def apply_waveform(self, channel: int, waveform: WaveformType, waveform_data: list[int], disable_output: bool = False) -> None:
        """Apply a waveform to a channel.

        Only applies to the following Enums:

        - ARB (waveform_data is the uploaded sample array; per the programming manual,
          this is the ``:TRACe``/``:DATA`` + ``:FUNCtion ARB`` upload-and-select path)

        Args:
            channel: Target output channel.
            waveform: The waveform to apply.
            waveform_data: The waveform data to apply.
            disable_output: Whether to disable the output during waveform application."""
        raise NotImplementedError(f"apply_waveform is not implemented for {type(self).__name__}")

    # --- Standard periodic waveforms ---

    @abc.abstractmethod
    def set_std_waveform(self, channel: int, waveform: WaveformType) -> None:
        """Set the waveform function on channel. Applies to all Enums — this is the
        ``:FUNCtion`` selector itself, so it is valid regardless of which waveform is chosen:

        - SINE
        - SQUARE
        - RAMP
        - PULSE
        - NOISE
        - DC
        - ARB
        """

    @abc.abstractmethod
    def get_std_waveform(self, channel: int) -> WaveformType:
        """Get the current waveform function on channel. Applies to all Enums (see
        ``set_std_waveform``).
        """

    @abc.abstractmethod
    def set_std_frequency(self, channel: int, frequency: float) -> None:
        """Set the output frequency (Hz) on channel. Only applies to the following Enums:

        - SINE
        - SQUARE
        - RAMP
        - PULSE
        - ARB (sets the arb waveform's repetition rate; equivalent to ``points / sample_rate``
          per the ``:FUNCtion:ARBitrary:SRATe`` relationship in the programming manual)
        """

    @abc.abstractmethod
    def get_std_frequency(self, channel: int) -> float:
        """Get the output frequency (Hz) on channel. Only applies to the following Enums:

        - SINE
        - SQUARE
        - RAMP
        - PULSE
        - ARB
        """

    @abc.abstractmethod
    def set_std_amplitude(self, channel: int, amplitude: float, unit: VoltageUnit) -> None:
        """Set the output amplitude on channel. Only applies to the following Enums:

        - SINE
        - SQUARE
        - RAMP
        - PULSE
        - NOISE
        - ARB
        """

    @abc.abstractmethod
    def get_std_amplitude(self, channel: int) -> tuple[float, VoltageUnit]:
        """Get the current output amplitude and voltage unit on channel. Only applies to the
        following Enums:

        - SINE
        - SQUARE
        - RAMP
        - PULSE
        - NOISE
        - ARB
        """

    @abc.abstractmethod
    def set_std_offset(self, channel: int, offset: float) -> None:
        """Set the DC offset (volts) on channel. Applies to all Enums — for DC, this
        offset value is the output level itself:

        - SINE
        - SQUARE
        - RAMP
        - PULSE
        - NOISE
        - DC
        - ARB
        """

    @abc.abstractmethod
    def get_std_offset(self, channel: int) -> float:
        """Get the DC offset (volts) on channel. Applies to all Enums (see ``set_std_offset``)."""

    @abc.abstractmethod
    def output_enable(self, channel: int, enable: bool) -> None:
        """Enable or disable the output on channel. Applies to all Enums — this is a
        channel-level output-stage setting, independent of which waveform is selected.
        """

    @abc.abstractmethod
    def get_output_state(self, channel: int) -> bool:
        """Return True if the output on channel is enabled. Applies to all Enums (see
        ``output_enable``).
        """

    @abc.abstractmethod
    def set_std_output_load(self, channel: int, load: float | None) -> None:
        """Set the output load impedance; None means high-Z. Applies to all Enums — like
        ``output_enable``, this is a channel-level output-stage setting independent of the
        selected waveform.
        """

    @abc.abstractmethod
    def get_std_output_load(self, channel: int) -> float | None:
        """Get the output load impedance; None means high-Z. Applies to all Enums (see
        ``set_std_output_load``).
        """

    @abc.abstractmethod
    def set_phase(self, channel: int, phase_deg: float) -> None:
        """Set the phase (degrees) for a channel. Only applies to the following Enums:

        - SINE
        - SQUARE
        - RAMP
        - PULSE
        - ARB
        """

    @abc.abstractmethod
    def get_phase(self, channel: int) -> float:
        """Get the current phase (degrees) for a channel. Only applies to the following Enums:

        - SINE
        - SQUARE
        - RAMP
        - PULSE
        - ARB
        """

    def align_phase(self) -> None:
        """Sync the phase of both channels. Only applies when both channels are set to one of
        the following Enums:

        - SINE
        - SQUARE
        - RAMP
        - PULSE
        - ARB
        """
        raise NotImplementedError(f"align_phase is not implemented for {type(self).__name__}")

    # --- Optional: standalone voltage unit ---

    def set_voltage_unit(self, channel: int, unit: VoltageUnit) -> None:
        """Set the voltage representation unit for a channel without changing the amplitude value.

        Only applies to the following Enums (same restriction as ``set_std_amplitude``,
        since the unit qualifies the amplitude value):

        - SINE
        - SQUARE
        - RAMP
        - PULSE
        - NOISE
        - ARB
        """
        raise NotImplementedError(f"set_voltage_unit is not implemented for {type(self).__name__}")

    def get_voltage_unit(self, channel: int) -> VoltageUnit:
        """Get the current voltage representation unit for a channel. Only applies to the
        following Enums (see ``set_voltage_unit``):

        - SINE
        - SQUARE
        - RAMP
        - PULSE
        - NOISE
        - ARB
        """
        raise NotImplementedError(f"get_voltage_unit is not implemented for {type(self).__name__}")

    # --- Optional: high/low level (alternative to amplitude + offset) ---

    def set_high_level(self, channel: int, volts: float) -> None:
        """Set the high voltage level for a channel.

        Only applies to the following Enums (same restriction as amplitude + offset, since
        high/low level is just another representation of that pair):

        - SINE
        - SQUARE
        - RAMP
        - PULSE
        - NOISE
        - ARB
        """
        raise NotImplementedError(f"set_high_level is not implemented for {type(self).__name__}")

    def set_low_level(self, channel: int, volts: float) -> None:
        """Set the low voltage level for a channel.

        Only applies to the following Enums (see ``set_high_level``):

        - SINE
        - SQUARE
        - RAMP
        - PULSE
        - NOISE
        - ARB
        """
        raise NotImplementedError(f"set_low_level is not implemented for {type(self).__name__}")

    # --- Optional: waveform-specific ---

    def set_square_duty_cycle(self, channel: int, duty_pct: float) -> None:
        """Set the duty cycle (%) for a square waveform on channel. Only applies to:

        - SQUARE
        """
        raise NotImplementedError(f"set_square_duty_cycle is not implemented for {type(self).__name__}")

    def set_ramp_symmetry(self, channel: int, symmetry_pct: float) -> None:
        """Set the symmetry (%) for a ramp waveform on channel. Only applies to:

        - RAMP
        """
        raise NotImplementedError(f"set_ramp_symmetry is not implemented for {type(self).__name__}")

    def set_pulse_width(self, channel: int, width_s: float) -> None:
        """Set the pulse width (seconds) for a pulse waveform on channel. Only applies to:

        - PULSE
        """
        raise NotImplementedError(f"set_pulse_width is not implemented for {type(self).__name__}")

    # --- Composite waveforms (add method groups here in later milestones) ---


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

    def channel(self, n: int) -> int:
        """Validate ``n`` is a valid channel number.

        Raises:
            ValueError: If ``n`` is outside the range 1–num_channels.
        """
        if n < 1 or n > self._num_channels:
            raise ValueError(f"Channel {n} is out of range for '{self.name}' (1–{self._num_channels})")
        return n

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
        """General-purpose command helper: call ``driver_method(channel, value)``, timestamp, and package.

        Covers the common two-argument driver pattern ``method(channel, value)``.
        Methods with extra parameters (e.g. ``set_std_amplitude``) handle their own
        driver calls directly.
        """
        with self._resource_lock:
            driver_method(channel, value)
            timestamp = time.time_ns()
        descriptor = f"ch{channel}.{channel_suffix}.cmd"
        return self._package_command(descriptor, value, timestamp, **kwargs)

    @publish_command
    def set_std_waveform(self, channel: int, waveform: WaveformType, **kwargs) -> Command:
        """Set the waveform type on channel. Applies to all Enums (SINE, SQUARE, RAMP, PULSE,
        NOISE, DC, ARB) — see ``AWGDriverBase.set_std_waveform``.
        """
        with self._resource_lock:
            self._driver.set_std_waveform(channel=channel, waveform=waveform)
            timestamp = time.time_ns()
        descriptor = f"ch{channel}.waveform.cmd"
        return self._package_command(descriptor, waveform.value, timestamp, **kwargs)

    def set_std_frequency(self, channel: int, frequency_hz: float, **kwargs) -> Command:
        """Set the output frequency (Hz) on channel. Applies to SINE, SQUARE, RAMP, PULSE,
        ARB — not NOISE or DC. See ``AWGDriverBase.set_std_frequency``.
        """
        return self._execute_command(self._driver.set_std_frequency, channel, frequency_hz, "frequency", **kwargs)

    @publish_command
    def set_std_amplitude(self, channel: int, amplitude: float, unit: VoltageUnit, **kwargs) -> Command:
        """Set the output amplitude on channel. Applies to SINE, SQUARE, RAMP, PULSE, NOISE,
        ARB — not DC. See ``AWGDriverBase.set_std_amplitude``.
        """
        with self._resource_lock:
            self._driver.set_std_amplitude(channel=channel, amplitude=amplitude, unit=unit)
            timestamp = time.time_ns()
        descriptor = f"ch{channel}.amplitude.cmd"
        return self._package_command(descriptor, amplitude, timestamp, **kwargs)

    def set_std_offset(self, channel: int, offset_v: float, **kwargs) -> Command:
        """Set the DC offset (volts) on channel. Applies to all Enums — for DC, this is the
        output level itself. See ``AWGDriverBase.set_std_offset``.
        """
        return self._execute_command(self._driver.set_std_offset, channel, offset_v, "offset", **kwargs)

    def output_enable(self, channel: int, enable: bool, **kwargs) -> Command:
        """Enable or disable the output on channel. Applies to all Enums — channel-level
        output stage, independent of waveform.
        """
        return self._execute_command(self._driver.output_enable, channel, enable, "enabled", **kwargs)

    @publish_command
    def set_std_output_load(self, channel: int, load: float | None, **kwargs) -> Command:
        """Set the output load impedance; None means high-Z. Applies to all Enums — channel-level
        output stage, independent of waveform.
        """
        with self._resource_lock:
            self._driver.set_std_output_load(channel=channel, load=load)
            timestamp = time.time_ns()
        descriptor = f"ch{channel}.load.cmd"
        load_value = "INF" if load is None else load
        return self._package_command(descriptor, load_value, timestamp, **kwargs)

    def get_std_waveform(self, channel: int) -> WaveformType:
        """Read back the current waveform type on channel. Applies to all Enums.

        Intentionally not decorated with ``@publish_measurement`` — WaveformType is
        non-numeric and cannot be represented as a float channel value.
        """
        with self._resource_lock:
            return self._driver.get_std_waveform(channel=channel)

    def get_std_amplitude(self, channel: int) -> tuple[float, VoltageUnit]:
        """Read back the current amplitude and voltage unit on channel. Applies to SINE,
        SQUARE, RAMP, PULSE, NOISE, ARB — not DC.

        Intentionally not decorated with ``@publish_measurement`` — the return includes
        a non-numeric VoltageUnit. Publish the float component separately if needed.
        """
        with self._resource_lock:
            return self._driver.get_std_amplitude(channel=channel)

    @publish_measurement
    def get_std_frequency(self, channel: int, **kwargs) -> Measurement | None:
        """Read back the current output frequency (Hz) on channel. Applies to SINE, SQUARE,
        RAMP, PULSE, ARB — not NOISE or DC.
        """
        with self._resource_lock:
            val = self._driver.get_std_frequency(channel=channel)
            timestamp = time.time_ns()
        descriptor = f"ch{channel}.frequency"
        return self._package_measurement(descriptor, val, timestamp, **kwargs)

    @publish_measurement
    def get_output_state(self, channel: int, **kwargs) -> Measurement | None:
        """Read back whether the output is enabled on channel. Applies to all Enums."""
        with self._resource_lock:
            val = self._driver.get_output_state(channel=channel)
            timestamp = time.time_ns()
        descriptor = f"ch{channel}.enabled"
        return self._package_measurement(descriptor, val, timestamp, **kwargs)

    @publish_measurement
    def get_phase(self, channel: int, **kwargs) -> Measurement | None:
        """Read back the current phase (degrees) on channel. Applies to SINE, SQUARE, RAMP,
        PULSE, ARB — not NOISE or DC.
        """
        with self._resource_lock:
            val = self._driver.get_phase(channel=channel)
            timestamp = time.time_ns()
        descriptor = f"ch{channel}.phase"
        return self._package_measurement(descriptor, val, timestamp, **kwargs)

    @publish_measurement
    def get_std_offset(self, channel: int, **kwargs) -> Measurement | None:
        """Read back the DC offset (volts) on channel. Applies to all Enums."""
        with self._resource_lock:
            val = self._driver.get_std_offset(channel=channel)
            timestamp = time.time_ns()
        descriptor = f"ch{channel}.offset"
        return self._package_measurement(descriptor, val, timestamp, **kwargs)

    @publish_measurement
    def get_std_output_load(self, channel: int, **kwargs) -> Measurement | None:
        """Read back the output load impedance on channel. Applies to all Enums.

        High-Z is published as ``float('inf')`` so the state is always visible in the data stream.
        """
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
        load: float | None = _UNSET,  # type: ignore[assignment]
        enable: bool | None = None,
        phase_deg: float | None = None,
        **kwargs,
    ) -> list[Command]:
        """Configure standard waveform parameters on channel in one call.

        Note: ``amplitude``/``unit`` and ``phase_deg`` are meaningless for DC and are still
        sent — see ``AWGDriverBase.set_std_amplitude``/``set_phase`` for the per-waveform
        restrictions this call doesn't check for you.

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
    def set_voltage_unit(self, channel: int, unit: VoltageUnit, **kwargs) -> Command:
        """Set the voltage unit on channel. Applies to SINE, SQUARE, RAMP, PULSE, NOISE,
        ARB — not DC.
        """
        with self._resource_lock:
            self._driver.set_voltage_unit(channel=channel, unit=unit)
            timestamp = time.time_ns()
        descriptor = f"ch{channel}.voltage_unit.cmd"
        return self._package_command(descriptor, unit.value, timestamp, **kwargs)

    def get_voltage_unit(self, channel: int) -> VoltageUnit:
        """Read back the current voltage unit on channel. Applies to SINE, SQUARE, RAMP,
        PULSE, NOISE, ARB — not DC.

        Intentionally not decorated with ``@publish_measurement`` — VoltageUnit is
        non-numeric and cannot be represented as a float channel value.
        """
        with self._resource_lock:
            return self._driver.get_voltage_unit(channel=channel)

    def set_high_level(self, channel: int, volts: float, **kwargs) -> Command:
        """Set the high voltage level (volts) on channel. Applies to SINE, SQUARE, RAMP,
        PULSE, NOISE, ARB — not DC.
        """
        return self._execute_command(self._driver.set_high_level, channel, volts, "high_level", **kwargs)

    def set_low_level(self, channel: int, volts: float, **kwargs) -> Command:
        """Set the low voltage level (volts) on channel. Applies to SINE, SQUARE, RAMP,
        PULSE, NOISE, ARB — not DC.
        """
        return self._execute_command(self._driver.set_low_level, channel, volts, "low_level", **kwargs)

    def set_phase(self, channel: int, phase_deg: float, **kwargs) -> Command:
        """Set the phase (degrees) on channel. Applies to SINE, SQUARE, RAMP, PULSE, ARB —
        not NOISE or DC.
        """
        return self._execute_command(self._driver.set_phase, channel, phase_deg, "phase", **kwargs)

    @publish_command
    def align_phase(self, **kwargs) -> Command:
        """Sync the phase of both channels. Only meaningful when both channels are SINE,
        SQUARE, RAMP, PULSE, or ARB — not NOISE or DC.
        """
        with self._resource_lock:
            self._driver.align_phase()
            timestamp = time.time_ns()
        descriptor = "phase.align.cmd"
        return self._package_command(descriptor, "ALIGN", timestamp, **kwargs)

    # --- Waveform-specific ---

    def set_square_duty_cycle(self, channel: int, duty_pct: float, **kwargs) -> Command:
        """Set the duty cycle (%) for a square waveform on channel. Only applies to SQUARE."""
        return self._execute_command(
            self._driver.set_square_duty_cycle, channel, duty_pct, "square.duty_cycle", **kwargs
        )

    def set_ramp_symmetry(self, channel: int, symmetry_pct: float, **kwargs) -> Command:
        """Set the symmetry (%) for a ramp waveform on channel. Only applies to RAMP."""
        return self._execute_command(self._driver.set_ramp_symmetry, channel, symmetry_pct, "ramp.symmetry", **kwargs)

    def set_pulse_width(self, channel: int, width_s: float, **kwargs) -> Command:
        """Set the pulse width (seconds) on channel. Only applies to PULSE."""
        return self._execute_command(self._driver.set_pulse_width, channel, width_s, "pulse.width", **kwargs)
