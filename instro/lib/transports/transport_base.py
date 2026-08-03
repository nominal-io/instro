"""Base every transport implements: connection lifecycle plus deferred-teardown shared ownership."""

import abc
import logging
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)


class TransportBase(abc.ABC):
    """The contract a transport implements: ``_open_session``, ``_teardown_session``, ``is_open``, plus shared ownership.

    Ownership is deferred-teardown: opened by the first owner, torn down by the last.
    """

    _holders: list[object]
    _lock: threading.RLock

    def __init__(self) -> None:
        """Initialize holder list and reentrant lock."""
        self._holders = []
        self._lock = threading.RLock()

    def open(self, holder: object | None = None) -> bool:
        """Open the connection (idempotent); with a holder, admit it and return True only for the first owner."""
        with self._lock:
            if holder is None:
                self._open_session()
                return True
            if holder in self._holders:
                return False
            self._open_session()
            self._holders.append(holder)
            return len(self._holders) == 1

    def close(
        self,
        holder: object | None = None,
        on_last_release: Callable[[], None] | None = None,
    ) -> None:
        """Close and teardown; with a holder, remove it and tear down only when the last owner leaves."""
        with self._lock:
            if holder is not None:
                if holder not in self._holders:
                    return
                self._holders.remove(holder)
                if self._holders:
                    return
            elif self._holders:
                logger.warning("Declining close: session still owned by %d holder(s)", len(self._holders))
                return
            try:
                if on_last_release is not None:
                    on_last_release()
            finally:
                # on_last_release may reentrantly open() with a new holder (the RLock permits
                # this on the same thread); if so, leave teardown to that holder's own close().
                if not self._holders:
                    self._teardown_session()

    def lock(self) -> threading.RLock:
        """Return the reentrant resource lock for atomic multi-step sequences; the holder may write/query/read inside it."""
        return self._lock

    def __del__(self) -> None:
        """Teardown on GC, bypassing guards, swallowing errors."""
        try:
            self._teardown_session()
        except Exception:
            pass

    @property
    @abc.abstractmethod
    def is_open(self) -> bool:
        """Whether the underlying connection is currently open."""
        ...

    @abc.abstractmethod
    def _open_session(self) -> None:
        """Open the connection. Idempotent. Called by open()."""
        ...

    @abc.abstractmethod
    def _teardown_session(self) -> None:
        """Tear down the connection. Called by close() and __del__()."""
        ...
