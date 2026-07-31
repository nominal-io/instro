"""Rigol DG1022Z arbitrary waveform generator driver (DG1000Z series)."""

from __future__ import annotations

from instro.lib.transports.visa import VisaConfig, VisaDriver
from instro.unstable.awg.awg import AWGDriverBase
from instro.unstable.awg.types import (
    AmplitudeMeasurementUnit,
    Arbitrary,
    BurstType,
    HarmonicType,
    ModulationType,
    Pulse,
    Sawtooth,
    Sine,
    Square,
    StaticValue,
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


class RigolDG1022Z(AWGDriverBase):
    """SCPI driver for the Rigol DG1022Z two-channel arbitrary waveform generator."""

    def __init__(self, visa_resource: str | VisaConfig) -> None:
        self._visa = VisaDriver(visa_resource)
        self._arb_waveforms: dict[int, Arbitrary] = {}

    def open(self) -> None:
        self._visa.open()

    def close(self) -> None:
        self._visa.close()

    def check_errors(self) -> None:
        """Query ``:SYSTem:ERRor?`` once and raise on a non-zero code. Does not drain the queue."""
        err = self._visa.query(":SYST:ERR?")
        code = err.strip().split(",", 1)[0].lstrip("+")
        if code != "0":
            raise RuntimeError(f"Rigol DG1022Z reported error: {err.strip()}")

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
                # The DG1000Z command set has no pulse-delay parameter.
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
                self._visa.write(f":SOUR{channel}:APPL:ARB {waveform.sample_rate_hz}")
                self.check_errors()
                self._visa.write(f":SOUR{channel}:TRAC:DATA:POIN VOLATILE,{num_points}")
                self.check_errors()
                for point, sample in enumerate(waveform.samples, start=1):
                    decimal_value = round((sample + 1) / 2 * 16383)
                    self._visa.write(f":SOUR{channel}:TRAC:DATA:VAL VOLATILE,{point},{decimal_value}")
                    # check_errors doesn't drain the queue (see check_errors docstring), so a batch write
                    # here could bury an error under later ones. Checking every point catches it at the
                    # point it occurred, at the cost of one extra query per point on the happy path.
                    self.check_errors()
                self._arb_waveforms[channel] = waveform
            elif isinstance(waveform, StaticValue):
                self._visa.write(f":SOUR{channel}:FUNC DC")
                self._visa.write(f":SOUR{channel}:VOLT:OFFS {waveform.value}")
            else:
                raise ValueError(f"unsupported waveform definition {type(waveform).__name__}")

    def get_waveform(self, channel: int) -> Waveform:
        _check_channel(channel)
        with self._visa.lock():
            resp = self._visa.query(f":SOUR{channel}:APPL?").strip().strip('"')
            fields = resp.split(",")
            name = fields[0]
            if name == "SIN":
                return Sine(frequency_hz=float(fields[1]), phase_deg=float(fields[4]))
            if name == "SQU":
                duty = float(self._visa.query(f":SOUR{channel}:FUNC:SQU:DCYC?"))
                return Square(frequency_hz=float(fields[1]), duty_cycle_pct=duty, phase_deg=float(fields[4]))
            if name == "RAMP":
                symmetry = float(self._visa.query(f":SOUR{channel}:FUNC:RAMP:SYMM?"))
                if symmetry == float(_SAWTOOTH_SYMMETRY_PCT):
                    return Sawtooth(frequency_hz=float(fields[1]), phase_deg=float(fields[4]))
                return Triangle(frequency_hz=float(fields[1]), phase_deg=float(fields[4]))
            if name == "PULSE":
                width = float(self._visa.query(f":SOUR{channel}:FUNC:PULS:WIDT?"))
                return Pulse(frequency_hz=float(fields[1]), width_s=width)
            if name == "DC":
                return StaticValue(value=float(fields[3]))
            if name == "USER":
                arb = self._arb_waveforms.get(channel)
                if arb is None:
                    raise RuntimeError(
                        f"channel {channel} outputs an arbitrary waveform not programmed by this driver;"
                        " sample data is not readable"
                    )
                return arb
            raise ValueError(f"Rigol DG1022Z reported unsupported waveform '{name}'")

    def set_amplitude(self, channel: int, amplitude: float, unit: AmplitudeMeasurementUnit) -> None:
        _check_channel(channel)
        if unit is AmplitudeMeasurementUnit.VP:
            raise ValueError("the DG1022Z has no VP amplitude unit; convert to VPP, VRMS, or DBM")
        with self._visa.lock():
            self._visa.write(f":SOUR{channel}:VOLT:UNIT {unit.value}")
            self._visa.write(f":SOUR{channel}:VOLT {amplitude}")

    def get_amplitude(self, channel: int) -> tuple[float, AmplitudeMeasurementUnit]:
        _check_channel(channel)
        with self._visa.lock():
            unit = AmplitudeMeasurementUnit(self._visa.query(f":SOUR{channel}:VOLT:UNIT?").strip())
            amplitude = float(self._visa.query(f":SOUR{channel}:VOLT?"))
        return amplitude, unit

    def set_offset(self, channel: int, offset: float) -> None:
        _check_channel(channel)
        self._visa.write(f":SOUR{channel}:VOLT:OFFS {offset}")

    def get_offset(self, channel: int) -> float:
        _check_channel(channel)
        with self._visa.lock():
            resp = self._visa.query(f":SOUR{channel}:APPL?").strip().strip('"')
            fields = resp.split(",")
            if fields[0] == "DC":
                # In DC mode VOLT:OFFS? always reads 0 on the DG1000Z; only APPL? carries the level.
                return float(fields[3])
            return float(self._visa.query(f":SOUR{channel}:VOLT:OFFS?"))

    def output_enable(self, channel: int, enable: bool) -> None:
        _check_channel(channel)
        self._visa.write(f":OUTP{channel} ON" if enable else f":OUTP{channel} OFF")

    def get_output_state(self, channel: int) -> bool:
        _check_channel(channel)
        return self._visa.query(f":OUTP{channel}?").strip() == "ON"

    def set_output_load(self, channel: int, load: float | None) -> None:
        _check_channel(channel)
        if load is None:
            self._visa.write(f":OUTP{channel}:LOAD INF")
        else:
            self._visa.write(f":OUTP{channel}:LOAD {load:g}")

    def get_output_load(self, channel: int) -> float | None:
        _check_channel(channel)
        load = float(self._visa.query(f":OUTP{channel}:LOAD?"))
        return None if load >= _HIGH_Z_SENTINEL else load

    def align_phase(self) -> None:
        self._visa.write(":SOUR1:PHAS:SYNC")

    def modulate(self, channel: int, mod_type: ModulationType, shape: Waveform, magnitude: float) -> None:
        _check_channel(channel)
        if not isinstance(mod_type, ModulationType):
            raise TypeError(f"mod_type must be a ModulationType, got {type(mod_type).__name__}")
        carrier = _mod_carrier(shape)
        frequency_hz = carrier.frequency_hz

        with self._visa.lock():
            if self._harmonics_enabled(channel):
                raise ValueError(f"the DG1022Z cannot modulate channel {channel} while harmonics are enabled on it")
            if self._burst_enabled(channel):
                raise ValueError(f"the DG1022Z cannot modulate channel {channel} while burst is enabled on it")
            if self._sweep_enabled(channel):
                raise ValueError(f"the DG1022Z cannot modulate channel {channel} while sweep is enabled on it")
            if mod_type in (ModulationType.AM, ModulationType.FM, ModulationType.PM):
                prefix = mod_type.value
                function = _MOD_INTERNAL_FUNCTIONS[type(carrier)]
                self._visa.write(f":SOUR{channel}:{prefix}:SOUR INT")
                self._visa.write(f":SOUR{channel}:{prefix}:INT:FUNC {function}")
                self._visa.write(f":SOUR{channel}:{prefix}:INT:FREQ {frequency_hz}")
                self._visa.write(f":SOUR{channel}:{prefix} {magnitude}")
                self._visa.write(f":SOUR{channel}:{prefix}:STAT ON")
            elif mod_type is ModulationType.ASK:
                self._visa.write(f":SOUR{channel}:ASK:SOUR INT")
                self._visa.write(f":SOUR{channel}:ASK:INT {frequency_hz}")
                self._visa.write(f":SOUR{channel}:ASK:AMPL {magnitude}")
                self._visa.write(f":SOUR{channel}:ASK:STAT ON")
            elif mod_type is ModulationType.FSK:
                self._visa.write(f":SOUR{channel}:FSK:SOUR INT")
                self._visa.write(f":SOUR{channel}:FSK:INT:RATE {frequency_hz}")
                self._visa.write(f":SOUR{channel}:FSK {magnitude}")
                self._visa.write(f":SOUR{channel}:FSK:STAT ON")
            else:
                raise AssertionError(f"unhandled ModulationType {mod_type}")

    def enable_harmonics(
        self, channel: int, order: int, harm_type: HarmonicType, user_harmonics: str | None = None
    ) -> None:
        _check_channel(channel)
        _check_harmonic_order(order)
        if not isinstance(harm_type, HarmonicType):
            raise TypeError(f"harm_type must be a HarmonicType, got {type(harm_type).__name__}")
        if harm_type is HarmonicType.USER:
            if user_harmonics is None or len(user_harmonics) != 7 or any(bit not in "01" for bit in user_harmonics):
                raise ValueError(
                    "user_harmonics must be a 7-character string of '0'/'1' (harmonics order 2-8) when"
                    f" harm_type is HarmonicType.USER, got {user_harmonics!r}"
                )
        elif user_harmonics is not None:
            raise ValueError("user_harmonics is only valid when harm_type is HarmonicType.USER")

        with self._visa.lock():
            if self._modulation_enabled(channel):
                raise ValueError(f"the DG1022Z cannot enable harmonics on channel {channel} while it is modulated")
            if self._burst_enabled(channel):
                raise ValueError(
                    f"the DG1022Z cannot enable harmonics on channel {channel} while burst is enabled on it"
                )
            if self._sweep_enabled(channel):
                raise ValueError(
                    f"the DG1022Z cannot enable harmonics on channel {channel} while sweep is enabled on it"
                )
            carrier = self.get_waveform(channel)
            if not isinstance(carrier, Sine):
                raise ValueError(
                    f"the DG1022Z can only enable harmonics on a Sine wave; channel {channel} outputs "
                    f"{type(carrier).__name__}"
                )
            self._visa.write(f":SOUR{channel}:HARM:ORDE {order}")
            if harm_type is HarmonicType.USER:
                self._visa.write(f":SOUR{channel}:HARM:TYP USER")
                self._visa.write(f":SOUR{channel}:HARM:USER X{user_harmonics}")
            else:
                self._visa.write(f":SOUR{channel}:HARM:TYP {harm_type.value}")
            self._visa.write(f":SOUR{channel}:HARM:STAT ON")

    def burst(self, channel: int, burst_type: BurstType) -> None:
        _check_channel(channel)
        if not isinstance(burst_type, BurstType):
            raise TypeError(f"burst_type must be a BurstType, got {type(burst_type).__name__}")

        with self._visa.lock():
            if self._harmonics_enabled(channel):
                raise ValueError(f"the DG1022Z cannot burst channel {channel} while harmonics are enabled on it")
            if self._modulation_enabled(channel):
                raise ValueError(f"the DG1022Z cannot burst channel {channel} while it is modulated")
            if self._sweep_enabled(channel):
                raise ValueError(f"the DG1022Z cannot burst channel {channel} while sweep is enabled on it")
            carrier = self.get_waveform(channel)
            if isinstance(carrier, StaticValue):
                raise ValueError(f"the DG1022Z cannot burst a StaticValue (DC) waveform on channel {channel}")
            self._visa.write(f":SOUR{channel}:BURS:MODE {_BURST_MODES[burst_type]}")
            if burst_type is BurstType.GATED:
                self._visa.write(f":SOUR{channel}:BURS:GATE:POL NORM")
            elif burst_type is BurstType.INFINITE:
                self._visa.write(f":SOUR{channel}:BURS:TRIG:SOUR MAN")
            else:
                self._visa.write(f":SOUR{channel}:BURS:TRIG:SOUR INT")
            self._visa.write(f":SOUR{channel}:BURS:STAT ON")

    def sweep(self, channel: int, start_freq: float, stop_freq: float, sweep_type: SweepType) -> None:
        _check_channel(channel)
        if not isinstance(sweep_type, SweepType):
            raise TypeError(f"sweep_type must be a SweepType, got {type(sweep_type).__name__}")
        if start_freq <= 0 or stop_freq <= 0:
            raise ValueError(f"start_freq and stop_freq must be positive, got {start_freq}, {stop_freq}")

        with self._visa.lock():
            if self._harmonics_enabled(channel):
                raise ValueError(f"the DG1022Z cannot sweep channel {channel} while harmonics are enabled on it")
            if self._modulation_enabled(channel):
                raise ValueError(f"the DG1022Z cannot sweep channel {channel} while it is modulated")
            if self._burst_enabled(channel):
                raise ValueError(f"the DG1022Z cannot sweep channel {channel} while burst is enabled on it")
            carrier = self.get_waveform(channel)
            if isinstance(carrier, (Pulse, StaticValue)):
                raise ValueError(f"the DG1022Z cannot sweep a {type(carrier).__name__} waveform on channel {channel}")
            self._visa.write(f":SOUR{channel}:FREQ:STAR {start_freq}")
            self._visa.write(f":SOUR{channel}:FREQ:STOP {stop_freq}")
            self._visa.write(f":SOUR{channel}:SWE:SPAC {sweep_type.value}")
            self._visa.write(f":SOUR{channel}:SWE:TRIG:SOUR INT")
            self._visa.write(f":SOUR{channel}:SWE:STAT ON")

    def _harmonics_enabled(self, channel: int) -> bool:
        return self._visa.query(f":SOUR{channel}:HARM:STAT?").strip() == "ON"

    def _sweep_enabled(self, channel: int) -> bool:
        return self._visa.query(f":SOUR{channel}:SWE:STAT?").strip() == "ON"

    def _burst_enabled(self, channel: int) -> bool:
        return self._visa.query(f":SOUR{channel}:BURS:STAT?").strip() == "ON"

    def _modulation_enabled(self, channel: int) -> bool:
        return any(
            self._visa.query(f":SOUR{channel}:{prefix}:STAT?").strip() == "ON"
            for prefix in ("AM", "FM", "PM", "ASK", "FSK")
        )

    def _write_frequency_and_phase(self, channel: int, frequency_hz: float, phase_deg: float) -> None:
        self._visa.write(f":SOUR{channel}:FREQ {frequency_hz}")
        self._visa.write(f":SOUR{channel}:PHAS {phase_deg % 360.0}")


def _check_channel(channel: int) -> None:
    if channel not in (1, 2):
        raise ValueError(f"Rigol DG1022Z channel must be 1 or 2, got {channel}")


def _check_harmonic_order(order: int) -> None:
    if not isinstance(order, int):
        raise TypeError(f"order must be an integer, got {order}")
    if order < 2 or order > 8:
        raise ValueError("order is out of range, 2 <= ORDER <= 8")


def _mod_carrier(shape: Waveform) -> Sine | Square | Sawtooth | Triangle:
    if not isinstance(shape, (Sine, Square, Sawtooth, Triangle)):
        raise ValueError(
            f"the DG1022Z cannot use {type(shape).__name__} as a modulating waveform;"
            " use Sine, Square, Sawtooth, or Triangle"
        )
    return shape
