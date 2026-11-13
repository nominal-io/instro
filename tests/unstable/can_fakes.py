"""Test double for ``bus.recv`` on a mocked python-can bus, driven by CanTransport's receive thread."""

from __future__ import annotations

import collections
import threading
import time
from collections.abc import Callable

import can


class ScriptedRecv:
    """Hands queued frames (or exceptions) to the receive thread, one per ``recv()`` call.

    Install as ``bus.recv.side_effect``. With nothing queued, ``recv`` blocks for its timeout and
    returns ``None``, like a quiet bus. :meth:`feed` returns once the receive thread has come back
    for more after the last fed frame, which means every fed frame has already been routed.
    """

    def __init__(self) -> None:
        self._items: collections.deque[can.Message | BaseException] = collections.deque()
        self._cond = threading.Condition()
        self._caught_up = False

    def recv(self, timeout: float | None = None) -> can.Message | None:
        with self._cond:
            if not self._items:
                self._caught_up = True
                self._cond.notify_all()
                self._cond.wait(timeout)
                if not self._items:
                    return None
            item = self._items.popleft()
        if isinstance(item, BaseException):
            raise item
        return item

    def feed(self, *frames: can.Message, timeout: float = 2.0) -> None:
        """Queue frames and wait until the receive thread has routed all of them."""
        with self._cond:
            self._items.extend(frames)
            self._caught_up = False
            self._cond.notify_all()
            routed = self._cond.wait_for(lambda: self._caught_up and not self._items, timeout)
        assert routed, "the receive thread did not consume the fed frames"

    def fail_with(self, exc: BaseException) -> None:
        """Make the next ``recv()`` raise ``exc`` (returns immediately; the thread then stops)."""
        with self._cond:
            self._items.append(exc)
            self._cond.notify_all()


def wait_until(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    """Poll ``predicate`` until it is true; fail the test after ``timeout`` seconds."""
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "condition not met before timeout"
        time.sleep(0.005)


def live_receive_threads() -> list[threading.Thread]:
    """CanTransport receive threads that are still running."""
    return [t for t in threading.enumerate() if t.name.startswith("CanTransport-rx-") and t.is_alive()]
