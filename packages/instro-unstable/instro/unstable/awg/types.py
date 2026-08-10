"""AWG shared types and waveform definitions."""

import math
from dataclasses import dataclass
from enum import Enum


class AmplitudeMeasurementUnit(Enum):
    VPP = "VPP"
    VP = "VP"
    VRMS = "VRMS"
    DBM = "DBM"


class ModulationType(Enum):
    AM = "AM"
    FM = "FM"
    PM = "PM"
    FSK = "FSK"
    ASK = "ASK"
    PSK = "PSK"
    PWM = "PWM"


class BurstType(Enum):
    NCYCLE = "NCYCLE"
    GATED = "GATED"
    INFINITE = "INFINITE"


class BurstTriggerSource(Enum):
    INTERNAL = "INT"
    EXTERNAL = "EXT"
    MANUAL = "MAN"


class GatePolarity(Enum):
    NORM = "NORM"
    INV = "INV"


def _require_positive(name: str, value: float) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value}")


def _require_percentage(name: str, value: float) -> None:
    if not 0.0 <= value <= 100.0:
        raise ValueError(f"{name} must be between 0 and 100, got {value}")


def _normalize_degree(value: float) -> float:
    """Wrap a phase angle into [-180.0, 180.0)."""
    return value - 360.0 * math.floor((value + 180.0) / 360.0)


def _crest_factor(waveform: "Waveform") -> float:
    """Peak-to-RMS ratio (Vp / Vrms) for the waveform shape; Arbitrary is derived from its samples."""
    if isinstance(waveform, Arbitrary):
        mean_square = sum(s * s for s in waveform.samples) / len(waveform.samples)
        return 1.0 / math.sqrt(mean_square) if mean_square else 1.0
    return _CREST_FACTORS[type(waveform)]


def convert_amplitude(
    value: float,
    from_unit: AmplitudeMeasurementUnit,
    to_unit: AmplitudeMeasurementUnit,
    waveform: "Waveform",
    impedance_ohms: float | None = None,
) -> float:
    """Convert an amplitude value between measurement units for a waveform shape.

    VPP/VP/VRMS conversions depend on the waveform's crest factor, which is universal
    math shared across every driver. DBM additionally requires ``impedance_ohms`` (the
    load the instrument drives), since power depends on it.
    """
    if not isinstance(from_unit, AmplitudeMeasurementUnit):
        raise TypeError(f"from_unit must be an AmplitudeMeasurementUnit, got {type(from_unit).__name__}")
    if not isinstance(to_unit, AmplitudeMeasurementUnit):
        raise TypeError(f"to_unit must be an AmplitudeMeasurementUnit, got {type(to_unit).__name__}")
    if from_unit is to_unit:
        return value

    needs_crest_factor = AmplitudeMeasurementUnit.VPP in (from_unit, to_unit) or AmplitudeMeasurementUnit.VP in (
        from_unit,
        to_unit,
    )
    crest_factor = _crest_factor(waveform) if needs_crest_factor else 1.0

    match from_unit:
        case AmplitudeMeasurementUnit.VPP:
            vrms = (value / 2.0) / crest_factor
        case AmplitudeMeasurementUnit.VP:
            vrms = value / crest_factor
        case AmplitudeMeasurementUnit.VRMS:
            vrms = value
        case AmplitudeMeasurementUnit.DBM:
            if impedance_ohms is None:
                raise ValueError("impedance_ohms is required to convert from DBM")
            _require_positive("impedance_ohms", impedance_ohms)
            power_w = 1e-3 * 10 ** (value / 10.0)
            vrms = math.sqrt(power_w * impedance_ohms)
        case _:
            raise AssertionError(f"unhandled AmplitudeMeasurementUnit {from_unit}")

    match to_unit:
        case AmplitudeMeasurementUnit.VPP:
            return vrms * crest_factor * 2.0
        case AmplitudeMeasurementUnit.VP:
            return vrms * crest_factor
        case AmplitudeMeasurementUnit.VRMS:
            return vrms
        case AmplitudeMeasurementUnit.DBM:
            if impedance_ohms is None:
                raise ValueError("impedance_ohms is required to convert to DBM")
            _require_positive("impedance_ohms", impedance_ohms)
            if vrms <= 0.0:
                raise ValueError("amplitude must be positive to convert to DBM")
            power_w = (vrms * vrms) / impedance_ohms
            return 10.0 * math.log10(power_w / 1e-3)
        case _:
            raise AssertionError(f"unhandled AmplitudeMeasurementUnit {to_unit}")


@dataclass(frozen=True)
class Sine:
    """Sine waveform definition."""

    frequency_hz: float
    phase_deg: float = 0.0

    def __post_init__(self) -> None:
        """Validate shape parameters at definition time."""
        _require_positive("frequency_hz", self.frequency_hz)
        object.__setattr__(self, "phase_deg", _normalize_degree(self.phase_deg))


@dataclass(frozen=True)
class StaticValue:
    """Static valued waveform definition."""

    value: float = 0.0


@dataclass(frozen=True)
class Square:
    """Continuous rectangular wave; high for duty_cycle_pct percent of each period."""

    frequency_hz: float
    duty_cycle_pct: float = 50.0
    phase_deg: float = 0.0

    def __post_init__(self) -> None:
        """Validate shape parameters at definition time."""
        _require_positive("frequency_hz", self.frequency_hz)
        _require_percentage("duty_cycle_pct", self.duty_cycle_pct)
        object.__setattr__(self, "phase_deg", _normalize_degree(self.phase_deg))


@dataclass(frozen=True)
class Sawtooth:
    """Sawtooth waveform definition."""

    frequency_hz: float
    phase_deg: float = 0.0

    def __post_init__(self) -> None:
        """Validate shape parameters at definition time."""
        _require_positive("frequency_hz", self.frequency_hz)
        object.__setattr__(self, "phase_deg", _normalize_degree(self.phase_deg))


@dataclass(frozen=True)
class Triangle:
    """Triangle waveform definition."""

    frequency_hz: float
    phase_deg: float = 0.0

    def __post_init__(self) -> None:
        """Validate shape parameters at definition time."""
        _require_positive("frequency_hz", self.frequency_hz)
        object.__setattr__(self, "phase_deg", _normalize_degree(self.phase_deg))


@dataclass(frozen=True)
class Pulse:
    """Continuous rectangular wave (not single-shot); each period goes high for width_s seconds after delay_s."""

    frequency_hz: float
    width_s: float
    delay_s: float = 0.0

    def __post_init__(self) -> None:
        """Validate shape parameters at definition time."""
        _require_positive("frequency_hz", self.frequency_hz)
        _require_positive("width_s", self.width_s)
        if self.delay_s < 0:
            raise ValueError(f"delay_s must be non-negative, got {self.delay_s}")
        if self.width_s + self.delay_s >= 1.0 / self.frequency_hz:
            raise ValueError(
                f"width_s + delay_s must fit within the period (1/frequency_hz), got {self.width_s + self.delay_s}"
            )


@dataclass(frozen=True)
class Arbitrary:
    """Arbitrary waveform definition; samples are normalized to [-1, 1] and scaled by amplitude/offset."""

    samples: tuple[float, ...]
    sample_rate_hz: float

    def __post_init__(self) -> None:
        """Validate shape parameters at definition time."""
        object.__setattr__(self, "samples", tuple(self.samples))
        _require_positive("sample_rate_hz", self.sample_rate_hz)
        if len(self.samples) < 2:
            raise ValueError(f"Arbitrary waveform must contain at least 2 samples, got {len(self.samples)}")
        if any(not -1.0 <= s <= 1.0 for s in self.samples):
            raise ValueError("samples must be normalized to [-1.0, 1.0]")


Waveform = Sine | Square | Sawtooth | Triangle | Pulse | Arbitrary | StaticValue


_CREST_FACTORS: dict[type, float] = {
    Sine: math.sqrt(2),
    Square: 1.0,
    Sawtooth: math.sqrt(3),
    Triangle: math.sqrt(3),
    Pulse: 1.0,
    StaticValue: 1.0,
}
