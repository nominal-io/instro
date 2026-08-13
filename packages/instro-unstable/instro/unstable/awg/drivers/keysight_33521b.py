"""Keysight 33521B arbitrary waveform generator driver (33500 series)."""

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
_FUNC_NAME_TO_CARRIER_TYPE: dict[str, type] = {
    "SIN": Sine,
    "SQU": Square,
    "RAMP": Sawtooth,
    "TRI": Triangle,
    "PULS": Pulse,
    "DC": StaticValue,
    "ARB": Arbitrary,
}


class Keysight33521B(AWGDriverBase):
    """SCPI driver for the Keysight 33521B arbitrary waveform generator."""

    def __init__(self, visa_resource: str | VisaConfig) -> None:
        self._visa = VisaDriver(visa_resource)
        self._arb_waveforms: dict[int, Arbitrary] = {}
        # The instrument has no query to read back which modulation type is configured
        # (only per-type :STAT?), so we cache the last type set by the user, same
        # rationale as _arb_waveforms above.
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
        with self._visa.lock():
            self._visa.write(f"VOLT:OFFS {offset}")
            self._check_errors()

    def get_offset(self, channel: int) -> float:
        _check_channel(channel)
        with self._visa.lock():
            result = float(self._visa.query("VOLT:OFFS?"))
            self._check_errors()
        return result

    def output_enable(self, channel: int, enable: bool) -> None:
        _check_channel(channel)
        with self._visa.lock():
            self._visa.write("OUTP ON" if enable else "OUTP OFF")
            self._check_errors()

    def get_output_state(self, channel: int) -> bool:
        _check_channel(channel)
        with self._visa.lock():
            result = self._visa.query("OUTP?").strip() == "1"
            self._check_errors()
        return result

    def set_output_load(self, channel: int, load: float | None) -> None:
        _check_channel(channel)
        with self._visa.lock():
            if load is None:
                self._visa.write("OUTP:LOAD INF")
            else:
                self._visa.write(f"OUTP:LOAD {load}")
            self._check_errors()

    def get_output_load(self, channel: int) -> float | None:
        _check_channel(channel)
        with self._visa.lock():
            load = float(self._visa.query("OUTP:LOAD?"))
            self._check_errors()
        return None if load >= _HIGH_Z_SENTINEL else load

    def set_modulation(self, channel: int, mod_type: ModulationType, shape: Waveform, magnitude: float) -> None:
        """Configures and enables modulation."""
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
            self._last_modulation_type = mod_type

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
        """Returns modulation type currently enabled, or the last type set by the user when none is enabled.

        Resyncs the cached type from hardware if an enabled :STAT register disagrees with it.
        """
        _check_channel(channel)
        with self._visa.lock():
            active = next(
                (
                    mod_type
                    for mod_type, prefix in _MOD_SCPI_PREFIX.items()
                    if self._visa.query(f"{prefix}:STAT?").strip() == "1"
                ),
                None,
            )
            self._check_errors()
            if active is not None and active != self._last_modulation_type:
                # prioritize hardware state
                self._last_modulation_type = active
            resolved = self._last_modulation_type
            if resolved is None:
                raise RuntimeError(f"channel {channel} has no modulation type currently enabled; set modulation first.")
        return resolved

    def get_modulation_state(self, channel: int) -> bool:
        _check_channel(channel)
        with self._visa.lock():
            result = any(self._visa.query(f"{prefix}:STAT?").strip() == "1" for prefix in _MOD_SCPI_PREFIX.values())
            self._check_errors()
        return result

    def _write_frequency_and_phase(self, frequency_hz: float, phase_deg: float) -> None:
        self._visa.write(f"FREQ {frequency_hz}")
        self._visa.write(f"PHAS {phase_deg % 360}")

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
