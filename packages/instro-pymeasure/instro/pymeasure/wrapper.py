"""Publish every command sent to, and every value read from, a pymeasure instrument."""

from __future__ import annotations

import functools
import inspect
import logging
import threading
import time
from enum import Enum
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from types import FunctionType
from typing import Any, Callable, Generic, TypeVar

from pymeasure.instruments.common_base import CommonBase

from instro.lib.instrument import Instrument
from instro.lib.publishers import Publisher
from instro.lib.types import BackgroundDaemonConfig, Command, Measurement
from instro.pymeasure.channel_map import ChannelMap
from instro.pymeasure.inspection import describe, original_class

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=CommonBase)

_SINK_ATTR = "_instro_sink"
# Longer lists/arrays are traces or buffers with no timebase to attach; they are skipped.
_MAX_SPLIT = 16

_published_classes: dict[type, type] = {}
_published_classes_lock = threading.Lock()


class _Sink:
    """Per-object link from a wrapped pymeasure object back to its `PymeasureInstrument`."""

    def __init__(self, owner: PymeasureInstrument, prefix: str):
        self.owner = owner
        self.prefix = prefix


class _PublishingProperty:
    """Delegates to the original pymeasure property, then publishes what was read or written."""

    def __init__(self, original: property, attr: str):
        self.original = original
        self.attr = attr
        self.__doc__ = original.__doc__

    def __get__(self, obj: Any, objtype: type | None = None) -> Any:
        # Class access returns the original so pymeasure's own scans (DynamicProperty setup) still see it.
        if obj is None:
            return self.original
        sink = obj.__dict__.get(_SINK_ATTR)
        if sink is None:
            return self.original.__get__(obj, objtype)
        return sink.owner._on_read(sink.prefix + self.attr, lambda: self.original.__get__(obj, objtype))

    def __set__(self, obj: Any, value: Any) -> None:
        sink = obj.__dict__.get(_SINK_ATTR)
        if sink is None:
            self.original.__set__(obj, value)
            return
        sink.owner._on_write(sink.prefix + self.attr, value, lambda: self.original.__set__(obj, value))

    def __delete__(self, obj: Any) -> None:
        self.original.__delete__(obj)


def _locked(method: FunctionType) -> Callable:
    @functools.wraps(method)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        sink = self.__dict__.get(_SINK_ATTR)
        if sink is None:
            return method(self, *args, **kwargs)
        with sink.owner._lock:
            return method(self, *args, **kwargs)

    return wrapper


def _published_class(cls: type) -> type:
    """Return (and cache) a subclass of ``cls`` whose properties publish and whose methods hold the lock."""
    with _published_classes_lock:
        cached = _published_classes.get(cls)
        if cached is not None:
            return cached
        namespace: dict[str, Any] = {
            "_instro_original_class": cls,
            "__module__": cls.__module__,
            "__qualname__": cls.__qualname__,
            "__doc__": cls.__doc__,
        }
        for name in dir(cls):
            if name.startswith("_"):
                continue
            attr = inspect.getattr_static(cls, name)
            if isinstance(attr, property):
                namespace[name] = _PublishingProperty(attr, name)
            elif isinstance(attr, FunctionType):
                namespace[name] = _locked(attr)
        published = type(cls.__name__, (cls,), namespace)
        _published_classes[cls] = published
        return published


def _scalar(value: Any) -> float | str | None:
    if isinstance(value, Enum):
        value = value.value
    if not isinstance(value, (int, float, str)) and hasattr(value, "magnitude"):  # pint Quantity
        value = value.magnitude
    if getattr(value, "ndim", None) == 0 and hasattr(value, "item"):  # numpy scalar
        value = value.item()
    if isinstance(value, (bool, int, float)):
        return float(value)
    if isinstance(value, str):
        return value
    return None


def _flatten(value: Any) -> list[tuple[str, float | str]] | None:
    """Return ``(channel suffix, value)`` pairs to publish, or ``None`` if the value can't be published."""
    scalar = _scalar(value)
    if scalar is not None:
        return [("", scalar)]
    if isinstance(value, (list, tuple)) or getattr(value, "ndim", None) == 1:
        items = [_scalar(item) for item in value]
        if 0 < len(items) <= _MAX_SPLIT and all(item is not None for item in items):
            return [(f".{index}", item) for index, item in enumerate(items) if item is not None]
    return None


class PymeasureInstrument(Instrument, Generic[T]):
    """An instro `Instrument` that publishes the traffic of a pymeasure instrument.

    Every property assignment on the wrapped instrument (and its channels) publishes a
    :class:`~instro.lib.types.Command` on ``<name>.<channel>.cmd``; every property read publishes a
    :class:`~instro.lib.types.Measurement` on ``<name>.<channel>``. This includes property access
    inside the driver's own methods, such as ``apply_voltage()``.

    The pymeasure object keeps working exactly as before and stays an instance of its class: the
    wrapper swaps in a generated subclass, and `close` (or `detach`) swaps the original back.
    """

    def __init__(
        self,
        name: str,
        instrument: T,
        publishers: list[Publisher] | None = None,
        *,
        channel_map: ChannelMap | dict[str, Any] | str | Path | None = None,
        poll: list[str] | None = None,
        device_attr: str = "device",
        close_device: bool = True,
        background_config: BackgroundDaemonConfig | None = None,
        **kwargs: Any,
    ):
        """Wrap ``instrument`` so its commands and readings reach ``publishers``.

        Example::

            from pymeasure.instruments.keysight import KeysightE36312A
            from instro.lib.publishers import FilePublisher
            from instro.pymeasure import PymeasureInstrument

            with PymeasureInstrument(
                name="psu",
                instrument=KeysightE36312A("USB0::0x2A8D::0x1102::MY123::INSTR"),
                publishers=[FilePublisher(format="jsonl")],
                channel_map="psu_map.json",
                poll=["ch_1.voltage", "ch_1.current"],
            ) as psu:
                psu.device.ch_1.voltage_setpoint = 5  # publishes psu.ch1.voltage_setpoint.cmd
                psu.start()                           # polls ch_1.voltage/current every second
                print(psu.get_channel("ch1.current", wait_for_new_samples=True).latest)

        Args:
            name: Channel-name prefix for everything published.
            instrument: The pymeasure instrument, already constructed (connected).
            publishers: Publishers that receive the commands and measurements.
            channel_map: Which properties publish and under which names: a `ChannelMap`, its dict
                form, or the path of a JSON file from ``instro pymeasure map``. ``None`` publishes
                every property under the names the inspection rules give it.
            poll: Attribute paths (``"ch_1.voltage"``) the background daemon reads every
                ``background_interval`` seconds once `start` is called.
            device_attr: Extra attribute name for the pymeasure instrument, in addition to
                ``device``; e.g. ``"smu"`` makes ``wrapper.smu`` work.
            close_device: Close the pymeasure instrument's adapter in `close`.
            background_config: Background-daemon settings.
            **kwargs: Default tags, or ``dataset_rid=`` (see `Instrument`).

        Raises:
            RuntimeError: ``instrument`` is already attached to another `PymeasureInstrument`.
            ValueError: The channel map does not fit ``instrument``, a ``poll`` path is unknown or
                not readable, or ``device_attr`` collides with an existing attribute.
        """
        existing = instrument.__dict__.get(_SINK_ATTR)
        if existing is not None:
            raise RuntimeError(
                f"{type(instrument).__name__} is already attached to instro instrument '{existing.owner.name}'"
            )
        super().__init__(name, publishers=publishers, background_config=background_config, **kwargs)
        self._device: T = instrument
        self._lock = threading.RLock()
        self._close_device = close_device
        self._attached: list[tuple[Any, type]] = []
        self._warned: set[str] = set()

        specs = describe(instrument)
        self._channel_names = self._load_map(channel_map).resolve(specs)
        self._add_pymeasure_tags(instrument)

        if device_attr != "device":
            if not device_attr.isidentifier() or hasattr(self, device_attr):
                raise ValueError(f"device_attr {device_attr!r} is not a free attribute name on {type(self).__name__}")
            setattr(self, device_attr, instrument)

        readable = {spec.path for spec in specs if spec.readable}
        self._poll = list(poll or [])
        for path in self._poll:
            if path not in readable:
                raise ValueError(f"poll path {path!r} is not a readable property of {type(instrument).__name__}")
            if path not in self._channel_names:
                logger.warning("poll path %r is read but not published: the channel map excludes it", path)
        if self._poll:
            self.add_background_daemon_function(self._poll_once)

        self._attach(instrument, "")
        logger.info(
            "Attached pymeasure %s as '%s' (%d published properties)",
            type(instrument).__name__,
            name,
            len(self._channel_names),
        )

    @property
    def device(self) -> T:
        """The wrapped pymeasure instrument; use it exactly as without instro."""
        return self._device

    def __enter__(self) -> PymeasureInstrument[T]:
        """Return this wrapper (typed, unlike `Instrument.__enter__`) for ``with`` blocks."""
        self.open()
        return self

    @property
    def channel_map(self) -> dict[str, str]:
        """Attribute path to channel name (without the instrument prefix) for every published property."""
        return dict(self._channel_names)

    def detach(self) -> None:
        """Stop publishing and restore the pymeasure objects' original classes. Idempotent."""
        while self._attached:
            obj, cls = self._attached.pop()
            obj.__dict__.pop(_SINK_ATTR, None)
            obj.__class__ = cls

    def close(self) -> None:
        """Stop the daemon, close the publishers, detach, and close the device if ``close_device``."""
        try:
            super().close()
        finally:
            self.detach()
            if self._close_device:
                self._device.adapter.close()

    @staticmethod
    def _load_map(channel_map: ChannelMap | dict[str, Any] | str | Path | None) -> ChannelMap:
        if channel_map is None:
            return ChannelMap.defaults()
        if isinstance(channel_map, ChannelMap):
            return channel_map
        if isinstance(channel_map, dict):
            return ChannelMap.from_dict(channel_map)
        return ChannelMap.load(channel_map)

    def _add_pymeasure_tags(self, instrument: CommonBase) -> None:
        for package in ("instro-pymeasure", "pymeasure"):
            try:
                self.default_tags.setdefault(package, version(package))
            except PackageNotFoundError:
                pass
        self.default_tags.setdefault("pymeasure-class", original_class(type(instrument)).__name__)

    def _attach(self, obj: Any, prefix: str) -> None:
        cls = type(obj)
        obj.__class__ = _published_class(cls)
        obj.__dict__[_SINK_ATTR] = _Sink(self, prefix)
        self._attached.append((obj, cls))
        for attr, child in list(vars(obj).items()):
            if isinstance(child, CommonBase) and attr != "parent" and not attr.startswith("_"):
                if child.__dict__.get(_SINK_ATTR) is None:
                    self._attach(child, f"{prefix}{attr}.")

    def _poll_once(self) -> None:
        for path in self._poll:
            target: Any = self._device
            for attr in path.split("."):
                target = getattr(target, attr)

    def _on_read(self, path: str, read: Callable[[], Any]) -> Any:
        with self._lock:
            value = read()
        name = self._channel_names.get(path)
        if name is not None:
            self._publish_values(path, name, value, timestamp=time.time_ns(), command=False)
        return value

    def _on_write(self, path: str, value: Any, write: Callable[[], None]) -> None:
        with self._lock:
            write()
        name = self._channel_names.get(path)
        if name is not None:
            self._publish_values(path, name, value, timestamp=time.time_ns(), command=True)

    def _publish_values(self, path: str, name: str, value: Any, *, timestamp: int, command: bool) -> None:
        pairs = _flatten(value)
        if pairs is None:
            if path not in self._warned:
                self._warned.add(path)
                logger.warning(
                    "Not publishing '%s' on instrument '%s': %s values aren't publishable "
                    "(scalars and lists of up to %d scalars are)",
                    path,
                    self.name,
                    type(value).__name__,
                    _MAX_SPLIT,
                )
            return
        tags = dict(self.default_tags)
        if command:
            self.publish(
                Command(
                    channel_data={f"{self.name}.{name}{suffix}.cmd": item for suffix, item in pairs},
                    timestamp=timestamp,
                    tags=tags,
                )
            )
        else:
            channel_data: dict[str, list[float] | list[str]] = {}
            for suffix, item in pairs:
                key = f"{self.name}.{name}{suffix}"
                if isinstance(item, str):
                    channel_data[key] = [item]
                else:
                    channel_data[key] = [float(item)]
            self.publish(Measurement(channel_data=channel_data, timestamps=[timestamp], tags=tags))


def attach(
    instrument: T,
    name: str,
    publishers: list[Publisher] | None = None,
    *,
    channel_map: ChannelMap | dict[str, Any] | str | Path | None = None,
    poll: list[str] | None = None,
    **kwargs: Any,
) -> PymeasureInstrument[T]:
    """Start publishing an existing pymeasure instrument's traffic, without changing how it is used.

    Your code keeps the pymeasure object and keeps calling it; this only adds publishing. Closing
    the returned handle flushes and closes the publishers and detaches, but leaves the instrument
    connected (``close_device=False``).

    Example::

        keithley = Keithley2400("GPIB::24")              # existing code
        recorder = attach(keithley, "smu", [FilePublisher(format="jsonl")])
        keithley.source_voltage = 1.0                    # existing code, now published
        recorder.close()

    Args:
        instrument: The pymeasure instrument.
        name: Channel-name prefix for everything published.
        publishers: Publishers that receive the commands and measurements.
        channel_map: See `PymeasureInstrument`.
        poll: See `PymeasureInstrument`.
        **kwargs: Any other `PymeasureInstrument` argument, default tags, or ``dataset_rid=``.

    Returns:
        The `PymeasureInstrument` doing the publishing; use it as a context manager or call ``close()``.
    """
    kwargs.setdefault("close_device", False)
    return PymeasureInstrument(name, instrument, publishers, channel_map=channel_map, poll=poll, **kwargs)
