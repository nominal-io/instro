"""Publish every command sent to, and every value read from, a QCoDeS instrument."""

from __future__ import annotations

import functools
import logging
import threading
import time
from enum import Enum
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Callable, Generic, TypeVar

from qcodes.instrument import InstrumentBase
from qcodes.parameters import ParameterBase

from instro.lib.instrument import Instrument
from instro.lib.publishers import Publisher
from instro.lib.types import BackgroundDaemonConfig, Command, Measurement
from instro.qcodes.channel_map import ChannelMap
from instro.qcodes.inspection import describe, iter_parameters

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=InstrumentBase)

_OWNER_ATTR = "_instro_owner"
# Longer lists/arrays are traces or buffers with no timebase to attach; they are skipped.
_MAX_SPLIT = 16
_ABSENT = object()


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
    if not (isinstance(value, (list, tuple)) or getattr(value, "ndim", None) == 1):
        return None
    # Check the length first so long traces are skipped without converting every sample.
    if not 0 < len(value) <= _MAX_SPLIT:
        return None
    pairs: list[tuple[str, float | str]] = []
    for index, item in enumerate(value):
        converted = _scalar(item)
        if converted is None:
            return None
        pairs.append((f".{index}", converted))
    return pairs


class QcodesInstrument(Instrument, Generic[T]):
    """An instro `Instrument` that publishes the traffic of a QCoDeS instrument.

    Every parameter set on the wrapped instrument (and its submodules and channels) publishes a
    :class:`~instro.lib.types.Command` on ``<name>.<channel>.cmd``; every parameter get publishes a
    :class:`~instro.lib.types.Measurement` on ``<name>.<channel>``. This includes parameter access
    inside the driver's own methods.

    The QCoDeS object keeps working exactly as before. The wrapper replaces the ``get`` and ``set``
    of each parameter on that instance, and `close` (or `detach`) puts the originals back.
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

            from qcodes.instrument_drivers.mock_instruments import DummyInstrument
            from instro.lib import FilePublisher
            from instro.qcodes import QcodesInstrument

            with QcodesInstrument(
                name="dac",
                instrument=DummyInstrument("dac_driver", gates=["g1", "g2"]),
                publishers=[FilePublisher(format="jsonl")],
                poll=["g1"],
            ) as dac:
                dac.device.g1(1.5)  # publishes dac.g1.cmd
                dac.start()         # polls g1 every second
                print(dac.get_channel("g1", wait_for_new_samples=True).latest)

        Args:
            name: Channel-name prefix for everything published.
            instrument: The QCoDeS instrument, already constructed (connected).
            publishers: Publishers that receive the commands and measurements.
            channel_map: Which parameters publish and under which names: a `ChannelMap`, its dict
                form, or the path of a JSON file from ``instro qcodes map``. ``None`` publishes
                every parameter under the names the inspection rules give it.
            poll: Parameter paths (``"ch1.voltage"``) the background daemon reads every
                ``background_interval`` seconds once `start` is called.
            device_attr: Extra attribute name for the QCoDeS instrument, in addition to
                ``device``; e.g. ``"smu"`` makes ``wrapper.smu`` work.
            close_device: Close the QCoDeS instrument in `close`. This releases its connection and its
                QCoDeS name. `close` doesn't turn outputs off; do that first if you want the instrument
                left in its safe state.
            background_config: Background-daemon settings.
            **kwargs: Default tags, or ``dataset_rid=`` (see `Instrument`).

        Raises:
            RuntimeError: ``instrument`` is already attached to another `QcodesInstrument`.
            ValueError: The channel map does not fit ``instrument``, a ``poll`` path is unknown or
                not gettable, or ``device_attr`` collides with an existing attribute.
        """
        existing = instrument.__dict__.get(_OWNER_ATTR)
        if existing is not None:
            raise RuntimeError(
                f"{type(instrument).__name__} is already attached to instro instrument '{existing.name}'"
            )
        super().__init__(name, publishers=publishers, background_config=background_config, **kwargs)
        self._device: T = instrument
        self._lock = threading.RLock()
        self._close_device = close_device
        # (object, attribute, value the instance held before, or _ABSENT if the class provided it)
        self._patched: list[tuple[Any, str, Any]] = []
        self._warned: set[str] = set()

        specs = describe(instrument)
        self._channel_names = self._load_map(channel_map).resolve(specs)
        self._add_qcodes_tags(instrument)

        if device_attr != "device":
            if not device_attr.isidentifier() or hasattr(self, device_attr):
                raise ValueError(f"device_attr {device_attr!r} is not a free attribute name on {type(self).__name__}")
            setattr(self, device_attr, instrument)

        parameters = dict(iter_parameters(instrument))
        readable = {spec.path for spec in specs if spec.readable}
        self._poll = list(poll or [])
        for path in self._poll:
            if path not in readable:
                raise ValueError(f"poll path {path!r} is not a gettable parameter of {type(instrument).__name__}")
            if path not in self._channel_names:
                logger.warning("poll path %r is read but not published: the channel map excludes it", path)
        self._poll_parameters = [parameters[path] for path in self._poll]
        if self._poll:
            self.add_background_daemon_function(self._poll_once)

        try:
            self._attach(instrument, parameters)
        except Exception:
            self.detach()
            raise
        logger.info(
            "Attached QCoDeS %s as '%s' (%d published parameters)",
            type(instrument).__name__,
            name,
            len(self._channel_names),
        )

    @property
    def device(self) -> T:
        """The wrapped QCoDeS instrument; use it exactly as without instro."""
        return self._device

    def __enter__(self) -> QcodesInstrument[T]:
        """Return this wrapper (typed, unlike `Instrument.__enter__`) for ``with`` blocks."""
        self.open()
        return self

    @property
    def channel_map(self) -> dict[str, str]:
        """Parameter path to channel name (without the instrument prefix) for every published parameter."""
        return dict(self._channel_names)

    def detach(self) -> None:
        """Stop the background daemon, stop publishing, and restore the original ``get``/``set``. Idempotent."""
        # A daemon left running would keep reading the device, through whichever recorder attaches next.
        self.stop()
        while self._patched:
            obj, attr, original = self._patched.pop()
            if original is _ABSENT:
                obj.__dict__.pop(attr, None)
            else:
                obj.__dict__[attr] = original
        # A later recorder may own the device by now; only clear our own marker.
        if self._device.__dict__.get(_OWNER_ATTR) is self:
            self._device.__dict__.pop(_OWNER_ATTR, None)

    def close(self) -> None:
        """Stop the daemon, close the publishers, detach, and close the device if ``close_device``."""
        try:
            super().close()
        finally:
            try:
                self.detach()
            finally:
                if self._close_device:
                    close = getattr(self._device, "close", None)
                    if callable(close):
                        close()

    @staticmethod
    def _load_map(channel_map: ChannelMap | dict[str, Any] | str | Path | None) -> ChannelMap:
        if channel_map is None:
            return ChannelMap.defaults()
        if isinstance(channel_map, ChannelMap):
            return channel_map
        if isinstance(channel_map, dict):
            return ChannelMap.from_dict(channel_map)
        return ChannelMap.load(channel_map)

    def _add_qcodes_tags(self, instrument: InstrumentBase) -> None:
        for package in ("instro-qcodes", "qcodes"):
            try:
                self.default_tags.setdefault(package, version(package))
            except PackageNotFoundError:
                pass
        self.default_tags.setdefault("qcodes-class", type(instrument).__name__)

    def _patch(self, obj: Any, attr: str, replacement: Any) -> None:
        self._patched.append((obj, attr, obj.__dict__.get(attr, _ABSENT)))
        obj.__dict__[attr] = replacement

    def _attach(self, instrument: InstrumentBase, parameters: dict[str, ParameterBase]) -> None:
        instrument.__dict__[_OWNER_ATTR] = self
        # Direct I/O from user code is serialized with background polling, like parameter access.
        for method in ("write", "ask"):
            original = getattr(instrument, method, None)
            if callable(original):
                self._patch(instrument, method, self._locked(original))
        for path, parameter in parameters.items():
            if parameter.gettable:
                self._patch(parameter, "get", self._reader(path, parameter.get))
            if parameter.settable:
                self._patch(parameter, "set", self._writer(path, parameter.set))

    def _locked(self, method: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(method)
        def locked(*args: Any, **kwargs: Any) -> Any:
            with self._lock:
                return method(*args, **kwargs)

        return locked

    def _reader(self, path: str, get: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(get)
        def read(*args: Any, **kwargs: Any) -> Any:
            with self._lock:
                value = get(*args, **kwargs)
            self._publish_safely(path, value, command=False)
            return value

        return read

    def _writer(self, path: str, set_: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(set_)
        def write(*args: Any, **kwargs: Any) -> Any:
            with self._lock:
                result = set_(*args, **kwargs)
            if len(args) == 1 and not kwargs:
                self._publish_safely(path, args[0], command=True)
            elif not args and list(kwargs) == ["value"]:
                self._publish_safely(path, kwargs["value"], command=True)
            else:
                logger.warning("Not publishing the set of '%s' on instrument '%s': no single value", path, self.name)
            return result

        return write

    def _poll_once(self) -> None:
        for path, parameter in zip(self._poll, self._poll_parameters):
            # One failing parameter must not stop the others from being read.
            try:
                parameter.get()
            except Exception:
                logger.exception("Failed to read poll parameter '%s' on instrument '%s'", path, self.name)

    def _publish_safely(self, path: str, value: Any, *, command: bool) -> None:
        name = self._channel_names.get(path)
        if name is None:
            return
        # The instrument already acted; raising here would hide the value, or invite a retried set.
        try:
            self._publish_values(path, name, value, timestamp=time.time_ns(), command=command)
        except Exception:
            logger.exception(
                "Failed to publish %s of '%s' on instrument '%s'", "set" if command else "get", path, self.name
            )

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
) -> QcodesInstrument[T]:
    """Start publishing an existing QCoDeS instrument's traffic, without changing how it is used.

    Your code keeps the QCoDeS object and keeps calling it; this only adds publishing. Closing
    the returned handle flushes and closes the publishers and detaches, but leaves the instrument
    connected (``close_device=False``).

    Example::

        smu = Keithley2450("smu_driver", "USB0::0x05E6::0x2450::04567890::INSTR")  # existing code
        recorder = attach(smu, "smu", [FilePublisher(format="jsonl")])
        smu.source.voltage(1.0)                                                    # existing code, now published
        recorder.close()

    Args:
        instrument: The QCoDeS instrument.
        name: Channel-name prefix for everything published.
        publishers: Publishers that receive the commands and measurements.
        channel_map: See `QcodesInstrument`.
        poll: See `QcodesInstrument`.
        **kwargs: Any other `QcodesInstrument` argument, default tags, or ``dataset_rid=``.

    Returns:
        The `QcodesInstrument` doing the publishing; use it as a context manager or call ``close()``.
    """
    kwargs.setdefault("close_device", False)
    return QcodesInstrument(name, instrument, publishers, channel_map=channel_map, poll=poll, **kwargs)
