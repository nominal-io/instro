"""Discover the publishable parameters of a QCoDeS instrument and name their channels."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from qcodes.instrument import InstrumentBase
from qcodes.parameters import ParameterBase

ParameterKind = Literal["control", "measurement", "setting"]

# Housekeeping parameters that QCoDeS' base classes add to the root instrument (`IDN`, VISA `timeout`).
BASE_NAMES = frozenset({"IDN", "timeout"})


@dataclass(frozen=True)
class ChannelSpec:
    """One publishable parameter of a QCoDeS instrument.

    Attributes:
        path: Attribute path from the root instrument, e.g. ``"ch1.voltage"``.
        kind: ``"control"`` (get and set), ``"measurement"`` (get only), or ``"setting"`` (set only).
        readable: Getting the parameter queries the instrument.
        writable: Setting the parameter sends a command.
        unit: The parameter's unit, or an empty string.
        label: The parameter's label, or an empty string.
        default_name: Channel name the inspection rules give this parameter, without the
            instrument-name prefix or ``.cmd`` suffix.
    """

    path: str
    kind: ParameterKind
    readable: bool
    writable: bool
    unit: str
    label: str
    default_name: str


def default_channel_name(path: str) -> str:
    """Return the channel name the inspection rules give an attribute path.

    Any character outside ``[A-Za-z0-9_]`` in a path segment becomes ``_``.

    Args:
        path: Attribute path, e.g. ``"ch1.voltage"``.

    Returns:
        Dotted channel name, e.g. ``"ch1.voltage"``.
    """
    return ".".join(re.sub(r"\W", "_", segment) for segment in path.split("."))


def describe(instrument: InstrumentBase) -> list[ChannelSpec]:
    """List every publishable parameter of a QCoDeS instrument, including its submodules and channels.

    Parameters are added when a driver is constructed, so this takes a connected instance, not a class.
    The root instrument's ``IDN`` and VISA ``timeout`` parameters are left out.

    Args:
        instrument: A QCoDeS instrument instance.

    Returns:
        One `ChannelSpec` per parameter, in the order the driver registered them.
    """
    return [_spec_for(path, parameter) for path, parameter in iter_parameters(instrument)]


def iter_modules(instrument: InstrumentBase) -> list[tuple[str, InstrumentBase]]:
    """Return ``(attribute path, module)`` for every submodule and channel below ``instrument``, depth first."""
    found: list[tuple[str, InstrumentBase]] = []
    _collect_modules(instrument, "", found, {id(instrument)})
    return found


def iter_parameters(instrument: InstrumentBase) -> list[tuple[str, ParameterBase]]:
    """Return ``(attribute path, parameter)`` for every gettable or settable parameter to publish."""
    found: list[tuple[str, ParameterBase]] = []
    for prefix, module in [("", instrument), *iter_modules(instrument)]:
        for name, parameter in module.parameters.items():
            if not prefix and name in BASE_NAMES:
                continue
            if parameter.gettable or parameter.settable:
                found.append((f"{prefix}.{name}" if prefix else name, parameter))
    return found


def _collect_modules(parent: Any, prefix: str, found: list[tuple[str, InstrumentBase]], seen: set[int]) -> None:
    children: list[tuple[str, InstrumentBase]] = []
    registered = dict(getattr(parent, "instrument_modules", {}))
    children.extend(registered.items())
    # A channel list's members are modules too; skip ones already registered under their own name.
    for list_name, submodule in getattr(parent, "submodules", {}).items():
        if list_name in registered or isinstance(submodule, InstrumentBase):
            continue
        for member in submodule:
            if isinstance(member, InstrumentBase):
                children.append((f"{list_name}.{member.short_name}", member))
    for name, child in children:
        if id(child) in seen:
            continue
        seen.add(id(child))
        path = f"{prefix}{name}"
        found.append((path, child))
        _collect_modules(child, f"{path}.", found, seen)


def _spec_for(path: str, parameter: ParameterBase) -> ChannelSpec:
    readable, writable = bool(parameter.gettable), bool(parameter.settable)
    kind: ParameterKind = "control" if readable and writable else "measurement" if readable else "setting"
    return ChannelSpec(
        path=path,
        kind=kind,
        readable=readable,
        writable=writable,
        unit=str(getattr(parameter, "unit", "") or ""),
        label=str(getattr(parameter, "label", "") or ""),
        default_name=default_channel_name(path),
    )
