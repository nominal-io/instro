"""Publisher protocol and buffering wrappers."""

import abc
import logging
import queue
import threading
from typing import Protocol, cast, overload

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
    """A publisher that can be shared between multiple instruments.

    Publishers are treated as exclusive resources, owned by a single instrument. Instruments attempt to close the publisher that they own when they are closed.
    ``SharedPublisher`` is an escape hatch to allow multiple instruments to share a single publisher that will only be closed when the last instrument that is sharing it is closed.
    """

    class __ControlBlock:
        def __init__(self, publisher: Publisher):
            self.__lock = threading.Lock()
            self.__count = 1
            self.__publisher = publisher

        def increment(self) -> "SharedPublisher.__ControlBlock":
            with self.__lock:
                if self.__count == 0:
                    raise RuntimeError(
                        "attempted to increment a shared publisher that was already closed."
                        "If you're seeing this, it's probably a bug. Please report it to the instro developers."
                    )

                self.__count += 1

            return self

        def decrement(self) -> None:
            with self.__lock:
                if self.__count <= 0:
                    raise RuntimeError(
                        "attempted to decrement a shared publisher that was already closed."
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
            self.__close()

    @overload
    def __init__(self, _state: "SharedPublisher.__ControlBlock", /): ...
    @overload
    def __init__(self, publisher: Publisher, /): ...

    def __init__(self, pub_or_state: "Publisher | SharedPublisher.__ControlBlock", /):
        self.__state = None
        if isinstance(state := pub_or_state, SharedPublisher.__ControlBlock):
            self.__state = state
            self.__state.increment()
        else:
            self.__state = SharedPublisher.__ControlBlock(cast(Publisher, pub_or_state))

    def __get_state(self) -> "SharedPublisher.__ControlBlock":
        if self.__state is None:
            raise RuntimeError("attempted to access a shared publisher that was already closed")
        return self.__state

    def clone(self) -> "SharedPublisher":
        """Clone the shared publisher for use with another instrument."""
        return SharedPublisher(self.__get_state())

    def publish(self, data: Measurement | Command, **kwargs) -> None:
        """Publish data to the underlying publisher being shared."""
        self.__get_state().publish(data, **kwargs)

    def close(self) -> None:
        """Close the this instance and possibly release the underlying publisher."""
        if self.__state is not None:
            self.__state.decrement()
            self.__state = None

    def __del__(self) -> None:
        """Ensure that the shared publisher is closed when the instance is garbage collected."""
        self.close()
