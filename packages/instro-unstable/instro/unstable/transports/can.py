"""CAN transport driver. Wraps python-can; callers own frame encoding, this owns I/O, receive demux, and locking."""

from __future__ import annotations

import collections
import dataclasses
import logging
from typing import Any, Callable

import can

from instro.lib.transports.transport_base import TransportBase

logger = logging.getLogger(__name__)

DEFAULT_SUBSCRIPTION_DEPTH = 512


@dataclasses.dataclass
class CanConfig:
    """Connection parameters for a python-can bus.

    ``interface`` is required because a CAN channel alone does not identify the
    adapter type (``"gs_usb"``, ``"slcan"``, ``"socketcan"``, ...), unlike a
    VISA resource string.
    """

    channel: str | int
    interface: str
    bitrate: int = 500_000
    bus_kwargs: dict[str, Any] = dataclasses.field(default_factory=dict)


class CanSubscription:
    """A subscriber's view of the shared bus: drained frames matching its filter, oldest dropped when full."""

    def __init__(self, transport: CanDriver, frame_filter: Callable[[can.Message], bool], depth: int) -> None:
        self._transport = transport
        self._filter = frame_filter
        self._frames: collections.deque[can.Message] = collections.deque(maxlen=depth)

    def drain(self) -> list[can.Message]:
        """Pull pending bus frames through the demux and return this subscription's matches, oldest first."""
        return self._transport._drain_for(self)


class CanDriver(TransportBase):
    """Transport for CAN-bus instruments. Composed by concrete drivers, not extended.

    CAN is a broadcast bus, so this differs from request/response transports in two ways.
    Several drivers on one physical adapter share this transport, each passing itself as the
    holder to :meth:`open`/:meth:`close`; the bus closes when the last owner leaves. Receiving
    goes through :meth:`subscribe`: any subscriber's drain routes pending frames to every
    matching subscription, so one driver's read never consumes another driver's frames.
    Send is fire-and-forget; there is no request/response helper.
    """

    def __init__(self, config: CanConfig) -> None:
        super().__init__()
        self._config = config
        self._bus: can.BusABC | None = None
        self._subscriptions: list[CanSubscription] = []

    @property
    def is_open(self) -> bool:
        """Whether the underlying python-can bus is currently open."""
        return self._bus is not None

    def _open_session(self) -> None:
        """Open the python-can bus. Idempotent. Called by open()."""
        with self._lock:
            if self._bus is not None:
                return
            cfg = self._config
            logger.info("Opening CAN bus %s on interface %s at %d bit/s", cfg.channel, cfg.interface, cfg.bitrate)
            self._bus = can.Bus(interface=cfg.interface, channel=cfg.channel, bitrate=cfg.bitrate, **cfg.bus_kwargs)

    def _teardown_session(self) -> None:
        """Shut down the python-can bus and drop buffered frames, so a reopen never replays the previous session."""
        if self._bus is None:
            return
        try:
            self._bus.shutdown()
        finally:
            self._bus = None
            for subscription in self._subscriptions:
                subscription._frames.clear()

    def send(self, arbitration_id: int, data: bytes, is_extended_id: bool = True) -> None:
        """Send one frame; the caller owns payload encoding."""
        with self._lock:
            bus = self._require_open_locked()
            bus.send(can.Message(arbitration_id=arbitration_id, data=data, is_extended_id=is_extended_id))

    def subscribe(
        self,
        frame_filter: Callable[[can.Message], bool],
        depth: int = DEFAULT_SUBSCRIPTION_DEPTH,
    ) -> CanSubscription:
        """Register a receive filter and return the subscription whose drain() yields the matching frames."""
        with self._lock:
            subscription = CanSubscription(self, frame_filter, depth)
            self._subscriptions.append(subscription)
            return subscription

    def unsubscribe(self, subscription: CanSubscription) -> None:
        """Stop routing frames to ``subscription``; unknown subscriptions are ignored."""
        with self._lock:
            if subscription in self._subscriptions:
                self._subscriptions.remove(subscription)

    def _drain_for(self, subscription: CanSubscription) -> list[can.Message]:
        """Route all pending bus frames to their subscribers, then hand over ``subscription``'s buffer."""
        with self._lock:
            bus = self._require_open_locked()
            while (message := bus.recv(timeout=0.0)) is not None:
                for candidate in self._subscriptions:
                    if candidate._filter(message):
                        candidate._frames.append(message)
            frames = list(subscription._frames)
            subscription._frames.clear()
            return frames

    def _require_open_locked(self) -> can.BusABC:
        if self._bus is None:
            raise RuntimeError(f"CanDriver is not open. Call open() first. Channel: {self._config.channel}")
        return self._bus
