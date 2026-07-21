"""AWG shared types and waveform definitions."""

import math
from dataclasses import dataclass
from enum import Enum


class AmplitudeMeasurementUnit(Enum):
    VPP = "VPP"
    VP = "VP"
    VRMS = "VRMS"
    DBM = "DBM"


def _require_positive(name: str, value: float) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value}")


def _require_percentage(name: str, value: float) -> None:
    if not 0.0 <= value <= 100.0:
        raise ValueError(f"{name} must be between 0 and 100, got {value}")


def _normalize_degree(value: float) -> float:
    """Wrap a phase angle into [-180.0, 180.0)."""
    return value - 360.0 * math.floor((value + 180.0) / 360.0)


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
class Square:
    """Square waveform definition."""

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
    """Pulse waveform definition."""

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


Waveform = Sine | Square | Sawtooth | Triangle | Pulse | Arbitrary
