"""Keysight 33500B arbitrary waveform generator driver (33500 series)."""

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
                self._visa.write("FUNC PULS")
                phase_deg = waveform.delay_s * waveform.frequency_hz * 360.0
                self._write_frequency_and_phase(waveform.frequency_hz, phase_deg)
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
                phase = float(self._visa.query("PHAS?"))
                width = float(self._visa.query("FUNC:PULS:WIDT?"))
                delay = max(0.0, phase / 360.0 / frequency)
                return Pulse(frequency_hz=frequency, width_s=width, delay_s=delay)
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

    def set_modulation(self, channel: int, mod_type: ModulationType, shape: Waveform, magnitude: float) -> None:
        """Configures and enables modulation"""
        _check_channel(channel)
        if not isinstance(mod_type, ModulationType):
            raise TypeError(f"mod_type must be a ModulationType, got {type(mod_type).__name__}")
        if mod_type is ModulationType.ASK:
            raise ValueError("ASK modulation is not supported by the Keysight 33500B")
        modulator = _validate_modulator(shape)
        prefix = _MOD_SCPI_PREFIX[mod_type]

        with self._visa.lock():
            # `shape` is the modulator/baseband signal, the carrier is what set_waveform last programmed.
            _validate_carrier(mod_type, self.get_waveform(channel))
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
            self._visa.write(f"{prefix}:STAT ON")
            self.check_errors()

    def modulation_enable(self, channel: int, enable: bool) -> None:
        """Disables modulation"""
        _check_channel(channel)
        if enable:
            raise ValueError(
                "the Keysight 33500B enables modulation as part of set_modulation;"
                " modulation_enable only supports disabling (enable=False)"
            )
        with self._visa.lock():
            for prefix in _MOD_SCPI_PREFIX.values():
                self._visa.write(f"{prefix}:STAT OFF")
            self.check_errors()

    def get_modulation_type(self, channel: int) -> ModulationType:
        _check_channel(channel)
        with self._visa.lock():
            for mod_type, prefix in _MOD_SCPI_PREFIX.items():
                if self._visa.query(f"{prefix}:STAT?").strip() == "1":
                    return mod_type
        raise RuntimeError(f"channel {channel} has no modulation type currently enabled; set modulation first.")

    def get_modulation_state(self, channel: int) -> bool:
        _check_channel(channel)
        with self._visa.lock():
            return any(self._visa.query(f"{prefix}:STAT?").strip() == "1" for prefix in _MOD_SCPI_PREFIX.values())

    def _write_frequency_and_phase(self, frequency_hz: float, phase_deg: float) -> None:
        self._visa.write(f"FREQ {frequency_hz}")
        self._visa.write(f"PHAS {phase_deg % 360}")
        self.check_errors()


def _check_channel(channel: int) -> None:
    if channel != 1:
        raise ValueError(f"Keysight 33500B only supports 1 channel, got {channel}")


def _validate_carrier(mod_type: ModulationType, carrier: Waveform) -> None:
    """Validate that the channel's currently active carrier waveform supports the given modulation type."""
    if not isinstance(carrier, (Sine, Square, Sawtooth, Triangle, Pulse, Arbitrary)):
        raise ValueError(
            f"the Keysight 33500B cannot apply {mod_type.name} modulation to a {type(carrier).__name__} carrier;"
            " carrier must be Sine, Square, Sawtooth, Triangle, Pulse, or Arbitrary"
        )
    if mod_type is ModulationType.PWM and not isinstance(carrier, Pulse):
        raise ValueError(
            f"the Keysight 33500B can only apply PWM modulation to a Pulse carrier, not {type(carrier).__name__}"
        )
    if isinstance(carrier, Pulse) and mod_type is not ModulationType.PWM:
        raise ValueError(f"the Keysight 33500B cannot apply {mod_type.name} modulation to a Pulse carrier")


def _validate_modulator(shape: Waveform) -> Sine | Square | Sawtooth | Triangle:
    """Validate the modulating waveform passed to ``set_modulation()``; not to be confused with the channel's carrier."""
    if not isinstance(shape, (Sine, Square, Sawtooth, Triangle)):
        raise ValueError(
            f"the Keysight 33500B cannot use {type(shape).__name__} as a modulating waveform;"
            " use Sine, Square, Sawtooth, or Triangle"
        )
    return shape
