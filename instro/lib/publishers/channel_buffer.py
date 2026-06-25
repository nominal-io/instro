import sys
import threading
import time
from abc import ABC, abstractmethod
from collections import Counter, deque
from typing import Any

import numpy as np
from numpy.typing import NDArray

from instro.lib.types import Command, Measurement


class ChannelNotFoundError(TimeoutError):
    """Raised when a channel does not appear within the specified timeout."""


class ChannelValueTimeoutError(TimeoutError):
    """Raised when sufficient channel values are not available within the specified timeout."""


class ChannelBufferPublisher(ABC):
    """Base class for channel buffer publishers that store measurements in memory."""

    def __init__(self, maxlen: int):
        self.maxlen = maxlen
        self._values: dict[str, Any] = {}
        self._timestamps: dict[str, Any] = {}
        self._condition = threading.Condition()
        self._total_added_count: Counter[str] = Counter()
        self._closed = False

    @abstractmethod
    def _ensure_channel(self, channel_name: str) -> None:
        """Ensure a channel exists in the buffers. Must be implemented by subclasses."""

    @abstractmethod
    def _extend_values(self, channel_name: str, values) -> None:
        """Extend the values buffer for a channel. Must be implemented by subclasses."""

    @abstractmethod
    def _extend_timestamps(self, channel_name: str, timestamps) -> None:
        """Extend the timestamps buffer for a channel. Must be implemented by subclasses."""

    @abstractmethod
    def _get_values(self, channel_name: str, length: int) -> list[float]:
        """Get the latest values from a channel. Must be implemented by subclasses."""

    @abstractmethod
    def _get_timestamps(self, channel_name: str, length: int) -> list[int]:
        """Get the latest timestamps from a channel. Must be implemented by subclasses."""

    @abstractmethod
    def _points_in_buffer(self, channel_name: str) -> int:
        """Get the current number of points in the buffer for a given channel. Must be implemented by subclasses."""

    def publish(self, data: Measurement | Command, **kwargs):
        """Publish measurement data to the buffers with thread synchronization."""
        if isinstance(data, Measurement):
            with self._condition:
                for channel_name, values in data.channel_data.items():
                    self._ensure_channel(channel_name)
                    n_new = len(values)
                    self._extend_values(channel_name, values)
                    self._extend_timestamps(channel_name, data.timestamps)
                    self._total_added_count[channel_name] += n_new
                self._condition.notify_all()

    def get(
        self, channel_name: str, length: int = 1, wait_for_new_samples: bool = False, timeout: float = 10.0
    ) -> Measurement:
        """Return the trailing ``length`` samples for ``channel_name``.

        With ``wait_for_new_samples=True`` blocks for ``length`` new samples; otherwise waits for
        the buffer to already hold ``length``.

        Raises:
            ChannelNotFoundError: Channel did not appear within ``timeout``.
            ChannelValueTimeoutError: Values did not arrive within ``timeout``.
        """
        start_time = time.monotonic()
        if wait_for_new_samples:
            # Wait for channel to exist if it doesn't exist yet
            if channel_name not in self._values:
                with self._condition:
                    if not self._condition.wait_for(lambda: channel_name in self._values or self._closed, timeout):
                        raise ChannelNotFoundError(f"Channel '{channel_name}' did not appear within {timeout} seconds.")
                    if self._closed:
                        raise ValueError(f"Publisher is closed. Channel '{channel_name}' data is not available.")
            elapsed = time.monotonic() - start_time
            remaining = timeout - elapsed
            return self._get_with_wait(channel_name, length, remaining)

        # Non-waiting path: wait for channel to exist and buffer to have enough samples
        return self._get_with_buffer_wait(channel_name, length, timeout)

    def _get_with_buffer_wait(self, channel_name: str, length: int, timeout: float = 10.0) -> Measurement:
        """Wait for the channel to exist and for the buffer to hold ``length`` samples, then return them."""
        start_time = time.monotonic()
        with self._condition:
            # Wait for channel to exist
            if not self._condition.wait_for(lambda: channel_name in self._values or self._closed, timeout):
                raise ChannelNotFoundError(f"Channel '{channel_name}' did not appear within {timeout} seconds.")

            if self._closed:
                raise ValueError(f"Publisher is closed. Channel '{channel_name}' data is not available.")

            # Wait for buffer to have at least 'length' samples
            elapsed = time.monotonic() - start_time
            remaining = timeout - elapsed
            if remaining > 0:
                if not self._condition.wait_for(
                    lambda: self._points_in_buffer(channel_name) >= length or self._closed, remaining
                ):
                    current_length = self._points_in_buffer(channel_name)
                    raise ChannelValueTimeoutError(
                        f"Channel '{channel_name}' did not accumulate {length} sample(s) within {timeout} seconds. "
                        f"Only {current_length} sample(s) available."
                    )

            if self._closed:
                current_length = self._points_in_buffer(channel_name)
                if current_length < length:
                    raise ChannelValueTimeoutError(
                        f"Publisher closed before channel '{channel_name}' accumulated {length} sample(s). "
                        f"Only {current_length} sample(s) available."
                    )

            # Extract the last 'length' items (these are the newest)
            n = min(length, self._points_in_buffer(channel_name))

            # Get the values and timestamps
            values = self._get_values(channel_name, n)
            timestamps = self._get_timestamps(channel_name, n)

        return Measurement(
            channel_data={channel_name: values},
            timestamps=timestamps,
        )

    def _get_with_wait(self, channel_name: str, length: int, timeout: float = 10.0) -> Measurement:
        """Block until ``length`` new samples arrive on ``channel_name`` (or close), then return them."""
        with self._condition:
            # Record the current count when we start waiting
            start_count = self._total_added_count[channel_name]
            target_count = start_count + length

            # Wait until 'length' new values have been added or publisher is closed
            if not self._condition.wait_for(
                lambda: self._total_added_count[channel_name] >= target_count or self._closed, timeout
            ):
                raise ChannelValueTimeoutError(
                    f"Channel '{channel_name}' did not receive {length} new value(s) within {timeout} seconds. "
                    f"Only {self._total_added_count[channel_name] - start_count} new value(s) were received."
                )

            if self._closed:
                # Check if we have enough values before publisher closed
                if self._total_added_count[channel_name] < target_count:
                    raise ChannelValueTimeoutError(
                        f"Publisher closed before channel '{channel_name}' received {length} new value(s)."
                    )

            # Extract the last 'length' items (these are the newest)
            n = min(length, self._points_in_buffer(channel_name))

            # Get the values and timestamps
            values_list = self._get_values(channel_name, n)
            timestamps_list = self._get_timestamps(channel_name, n)

        return Measurement(
            channel_data={channel_name: values_list},
            timestamps=timestamps_list,
        )

    def close(self) -> None:
        """Close the publisher and clear all buffers with thread synchronization."""
        with self._condition:
            self._closed = True
            self._condition.notify_all()
            self._values.clear()
            self._timestamps.clear()
            self._total_added_count.clear()

    @property
    @abstractmethod
    def size_bytes(self) -> int:
        """Return the current memory in bytes."""


class DequeInMemoryPublisher(ChannelBufferPublisher):
    def __init__(self, maxlen: int):
        super().__init__(maxlen)

    def _ensure_channel(self, channel_name: str) -> None:
        if channel_name not in self._values:
            self._values[channel_name] = deque(maxlen=self.maxlen)
            self._timestamps[channel_name] = deque(maxlen=self.maxlen)

    def _extend_values(self, channel_name: str, values) -> None:
        self._values[channel_name].extend(values)

    def _extend_timestamps(self, channel_name: str, timestamps) -> None:
        self._timestamps[channel_name].extend(timestamps)

    def _get_values(self, channel_name: str, length: int) -> list[float]:
        n = min(length, len(self._values[channel_name]))
        return list(self._values[channel_name])[-n:] if n else []

    def _get_timestamps(self, channel_name: str, length: int) -> list[int]:
        n = min(length, len(self._timestamps[channel_name]))
        return list(self._timestamps[channel_name])[-n:] if n else []

    def _points_in_buffer(self, channel_name: str) -> int:
        return len(self._values[channel_name])

    @property
    def size_bytes(self) -> int:
        """Return the current memory in bytes."""
        size = sys.getsizeof(self._values) + sys.getsizeof(self._timestamps)

        # Add size of each deque and its elements
        for channel_name in self._values:
            values_deque = self._values[channel_name]
            timestamps_deque = self._timestamps[channel_name]

            # Size of deque objects
            size += sys.getsizeof(values_deque) + sys.getsizeof(timestamps_deque)

            # Size of elements: floats are typically 8 bytes, ints are typically 8 bytes
            size += len(values_deque) * 8  # float
            size += len(timestamps_deque) * 8  # int

        return size


class NumpyInMemoryPublisher(ChannelBufferPublisher):
    def __init__(self, maxlen: int, value_dtype: Any = np.float32):
        super().__init__(maxlen)
        self._value_dtype = value_dtype

    def _ensure_channel(self, channel_name: str) -> None:
        if channel_name not in self._values:
            self._values[channel_name] = NumpyRingBuffer(self.maxlen, self._value_dtype)
            self._timestamps[channel_name] = NumpyRingBuffer(self.maxlen, np.int64)

    def _extend_values(self, channel_name: str, values) -> None:
        self._values[channel_name].extend(values)

    def _extend_timestamps(self, channel_name: str, timestamps) -> None:
        self._timestamps[channel_name].extend(timestamps)

    def _get_values(self, channel_name: str, length: int) -> list[float]:
        return self._values[channel_name].get_latest(length).tolist()

    def _get_timestamps(self, channel_name: str, length: int) -> list[int]:
        return self._timestamps[channel_name].get_latest(length).tolist()

    def _points_in_buffer(self, channel_name: str) -> int:
        return len(self._values[channel_name])

    @property
    def size_bytes(self) -> int:
        """Return the current memory in bytes."""
        size = sys.getsizeof(self._values) + sys.getsizeof(self._timestamps)

        # Add size of each RingBuffer and its numpy arrays
        for channel_name in self._values:
            values_buffer = self._values[channel_name]
            timestamps_buffer = self._timestamps[channel_name]

            # Size of RingBuffer objects
            size += sys.getsizeof(values_buffer) + sys.getsizeof(timestamps_buffer)

            # Size of numpy arrays (nbytes gives actual data size)
            size += values_buffer._buffer.nbytes
            size += timestamps_buffer._buffer.nbytes

        return size


class NumpyRingBuffer:
    def __init__(self, maxlen: int, dtype: Any):
        self._buffer: NDArray = np.zeros(maxlen, dtype=dtype)
        self._maxlen = maxlen
        self._index = 0  # Next write position
        self._size = 0  # Current number of valid elements

    def extend(self, values) -> None:
        values = np.asarray(values, dtype=self._buffer.dtype)
        n = len(values)

        if n == 0:
            return

        if n >= self._maxlen:
            # Incoming data fills or exceeds buffer — just take the last maxlen
            self._buffer[:] = values[-self._maxlen :]
            self._index = 0
            self._size = self._maxlen
            return

        # Where does the write end?
        end = self._index + n

        if end <= self._maxlen:
            # No wraparound
            self._buffer[self._index : end] = values
        else:
            # Wraparound
            first = self._maxlen - self._index
            self._buffer[self._index :] = values[:first]
            self._buffer[: n - first] = values[first:]

        self._index = end % self._maxlen
        self._size = min(self._size + n, self._maxlen)

    def get_latest(self, n: int) -> NDArray:
        n = min(n, self._size)
        if n == 0:
            return np.array([], dtype=self._buffer.dtype)

        # Data is stored oldest-to-newest up to _index
        # Latest n items end at _index (exclusive)
        start = (self._index - n) % self._maxlen

        if start < self._index:
            # No wraparound
            return self._buffer[start : self._index].copy()
        else:
            # Wraparound
            return np.concatenate([self._buffer[start:], self._buffer[: self._index]])

    def __len__(self) -> int:
        """Return the current number of valid elements in the buffer."""
        return self._size
