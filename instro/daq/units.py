"""Units for DAQ channels, backed by pint. One shared registry so units compare and convert across drivers."""

from pint import UndefinedUnitError, Unit, UnitRegistry

_UREG: UnitRegistry = UnitRegistry()


def parse_unit(unit: str | Unit) -> Unit:
    """Parse unit string into a pint unit."""
    try:
        return _UREG.Unit(unit)
    except (UndefinedUnitError, AttributeError, TypeError) as error:
        raise ValueError(f"unit '{unit}' is not a known unit.") from error


def convert_units(value: float, from_unit: Unit, to_unit: Unit) -> float:
    """Convert ``value`` between two compatible pint units."""
    if from_unit == to_unit:
        return value
    return float(_UREG.Quantity(value, from_unit).to(to_unit).magnitude)
