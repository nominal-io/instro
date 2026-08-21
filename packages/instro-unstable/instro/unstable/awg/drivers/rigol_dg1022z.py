"""Rigol DG1022Z arbitrary waveform generator driver (DG1000Z series)."""

from __future__ import annotations

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

_ARB_MIN_POINTS = 9
_ARB_MAX_POINTS = 16384

_SAWTOOTH_SYMMETRY_PCT = 100
_TRIANGLE_SYMMETRY_PCT = 50

_MOD_INTERNAL_FUNCTIONS: dict[type, str] = {
    Sine: "SIN",
    Square: "SQU",
    Sawtooth: "RAMP",
    Triangle: "TRI",
}

_BURST_MODES: dict[BurstType, str] = {
    BurstType.NCYCLE: "TRIG",
    BurstType.GATED: "GAT",
    BurstType.INFINITE: "INF",
}
_BURST_TYPES: dict[str, BurstType] = {mode: burst_type for burst_type, mode in _BURST_MODES.items()}

# :SWE:SPAC? always echoes the instrument's own abbreviated mnemonic, never the full keyword written.
_SWEEP_SPACING_READBACK: dict[str, SweepType] = {
    "LIN": SweepType.LINEAR,
    "LOG": SweepType.LOG,
    "STE": SweepType.STEP,
}

# :FUNC? mnemonic -> user-facing waveform name, for carriers the DG1022Z cannot sweep.
_INVALID_SWEEPS: dict[str, str] = {
    "DC": StaticValue.__name__,
    "NOIS": "Noise",
    "PULS": Pulse.__name__,
}


class RigolDG1022Z(AWGDriverBase):
    """SCPI driver for the Rigol DG1022Z two-channel arbitrary waveform generator."""

    def __init__(self, visa_resource: str | VisaConfig) -> None:
        self._visa = VisaDriver(visa_resource)
        self._arb_waveforms: dict[int, Arbitrary] = {}

    def open(self) -> None:
        self._visa.open()

    def close(self) -> None:
        self._visa.close()

    def set_waveform(self, channel: int, waveform: Waveform) -> None:
        _check_channel(channel)
        with self._visa.lock():
            if isinstance(waveform, Sine):
                self._visa.write(f":SOUR{channel}:FUNC SIN")
                self._write_frequency_and_phase(channel, waveform.frequency_hz, waveform.phase_deg)
            elif isinstance(waveform, Square):
                self._visa.write(f":SOUR{channel}:FUNC SQU")
                self._write_frequency_and_phase(channel, waveform.frequency_hz, waveform.phase_deg)
                self._visa.write(f":SOUR{channel}:FUNC:SQU:DCYC {waveform.duty_cycle_pct}")
            elif isinstance(waveform, Sawtooth):
                self._visa.write(f":SOUR{channel}:FUNC RAMP")
                self._write_frequency_and_phase(channel, waveform.frequency_hz, waveform.phase_deg)
                self._visa.write(f":SOUR{channel}:FUNC:RAMP:SYMM {_SAWTOOTH_SYMMETRY_PCT}")
            elif isinstance(waveform, Triangle):
                self._visa.write(f":SOUR{channel}:FUNC RAMP")
                self._write_frequency_and_phase(channel, waveform.frequency_hz, waveform.phase_deg)
                self._visa.write(f":SOUR{channel}:FUNC:RAMP:SYMM {_TRIANGLE_SYMMETRY_PCT}")
            elif isinstance(waveform, Pulse):
                if waveform.delay_s != 0.0:
                    raise ValueError("the DG1022Z cannot program a pulse delay; Pulse.delay_s must be 0")
                self._visa.write(f":SOUR{channel}:FUNC PULS")
                self._visa.write(f":SOUR{channel}:FREQ {waveform.frequency_hz}")
                self._visa.write(f":SOUR{channel}:FUNC:PULS:WIDT {waveform.width_s}")
            elif isinstance(waveform, Arbitrary):
                # Use per-point downloads to allow both USB and Ethernet compatibility and a higher download size ceiling.
                num_points = len(waveform.samples)
                if not _ARB_MIN_POINTS <= num_points <= _ARB_MAX_POINTS:
                    raise ValueError(
                        f"the DG1022Z accepts {_ARB_MIN_POINTS} to {_ARB_MAX_POINTS} arbitrary points"
                        f" per download, got {num_points}"
                    )
                self._write_checked(f":SOUR{channel}:APPL:ARB {waveform.sample_rate_hz}")
                self._write_checked(f":SOUR{channel}:TRAC:DATA:POIN VOLATILE,{num_points}")
                for point, sample in enumerate(waveform.samples, start=1):
                    decimal_value = round((sample + 1) / 2 * 16383)
                    # error queue is not drained, so checking every point avoids lost error messages in exchange for a longer runtime
                    self._write_checked(f":SOUR{channel}:TRAC:DATA:VAL VOLATILE,{point},{decimal_value}")
                self._arb_waveforms[channel] = waveform
            elif isinstance(waveform, StaticValue):
                self._visa.write(f":SOUR{channel}:FUNC DC")
                self._visa.write(f":SOUR{channel}:VOLT:OFFS {waveform.value}")
            else:
                raise ValueError(f"unsupported waveform definition {type(waveform).__name__}")

            if not isinstance(waveform, Arbitrary):
                self._check_errors()

    def get_waveform(self, channel: int) -> Waveform:
        _check_channel(channel)
        with self._visa.lock():
            resp = self._visa.query(f":SOUR{channel}:APPL?").strip().strip('"')
            fields = resp.split(",")
            name = fields[0]
            if name == "SIN":
                result: Waveform = Sine(frequency_hz=float(fields[1]), phase_deg=float(fields[4]))
            elif name == "SQU":
                duty = float(self._visa.query(f":SOUR{channel}:FUNC:SQU:DCYC?"))
                result = Square(frequency_hz=float(fields[1]), duty_cycle_pct=duty, phase_deg=float(fields[4]))
            elif name == "RAMP":
                symmetry = float(self._visa.query(f":SOUR{channel}:FUNC:RAMP:SYMM?"))
                if symmetry == float(_SAWTOOTH_SYMMETRY_PCT):
                    result = Sawtooth(frequency_hz=float(fields[1]), phase_deg=float(fields[4]))
                else:
                    result = Triangle(frequency_hz=float(fields[1]), phase_deg=float(fields[4]))
            elif name == "PULSE":
                width = float(self._visa.query(f":SOUR{channel}:FUNC:PULS:WIDT?"))
                result = Pulse(frequency_hz=float(fields[1]), width_s=width)
            elif name == "DC":
                result = StaticValue(value=float(fields[3]))
            elif name == "USER":
                arb = self._arb_waveforms.get(channel)
                if arb is None:
                    raise RuntimeError(
                        f"channel {channel} outputs an arbitrary waveform not programmed by this driver;"
                        " sample data is not readable"
                    )
                result = arb
            else:
                raise ValueError(f"Rigol DG1022Z reported unsupported waveform '{name}'")
            self._check_errors()
            return result

    def set_amplitude(self, channel: int, amplitude: float, unit: AmplitudeMeasurementUnit) -> None:
        _check_channel(channel)
        if unit is AmplitudeMeasurementUnit.VP:
            raise ValueError("the DG1022Z has no VP amplitude unit; convert to VPP, VRMS, or DBM")
        with self._visa.lock():
            self._visa.write(f":SOUR{channel}:VOLT:UNIT {unit.value}")
            self._visa.write(f":SOUR{channel}:VOLT {amplitude}")
            self._check_errors()

    def get_amplitude(self, channel: int) -> tuple[float, AmplitudeMeasurementUnit]:
        _check_channel(channel)
        with self._visa.lock():
            unit = AmplitudeMeasurementUnit(self._visa.query(f":SOUR{channel}:VOLT:UNIT?").strip())
            amplitude = float(self._visa.query(f":SOUR{channel}:VOLT?"))
            self._check_errors()
        return amplitude, unit

    def set_offset(self, channel: int, offset: float) -> None:
        _check_channel(channel)
        with self._visa.lock():
            self._visa.write(f":SOUR{channel}:VOLT:OFFS {offset}")
            self._check_errors()

    def get_offset(self, channel: int) -> float:
        _check_channel(channel)
        with self._visa.lock():
            resp = self._visa.query(f":SOUR{channel}:APPL?").strip().strip('"')
            fields = resp.split(",")
            if fields[0] == "DC":
                # In DC mode VOLT:OFFS? always reads 0 on the DG1000Z; only APPL? carries the level.
                result = float(fields[3])
            else:
                result = float(self._visa.query(f":SOUR{channel}:VOLT:OFFS?"))
            self._check_errors()
        return result

    def output_enable(self, channel: int, enable: bool) -> None:
        _check_channel(channel)
        with self._visa.lock():
            self._visa.write(f":OUTP{channel} ON" if enable else f":OUTP{channel} OFF")
            self._check_errors()

    def get_output_state(self, channel: int) -> bool:
        _check_channel(channel)
        with self._visa.lock():
            result = self._visa.query(f":OUTP{channel}?").strip() == "ON"
            self._check_errors()
        return result

    def set_output_load(self, channel: int, load: float | None) -> None:
        _check_channel(channel)
        with self._visa.lock():
            if load is None:
                self._visa.write(f":OUTP{channel}:LOAD INF")
            else:
                self._visa.write(f":OUTP{channel}:LOAD {load:g}")
            self._check_errors()

    def get_output_load(self, channel: int) -> float | None:
        _check_channel(channel)
        with self._visa.lock():
            load = float(self._visa.query(f":OUTP{channel}:LOAD?"))
            self._check_errors()
        return None if load >= _HIGH_Z_SENTINEL else load

    def align_phase(self) -> None:
        with self._visa.lock():
            self._visa.write(":SOUR1:PHAS:SYNC")
            self._check_errors()

    def set_modulation(self, channel: int, mod_type: ModulationType, shape: Waveform, magnitude: float) -> None:
        _check_channel(channel)
        if not isinstance(mod_type, ModulationType):
            raise TypeError(f"mod_type must be a ModulationType, got {type(mod_type).__name__}")
        # `shape` is the modulator/baseband signal, the carrier is what set_waveform last programmed.
        _validate_carrier(mod_type, self.get_waveform(channel))
        modulator = _validate_modulator(shape)
        frequency_hz = modulator.frequency_hz

        with self._visa.lock():
            if mod_type in (ModulationType.AM, ModulationType.FM, ModulationType.PM, ModulationType.PWM):
                prefix = mod_type.value
                function = _MOD_INTERNAL_FUNCTIONS[type(modulator)]
                self._visa.write(f":SOUR{channel}:{prefix}:SOUR INT")
                self._visa.write(f":SOUR{channel}:{prefix}:INT:FUNC {function}")
                self._visa.write(f":SOUR{channel}:{prefix}:INT:FREQ {frequency_hz}")
                self._visa.write(f":SOUR{channel}:{prefix} {magnitude}")
            elif mod_type is ModulationType.ASK:
                self._visa.write(f":SOUR{channel}:ASK:SOUR INT")
                self._visa.write(f":SOUR{channel}:ASK:INT {frequency_hz}")
                self._visa.write(f":SOUR{channel}:ASK:AMPL {magnitude}")
            elif mod_type is ModulationType.FSK:
                self._visa.write(f":SOUR{channel}:FSK:SOUR INT")
                self._visa.write(f":SOUR{channel}:FSK:INT:RATE {frequency_hz}")
                self._visa.write(f":SOUR{channel}:FSK {magnitude}")
            elif mod_type is ModulationType.PSK:
                self._visa.write(f":SOUR{channel}:PSK:SOUR INT")
                self._visa.write(f":SOUR{channel}:PSK:INT:RATE {frequency_hz}")
                self._visa.write(f":SOUR{channel}:PSK:PHAS {magnitude}")
            else:
                raise AssertionError(f"unhandled ModulationType {mod_type}")
            self._visa.write(f":SOUR{channel}:MOD:TYP {mod_type.value}")
            self._check_errors()

    def modulation_enable(self, channel: int, enable: bool) -> None:
        _check_channel(channel)
        with self._visa.lock():
            self._visa.write(f":SOUR{channel}:MOD:STAT {'ON' if enable else 'OFF'}")
            self._check_errors()

    def get_modulation_type(self, channel: int) -> ModulationType:
        _check_channel(channel)
        with self._visa.lock():
            result = ModulationType(self._visa.query(f":SOUR{channel}:MOD:TYP?").strip())
            self._check_errors()
        return result

    def get_modulation_state(self, channel: int) -> bool:
        _check_channel(channel)
        with self._visa.lock():
            result = self._visa.query(f":SOUR{channel}:MOD:STAT?").strip() == "ON"
            self._check_errors()
        return result

    def set_burst(self, channel: int, burst_type: BurstType) -> None:
        _check_channel(channel)
        if not isinstance(burst_type, BurstType):
            raise TypeError(f"burst_type must be a BurstType, got {type(burst_type).__name__}")
        with self._visa.lock():
            carrier = self.get_waveform(channel)
            if isinstance(carrier, StaticValue):
                raise ValueError(f"the DG1022Z cannot burst a StaticValue (DC) waveform on channel {channel}")
            self._visa.write(f":SOUR{channel}:BURS:MODE {_BURST_MODES[burst_type]}")
            self._check_errors()

    def get_burst_type(self, channel: int) -> BurstType:
        _check_channel(channel)
        with self._visa.lock():
            mode = self._visa.query(f":SOUR{channel}:BURS:MODE?").strip()
            self._check_errors()
        if mode not in _BURST_TYPES:
            raise ValueError(f"Rigol DG1022Z reported unsupported burst mode '{mode}'")
        return _BURST_TYPES[mode]

    def burst_enable(self, channel: int, enable: bool) -> None:
        _check_channel(channel)
        self._write_checked(f":SOUR{channel}:BURS:STAT {'ON' if enable else 'OFF'}")

    def get_burst_state(self, channel: int) -> bool:
        _check_channel(channel)
        with self._visa.lock():
            result = self._visa.query(f":SOUR{channel}:BURS:STAT?").strip() == "ON"
            self._check_errors()
        return result

    def set_burst_trigger(self, channel: int, source: BurstTriggerSource) -> None:
        _check_channel(channel)
        if not isinstance(source, BurstTriggerSource):
            raise TypeError(f"source must be a BurstTriggerSource, got {type(source).__name__}")
        with self._visa.lock():
            burst_type = self.get_burst_type(channel)
            if burst_type is BurstType.GATED:
                raise ValueError(
                    f"Cannot trigger since channel {channel} is in GATED burst mode, call set_burst with a different burst_type first"
                )
            if burst_type is BurstType.INFINITE and source is BurstTriggerSource.INTERNAL:
                raise ValueError(
                    f"Cannot use INTERNAL trigger source since channel {channel} is in INFINITE burst mode,"
                    " use EXTERNAL or MANUAL instead"
                )
            self._visa.write(f":SOUR{channel}:BURS:TRIG:SOUR {source.value}")
            self._check_errors()

    def get_burst_trigger(self, channel: int) -> BurstTriggerSource:
        _check_channel(channel)
        with self._visa.lock():
            result = BurstTriggerSource(self._visa.query(f":SOUR{channel}:BURS:TRIG:SOUR?").strip())
            self._check_errors()
        return result

    def fire_burst_trigger(self, channel: int) -> None:
        _check_channel(channel)
        with self._visa.lock():
            if not self.get_burst_state(channel):
                raise ValueError(
                    f"Cannot fire a burst trigger on channel {channel} unless burst mode is already"
                    " enabled, call burst_enable(channel, True) first"
                )
            source = self.get_burst_trigger(channel)
            if source is not BurstTriggerSource.MANUAL:
                raise ValueError(
                    f"Cannot fire a burst trigger on channel {channel} unless the trigger source is"
                    f" already MANUAL, call set_burst_trigger(channel, BurstTriggerSource.MANUAL) first. Got: {source.name}"
                )
            self._write_checked(f":SOUR{channel}:BURS:TRIG")

    def set_burst_delay(self, channel: int, delay_s: float) -> None:
        _check_channel(channel)
        if delay_s < 0:
            raise ValueError(f"delay_s must be non-negative, got {delay_s}")
        self._write_checked(f":SOUR{channel}:BURS:TDEL {delay_s}")

    def get_burst_delay(self, channel: int) -> float:
        _check_channel(channel)
        with self._visa.lock():
            result = float(self._visa.query(f":SOUR{channel}:BURS:TDEL?"))
            self._check_errors()
        return result

    def set_burst_gate_polarity(self, channel: int, gate_polarity: GatePolarity) -> None:
        _check_channel(channel)
        if not isinstance(gate_polarity, GatePolarity):
            raise TypeError(f"gate_polarity must be a GatePolarity, got {type(gate_polarity).__name__}")
        self._write_checked(f":SOUR{channel}:BURS:GATE:POL {gate_polarity.value}")

    def get_burst_gate_polarity(self, channel: int) -> GatePolarity:
        _check_channel(channel)
        with self._visa.lock():
            result = GatePolarity(self._visa.query(f":SOUR{channel}:BURS:GATE:POL?").strip())
            self._check_errors()
        return result

    def set_burst_ncycles(self, channel: int, n_cycles: int) -> None:
        _check_channel(channel)
        if n_cycles <= 0:
            raise ValueError(f"n_cycles must be >= 1, got {n_cycles}")
        self._write_checked(f":SOUR{channel}:BURS:NCYC {int(n_cycles)}")

    def get_burst_ncycles(self, channel: int) -> int:
        _check_channel(channel)
        with self._visa.lock():
            result = int(float(self._visa.query(f":SOUR{channel}:BURS:NCYC?")))
            self._check_errors()
        return result

    def set_burst_period(self, channel: int, period: float) -> None:
        _check_channel(channel)
        if period <= 0:
            raise ValueError(f"period must be positive, got {period}")
        self._write_checked(f":SOUR{channel}:BURS:INT:PER {period}")

    def get_burst_period(self, channel: int) -> float:
        _check_channel(channel)
        with self._visa.lock():
            result = float(self._visa.query(f":SOUR{channel}:BURS:INT:PER?"))
            self._check_errors()
        return result

    def set_sweep(self, channel: int, sweep_type: SweepType) -> None:
        _check_channel(channel)
        if not isinstance(sweep_type, SweepType):
            raise TypeError(f"sweep_type must be a SweepType, got {type(sweep_type).__name__}")
        with self._visa.lock():
            carrier = self._visa.query(f":SOUR{channel}:FUNC?").strip()
            self._check_errors()
            invalid_name = _INVALID_SWEEPS.get(carrier)
            if invalid_name is not None:
                raise ValueError(f"the DG1022Z cannot sweep a {invalid_name} on channel {channel}")
            self._visa.write(f":SOUR{channel}:SWE:SPAC {sweep_type.value}")
            self._check_errors()

    def get_sweep_type(self, channel: int) -> SweepType:
        _check_channel(channel)
        with self._visa.lock():
            resp = self._visa.query(f":SOUR{channel}:SWE:SPAC?").strip()
            self._check_errors()
        result = _SWEEP_SPACING_READBACK.get(resp)
        if result is None:
            raise ValueError(f"Rigol DG1022Z reported unsupported sweep type '{resp}'")
        return result

    def sweep_enable(self, channel: int, enable: bool) -> None:
        _check_channel(channel)
        self._write_checked(f":SOUR{channel}:SWE:STAT {'ON' if enable else 'OFF'}")

    def get_sweep_state(self, channel: int) -> bool:
        _check_channel(channel)
        with self._visa.lock():
            result = self._visa.query(f":SOUR{channel}:SWE:STAT?").strip() == "ON"
            self._check_errors()
        return result

    def set_sweep_trigger(self, channel: int, source: SweepTriggerSource) -> None:
        _check_channel(channel)
        if not isinstance(source, SweepTriggerSource):
            raise TypeError(f"source must be a SweepTriggerSource, got {type(source).__name__}")
        self._write_checked(f":SOUR{channel}:SWE:TRIG:SOUR {source.value}")

    def get_sweep_trigger(self, channel: int) -> SweepTriggerSource:
        _check_channel(channel)
        with self._visa.lock():
            resp = self._visa.query(f":SOUR{channel}:SWE:TRIG:SOUR?").strip()
            self._check_errors()
        return SweepTriggerSource(resp)

    def set_sweep_start_freq(self, channel: int, frequency_hz: float) -> None:
        _check_channel(channel)
        self._write_checked(f":SOUR{channel}:FREQ:STAR {frequency_hz}")

    def get_sweep_start_freq(self, channel: int) -> float:
        _check_channel(channel)
        with self._visa.lock():
            result = float(self._visa.query(f":SOUR{channel}:FREQ:STAR?"))
            self._check_errors()
        return result

    def set_sweep_end_freq(self, channel: int, frequency_hz: float) -> None:
        _check_channel(channel)
        self._write_checked(f":SOUR{channel}:FREQ:STOP {frequency_hz}")

    def get_sweep_end_freq(self, channel: int) -> float:
        _check_channel(channel)
        with self._visa.lock():
            result = float(self._visa.query(f":SOUR{channel}:FREQ:STOP?"))
            self._check_errors()
        return result

    def set_sweep_time(self, channel: int, sweep_time: float) -> None:
        _check_channel(channel)
        self._write_checked(f":SOUR{channel}:SWE:TIME {sweep_time}")

    def get_sweep_time(self, channel: int) -> float:
        _check_channel(channel)
        with self._visa.lock():
            result = float(self._visa.query(f":SOUR{channel}:SWE:TIME?"))
            self._check_errors()
        return result

    def set_sweep_stop_hold_time(self, channel: int, hold_time: float) -> None:
        _check_channel(channel)
        self._write_checked(f":SOUR{channel}:SWE:HTIM {hold_time}")

    def get_sweep_stop_hold_time(self, channel: int) -> float:
        _check_channel(channel)
        with self._visa.lock():
            result = float(self._visa.query(f":SOUR{channel}:SWE:HTIM?"))
            self._check_errors()
        return result

    def set_sweep_start_hold_time(self, channel: int, hold_time: float) -> None:
        _check_channel(channel)
        self._write_checked(f":SOUR{channel}:SWE:HTIM:STAR {hold_time}")

    def get_sweep_start_hold_time(self, channel: int) -> float:
        _check_channel(channel)
        with self._visa.lock():
            result = float(self._visa.query(f":SOUR{channel}:SWE:HTIM:STAR?"))
            self._check_errors()
        return result

    def set_sweep_return_time(self, channel: int, return_time: float) -> None:
        _check_channel(channel)
        self._write_checked(f":SOUR{channel}:SWE:RTIM {return_time}")

    def get_sweep_return_time(self, channel: int) -> float:
        _check_channel(channel)
        with self._visa.lock():
            result = float(self._visa.query(f":SOUR{channel}:SWE:RTIM?"))
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
                    f" already MANUAL, call set_sweep_trigger(channel, SweepTriggerSource.MANUAL) first. Got: {source.name}"
                )
            self._write_checked(f":SOUR{channel}:SWE:TRIG")

    def _write_frequency_and_phase(self, channel: int, frequency_hz: float, phase_deg: float) -> None:
        self._visa.write(f":SOUR{channel}:FREQ {frequency_hz}")
        self._visa.write(f":SOUR{channel}:PHAS {phase_deg % 360.0}")

    def _write_checked(self, command: str) -> None:
        with self._visa.lock():
            self._visa.write(command)
            self._check_errors()

    def _check_errors(self) -> None:
        """Query :SYSTem:ERRor? once and raise on a non-zero code. Does not drain the queue."""
        err = self._visa.query(":SYST:ERR?")
        code = err.strip().split(",", 1)[0].lstrip("+")
        if code != "0":
            raise RuntimeError(f"Rigol DG1022Z reported error: {err.strip()}")


def _check_channel(channel: int) -> None:
    if channel not in (1, 2):
        raise ValueError(f"Rigol DG1022Z channel must be 1 or 2, got {channel}")


def _validate_carrier(mod_type: ModulationType, carrier: Waveform) -> None:
    """Validate that the channel's currently active carrier waveform supports the given modulation type."""
    if not isinstance(carrier, (Sine, Square, Sawtooth, Triangle, Pulse, Arbitrary)):
        raise ValueError(
            f"the DG1022Z cannot apply {mod_type.name} modulation to a {type(carrier).__name__} carrier;"
            " carrier must be Sine, Square, Sawtooth, Triangle, Pulse, or Arbitrary"
        )
    if mod_type is ModulationType.PWM and not isinstance(carrier, Pulse):
        raise ValueError(f"the DG1022Z can only apply PWM modulation to a Pulse carrier, not {type(carrier).__name__}")
    if isinstance(carrier, Pulse) and mod_type is not ModulationType.PWM:
        raise ValueError(f"the DG1022Z cannot apply {mod_type.name} modulation to a Pulse carrier")


def _validate_modulator(shape: Waveform) -> Sine | Square | Sawtooth | Triangle:
    """Validate the modulating waveform passed to ``set_modulation()``; not to be confused with the channel's carrier."""
    if not isinstance(shape, (Sine, Square, Sawtooth, Triangle)):
        raise ValueError(
            f"the DG1022Z cannot use {type(shape).__name__} as a modulating waveform;"
            " use Sine, Square, Sawtooth, or Triangle"
        )
    return shape
