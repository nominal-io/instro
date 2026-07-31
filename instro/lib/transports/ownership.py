"""Deferred-teardown ownership of shared connections: opened by first owner, torn down by last."""

import abc
import logging
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)


class OwnershipContext(abc.ABC):
    """Deferred-teardown ownership of a shared connection: opened by the first owner, torn down by the last."""

    _holders: list[object]
    _lock: threading.RLock

    def __init__(self) -> None:
        """Initialize holder list and reentrant lock."""
        self._holders = []
        self._lock = threading.RLock()

    def acquire(self, holder: object) -> bool:
        """Admit holder and ensure session is open. Returns True if this is the first owner."""
        with self._lock:
            if holder in self._holders:
                return False
            self.open()
            self._holders.append(holder)
            return len(self._holders) == 1

    def release(
        self,
        holder: object,
        on_last_release: Callable[[], None] | None = None,
    ) -> None:
        """Remove holder; on last release, run callback then teardown. No-op if holder not in list."""
        with self._lock:
            if holder not in self._holders:
                return
            self._holders.remove(holder)
            if self._holders:
                return
            try:
                if on_last_release is not None:
                    on_last_release()
            finally:
                self._teardown_session()

    def close(self) -> None:
        """Close and teardown. Guarded: only when no holders remain."""
        with self._lock:
            if self._holders:
                logger.warning("Declining close: session still owned by %d holder(s)", len(self._holders))
                return
            self._teardown_session()

    def lock(self) -> threading.RLock:
        """Return the reentrant resource lock for atomic multi-step sequences.

        Example::

            with driver.lock():
                driver.write("CONF:VOLT:DC")
                driver.write("RANGE 10")
                value = driver.query("READ?")

        Reentrant: the holding thread can call write/query/read inside the with.
        """
        return self._lock

    def __del__(self) -> None:
        """Teardown on GC, bypassing guards, swallowing errors."""
        try:
            self._teardown_session()
        except Exception:
            pass

    @abc.abstractmethod
    def open(self) -> None:
        """Open the connection. Idempotent."""
        ...

    @abc.abstractmethod
    def _teardown_session(self) -> None:
        """Tear down the connection. Called by close() and __del__()."""
        ...
