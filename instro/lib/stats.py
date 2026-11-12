"""Counters for the data an instrument publishes: per channel, and per publisher.

`ChannelState` and `MonitorState` come from the live terminal monitor (nominal-io/instro#510), which feeds them
from network sources; an `Instrument` feeds the same store from its own `publish` calls.
"""

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field, replace

from instro.lib.types import Command, Measurement

logger = logging.getLogger(__name__)

_ARRIVAL_HISTORY = 512
_ARRIVAL_BUCKET_S = 0.1
"""Arrivals closer together than this share one history entry, so the history spans at least
``_ARRIVAL_HISTORY * _ARRIVAL_BUCKET_S`` seconds however fast a channel updates."""
_ERROR_LOG_INTERVAL_S = 60.0


@dataclass
class ChannelState:
    """Latest known state of one channel."""

    channel: str
    """The full channel name, such as ``"bench.ch1.voltage"``."""
    kind: str
    """``"measurement"`` or ``"command"``."""
    value: float | str | None
    """The newest value."""
    timestamp: int
    """The newest value's timestamp, in nanoseconds since the epoch."""
    source: str
    """Who published the newest value: the instrument name, or a monitor connection's ``host:port``."""
    count: int = 0
    """Samples received so far."""
    connected: bool = True
    """Whether the source is still connected. Always `True` for an instrument's own channels."""
    last_received: float = 0.0
    """When the newest value arrived, as a `time.monotonic` reading in seconds."""
    arrivals: deque[tuple[float, int]] = field(default_factory=lambda: deque(maxlen=_ARRIVAL_HISTORY), repr=False)
    """Recent arrivals as ``(time.monotonic() seconds, sample count)`` pairs, used by `rate`. Arrivals within
    ~0.1 s of each other are merged into one pair, so `rate` is accurate for windows up to about 50 s."""

    @property
    def instrument(self) -> str:
        """The channel name's prefix before the first ``.``, which is the instrument name."""
        return self.channel.split(".", 1)[0]

    def rate(self, window_s: float, now: float | None = None) -> float:
        """Return the samples per second received over the trailing ``window_s`` seconds.

        Args:
            window_s: Length of the window, in seconds.
            now: End of the window as a `time.monotonic` reading, in seconds; the current time when omitted.

        Returns:
            float: Samples per second.
        """
        now = time.monotonic() if now is None else now
        cutoff = now - window_s
        return sum(n for t, n in self.arrivals if t >= cutoff) / window_s


class MonitorState:
    """Thread-safe store of the latest value, count and rate of every channel, fed by one or more sources."""

    def __init__(self, rate_window_s: float = 5.0):
        """Create an empty store.

        Args:
            rate_window_s: Window, in seconds, that readers such as the notebook card use for `ChannelState.rate`.

        Example:
            .. code-block:: python

                from instro.lib.stats import MonitorState
                from instro.lib.types import Measurement

                state = MonitorState()
                state.ingest("bench", Measurement(channel_data={"bench.v": [1.0, 1.1]}, timestamps=[10, 20]))
                [(s.channel, s.value, s.count) for s in state.snapshot()]  # [("bench.v", 1.1, 2)]
        """
        self.rate_window_s = rate_window_s
        self._lock = threading.Lock()
        self._channels: dict[str, ChannelState] = {}
        self._sources: dict[str, bool] = {}
        self.frames_received = 0

    def source_connected(self, source: str) -> None:
        """Mark ``source``, such as a monitor connection's ``host:port``, as connected."""
        with self._lock:
            self._sources[source] = True

    def source_disconnected(self, source: str) -> None:
        """Mark ``source`` and every channel it last published as disconnected. The channels keep their values."""
        with self._lock:
            self._sources[source] = False
            for state in self._channels.values():
                if state.source == source:
                    state.connected = False

    def ingest(self, source: str, data: Measurement | Command) -> None:
        """Record the newest sample of every channel in ``data`` against ``source``."""
        now = time.monotonic()
        with self._lock:
            self.frames_received += 1
            if isinstance(data, Measurement):
                for channel, values in data.channel_data.items():
                    if not values:
                        continue
                    timestamp = data.timestamps[-1] if data.timestamps else 0
                    self._update(channel, "measurement", values[-1], timestamp, len(values), source, now)
            else:
                for channel, value in data.channel_data.items():
                    self._update(channel, "command", value, data.timestamp, 1, source, now)

    def _update(
        self, channel: str, kind: str, value: float | str, timestamp: int, n: int, source: str, now: float
    ) -> None:
        state = self._channels.get(channel)
        if state is None:
            state = ChannelState(channel=channel, kind=kind, value=value, timestamp=timestamp, source=source)
            self._channels[channel] = state
        state.kind = kind
        state.value = value
        state.timestamp = timestamp
        state.source = source
        state.connected = True
        state.count += n
        state.last_received = now
        if state.arrivals and now - state.arrivals[-1][0] < _ARRIVAL_BUCKET_S:
            bucket_start, bucket_count = state.arrivals[-1]
            state.arrivals[-1] = (bucket_start, bucket_count + n)
        else:
            state.arrivals.append((now, n))

    def snapshot(self) -> list[ChannelState]:
        """Return copies of every channel state, sorted by channel name."""
        with self._lock:
            ordered = sorted(self._channels.values(), key=lambda s: s.channel)
            return [replace(s, arrivals=deque(s.arrivals, maxlen=_ARRIVAL_HISTORY)) for s in ordered]

    def sources(self) -> dict[str, bool]:
        """Return every source seen so far, mapped to whether it is still connected."""
        with self._lock:
            return dict(self._sources)

    def clear(self) -> None:
        """Forget every channel and every disconnected source."""
        with self._lock:
            self._channels.clear()
            self._sources = {s: alive for s, alive in self._sources.items() if alive}


@dataclass(frozen=True)
class PublisherStats:
    """What one publisher has done with an instrument's data. A snapshot: it doesn't change after it is taken."""

    publisher: str
    """The publisher's class name."""
    sent: int
    """`Measurement` and `Command` objects the publisher accepted."""
    errors: int
    """Objects the publisher raised on. The instrument logged them and kept going."""
    last_sent_ns: int | None
    """When the publisher last accepted an object, in nanoseconds since the epoch."""
    last_error: str | None
    """The most recent error, as ``"<type>: <message>"``."""
    last_error_ns: int | None
    """When the most recent error happened, in nanoseconds since the epoch."""


class _PublisherCounter:
    """Counts one publisher's sends and errors, and logs errors without flooding the log."""

    def __init__(self, publisher: object):
        # Holding the publisher keeps its id() from being reused while this counter is keyed on it.
        self.publisher = publisher
        self._lock = threading.Lock()
        self._sent = 0
        self._errors = 0
        self._last_sent_ns: int | None = None
        self._last_error: str | None = None
        self._last_error_ns: int | None = None
        self._last_logged: float | None = None
        self._errors_since_log = 0

    def record_sent(self) -> None:
        with self._lock:
            self._sent += 1
            self._last_sent_ns = time.time_ns()

    def record_error(self, error: Exception, instrument: str) -> None:
        """Count ``error`` and log it: in full the first time, then at most once a minute with a count."""
        now = time.monotonic()
        with self._lock:
            self._errors += 1
            self._last_error = f"{type(error).__name__}: {error}"
            self._last_error_ns = time.time_ns()
            self._errors_since_log += 1
            first = self._last_logged is None
            due = self._last_logged is None or now - self._last_logged >= _ERROR_LOG_INTERVAL_S
            if not due:
                return
            repeats = self._errors_since_log
            latest = self._last_error
            self._last_logged = now
            self._errors_since_log = 0
        name = type(self.publisher).__name__
        if first:
            logger.error(
                "Publisher '%s' on instrument '%s' raised; dropping that data for it and continuing with the "
                "other publishers. Later failures are logged at most once a minute.",
                name,
                instrument,
                exc_info=error,
            )
        else:
            logger.warning(
                "Publisher '%s' on instrument '%s' failed %d time(s) since the last report; latest: %s",
                name,
                instrument,
                repeats,
                latest,
            )

    def snapshot(self) -> PublisherStats:
        with self._lock:
            return PublisherStats(
                publisher=type(self.publisher).__name__,
                sent=self._sent,
                errors=self._errors,
                last_sent_ns=self._last_sent_ns,
                last_error=self._last_error,
                last_error_ns=self._last_error_ns,
            )
