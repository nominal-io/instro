"""CAN transport driver. Wraps python-can; callers own frame encoding, this owns I/O, receive demux, and locking."""

from __future__ import annotations

import collections
import dataclasses
import logging
import threading
import time
import weakref
from typing import Any, Callable

import can

from instro.lib.transports.transport_base import TransportBase

logger = logging.getLogger(__name__)

DEFAULT_SUBSCRIPTION_DEPTH = 512
RECEIVE_POLL_INTERVAL_S = 0.05
"""How long the receive thread blocks in ``bus.recv`` before re-checking for shutdown."""


def _prime_gs_usb_backend() -> None:
    """Load the bundled libusb into pyusb's backend cache; hosts without a system libusb (notably Windows) find no backend otherwise."""
    import libusb_package
    import usb.backend.libusb1

    usb.backend.libusb1.get_backend(find_library=libusb_package.find_library)


def _release_gs_usb_device(bus: can.BusABC) -> None:
    """Free the libusb handle python-can's gs_usb shutdown leaks; without this a reopen in the same process fails with EACCES."""
    import usb.util

    device = getattr(getattr(bus, "gs_usb", None), "gs_usb", None)
    if device is not None:
        usb.util.dispose_resources(device)


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


@dataclasses.dataclass(frozen=True)
class CanFrame:
    """A received frame plus the host wall-clock time it came off the bus.

    ``received_at_ns`` is ``time.time_ns()`` taken by the transport's receive thread as soon as
    python-can returned the frame, so it reflects arrival, not when a subscriber got around to
    draining. Adapter timestamps (``message.timestamp``) are left untouched; they are often a
    device counter (gs_usb) rather than wall-clock time.
    """

    message: can.Message
    received_at_ns: int


class CanSubscription:
    """A subscriber's view of the shared bus: received frames matching its filter, oldest dropped when full."""

    def __init__(self, transport: CanTransport, frame_filter: Callable[[can.Message], bool], depth: int) -> None:
        self._transport = transport
        self._filter = frame_filter
        self._frames: collections.deque[CanFrame] = collections.deque(maxlen=depth)
        self._filter_error: Exception | None = None

    def drain(self) -> list[can.Message]:
        """Return this subscription's frames received since the last drain, oldest first."""
        return [frame.message for frame in self._transport._drain_for(self)]

    def drain_frames(self) -> list[CanFrame]:
        """Like :meth:`drain`, but each frame carries the host time it arrived (``received_at_ns``)."""
        return self._transport._drain_for(self)


class CanTransport(TransportBase):
    """Transport for CAN-bus instruments. Composed by concrete drivers, not extended.

    CAN is a broadcast bus, so this differs from request/response transports in two ways.
    Several drivers on one physical adapter share this transport, each passing itself as the
    holder to :meth:`open`/:meth:`close`; the bus closes when the last owner leaves. Receiving
    goes through :meth:`subscribe`: while the bus is open a background thread pulls every frame
    off the adapter, stamps it with host time on arrival, and routes it to each matching
    subscription, so one driver's read never consumes another driver's frames and a slow
    drain neither delays timestamps nor lets the adapter's receive FIFO overflow.
    Send is fire-and-forget; there is no request/response helper.
    """

    def __init__(self, config: CanConfig) -> None:
        super().__init__()
        self._config = config
        self._bus: can.BusABC | None = None
        self._subscriptions: list[CanSubscription] = []
        # Guards subscription buffers and receive-thread state. Kept separate from the transport
        # lock so teardown (which runs under the transport lock) can join the receive thread.
        self._rx_lock = threading.Lock()
        self._rx_thread: threading.Thread | None = None
        self._rx_stop = threading.Event()
        self._rx_error: Exception | None = None

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
            if cfg.interface == "gs_usb":
                _prime_gs_usb_backend()
            try:
                bus = can.Bus(interface=cfg.interface, channel=cfg.channel, bitrate=cfg.bitrate, **cfg.bus_kwargs)
            except can.exceptions.CanInitializationError as exc:
                if cfg.interface != "gs_usb":
                    raise
                raise can.exceptions.CanInitializationError(
                    f"Could not open gs_usb adapter (channel {cfg.channel}): check that the adapter is plugged in, "
                    "runs candleLight/gs_usb firmware (stock vendor firmware often speaks neither gs_usb nor slcan), "
                    f"and is not held by another process. Original error: {exc}"
                ) from exc
            self._bus = bus
            self._start_receiving(bus)

    def _teardown_session(self) -> None:
        """Stop receiving, shut down the bus, and drop buffered frames, so a reopen never replays the previous session."""
        if self._bus is None:
            return
        try:
            self._stop_receiving()
            self._bus.shutdown()
            if self._config.interface == "gs_usb":
                _release_gs_usb_device(self._bus)
        finally:
            self._bus = None
            with self._rx_lock:
                self._rx_error = None
                for subscription in self._subscriptions:
                    subscription._frames.clear()
                    subscription._filter_error = None

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
        """Register a receive filter and return the subscription whose drain() yields the matching frames.

        ``frame_filter`` runs on the transport's receive thread for every frame, so keep it fast and
        free of side effects. If it raises, that subscription's next drain re-raises the error.
        """
        subscription = CanSubscription(self, frame_filter, depth)
        with self._lock, self._rx_lock:
            self._subscriptions.append(subscription)
        return subscription

    def unsubscribe(self, subscription: CanSubscription) -> None:
        """Stop routing frames to ``subscription``; unknown subscriptions are ignored."""
        with self._lock, self._rx_lock:
            if subscription in self._subscriptions:
                self._subscriptions.remove(subscription)

    def _drain_for(self, subscription: CanSubscription) -> list[CanFrame]:
        """Hand over ``subscription``'s buffered frames, raising any error the receive thread hit."""
        with self._lock:
            self._require_open_locked()
            with self._rx_lock:
                if self._rx_error is not None:
                    raise can.CanOperationError(
                        f"CAN receive stopped on channel {self._config.channel}; close and reopen the transport. "
                        f"Cause: {self._rx_error!r}"
                    ) from self._rx_error
                if subscription._filter_error is not None:
                    error, subscription._filter_error = subscription._filter_error, None
                    raise error
                frames = list(subscription._frames)
                subscription._frames.clear()
                return frames

    def _start_receiving(self, bus: can.BusABC) -> None:
        self._rx_stop = threading.Event()
        self._rx_error = None
        # The thread holds only a weak reference, so a transport dropped without close() can
        # still be collected (TransportBase.__del__ then tears the session down).
        self._rx_thread = threading.Thread(
            target=CanTransport._receive_loop,
            args=(weakref.ref(self), bus, self._rx_stop),
            name=f"CanTransport-rx-{self._config.interface}-{self._config.channel}",
            daemon=True,
        )
        self._rx_thread.start()

    def _stop_receiving(self) -> None:
        self._rx_stop.set()
        thread, self._rx_thread = self._rx_thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(1.0, 10 * RECEIVE_POLL_INTERVAL_S))
            if thread.is_alive():
                logger.warning("CAN receive thread did not stop within its timeout; shutting the bus down anyway")

    @staticmethod
    def _receive_loop(transport_ref: weakref.ref[CanTransport], bus: can.BusABC, stop: threading.Event) -> None:
        """Receive-thread body: pull frames off the bus, stamp them with host time, route them to subscribers."""
        while not stop.is_set():
            try:
                message = bus.recv(timeout=RECEIVE_POLL_INTERVAL_S)
            except Exception as exc:
                transport = transport_ref()
                if stop.is_set() or transport is None:
                    return  # the bus is being shut down under us
                logger.error(
                    "CAN receive failed on channel %s; receiving stopped", transport._config.channel, exc_info=True
                )
                with transport._rx_lock:
                    transport._rx_error = exc
                return
            if message is None:
                continue
            received_at_ns = time.time_ns()
            if stop.is_set():
                return  # never route a late frame into a session that has been torn down
            transport = transport_ref()
            if transport is None:
                return
            transport._route(CanFrame(message=message, received_at_ns=received_at_ns))
            del transport  # don't keep the transport alive while blocked in the next recv

    def _route(self, frame: CanFrame) -> None:
        """Append ``frame`` to every subscription whose filter accepts it (receive thread)."""
        with self._rx_lock:
            for subscription in self._subscriptions:
                try:
                    matched = subscription._filter(frame.message)
                except Exception as exc:
                    # Surface a broken filter on that subscriber's next drain instead of
                    # killing the receive thread every other subscriber depends on.
                    if subscription._filter_error is None:
                        subscription._filter_error = exc
                    continue
                if matched:
                    subscription._frames.append(frame)

    def _require_open_locked(self) -> can.BusABC:
        if self._bus is None:
            raise RuntimeError(f"CanTransport is not open. Call open() first. Channel: {self._config.channel}")
        return self._bus
