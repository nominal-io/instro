"""Loopback TCP receiver that keeps the latest value of every channel any connected script publishes."""

import logging
import socketserver
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field, replace

from instro.lib.consumers.monitor.protocol import DEFAULT_HOST, DEFAULT_PORT, decode
from instro.lib.types import Command, Measurement

logger = logging.getLogger(__name__)

_ARRIVAL_HISTORY = 512


@dataclass
class ChannelState:
    """Latest known state of one channel as seen by the monitor."""

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
    """Thread-safe latest-value store fed by `MonitorServer` and read by the UI."""

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


class _FrameHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        state: MonitorState = self.server.state  # type: ignore[attr-defined]
        source = f"{self.client_address[0]}:{self.client_address[1]}"
        state.source_connected(source)
        logger.info("Monitor source connected: %s", source)
        try:
            for line in self.rfile:
                if not line.strip():
                    continue
                try:
                    state.ingest(source, decode(line))
                except (ValueError, KeyError, TypeError) as e:
                    logger.warning("Dropping malformed monitor frame from %s: %s", source, e)
        finally:
            state.source_disconnected(source)
            logger.info("Monitor source disconnected: %s", source)


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    # SO_REUSEADDR on Windows lets a second process bind an in-use port, so only enable it elsewhere.
    allow_reuse_address = sys.platform != "win32"

    def __init__(self, address: tuple[str, int], state: MonitorState):
        self.state = state
        super().__init__(address, _FrameHandler)


class MonitorServer:
    """Accept `MonitorPublisher` connections on a background thread and feed a `MonitorState`."""

    def __init__(self, state: MonitorState, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
        self.state = state
        self._server = _Server((host, port), state)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True, name="instro-monitor")

    @property
    def host(self) -> str:
        return str(self._server.server_address[0])

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    def start(self) -> None:
        self._thread.start()
        logger.info("Monitor listening on %s:%d", self.host, self.port)

    def shutdown(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()
