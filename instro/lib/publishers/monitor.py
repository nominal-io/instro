"""Publisher that streams Measurement/Command data to a running `instro monitor` over loopback TCP."""

import contextlib
import logging
import queue
import socket
import threading

from instro.lib.consumers.monitor.protocol import DEFAULT_HOST, DEFAULT_PORT, encode
from instro.lib.types import Command, Measurement

logger = logging.getLogger(__name__)


class MonitorPublisher:
    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        max_queue_size: int = 1000,
        reconnect_interval: float = 1.0,
    ):
        """Stream data to the monitor at ``host:port``; never blocks or raises into the instrument.

        Frames queue up to ``max_queue_size`` and the oldest are dropped when full. If no
        monitor is listening the publisher warns once and keeps retrying every
        ``reconnect_interval`` seconds.
        """
        self._address = (host, port)
        self._reconnect_interval = reconnect_interval
        self._queue: queue.Queue[Measurement | Command] = queue.Queue(maxsize=max_queue_size)
        self._stop_event = threading.Event()
        self._warned_unavailable = False
        self.dropped = 0
        self._thread = threading.Thread(target=self._worker, daemon=True, name="monitor-publisher")
        self._thread.start()

    def publish(self, data: Measurement | Command, **kwargs) -> None:
        """Queue ``data`` for the sender thread, dropping the oldest frame if the queue is full."""
        if self._stop_event.is_set():
            return
        try:
            self._queue.put_nowait(data)
        except queue.Full:
            with contextlib.suppress(queue.Empty):
                self._queue.get_nowait()
            self.dropped += 1
            with contextlib.suppress(queue.Full):
                self._queue.put_nowait(data)

    def _connect(self) -> socket.socket | None:
        try:
            sock = socket.create_connection(self._address, timeout=1.0)
        except OSError as e:
            if not self._warned_unavailable:
                logger.warning(
                    "No instro monitor listening at %s:%d (%s); data is dropped until one starts. Run `instro monitor`.",
                    *self._address,
                    e,
                )
                self._warned_unavailable = True
            return None
        # A monitor that stops reading must not stall the sender forever; a timed-out send counts as a disconnect.
        sock.settimeout(2.0)
        self._warned_unavailable = False
        logger.info("Connected to instro monitor at %s:%d", *self._address)
        return sock

    def _send(self, sock: socket.socket, data: Measurement | Command) -> bool:
        try:
            frame = encode(data)
        except (TypeError, ValueError) as e:
            logger.warning("Dropping frame the monitor cannot encode: %s", e)
            return True
        try:
            sock.sendall(frame)
        except OSError as e:
            logger.info("Lost connection to instro monitor at %s:%d (%s); reconnecting", *self._address, e)
            return False
        return True

    def _worker(self) -> None:
        sock: socket.socket | None = None
        while not self._stop_event.is_set():
            if sock is None:
                sock = self._connect()
                if sock is None:
                    self._stop_event.wait(self._reconnect_interval)
                    continue
            try:
                data = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if not self._send(sock, data):
                sock.close()
                sock = None
        if sock is not None:
            while not self._queue.empty():
                if not self._send(sock, self._queue.get_nowait()):
                    break
            sock.close()

    def close(self) -> None:
        """Stop the sender thread, flushing queued frames if a monitor is connected."""
        self._stop_event.set()
        self._thread.join()
