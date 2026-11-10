"""Editable mapping from pymeasure attribute paths to instro channel names."""

from __future__ import annotations

import fnmatch
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pymeasure.instruments.common_base import CommonBase

from instro.pymeasure.inspection import ChannelSpec, describe, original_class

MAP_FORMAT = "instro-pymeasure-channel-map/1"

_ENTRY_KEYS = {"channel", "kind", "get", "set", "doc"}
_VALID_NAME = re.compile(r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*$")


@dataclass
class ChannelMap:
    """Which pymeasure properties publish, and under which channel names.

    Generate one from an instrument, save it, edit the ``channel`` of each entry (or set it to
    ``null`` to stop publishing that property), and pass the file or object to
    `PymeasureInstrument` or `attach`.

    A command channel is the mapped name plus ``.cmd``; a measurement channel is the mapped name.
    Both sit under the instrument name: ``<instrument name>.<mapped name>[.cmd]``.

    Attributes:
        channels: Attribute path (``"ch_1.voltage_setpoint"``) to channel name (``"ch1.vset"``), or
            ``None`` to leave that property unpublished.
        include_unmapped: Publish properties missing from ``channels`` under their default names.
        instrument: ``module.Class`` of the instrument the map was generated from; informational.
    """

    channels: dict[str, str | None] = field(default_factory=dict)
    include_unmapped: bool = False
    instrument: str | None = None

    @classmethod
    def defaults(cls) -> ChannelMap:
        """Return the map used when none is given: every property under its default name."""
        return cls(channels={}, include_unmapped=True)

    @classmethod
    def generate(cls, target: CommonBase | type[CommonBase]) -> ChannelMap:
        """Build a map listing every publishable property of ``target`` under its default name.

        Args:
            target: A pymeasure instrument instance (preferred: it sees channels added in
                ``__init__``) or class.

        Returns:
            A `ChannelMap` with one entry per property and ``include_unmapped=False``.
        """
        return cls(
            channels={spec.path: spec.default_name for spec in describe(target)},
            include_unmapped=False,
            instrument=_qualified_name(target),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChannelMap:
        """Parse the JSON-compatible form written by `to_dict`.

        Each ``channels`` value may be a channel-name string, ``null``, or an object whose
        ``channel`` key holds either; its ``kind``/``get``/``set``/``doc`` keys are informational.

        Raises:
            ValueError: The format tag is unknown or an entry is malformed.
        """
        fmt = data.get("format", MAP_FORMAT)
        if fmt != MAP_FORMAT:
            raise ValueError(f"Unsupported channel map format {fmt!r}; expected {MAP_FORMAT!r}")
        channels: dict[str, str | None] = {}
        for path, entry in data.get("channels", {}).items():
            if isinstance(entry, dict):
                unknown = set(entry) - _ENTRY_KEYS
                if unknown:
                    raise ValueError(f"Channel map entry {path!r} has unknown keys {sorted(unknown)}")
                entry = entry.get("channel")
            if entry is not None and not isinstance(entry, str):
                raise ValueError(f"Channel map entry {path!r} must be a string or null, got {entry!r}")
            channels[path] = entry
        return cls(
            channels=channels,
            include_unmapped=bool(data.get("include_unmapped", False)),
            instrument=data.get("instrument"),
        )

    @classmethod
    def load(cls, path: str | Path) -> ChannelMap:
        """Read a map from a JSON file written by `save` (or the ``instro pymeasure map`` command)."""
        return cls.from_dict(json.loads(Path(path).read_text()))

    def to_dict(self, specs: list[ChannelSpec] | None = None) -> dict[str, Any]:
        """Return the JSON-compatible form.

        Args:
            specs: Inspection results for the instrument. When given, each entry also records the
                property's kind, SCPI commands, and docstring to make the file easier to edit.
        """
        by_path = {spec.path: spec for spec in specs or []}
        channels: dict[str, Any] = {}
        for path, name in self.channels.items():
            spec = by_path.get(path)
            if spec is None:
                channels[path] = name
                continue
            entry: dict[str, Any] = {"channel": name, "kind": spec.kind}
            if spec.get_command:
                entry["get"] = spec.get_command
            if spec.set_command:
                entry["set"] = spec.set_command
            if spec.doc:
                entry["doc"] = spec.doc
            channels[path] = entry
        data: dict[str, Any] = {"format": MAP_FORMAT}
        if self.instrument:
            data["instrument"] = self.instrument
        data["include_unmapped"] = self.include_unmapped
        data["channels"] = channels
        return data

    def save(self, path: str | Path, target: CommonBase | type[CommonBase] | None = None) -> None:
        """Write the map as JSON.

        Args:
            path: Destination file.
            target: Instrument the map belongs to; when given, entries carry each property's kind,
                SCPI commands, and docstring.
        """
        specs = describe(target) if target is not None else None
        Path(path).write_text(json.dumps(self.to_dict(specs), indent=2) + "\n")

    def rename(self, names: dict[str, str | None]) -> ChannelMap:
        """Set the channel name of each attribute path in ``names`` (``None`` stops publishing it).

        Example::

            channel_map.rename({"ch_1.voltage_setpoint": "out1.v_set", "ch_1.current": "out1.i"})

        Returns:
            This map, so calls can be chained.

        Raises:
            KeyError: A path is not in the map.
        """
        missing = sorted(set(names) - set(self.channels))
        if missing:
            raise KeyError(f"Not in the channel map: {missing}")
        self.channels.update(names)
        return self

    def exclude(self, *patterns: str) -> ChannelMap:
        """Stop publishing every attribute path that matches one of the glob ``patterns``.

        Example::

            channel_map.exclude("ch_2.*", "ch_3.*")

        Returns:
            This map, so calls can be chained.

        Raises:
            KeyError: A pattern matches no path, which is usually a typo.
        """
        for path in self._matching(patterns):
            self.channels[path] = None
        return self

    def select(self, *patterns: str) -> ChannelMap:
        """Stop publishing every attribute path that matches none of the glob ``patterns``.

        Example::

            channel_map.select("ch_1.*")  # publish output 1 only

        Returns:
            This map, so calls can be chained.

        Raises:
            KeyError: A pattern matches no path, which is usually a typo.
        """
        keep = self._matching(patterns)
        for path in self.channels:
            if path not in keep:
                self.channels[path] = None
        return self

    def preview(self, target: CommonBase | type[CommonBase], name: str | None = None) -> list[dict[str, str | None]]:
        """Show exactly which channels each property publishes, separately for commands and measurements.

        Writing a property publishes a :class:`~instro.lib.types.Command` on the ``command`` channel; reading it
        publishes a :class:`~instro.lib.types.Measurement` on the ``measurement`` channel. A control has both,
        a measurement only the second, a setting only the first.

        Example::

            import pandas as pd
            pd.DataFrame(channel_map.preview(supply, "psu"))

        Args:
            target: The instrument (instance or class) the map is for.
            name: Instrument name to prefix, as passed to `attach` or `PymeasureInstrument`.

        Returns:
            One row per property with keys ``property``, ``kind``, ``command``, and ``measurement``;
            a channel is ``None`` when the property doesn't publish in that direction.

        Raises:
            ValueError: The map does not fit ``target`` (see `resolve`).
        """
        specs = describe(target)
        resolved = self.resolve(specs)
        prefix = f"{name}." if name else ""
        rows: list[dict[str, str | None]] = []
        for spec in specs:
            channel = resolved.get(spec.path)
            rows.append(
                {
                    "property": spec.path,
                    "kind": spec.kind,
                    "command": f"{prefix}{channel}.cmd" if channel and spec.writable else None,
                    "measurement": f"{prefix}{channel}" if channel and spec.readable else None,
                }
            )
        return rows

    def _matching(self, patterns: tuple[str, ...]) -> set[str]:
        matched: set[str] = set()
        for pattern in patterns:
            paths = fnmatch.filter(self.channels, pattern)
            if not paths:
                raise KeyError(f"No channel map entry matches {pattern!r}")
            matched.update(paths)
        return matched

    def resolve(self, specs: list[ChannelSpec]) -> dict[str, str]:
        """Return attribute path to channel name for every property that publishes.

        Args:
            specs: Inspection results for the instrument the map is applied to.

        Raises:
            ValueError: The map names a path the instrument does not have (a typo, or a pymeasure
                upgrade renamed it), a channel name is invalid or ends in ``.cmd``, or two
                properties map to the same channel name.
        """
        known = {spec.path: spec for spec in specs}
        unknown = sorted(set(self.channels) - set(known))
        if unknown:
            raise ValueError(
                f"Channel map names properties the instrument does not have: {unknown}. "
                "Regenerate the map with `instro pymeasure map` if pymeasure renamed them."
            )

        resolved: dict[str, str] = {}
        for path, spec in known.items():
            if path in self.channels:
                name = self.channels[path]
            elif self.include_unmapped:
                name = spec.default_name
            else:
                continue
            if name is None:
                continue
            if not _VALID_NAME.match(name):
                raise ValueError(f"Channel name {name!r} for {path!r} must be dot-separated [A-Za-z0-9_] segments")
            if name == "cmd" or name.endswith(".cmd"):
                raise ValueError(f"Channel name {name!r} for {path!r} must not end in '.cmd'; it is added to commands")
            resolved[path] = name

        duplicates = sorted(name for name, count in Counter(resolved.values()).items() if count > 1)
        if duplicates:
            raise ValueError(f"Channel map gives several properties the same channel name: {duplicates}")
        return resolved


def _qualified_name(target: Any) -> str:
    cls = original_class(target if isinstance(target, type) else type(target))
    return f"{cls.__module__}.{cls.__qualname__}"
