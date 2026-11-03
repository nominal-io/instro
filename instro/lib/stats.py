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
_ERROR_LOG_INTERVAL_S = 60.0


@dataclass
class ChannelState:
    """Latest known state of one channel."""

    channel: str
    kind: str
    value: float | str | None
    timestamp: int
    source: str
    count: int = 0
    connected: bool = True
    last_received: float = 0.0
    arrivals: deque[tuple[float, int]] = field(default_factory=lambda: deque(maxlen=_ARRIVAL_HISTORY), repr=False)

    @property
    def instrument(self) -> str:
        return self.channel.split(".", 1)[0]

    def rate(self, window_s: float, now: float | None = None) -> float:
        """Samples per second received over the trailing ``window_s`` seconds."""
        now = time.monotonic() if now is None else now
        cutoff = now - window_s
        return sum(n for t, n in self.arrivals if t >= cutoff) / window_s


class MonitorState:
    """Thread-safe store of the latest value, count and rate of every channel, fed by one or more sources."""

    def __init__(self, rate_window_s: float = 5.0):
        self.rate_window_s = rate_window_s
        self._lock = threading.Lock()
        self._channels: dict[str, ChannelState] = {}
        self._sources: dict[str, bool] = {}
        self.frames_received = 0

    def source_connected(self, source: str) -> None:
        with self._lock:
            self._sources[source] = True

    def source_disconnected(self, source: str) -> None:
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
        state.arrivals.append((now, n))

    def snapshot(self) -> list[ChannelState]:
        """Return copies of every channel state, sorted by channel name."""
        with self._lock:
            ordered = sorted(self._channels.values(), key=lambda s: s.channel)
            return [replace(s, arrivals=deque(s.arrivals, maxlen=_ARRIVAL_HISTORY)) for s in ordered]

    def sources(self) -> dict[str, bool]:
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
