"""Publisher protocol and buffering wrappers."""

import abc
import logging
import queue
import threading
from typing import Protocol

from instro.lib.types import Command, Measurement

logger = logging.getLogger(__name__)


class Publisher(Protocol):
    def publish(self, data: Measurement | Command, **kwargs) -> None: ...
    def close(self) -> None: ...


class BufferedPublisher(abc.ABC):
    def __init__(self, publisher: Publisher, buffer_size: int = 1000):
        self.publisher = publisher
        self.buffer: list[Measurement | Command] = []
        self.buffer_size = buffer_size

    def publish(self, data: Measurement | Command, **kwargs) -> None:
        self.buffer.append(data)
        if len(self.buffer) >= self.buffer_size:
            self.publish_batch()
            self.buffer.clear()

    @abc.abstractmethod
    def publish_batch(self) -> None:
        pass

    def close(self) -> None:
        self.publish_batch()
        self.publisher.close()


class BasicBufferedPublisher(BufferedPublisher):
    def publish_batch(self):
        for data in self.buffer:
            self.publisher.publish(data)


class QueuedPublisher(Publisher):
    def __init__(self, publisher: Publisher, max_queue_size: int = 1000, wait_for_queue: bool = False):
        self.publisher = publisher
        self._queue: queue.Queue[tuple[Measurement | Command, dict]] = queue.Queue(maxsize=max_queue_size)
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._wait_for_queue = wait_for_queue
        self._thread.start()

    def publish(self, data: Measurement | Command, **kwargs):
        if self._stop_event.is_set():
            logger.warning(
                "Dropping publish request because QueuedPublisher is closing (publisher=%s)",
                self.publisher.__class__.__name__,
            )
            return
        self._queue.put((data, kwargs))

    def _worker(self):
        while not self._stop_event.is_set() or (self._wait_for_queue and not self._queue.empty()):
            try:
                data, kwargs = self._queue.get(timeout=0.1)
                self.publisher.publish(data, **kwargs)
                self._queue.task_done()
            except queue.Empty:
                continue

    def close(self):
        if not self._wait_for_queue and not self._queue.empty():
            logger.warning(
                "Closing QueuedPublisher with %d queued item(s) that may be dropped (publisher=%s)",
                self._queue.qsize(),
                self.publisher.__class__.__name__,
            )
        self._stop_event.set()
        self._thread.join()
        self.publisher.close()


class SharedPublisher(Publisher):
    """A publisher that can be shared between multiple instruments."""

    class __ControlBlock:
        def __init__(self, publisher: Publisher):
            self.__lock = threading.RLock()
            self.__count = 1
            self.__publisher = publisher

        def increment(self) -> "SharedPublisher.__ControlBlock":
            with self.__lock:
                if self.__count == 0:
                    raise RuntimeError(
                        "attempted to increment a shared publisher that was already closed. "
                        "If you're seeing this, it's probably a bug. Please report it to the instro developers."
                    )

                self.__count += 1

            return self

        def decrement(self) -> None:
            with self.__lock:
                if self.__count <= 0:
                    raise RuntimeError(
                        "attempted to decrement a shared publisher that was already closed. "
                        "If you're seeing this, it's probably a bug. Please report it to the instro developers."
                    )

                self.__count -= 1
                if self.__count == 0:
                    self.__close()

        def publish(self, data: Measurement | Command, **kwargs) -> None:
            with self.__lock:
                self.__publisher.publish(data, **kwargs)

        def __close(self) -> None:
            self.__publisher.close()

        def __del__(self) -> None:
            if self.__count > 0:
                self.__close()

    def __init__(self, publisher: Publisher):
        """Initialize a shared publisher, assuming exclusive ownership of the publisher provided."""
        self.__state_lock = threading.Lock()
        self.__state: SharedPublisher.__ControlBlock | None = SharedPublisher.__ControlBlock(publisher)

    @classmethod
    def __from_state(cls, state: "SharedPublisher.__ControlBlock") -> "SharedPublisher":
        instance = cls.__new__(cls)
        instance.__state_lock = threading.Lock()
        instance.__state = state
        return instance

    def clone(self) -> "SharedPublisher":
        """Clone the shared publisher for use with another instrument."""
        with self.__state_lock:
            if (state := self.__state) is None:
                raise RuntimeError("attempted to clone a shared publisher handle that was already closed")
            return SharedPublisher.__from_state(state.increment())

    def publish(self, data: Measurement | Command, **kwargs) -> None:
        """Publish data to the underlying publisher being shared."""
        with self.__state_lock:
            if (state := self.__state) is None:
                raise RuntimeError("attempted to publish to a shared publisher handle that was already closed")
            state.publish(data, **kwargs)

    def close(self) -> None:
        """Close this instance and possibly release the underlying publisher."""
        with self.__state_lock:
            if self.__state is not None:
                self.__state.decrement()
                self.__state = None

    def __del__(self) -> None:
        """Ensure that the shared publisher is closed when the instance is garbage collected."""
        self.close()
