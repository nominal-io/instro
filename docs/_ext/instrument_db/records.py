"""The normalized instrument record every driver source emits, and the category table."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

# category -> display title. Instro's categories first, then ones only third-party libraries have.
CATEGORY_TITLES: dict[str, str] = {
    "psu": "Power supplies",
    "dmm": "Multimeters and source meters",
    "eload": "Electronic loads",
    "scope": "Oscilloscopes",
    "awg": "Function and waveform generators",
    "daq": "Data acquisition",
    "i2c": "I2C adapters",
    "flowcontroller": "Flow controllers",
    "sdr": "Software defined radios",
    "vna": "Network analyzers",
    "spectrum": "Spectrum analyzers",
    "temperature": "Temperature controllers and chambers",
    "lockin": "Lock-in amplifiers",
    "motion": "Motion and positioning",
    "laser": "Lasers and optics",
    "pressure": "Pressure and vacuum",
    "rf": "RF sources and meters",
    "other": "Other instruments",
}


def category_title(category: str) -> str:
    return CATEGORY_TITLES.get(category, category.replace("_", " ").title())


def slug(text: str) -> str:
    """Lowercase ``text`` with runs of non-alphanumerics turned into one hyphen (for URLs)."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "x"


@dataclass(frozen=True)
class InstrumentRecord:
    """One supported instrument, as listed in the database.

    Everything a source knows goes in ``extra``; the page templates for a ``driver_type`` read it. Records are
    plain JSON so they can be snapshotted at release time and re-rendered with newer templates later.

    Attributes:
        driver_type: The library the driver comes from: ``"instro"``, ``"pymeasure"``, ...
        category: Instrument category key (see ``CATEGORY_TITLES``).
        manufacturer: Display name of the manufacturer.
        model: Model or series, without the manufacturer.
        name: Driver class name.
        import_path: Dotted path of the driver class.
        summary: One-line description.
        tier: ``"stable"``, ``"unstable"``, or ``"contrib"`` for instro drivers; ``""`` otherwise.
        simulated: A virtual or simulated driver, with no hardware behind it.
        extra: Source-specific fields for the page template.
    """

    driver_type: str
    category: str
    manufacturer: str
    model: str
    name: str
    import_path: str
    summary: str = ""
    tier: str = ""
    simulated: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        """URL slug, unique within ``driver_type``."""
        return slug(self.extra.get("id") or self.name)

    @property
    def display_name(self) -> str:
        """Manufacturer and model, as shown in lists and search results."""
        if self.manufacturer.lower() in self.model.lower():
            return self.model
        return f"{self.manufacturer} {self.model}".strip()

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> InstrumentRecord:
        return cls(**data)
