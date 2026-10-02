"""Shared SDR types."""

from __future__ import annotations

from enum import Enum


class Direction(str, Enum):
    """Signal path a configuration call applies to.

    Attributes:
        RX: The receive path.
        TX: The transmit path. Receive-only radios accept ``RX`` alone.
    """

    RX = "rx"
    TX = "tx"
