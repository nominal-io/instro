# This code is derived from concepts in the py-lab-hal codebase (Apache 2.0 licensed)
# Original py-lab-hal repository: https://github.com/google/py-lab-hal

"""Publisher protocol and buffering wrappers."""

import abc
import logging
import queue
import threading
from typing import Protocol

from instro.lib.types import Command, Measurement

logger = logging.getLogger(__name__)


class Publisher(Protocol):
    def publish(self, data: Measurement | Command, **kwargs): ...

    def close(self): ...


class BufferedPublisher(abc.ABC):
    def __init__(self, publisher: Publisher, buffer_size: int = 1000):
        self.publisher = publisher
        self.buffer: list[Measurement | Command] = []
        self.buffer_size = buffer_size

    def publish(self, data: Measurement | Command, **kwargs):
        self.buffer.append(data)
        if len(self.buffer) >= self.buffer_size:
            self.publish_batch()
            self.buffer.clear()

    @abc.abstractmethod
    def publish_batch(self):
        pass

    def close(self):
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
