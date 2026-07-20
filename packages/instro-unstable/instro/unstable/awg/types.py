"""AWG shared types and waveform definitions."""

from dataclasses import dataclass
from enum import Enum


class VoltageUnit(Enum):
    VPP = "VPP"
    VRMS = "VRMS"
    DBM = "DBM"


def _require_positive(name: str, value: float) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value}")


def _require_percentage(name: str, value: float) -> None:
    if not 0.0 <= value <= 100.0:
        raise ValueError(f"{name} must be between 0 and 100, got {value}")


@dataclass(frozen=True)
class Sine:
    """Sine waveform definition."""

    frequency_hz: float
    phase_deg: float = 0.0

    def __post_init__(self) -> None:
        """Validate shape parameters at definition time."""
        _require_positive("frequency_hz", self.frequency_hz)


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


@dataclass(frozen=True)
class Ramp:
    """Ramp waveform definition."""

    frequency_hz: float
    symmetry_pct: float = 50.0
    phase_deg: float = 0.0

    def __post_init__(self) -> None:
        """Validate shape parameters at definition time."""
        _require_positive("frequency_hz", self.frequency_hz)
        _require_percentage("symmetry_pct", self.symmetry_pct)


@dataclass(frozen=True)
class Pulse:
    """Pulse waveform definition; delay stands in for phase, which pulse hardware rarely supports."""

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
class Noise:
    """Noise waveform definition; bandwidth is instrument-fixed."""


@dataclass(frozen=True)
class DC:
    """Constant-output definition; the level is commanded via the instrument offset."""


@dataclass(frozen=True)
class Arbitrary:
    """Arbitrary waveform definition; samples are normalized to [-1, 1] and scaled by amplitude/offset."""

    # Requires >= 2 samples, enforced by InstroAWG.set_waveform.
    samples: tuple[float, ...]
    sample_rate_hz: float
    # Use driver's default memory slot.
    name: str = ""

    def __post_init__(self) -> None:
        """Validate shape parameters at definition time."""
        object.__setattr__(self, "samples", tuple(self.samples))
        _require_positive("sample_rate_hz", self.sample_rate_hz)
        if any(not -1.0 <= s <= 1.0 for s in self.samples):
            raise ValueError("samples must be normalized to [-1.0, 1.0]")


Waveform = Sine | Square | Ramp | Pulse | Noise | DC | Arbitrary
