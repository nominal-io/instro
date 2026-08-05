"""Keysight 33500B arbitrary waveform generator driver (33500 series)."""

from __future__ import annotations

from instro.lib.transports.visa import VisaConfig, VisaDriver
from instro.unstable.awg.awg import AWGDriverBase
from instro.unstable.awg.types import (
    AmplitudeMeasurementUnit,
    Arbitrary,
    Pulse,
    Sawtooth,
    Sine,
    Square,
    StaticValue,
    Triangle,
    Waveform,
)

_HIGH_Z_SENTINEL = 9.9e37

_ARB_NAME = "INSTRO_ARB"
_ARB_MIN_POINTS = 8
_ARB_MAX_POINTS = 65536

_SAWTOOTH_SYMMETRY_PCT = 100


class Keysight33500B(AWGDriverBase):
    """SCPI driver for the Keysight 33500B arbitrary waveform generator."""

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
            raise RuntimeError(f"Keysight 33500B reported error: {err.strip()}")

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
                if waveform.delay_s != 0.0:
                    raise ValueError("the Keysight 33500B cannot program a pulse delay; Pulse.delay_s must be 0")
                self._visa.write("FUNC PULS")
                self._visa.write(f"FREQ {waveform.frequency_hz}")
                self._visa.write(f"FUNC:PULS:WIDT {waveform.width_s}")
                self.check_errors()
            elif isinstance(waveform, Arbitrary):
                num_points = len(waveform.samples)
                if not _ARB_MIN_POINTS <= num_points <= _ARB_MAX_POINTS:
                    raise ValueError(
                        f"the Keysight 33500B accepts {_ARB_MIN_POINTS} to {_ARB_MAX_POINTS} arbitrary points"
                        f" per download, got {num_points}"
                    )
                samples_csv = ",".join(str(sample) for sample in waveform.samples)
                self._visa.write(f"DATA:ARB {_ARB_NAME}, {samples_csv}")
                self.check_errors()
                self._visa.write(f"FUNC:ARB:SRAT {waveform.sample_rate_hz}")
                self._visa.write(f"FUNC:ARB {_ARB_NAME}")
                self._visa.write("FUNC ARB")
                self.check_errors()
                self._arb_waveforms[channel] = waveform
            elif isinstance(waveform, StaticValue):
                self._visa.write("FUNC DC")
                self._visa.write(f"VOLT:OFFS {waveform.value}")
            else:
                raise ValueError(f"unsupported waveform definition {type(waveform).__name__}")

    def get_waveform(self, channel: int) -> Waveform:
        _check_channel(channel)
        with self._visa.lock():
            name = self._visa.query("FUNC?").strip()
            if name == "SIN":
                frequency = float(self._visa.query("FREQ?"))
                phase = float(self._visa.query("PHAS?"))
                return Sine(frequency_hz=frequency, phase_deg=phase)
            if name == "SQU":
                frequency = float(self._visa.query("FREQ?"))
                phase = float(self._visa.query("PHAS?"))
                duty = float(self._visa.query("FUNC:SQU:DCYC?"))
                return Square(frequency_hz=frequency, duty_cycle_pct=duty, phase_deg=phase)
            if name == "RAMP":
                frequency = float(self._visa.query("FREQ?"))
                phase = float(self._visa.query("PHAS?"))
                return Sawtooth(frequency_hz=frequency, phase_deg=phase)
            if name == "TRI":
                frequency = float(self._visa.query("FREQ?"))
                phase = float(self._visa.query("PHAS?"))
                return Triangle(frequency_hz=frequency, phase_deg=phase)
            if name == "PULS":
                frequency = float(self._visa.query("FREQ?"))
                width = float(self._visa.query("FUNC:PULS:WIDT?"))
                return Pulse(frequency_hz=frequency, width_s=width)
            if name == "DC":
                offset = float(self._visa.query("VOLT:OFFS?"))
                return StaticValue(value=offset)
            if name == "ARB":
                arb = self._arb_waveforms.get(channel)
                if arb is None:
                    raise RuntimeError(
                        f"channel {channel} outputs an arbitrary waveform not programmed by this driver;"
                        " sample data is not readable"
                    )
                return arb
            raise ValueError(f"Keysight 33500B reported unsupported waveform '{name}'")

    def set_amplitude(self, channel: int, amplitude: float, unit: AmplitudeMeasurementUnit) -> None:
        _check_channel(channel)
        if unit is AmplitudeMeasurementUnit.VP:
            raise ValueError("VP is not supported by Keysight 33500B")
        with self._visa.lock():
            self._visa.write(f"VOLT:UNIT {unit.value}")
            self._visa.write(f"VOLT {amplitude}")

    def get_amplitude(self, channel: int) -> tuple[float, AmplitudeMeasurementUnit]:
        _check_channel(channel)
        with self._visa.lock():
            magnitude = float(self._visa.query("VOLT?"))
            unit = AmplitudeMeasurementUnit(self._visa.query("VOLT:UNIT?").strip())
        return (magnitude, unit)

    def set_offset(self, channel: int, offset: float) -> None:
        _check_channel(channel)
        self._visa.write(f"VOLT:OFFS {offset}")

    def get_offset(self, channel: int) -> float:
        _check_channel(channel)
        return float(self._visa.query("VOLT:OFFS?"))

    def output_enable(self, channel: int, enable: bool) -> None:
        _check_channel(channel)
        self._visa.write("OUTP ON" if enable else "OUTP OFF")

    def get_output_state(self, channel: int) -> bool:
        _check_channel(channel)
        return self._visa.query("OUTP?").strip() == "1"

    def set_output_load(self, channel: int, load: float | None) -> None:
        _check_channel(channel)
        if load is None:
            self._visa.write("OUTP:LOAD INF")
        else:
            self._visa.write(f"OUTP:LOAD {load}")

    def get_output_load(self, channel: int) -> float | None:
        _check_channel(channel)
        load = float(self._visa.query("OUTP:LOAD?"))
        return None if load >= _HIGH_Z_SENTINEL else load

    def _write_frequency_and_phase(self, frequency_hz: float, phase_deg: float) -> None:
        self._visa.write(f"FREQ {frequency_hz}")
        self._visa.write(f"PHAS {phase_deg % 360}")
        self.check_errors()


def _check_channel(channel: int) -> None:
    if channel != 1:
        raise ValueError(f"Keysight 33500B only supports 1 channel, got {channel}")
