"""Rigol DG1022Z arbitrary waveform generator driver (DG1000Z series)."""

from __future__ import annotations

from instro.lib.transports.visa import VisaConfig, VisaDriver
from instro.unstable.awg.awg import AWGDriverBase
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

    def _write_frequency_and_phase(self, channel: int, frequency_hz: float, phase_deg: float) -> None:
        self._visa.write(f":SOUR{channel}:FREQ {frequency_hz}")
        self._visa.write(f":SOUR{channel}:PHAS {phase_deg % 360.0}")


def _check_channel(channel: int) -> None:
    if channel not in (1, 2):
        raise ValueError(f"Rigol DG1022Z channel must be 1 or 2, got {channel}")


def _mod_carrier(shape: Waveform) -> Sine | Square | Sawtooth | Triangle:
    if not isinstance(shape, (Sine, Square, Sawtooth, Triangle)):
        raise ValueError(
            f"the DG1022Z cannot use {type(shape).__name__} as a modulating waveform;"
            " use Sine, Square, Sawtooth, or Triangle"
        )
    return shape
