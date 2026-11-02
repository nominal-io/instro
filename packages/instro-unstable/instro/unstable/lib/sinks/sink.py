import abc
import threading
from typing import Any, Iterator

from instro.unstable.lib.consumers import Consumer


class Sink(abc.ABC):
    """Abstract base class for data sinks. """

    def __init__(self):
        self._running = False
        self._thread = None

    def start(self, consumer: Consumer):
        """Starts consuming from the reader in a background thread."""
        if self._thread and self._thread.is_alive():
            print(f"{self.__class__.__name__} is already running.")
            return

        self._running = True
        self._thread = threading.Thread(target=self._consume_loop, args=(consumer.consume(),), daemon=True)
        self._thread.start()

    def stop(self):
        """Gracefully stops the background consumption thread."""
        self._running = False
        if self._thread:
            self._thread.join()

    def _consume_loop(self, consumer: Iterator[Any]):
        """Internal loop to fetch items and pass them to the processor."""
        for item in consumer:
            if not self._running:
                break
            self.process_item(item)

    @abc.abstractmethod
    def process_item(self, item: Any):
        """Implement this method in subclasses to define how to handle each item."""
        raise NotImplementedError("Subclasses must implement this method.")
