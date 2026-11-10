"""Discover the publishable properties of a pymeasure instrument and name their channels."""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from typing import Any, Literal

from pymeasure.instruments import Channel, Instrument
from pymeasure.instruments.common_base import CommonBase

try:
    from pymeasure.instruments.generic_types import SCPIMixin

    _BASE_CLASSES: tuple[type, ...] = (CommonBase, Instrument, Channel, SCPIMixin)
except ImportError:  # pymeasure < 0.14 has no SCPIMixin
    _BASE_CLASSES = (CommonBase, Instrument, Channel)

PropertyKind = Literal["control", "measurement", "setting", "property"]

_MISSING = object()

# Housekeeping properties (`id`, `status`, `next_error`, …) that every pymeasure instrument inherits.
_BASE_NAMES = frozenset(name for base in _BASE_CLASSES for name in dir(base))


@dataclass(frozen=True)
class ChannelSpec:
    """One publishable property of a pymeasure instrument.

    Attributes:
        path: Attribute path from the root instrument, e.g. ``"ch_1.voltage_setpoint"``.
        kind: ``"control"`` (read and write), ``"measurement"`` (read only), ``"setting"``
            (write only), or ``"property"`` (a hand-written Python property).
        readable: Reading the property queries the instrument.
        writable: Assigning the property sends a command.
        get_command: SCPI query template, or ``None``.
        set_command: SCPI command template, or ``None``.
        doc: First line of the property's docstring.
        default_name: Channel name the inspection rules give this property, without the
            instrument-name prefix or ``.cmd`` suffix.
    """

    path: str
    kind: PropertyKind
    readable: bool
    writable: bool
    get_command: str | None
    set_command: str | None
    doc: str
    default_name: str


def default_channel_name(path: str) -> str:
    """Return the channel name the inspection rules give an attribute path.

    pymeasure channel attributes ``ch_<id>`` become ``ch<id>`` to match instro's naming, and any
    character outside ``[A-Za-z0-9_]`` becomes ``_``.

    Args:
        path: Attribute path, e.g. ``"ch_1.voltage_setpoint"``.

    Returns:
        Dotted channel name, e.g. ``"ch1.voltage_setpoint"``.
    """
    segments = []
    for segment in path.split("."):
        segment = re.sub(r"^ch_(\w+)$", r"ch\1", segment)
        segments.append(re.sub(r"\W", "_", segment))
    return ".".join(segments)


def original_class(cls: type) -> type:
    """Return the pymeasure class behind a class the wrapper generated, or ``cls`` itself."""
    return getattr(cls, "_instro_original_class", cls)


def describe(target: CommonBase | type[CommonBase]) -> list[ChannelSpec]:
    """List every publishable property of a pymeasure instrument, including its channels.

    Pass an instance where possible: channels a driver adds in ``__init__`` (rather than with
    ``ChannelCreator``/``MultiChannelCreator`` at class level) only exist on an instance.

    Properties inherited from pymeasure's base classes (``id``, ``status``, ``next_error``, …)
    are left out.

    Args:
        target: A pymeasure instrument instance or class.

    Returns:
        One `ChannelSpec` per property, instrument properties first, then each channel's.
    """
    specs: list[ChannelSpec] = []
    _walk(target, "", specs, set())
    return specs


def _walk(target: Any, prefix: str, specs: list[ChannelSpec], seen: set[int]) -> None:
    is_class = inspect.isclass(target)
    # Only instances can form cycles; classes repeat legitimately (ch_1..ch_3 sharing one channel class).
    if not is_class:
        if id(target) in seen:
            return
        seen.add(id(target))
    cls = original_class(target if is_class else type(target))

    for name in sorted(dir(cls)):
        if name.startswith("_") or name in _BASE_NAMES:
            continue
        attr = inspect.getattr_static(cls, name)
        if isinstance(attr, property):
            spec = _spec_for(prefix + name, attr)
            if spec is not None:
                specs.append(spec)

    for child_name, child in _children(target, cls):
        _walk(child, f"{prefix}{child_name}.", specs, seen)


def _children(target: Any, cls: type) -> list[tuple[str, Any]]:
    """Return ``(attribute name, child)`` pairs: instances from ``vars``, classes from channel creators."""
    if not inspect.isclass(target):
        return [
            (name, value)
            for name, value in vars(target).items()
            if isinstance(value, CommonBase) and name != "parent" and not name.startswith("_")
        ]

    children: list[tuple[str, Any]] = []
    for name, creator in CommonBase.get_channels(cls):
        for child_cls, child_id in creator.pairs:
            prefix = creator.kwargs.get("prefix")
            if isinstance(creator, CommonBase.MultiChannelCreator) and prefix and child_id is not None:
                children.append((f"{prefix}{child_id}", child_cls))
            else:
                children.append((name, child_cls))
    return children


def _command(fn: Any, param: str) -> Any:
    if fn is None:
        return _MISSING
    try:
        parameter = inspect.signature(fn).parameters.get(param)
    except (TypeError, ValueError):
        return _MISSING
    return _MISSING if parameter is None else parameter.default


def _spec_for(path: str, prop: property) -> ChannelSpec | None:
    get_command = _command(prop.fget, "get_command")
    set_command = _command(prop.fset, "set_command")
    kind: PropertyKind
    if get_command is _MISSING and set_command is _MISSING:
        readable, writable = prop.fget is not None, prop.fset is not None
        get_command = set_command = None
        kind = "property"
    else:
        get_command = None if get_command is _MISSING else get_command
        set_command = None if set_command is _MISSING else set_command
        readable, writable = get_command is not None, set_command is not None
        kind = "control" if readable and writable else "measurement" if readable else "setting"
    if not (readable or writable):
        return None
    # pymeasure appends "(dynamic)" to dynamic properties' docstrings.
    doc = (inspect.getdoc(prop) or "").removesuffix("(dynamic)").strip().splitlines()
    return ChannelSpec(
        path=path,
        kind=kind,
        readable=readable,
        writable=writable,
        get_command=get_command if isinstance(get_command, str) else None,
        set_command=set_command if isinstance(set_command, str) else None,
        doc=doc[0] if doc else "",
        default_name=default_channel_name(path),
    )
