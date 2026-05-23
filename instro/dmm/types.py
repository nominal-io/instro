"""DMM shared types: ``MeasurementFunction``, ``DMMMeasurementConfig``, ``RangeMode``."""

from dataclasses import dataclass
from enum import Enum


class MeasurementFunction(Enum):
    """DMM measurement function (mode)."""

    DC_VOLTAGE = "DC_VOLTAGE"
    AC_VOLTAGE = "AC_VOLTAGE"
    TWO_WIRE_RESISTANCE = "TWO_WIRE_RESISTANCE"
    FOUR_WIRE_RESISTANCE = "FOUR_WIRE_RESISTANCE"
    DC_CURRENT = "DC_CURRENT"
    AC_CURRENT = "AC_CURRENT"


class RangeMode(Enum):
    """DMM range mode published on ``range_mode.cmd``."""

    AUTO = "AUTO"
    MANUAL = "MANUAL"


@dataclass
class DMMMeasurementConfig:
    """DMM acquisition config. Drivers apply only the options they support; the rest are ignored or raise.

    Attributes:
        function: Measurement function (required).
        digits: Resolution in digits; ``None`` = instrument default.
        aperture_seconds: Integration time in seconds (vendor-dependent; mutually exclusive with ``aperture_nplc``).
        aperture_nplc: Integration time in power-line cycles (vendor-dependent).
        range: Manual range in the active function's units; ``None`` = auto range.
    """

    function: MeasurementFunction
    digits: int | None = None
    aperture_seconds: float | None = None
    aperture_nplc: float | None = None
    range: float | None = None
