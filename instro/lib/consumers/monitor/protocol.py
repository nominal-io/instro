"""Wire format shared by `MonitorPublisher` and `MonitorServer`: one JSON object per line over loopback TCP."""

import json
import math
from typing import Any

from instro.lib.types import Command, Measurement

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 47251


def _json_safe(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


def _nan_for_null(value: Any) -> Any:
    return math.nan if value is None else value


def encode(data: Measurement | Command) -> bytes:
    """Serialize one Measurement or Command as a newline-terminated JSON frame."""
    frame: dict[str, Any]
    if isinstance(data, Measurement):
        frame = {
            "kind": "measurement",
            "channel_data": data.channel_data,
            "timestamps": data.timestamps,
            "tags": data.tags or {},
        }
    elif isinstance(data, Command):
        frame = {
            "kind": "command",
            "channel_data": data.channel_data,
            "timestamp": data.timestamp,
            "tags": data.tags or {},
        }
    else:
        raise TypeError(f"encode expects Measurement or Command, got {type(data).__name__}")
    return (json.dumps(_json_safe(frame), allow_nan=False) + "\n").encode("utf-8")


def decode(line: bytes | str) -> Measurement | Command:
    """Parse one frame back into a Measurement or Command; JSON nulls become NaN."""
    frame = json.loads(line)
    if not isinstance(frame, dict):
        raise ValueError("monitor frame must be a JSON object")
    kind = frame.get("kind")
    tags = frame.get("tags") or None
    if kind == "measurement":
        return Measurement(
            channel_data={ch: [_nan_for_null(v) for v in values] for ch, values in frame["channel_data"].items()},
            timestamps=list(frame["timestamps"]),
            tags=tags,
        )
    if kind == "command":
        return Command(
            channel_data={ch: _nan_for_null(v) for ch, v in frame["channel_data"].items()},
            timestamp=frame["timestamp"],
            tags=tags,
        )
    raise ValueError(f"unknown monitor frame kind: {kind!r}")
