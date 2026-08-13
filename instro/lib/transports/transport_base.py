"""Base every transport implements: connection lifecycle plus deferred-teardown shared ownership."""

import abc
import logging
import threading

from instro.lib.exceptions import UnknownHolderError

logger = logging.getLogger(__name__)


class TransportBase(abc.ABC):
    """The contract a transport implements: ``_open_session``, ``_teardown_session``, ``is_open``, plus shared ownership.

    Ownership is deferred-teardown: opened by the first owner, torn down by the last. Holders are
    the objects that own the connection, so a device serving several categories holds the transport
    once on its own behalf and does its own device-level teardown before releasing.
    """

    _holders: list[object]
    _lock: threading.RLock

    def __init__(self) -> None:
        """Initialize holder tracking and the reentrant lock."""
        self._holders = []
        self._lock = threading.RLock()

    def open(self, holder: object | None = None) -> bool:
        """Open the connection (idempotent); with a holder, admit it and return True only for the first owner."""
        with self._lock:
            if holder is None:
                self._open_session()
                return True
            if self._holder_index(holder) is not None:
                return False
            self._open_session()
            self._holders.append(holder)
            return len(self._holders) == 1

    def close(self, holder: object | None = None) -> None:
        """Close and teardown; with a holder, remove it and tear down only when the last owner leaves.

        A holder that needs to talk to the instrument before the connection goes (releasing a remote
        lock, say) does that in its own ``close`` before calling this, while the session is still up.

        Raises ``UnknownHolderError`` when an unrecognized holder closes while others still own the
        connection, because that is the case where ignoring it silently strands the real owners and
        leaves the connection open forever. Once the last owner has left there is nothing to strand,
        so a repeat close is a no-op and close stays idempotent.
        """
        with self._lock:
            if holder is not None:
                index = self._holder_index(holder)
                if index is None:
                    if self._holders:
                        raise UnknownHolderError(
                            f"{type(holder).__name__} does not own this {type(self).__name__}, "
                            f"which is still held by {len(self._holders)} other owner(s); "
                            "close() must be passed the object that opened it"
                        )
                    return
                del self._holders[index]
                if self._holders:
                    return
            elif self._holders:
                logger.warning("Declining close: session still owned by %d holder(s)", len(self._holders))
                return
            self._teardown_session()

    def lock(self) -> threading.RLock:
        """Return the reentrant resource lock for atomic multi-step sequences; the holder may write/query/read inside it."""
        return self._lock

    def _holder_index(self, holder: object) -> int | None:
        """Position of ``holder`` by identity, or None; equal-but-distinct drivers must count as two owners."""
        for index, held in enumerate(self._holders):
            if held is holder:
                return index
        return None

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
