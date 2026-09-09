"""Consumer protocol and generic base implementations.

Consumers are the counterpart to publishers: they receive measurement and command
objects emitted by a publisher without imposing any domain-specific behavior.
"""

import abc
import logging
from typing import Any, Callable, Protocol

from instro.lib.types import Command, Measurement

logger = logging.getLogger(__name__)


class Consumer(abc.ABC):
    """Abstract Consumer ."""

    @abc.abstractmethod
    def consume(self) -> list[Measurement | Command]:
        """Read all records from the source. This is a convenience method for consumers that support batch reading."""
        raise NotImplementedError("consume() is not implemented")

    @abc.abstractmethod
    def open(self) -> None:
        """Open the source for reading."""
        raise NotImplementedError("open() is not implemented")

    @abc.abstractmethod
    def close(self) -> None:
        """Close the source after reading is complete."""
        raise NotImplementedError("close() is not implemented")

    def is_measurement_record(self, data: dict[str, Any]) -> bool:
        """Return True when the given record looks like a Measurement.

        This is a small standalone predicate so callers can change the
        detection logic later without touching reader implementations.
        """
        return "timestamps" in data and "channel_data" in data

    def is_command_record(self, data: dict[str, Any]) -> bool:
        """Return True when the given record looks like a Command."""
        return "timestamp" in data and "channel_data" in data

    def record_to_object(self, data: dict[str, Any]) -> Measurement | Command:
        """Convert a plain dict record into a `Measurement` or `Command`.

        Raises `TypeError` for unsupported records. Readers can call this
        to centralize the JSON<->object conversion logic.
        """
        if self.is_measurement_record(data):
            return Measurement(**data)
        if self.is_command_record(data):
            return Command(**data)
        raise TypeError(f"Unsupported record: {data!r}")
