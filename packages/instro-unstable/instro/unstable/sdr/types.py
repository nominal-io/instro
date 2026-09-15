"""Shared SDR types."""

from __future__ import annotations

from enum import Enum


class Direction(str, Enum):
    """Signal path a call applies to. Receive-only radios accept ``RX`` alone."""

    RX = "rx"
    TX = "tx"
