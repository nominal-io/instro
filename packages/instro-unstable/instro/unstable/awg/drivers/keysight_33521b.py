"""Keysight 33521B arbitrary waveform generator driver (33500 series)."""

from __future__ import annotations

import logging
import sys

from instro.lib.transports.visa import VisaConfig, VisaDriver
from instro.unstable.awg.awg import AWGDriverBase
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
)

_HIGH_Z_SENTINEL = 9.9e37

_ARB_NAME = "INSTRO_ARB"
_ARB_MIN_POINTS = 8
_ARB_MAX_POINTS = 65536

_SAWTOOTH_SYMMETRY_PCT = 100

_MOD_INTERNAL_FUNCTIONS: dict[type, str] = {
    Sine: "SIN",
    Square: "SQU",
    Sawtooth: "RAMP",
    Triangle: "TRI",
}
_MOD_SCPI_PREFIX: dict[ModulationType, str] = {
    ModulationType.AM: "AM",
    ModulationType.FM: "FM",
    ModulationType.PM: "PM",
    ModulationType.PWM: "PWM",
    ModulationType.FSK: "FSK",
    ModulationType.PSK: "BPSK",
}
_MOD_MAGNITUDE_NODE: dict[ModulationType, str] = {
    ModulationType.AM: "DEPT",
    ModulationType.FM: "DEV",
    ModulationType.PM: "DEV",
    ModulationType.PWM: "DEV",
}
logger = logging.getLogger(__name__)

_FUNC_NAME_TO_CARRIER_TYPE: dict[str, type] = {
    "SIN": Sine,
    "SQU": Square,
    "RAMP": Sawtooth,
    "TRI": Triangle,
    "PULS": Pulse,
    "DC": StaticValue,
    "ARB": Arbitrary,
}

_BURST_MODES: dict[BurstType, str] = {
    BurstType.NCYCLE: "TRIG",
    BurstType.GATED: "GAT",
}
_BURST_TYPES: dict[str, BurstType] = {mode: burst_type for burst_type, mode in _BURST_MODES.items()}

_BURST_TRIGGER_SOURCES: dict[BurstTriggerSource, str] = {
    BurstTriggerSource.INTERNAL: "IMM",
    BurstTriggerSource.EXTERNAL: "EXT",
    BurstTriggerSource.MANUAL: "BUS",
}
_BURST_TRIGGER_SOURCE_TYPES: dict[str, BurstTriggerSource] = {
    token: source for source, token in _BURST_TRIGGER_SOURCES.items()
}

_SWEEP_SPACING: dict[SweepType, str] = {
    SweepType.LINEAR: "LIN",
    SweepType.LOG: "LOG",
}
_SWEEP_SPACING_TYPES: dict[str, SweepType] = {spacing: sweep_type for sweep_type, spacing in _SWEEP_SPACING.items()}

_SWEEP_TRIGGER_SOURCES: dict[SweepTriggerSource, str] = {
    SweepTriggerSource.INTERNAL: "IMM",
    SweepTriggerSource.EXTERNAL: "EXT",
    SweepTriggerSource.MANUAL: "BUS",
}
_SWEEP_TRIGGER_SOURCE_TYPES: dict[str, SweepTriggerSource] = {
    token: source for source, token in _SWEEP_TRIGGER_SOURCES.items()
}


class Keysight33521B(AWGDriverBase):
    """SCPI driver for the Keysight 33521B arbitrary waveform generator."""

    def __init__(self, visa_resource: str | VisaConfig) -> None:
        self._visa = VisaDriver(visa_resource)
        self._arb_waveforms: dict[int, Arbitrary] = {}
        self._last_modulation_type: ModulationType | None = None

    def open(self) -> None:
        self._visa.open()

    def close(self) -> None:
        self._visa.close()

    def set_waveform(self, channel: int, waveform: Waveform) -> None:
        _check_channel(channel)
        with self._visa.lock():
            if isinstance(waveform, Sine):
                self._visa.write("FUNC SIN")
                self._write_frequency_and_phase(waveform.frequency_hz, waveform.phase_deg)
            elif isinstance(waveform, Square):
                self._visa.write("FUNC SQU")
                self._write_frequency_and_phase(waveform.frequency_hz, waveform.phase_deg)
                self._visa.write(f"FUNC:SQU:DCYC {waveform.duty_cycle_pct}")
            elif isinstance(waveform, Sawtooth):
                self._visa.write("FUNC RAMP")
                self._write_frequency_and_phase(waveform.frequency_hz, waveform.phase_deg)
                self._visa.write(f"FUNC:RAMP:SYMM {_SAWTOOTH_SYMMETRY_PCT}")
            elif isinstance(waveform, Triangle):
                self._visa.write("FUNC TRI")
                self._write_frequency_and_phase(waveform.frequency_hz, waveform.phase_deg)
            elif isinstance(waveform, Pulse):
                self._visa.write("FUNC PULS")
                phase_deg = waveform.delay_s * waveform.frequency_hz * 360.0
                self._write_frequency_and_phase(waveform.frequency_hz, phase_deg)
                self._visa.write(f"FUNC:PULS:WIDT {waveform.width_s}")
            elif isinstance(waveform, Arbitrary):
                num_points = len(waveform.samples)
                if not _ARB_MIN_POINTS <= num_points <= _ARB_MAX_POINTS:
                    raise ValueError(
                        f"the Keysight 33521B accepts {_ARB_MIN_POINTS} to {_ARB_MAX_POINTS} arbitrary points"
                        f" per download, got {num_points}"
                    )
                samples_csv = ",".join(str(sample) for sample in waveform.samples)
                self._visa.write(f"DATA:ARB {_ARB_NAME}, {samples_csv}")
                self._visa.write(f"FUNC:ARB:SRAT {waveform.sample_rate_hz}")
                self._visa.write(f"FUNC:ARB {_ARB_NAME}")
                self._visa.write("FUNC ARB")
                self._check_errors()
                self._arb_waveforms[channel] = waveform
            elif isinstance(waveform, StaticValue):
                self._visa.write("FUNC DC")
                self._visa.write(f"VOLT:OFFS {waveform.value}")
            else:
                raise ValueError(f"unsupported waveform definition {type(waveform).__name__}")
            self._check_errors()

    def get_waveform(self, channel: int) -> Waveform:
        _check_channel(channel)
        with self._visa.lock():
            name = self._visa.query("FUNC?").strip()
            if name == "SIN":
                frequency = float(self._visa.query("FREQ?"))
                phase = float(self._visa.query("PHAS?"))
                result: Waveform = Sine(frequency_hz=frequency, phase_deg=phase)
            elif name == "SQU":
                frequency = float(self._visa.query("FREQ?"))
                phase = float(self._visa.query("PHAS?"))
                duty = float(self._visa.query("FUNC:SQU:DCYC?"))
                result = Square(frequency_hz=frequency, duty_cycle_pct=duty, phase_deg=phase)
            elif name == "RAMP":
                frequency = float(self._visa.query("FREQ?"))
                phase = float(self._visa.query("PHAS?"))
                result = Sawtooth(frequency_hz=frequency, phase_deg=phase)
            elif name == "TRI":
                frequency = float(self._visa.query("FREQ?"))
                phase = float(self._visa.query("PHAS?"))
                result = Triangle(frequency_hz=frequency, phase_deg=phase)
            elif name == "PULS":
                frequency = float(self._visa.query("FREQ?"))
                phase = float(self._visa.query("PHAS?"))
                width = float(self._visa.query("FUNC:PULS:WIDT?"))
                delay = max(0.0, phase / 360.0 / frequency)
                result = Pulse(frequency_hz=frequency, width_s=width, delay_s=delay)
            elif name == "DC":
                offset = float(self._visa.query("VOLT:OFFS?"))
                result = StaticValue(value=offset)
            elif name == "ARB":
                arb = self._arb_waveforms.get(channel)
                if arb is None:
                    self._check_errors()
                    raise RuntimeError(
                        f"channel {channel} outputs an arbitrary waveform not programmed by this driver;"
                        " sample data is not readable"
                    )
                result = arb
            else:
                raise ValueError(f"Keysight 33521B reported unsupported waveform '{name}'")
            self._check_errors()
            return result

    def set_amplitude(self, channel: int, amplitude: float, unit: AmplitudeMeasurementUnit) -> None:
        _check_channel(channel)
        if unit is AmplitudeMeasurementUnit.VP:
            raise ValueError("VP is not supported by Keysight 33521B")
        with self._visa.lock():
            self._visa.write(f"VOLT:UNIT {unit.value}")
            self._visa.write(f"VOLT {amplitude}")
            self._check_errors()

    def get_amplitude(self, channel: int) -> tuple[float, AmplitudeMeasurementUnit]:
        _check_channel(channel)
        with self._visa.lock():
            magnitude = float(self._visa.query("VOLT?"))
            unit = AmplitudeMeasurementUnit(self._visa.query("VOLT:UNIT?").strip())
            self._check_errors()
        return (magnitude, unit)

    def set_offset(self, channel: int, offset: float) -> None:
        _check_channel(channel)
        self._write_checked(f"VOLT:OFFS {offset}")

    def get_offset(self, channel: int) -> float:
        _check_channel(channel)
        with self._visa.lock():
            result = float(self._visa.query("VOLT:OFFS?"))
            self._check_errors()
        return result

    def output_enable(self, channel: int, enable: bool) -> None:
        _check_channel(channel)
        self._write_checked("OUTP ON" if enable else "OUTP OFF")

    def get_output_state(self, channel: int) -> bool:
        _check_channel(channel)
        with self._visa.lock():
            result = self._visa.query("OUTP?").strip() == "1"
            self._check_errors()
        return result

    def set_output_load(self, channel: int, load: float | None) -> None:
        _check_channel(channel)
        self._write_checked("OUTP:LOAD INF" if load is None else f"OUTP:LOAD {load}")

    def get_output_load(self, channel: int) -> float | None:
        _check_channel(channel)
        with self._visa.lock():
            load = float(self._visa.query("OUTP:LOAD?"))
            self._check_errors()
        return None if load >= _HIGH_Z_SENTINEL else load

    def set_modulation(self, channel: int, mod_type: ModulationType, shape: Waveform, magnitude: float) -> None:
        """Configures modulation. Enabled state is persistent across set_modulation calls."""
        _check_channel(channel)
        if not isinstance(mod_type, ModulationType):
            raise TypeError(f"mod_type must be a ModulationType, got {type(mod_type).__name__}")
        if mod_type is ModulationType.ASK:
            raise ValueError("ASK modulation is not supported by the Keysight 33521B")
        modulator = _validate_modulator(shape)
        prefix = _MOD_SCPI_PREFIX[mod_type]
        with self._visa.lock():
            # `shape` is the modulator/baseband signal; the carrier is the channel's currently active function.
            carrier_name = self._visa.query("FUNC?").strip()
            carrier_type = _FUNC_NAME_TO_CARRIER_TYPE.get(carrier_name)
            if carrier_type is None:
                raise ValueError(f"Keysight 33521B reported unsupported waveform '{carrier_name}'")
            _validate_carrier(mod_type, carrier_type)
            was_enabled = self.get_modulation_state(channel)
            if was_enabled:
                self.modulation_enable(channel, False)
            try:
                self._visa.write(f"{prefix}:SOUR INT")
                if mod_type in (ModulationType.AM, ModulationType.FM, ModulationType.PM, ModulationType.PWM):
                    function = _MOD_INTERNAL_FUNCTIONS[type(modulator)]
                    self._visa.write(f"{prefix}:INT:FUNC {function}")
                    self._visa.write(f"{prefix}:INT:FREQ {modulator.frequency_hz}")
                    self._visa.write(f"{prefix}:{_MOD_MAGNITUDE_NODE[mod_type]} {magnitude}")
                elif mod_type is ModulationType.FSK:
                    self._visa.write(f"{prefix}:INT:RATE {modulator.frequency_hz}")
                    self._visa.write(f"{prefix}:FREQ {magnitude}")
                elif mod_type is ModulationType.PSK:
                    self._visa.write(f"{prefix}:INT:RATE {modulator.frequency_hz}")
                    self._visa.write(f"{prefix}:PHAS {magnitude}")
                else:
                    raise AssertionError(f"unhandled ModulationType {mod_type}")
                self._check_errors()
            except Exception:
                if was_enabled:
                    logger.warning(
                        "channel %d: modulation was disabled to reconfigure it but the reconfigure failed;"
                        " modulation remains disabled",
                        channel,
                    )
                raise
            self._last_modulation_type = mod_type
            if was_enabled:
                self.modulation_enable(channel, True)

    def modulation_enable(self, channel: int, enable: bool) -> None:
        """Enables the most recently configured modulation type, or disables modulation."""
        _check_channel(channel)
        with self._visa.lock():
            if enable:
                mod_type = self.get_modulation_type(channel)
                prefix = _MOD_SCPI_PREFIX[mod_type]
                self._visa.write(f"{prefix}:STAT ON")
            else:
                for prefix in _MOD_SCPI_PREFIX.values():
                    self._visa.write(f"{prefix}:STAT OFF")
            self._check_errors()

    def get_modulation_type(self, channel: int) -> ModulationType:
        """Returns modulation type currently enabled, or the last type set by the user when modulation is not enabled."""
        _check_channel(channel)
        with self._visa.lock():
            if self._last_modulation_type is None:
                raise RuntimeError(
                    f"channel {channel} has no modulation type currently configured; set modulation first."
                )
            return self._last_modulation_type

    def get_modulation_state(self, channel: int) -> bool:
        _check_channel(channel)
        with self._visa.lock():
            result = any(self._visa.query(f"{prefix}:STAT?").strip() == "1" for prefix in _MOD_SCPI_PREFIX.values())
            self._check_errors()
        return result

    def set_burst(self, channel: int, burst_type: BurstType) -> None:
        _check_channel(channel)
        if not isinstance(burst_type, BurstType):
            raise TypeError(f"burst_type must be a BurstType, got {type(burst_type).__name__}")
        mode = _BURST_MODES[BurstType.NCYCLE] if burst_type is BurstType.INFINITE else _BURST_MODES.get(burst_type)
        if mode is None:
            raise ValueError(f"the Keysight 33521B does not support {burst_type.name} burst mode")
        with self._visa.lock():
            carrier_name = self._visa.query("FUNC?").strip()
            carrier_type = _FUNC_NAME_TO_CARRIER_TYPE.get(carrier_name)
            self._check_errors()
            if carrier_type is None:
                raise ValueError(f"Keysight 33521B reported unsupported waveform '{carrier_name}'")
            if carrier_type is StaticValue:
                raise ValueError(f"the Keysight 33521B cannot burst a StaticValue (DC) waveform on channel {channel}")
            self._visa.write(f"BURS:MODE {mode}")
            if burst_type is BurstType.INFINITE:
                self._visa.write("BURS:NCYC INF")
            self._check_errors()

    def get_burst_type(self, channel: int) -> BurstType:
        """NCYCLE reads back as INFINITE when BURS:NCYC is the hardware's high-water sentinel for INF."""
        _check_channel(channel)
        with self._visa.lock():
            mode = self._visa.query("BURS:MODE?").strip()
            ncycles_raw = self._visa.query("BURS:NCYC?") if mode == _BURST_MODES[BurstType.NCYCLE] else None
            self._check_errors()
        if mode not in _BURST_TYPES:
            raise ValueError(f"Keysight 33521B reported unsupported burst mode '{mode}'")
        if ncycles_raw is not None and float(ncycles_raw) >= _HIGH_Z_SENTINEL:
            return BurstType.INFINITE
        return _BURST_TYPES[mode]

    def burst_enable(self, channel: int, enable: bool) -> None:
        _check_channel(channel)
        self._write_checked(f"BURS:STAT {'ON' if enable else 'OFF'}")

    def get_burst_state(self, channel: int) -> bool:
        _check_channel(channel)
        with self._visa.lock():
            result = self._visa.query("BURS:STAT?").strip() == "1"
            self._check_errors()
        return result

    def set_burst_trigger(self, channel: int, source: BurstTriggerSource) -> None:
        _check_channel(channel)
        if not isinstance(source, BurstTriggerSource):
            raise TypeError(f"source must be a BurstTriggerSource, got {type(source).__name__}")
        self._write_checked(f"TRIG:SOUR {_BURST_TRIGGER_SOURCES[source]}")

    def get_burst_trigger(self, channel: int) -> BurstTriggerSource:
        _check_channel(channel)
        with self._visa.lock():
            token = self._visa.query("TRIG:SOUR?").strip()
            self._check_errors()
        if token not in _BURST_TRIGGER_SOURCE_TYPES:
            raise ValueError(f"Keysight 33521B reported unsupported trigger source '{token}'")
        return _BURST_TRIGGER_SOURCE_TYPES[token]

    def fire_burst_trigger(self, channel: int) -> None:
        _check_channel(channel)
        with self._visa.lock():
            if not self.get_burst_state(channel):
                raise ValueError(
                    f"Cannot fire a burst trigger on channel {channel} unless burst mode is already"
                    " enabled, call burst_enable(channel, True) first"
                )
            burst_type = self.get_burst_type(channel)
            if burst_type not in (BurstType.NCYCLE, BurstType.INFINITE):
                raise ValueError(
                    f"fire_burst_trigger fires a single N-cycle burst; channel {channel} is in {burst_type.name} mode"
                )
            source = self.get_burst_trigger(channel)
            if source is not BurstTriggerSource.MANUAL:
                raise ValueError(
                    f"Cannot fire a burst trigger on channel {channel} unless the trigger source is"
                    f" already MANUAL, call set_burst_trigger(channel, BurstTriggerSource.MANUAL) first. Current source: {source.name}"
                )
            self._write_checked("*TRG")

    def set_burst_delay(self, channel: int, delay_s: float) -> None:
        _check_channel(channel)
        if delay_s < 0:
            raise ValueError(f"delay_s must be non-negative, got {delay_s}")
        self._write_checked(f"TRIG:DEL {delay_s}")

    def get_burst_delay(self, channel: int) -> float:
        _check_channel(channel)
        with self._visa.lock():
            raw = self._visa.query("TRIG:DEL?")
            self._check_errors()
        return float(raw)

    def set_burst_gate_polarity(self, channel: int, gate_polarity: GatePolarity) -> None:
        _check_channel(channel)
        if not isinstance(gate_polarity, GatePolarity):
            raise TypeError(f"gate_polarity must be a GatePolarity, got {type(gate_polarity).__name__}")
        self._write_checked(f"BURS:GATE:POL {gate_polarity.value}")

    def get_burst_gate_polarity(self, channel: int) -> GatePolarity:
        _check_channel(channel)
        with self._visa.lock():
            raw = self._visa.query("BURS:GATE:POL?").strip()
            self._check_errors()
        return GatePolarity(raw)

    def set_burst_ncycles(self, channel: int, n_cycles: int) -> None:
        _check_channel(channel)
        if n_cycles <= 0:
            raise ValueError(f"n_cycles must be >= 1, got {n_cycles}")
        self._write_checked(f"BURS:NCYC {n_cycles}")

    def get_burst_ncycles(self, channel: int) -> int:
        _check_channel(channel)
        with self._visa.lock():
            raw = self._visa.query("BURS:NCYC?")
            self._check_errors()
        value = float(raw)
        return sys.maxsize if value >= _HIGH_Z_SENTINEL else int(value)

    def set_burst_period(self, channel: int, period: float) -> None:
        _check_channel(channel)
        if period <= 0:
            raise ValueError(f"period must be positive, got {period}")
        self._write_checked(f"BURS:INT:PER {period}")

    def get_burst_period(self, channel: int) -> float:
        _check_channel(channel)
        with self._visa.lock():
            raw = self._visa.query("BURS:INT:PER?")
            self._check_errors()
        return float(raw)

    def set_sweep(self, channel: int, sweep_type: SweepType) -> None:
        _check_channel(channel)
        if not isinstance(sweep_type, SweepType):
            raise TypeError(f"sweep_type must be a SweepType, got {type(sweep_type).__name__}")
        if sweep_type not in _SWEEP_SPACING:
            raise ValueError(f"the Keysight 33521B does not support {sweep_type.name} sweep spacing")
        with self._visa.lock():
            carrier_name = self._visa.query("FUNC?").strip()
            if carrier_name == "DC":
                raise ValueError(f"the Keysight 33521B cannot sweep a StaticValue (DC) waveform on channel {channel}")
            self._visa.write(f"SWE:SPAC {_SWEEP_SPACING[sweep_type]}")
            self._check_errors()

    def get_sweep_type(self, channel: int) -> SweepType:
        _check_channel(channel)
        with self._visa.lock():
            token = self._visa.query("SWE:SPAC?").strip()
            self._check_errors()
        if token not in _SWEEP_SPACING_TYPES:
            raise ValueError(f"Keysight 33521B reported unsupported sweep type '{token}'")
        return _SWEEP_SPACING_TYPES[token]

    def sweep_enable(self, channel: int, enable: bool) -> None:
        _check_channel(channel)
        self._write_checked(f"SWE:STAT {'ON' if enable else 'OFF'}")

    def get_sweep_state(self, channel: int) -> bool:
        _check_channel(channel)
        with self._visa.lock():
            result = self._visa.query("SWE:STAT?").strip() == "1"
            self._check_errors()
        return result

    def set_sweep_trigger(self, channel: int, source: SweepTriggerSource) -> None:
        _check_channel(channel)
        if not isinstance(source, SweepTriggerSource):
            raise TypeError(f"source must be a SweepTriggerSource, got {type(source).__name__}")
        self._write_checked(f"TRIG:SOUR {_SWEEP_TRIGGER_SOURCES[source]}")

    def get_sweep_trigger(self, channel: int) -> SweepTriggerSource:
        _check_channel(channel)
        with self._visa.lock():
            token = self._visa.query("TRIG:SOUR?").strip()
            self._check_errors()
        if token not in _SWEEP_TRIGGER_SOURCE_TYPES:
            raise ValueError(f"Keysight 33521B reported unsupported trigger source '{token}'")
        return _SWEEP_TRIGGER_SOURCE_TYPES[token]

    def set_sweep_start_freq(self, channel: int, frequency_hz: float) -> None:
        _check_channel(channel)
        self._write_checked(f"FREQ:STAR {frequency_hz}")

    def get_sweep_start_freq(self, channel: int) -> float:
        _check_channel(channel)
        with self._visa.lock():
            result = float(self._visa.query("FREQ:STAR?"))
            self._check_errors()
        return result

    def set_sweep_end_freq(self, channel: int, frequency_hz: float) -> None:
        _check_channel(channel)
        self._write_checked(f"FREQ:STOP {frequency_hz}")

    def get_sweep_end_freq(self, channel: int) -> float:
        _check_channel(channel)
        with self._visa.lock():
            result = float(self._visa.query("FREQ:STOP?"))
            self._check_errors()
        return result

    def set_sweep_time(self, channel: int, sweep_time: float) -> None:
        _check_channel(channel)
        if sweep_time <= 0:
            raise ValueError(f"sweep_time must be positive, got {sweep_time}")
        self._write_checked(f"SWE:TIME {sweep_time}")

    def get_sweep_time(self, channel: int) -> float:
        _check_channel(channel)
        with self._visa.lock():
            result = float(self._visa.query("SWE:TIME?"))
            self._check_errors()
        return result

    def set_sweep_hold_time(self, channel: int, hold_time: float) -> None:
        _check_channel(channel)
        if hold_time < 0:
            raise ValueError(f"hold_time must be non-negative, got {hold_time}")
        self._write_checked(f"SWE:HTIM {hold_time}")

    def get_sweep_hold_time(self, channel: int) -> float:
        _check_channel(channel)
        with self._visa.lock():
            result = float(self._visa.query("SWE:HTIM?"))
            self._check_errors()
        return result

    def set_sweep_return_time(self, channel: int, return_time: float) -> None:
        _check_channel(channel)
        if return_time < 0:
            raise ValueError(f"return_time must be non-negative, got {return_time}")
        self._write_checked(f"SWE:RTIM {return_time}")

    def get_sweep_return_time(self, channel: int) -> float:
        _check_channel(channel)
        with self._visa.lock():
            result = float(self._visa.query("SWE:RTIM?"))
            self._check_errors()
        return result

    def fire_sweep_trigger(self, channel: int) -> None:
        _check_channel(channel)
        with self._visa.lock():
            if not self.get_sweep_state(channel):
                raise ValueError(
                    f"Cannot fire a sweep trigger on channel {channel} unless sweep mode is already"
                    " enabled, call sweep_enable(channel, True) first"
                )
            source = self.get_sweep_trigger(channel)
            if source is not SweepTriggerSource.MANUAL:
                raise ValueError(
                    f"Cannot fire a sweep trigger on channel {channel} unless the trigger source is"
                    f" already MANUAL, call set_sweep_trigger(channel, SweepTriggerSource.MANUAL) first. Current source: {source.name}"
                )
            self._write_checked("*TRG")

    def _write_frequency_and_phase(self, frequency_hz: float, phase_deg: float) -> None:
        self._visa.write(f"FREQ {frequency_hz}")
        self._visa.write(f"PHAS {phase_deg % 360}")

    def _write_checked(self, command: str) -> None:
        with self._visa.lock():
            self._visa.write(command)
            self._check_errors()

    def _check_errors(self) -> None:
        """Query :SYSTem:ERRor? once and raise on a non-zero code. Does not drain the queue."""
        err = self._visa.query(":SYST:ERR?")
        code = err.strip().split(",", 1)[0].lstrip("+")
        if code != "0":
            raise RuntimeError(f"Keysight 33521B reported error: {err.strip()}")


def _check_channel(channel: int) -> None:
    if channel != 1:
        raise ValueError(f"Keysight 33521B only supports 1 channel, got {channel}")


def _validate_carrier(mod_type: ModulationType, carrier_type: type) -> None:
    """Validate that the channel's currently active carrier waveform type supports the given modulation type."""
    if carrier_type not in (Sine, Square, Sawtooth, Triangle, Pulse, Arbitrary):
        raise ValueError(
            f"the Keysight 33521B cannot apply {mod_type.name} modulation to a {carrier_type.__name__} carrier;"
            " carrier must be Sine, Square, Sawtooth, Triangle, Pulse, or Arbitrary"
        )
    if mod_type is ModulationType.PWM and carrier_type is not Pulse:
        raise ValueError(
            f"the Keysight 33521B can only apply PWM modulation to a Pulse carrier, not {carrier_type.__name__}"
        )
    if carrier_type is Pulse and mod_type is not ModulationType.PWM:
        raise ValueError(f"the Keysight 33521B cannot apply {mod_type.name} modulation to a Pulse carrier")


def _validate_modulator(shape: Waveform) -> Sine | Square | Sawtooth | Triangle:
    """Validate the modulating waveform passed to ``set_modulation()``; not to be confused with the channel's carrier."""
    if not isinstance(shape, (Sine, Square, Sawtooth, Triangle)):
        raise ValueError(
            f"the Keysight 33521B cannot use {type(shape).__name__} as a modulating waveform;"
            " use Sine, Square, Sawtooth, or Triangle"
        )
    return shape
