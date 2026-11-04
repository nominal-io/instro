"""File-based publishers (JSON, JSONL, CSV, Avro)."""

import csv
import json
import math
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Protocol

import fastavro

from instro.lib import _display
from instro.lib.types import Command, Measurement


class FileWriter(Protocol):
    file_path: Path

    def write(self, data: Measurement | Command): ...

    def open(self): ...

    def close(self): ...


class FilePublisher:
    def __init__(
        self,
        directory: str | Path,
        format: Literal["json", "jsonl", "csv", "avro"] = "avro",
        custom_file_name: str | None = None,
    ):
        """Write Measurement/Command data to a file.

        Args:
            directory: Output directory.
            format: ``"jsonl"``, ``"csv"``, ``"avro"`` (default), or ``"json"`` (deprecated).
            custom_file_name: Filename without extension; defaults to ``measurements-<UTC-timestamp>``.
        """
        self.directory = Path(directory)
        self.format = format
        self.custom_file_name = custom_file_name
        self.writer: FileWriter

        # Determine file name
        if custom_file_name is None:
            # Create timestamp suffix in YYYY-MM-DD-hh-mm-ss format
            timestamp_suffix = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
            file_name = f"measurements-{timestamp_suffix}"
        else:
            file_name = custom_file_name

        # Add file extension
        file_name = f"{file_name}.{format}"

        # Create full file path
        self.file_path = self.directory / file_name

        # Initialize appropriate writer based on format
        if format == "json":
            warnings.warn(
                'FilePublisher format="json" rewrites the whole file on every publish and is deprecated; '
                'use format="jsonl" instead.',
                DeprecationWarning,
                stacklevel=2,
            )
            self.writer = JsonFileWriter(self.file_path)
        elif format == "jsonl":
            self.writer = JsonlFileWriter(self.file_path)
        elif format == "csv":
            self.writer = CsvFileWriter(self.file_path)
        elif format == "avro":
            self.writer = AvroFileWriter(self.file_path)
        else:
            raise ValueError(f"Unsupported format: {format}")

    def publish(self, data: Measurement | Command, **kwargs):
        """Publish data to file using the appropriate writer."""
        SHARED_PUBLISHER_WARNING = (
            "If you're attempting to publish from multiple instruments, consider using SharedPublisher."
        )

        try:
            self.writer.write(data)
        except Exception as e:
            add_note = getattr(e, "add_note", None)
            if add_note:
                add_note(SHARED_PUBLISHER_WARNING)

            elif e.args:
                e.args = (f"{e.args}\n\n{SHARED_PUBLISHER_WARNING}", *e.args[1:])

            raise

    def open(self):
        # Stubbing out open method for when we opt to refactor this to use a file handle
        pass

    def close(self):
        """Close the publisher and ensure all data is written."""
        self.writer.close()

    def __repr__(self) -> str:
        """Name the file this publisher writes to."""
        return f"FilePublisher(file_path={str(self.file_path)!r}, format={self.format!r})"

    def _display_summary(self) -> str:
        return f"{_display.escape(self.format)} file {_display.code(self.file_path)}"

    def _repr_html_(self) -> str | None:
        """Show the file this publisher writes to."""
        return _display.render(
            kind="FilePublisher",
            title=self.file_path.name,
            fallback=repr(self),
            fields=[("Path", _display.code(self.file_path)), ("Format", _display.escape(self.format))],
        )


class JsonFileWriter:
    """Handles JSON format writing with proper file management."""

    def __init__(self, file_path: Path):
        self.file_path = file_path
        self._ensure_file_exists()

    def _ensure_file_exists(self):
        """Create directory and file if they don't exist."""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.file_path.exists():
            # Create empty file with proper JSON structure
            with open(self.file_path, "w") as f:
                f.write("[]")  # Start with empty JSON array

    def write(self, data: Measurement | Command):
        """Append data to JSON file."""
        # Read existing content
        try:
            with open(self.file_path, "r") as f:
                content = f.read().strip()
                if content:
                    existing_data = json.loads(content)
                else:
                    existing_data = []
        except (json.JSONDecodeError, FileNotFoundError):
            existing_data = []

        # Append new data
        if isinstance(existing_data, list):
            existing_data.append(data.__dict__)
        else:
            existing_data = [existing_data, data.__dict__]

        # Write back to file
        with open(self.file_path, "w") as f:
            json.dump(existing_data, f, indent=2)

    def open(self):
        # Stubbing out open method for when we opt to refactor this to use a file handle
        pass

    def close(self):
        """Close the file writer."""
        pass


def _json_safe(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


class JsonlFileWriter:
    """Handles newline-delimited JSON writing with a persistent file handle."""

    def __init__(self, file_path: Path):
        self.file_path = file_path
        self._ensure_file_exists()
        self._file = open(self.file_path, "a")

    def _ensure_file_exists(self):
        """Create directory if it doesn't exist."""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, data: Measurement | Command):
        """Append data as one JSON line, mapping non-finite floats to null."""
        self._file.write(json.dumps(_json_safe(data.__dict__), allow_nan=False) + "\n")
        self._file.flush()

    def open(self):
        pass

    def close(self):
        """Close the file writer."""
        self._file.close()


class CsvFileWriter:
    """Handles CSV format writing with a persistent file handle."""

    def __init__(self, file_path: Path):
        self.file_path = file_path
        self._ensure_file_exists()
        self._file = open(self.file_path, "a", newline="")
        self.writer = csv.DictWriter(self._file, fieldnames=["timestamp", "channel", "value", "tags"])
        if self.file_path.stat().st_size == 0:
            self.writer.writeheader()
            self._file.flush()

    def _ensure_file_exists(self):
        """Create directory if it doesn't exist."""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, data: Measurement | Command):
        """Append data to CSV file."""
        if isinstance(data, Measurement):
            self._write_measurement(data)
        elif isinstance(data, Command):
            self._write_command(data)
        self._file.flush()

    def _write_measurement(self, data: Measurement):
        """Write Measurement data as individual rows."""
        # Write each channel's data as separate rows
        for channel_name, values in data.channel_data.items():
            for i, value in enumerate(values):
                # Each channel should have the same number of values as timestamps
                # but handle edge case where they don't match
                timestamp = data.timestamps[i] if i < len(data.timestamps) else data.timestamps[-1]
                tags = json.dumps(data.tags) if data.tags else ""
                self.writer.writerow({"timestamp": timestamp, "channel": channel_name, "value": value, "tags": tags})

    def _write_command(self, data: Command):
        """Write Command data as individual rows."""
        # Write each channel's data as separate rows
        for channel_name, value in data.channel_data.items():
            tags = json.dumps(data.tags) if data.tags else ""
            self.writer.writerow({"timestamp": data.timestamp, "channel": channel_name, "value": value, "tags": tags})

    def open(self):
        pass

    def close(self):
        """Close the file writer."""
        self._file.close()


class AvroFileWriter:
    """Handles Avro format writing with proper file management using fastavro."""

    def __init__(self, file_path: Path):
        self.file_path = file_path
        self._ensure_file_exists()

        # Define Schema compatible with Nominal Core
        self.schema = {
            "type": "record",
            "namespace": "io.nominal.ingest",
            "name": "AvroStream",
            "fields": [
                {"name": "channel", "type": "string"},
                {"name": "timestamps", "type": {"type": "array", "items": "long"}},
                {"name": "values", "type": {"type": "array", "items": ["double", "string"]}},
                {"name": "tags", "type": {"type": "map", "values": "string"}, "default": {}},
            ],
        }
        self._parsed_schema = fastavro.parse_schema(self.schema)

        # Open file and initialize writer
        self._file = open(self.file_path, "wb")
        # Using snappy compression requires cramjam package
        self.writer = fastavro.write.Writer(self._file, self._parsed_schema, codec="snappy")

    def _ensure_file_exists(self):
        """Create directory if it doesn't exist."""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, data: Measurement | Command):
        """Append data to Avro file."""
        if isinstance(data, Measurement):
            self._write_measurement(data)
        elif isinstance(data, Command):
            self._write_command(data)
        self.writer.flush()

    def _write_measurement(self, data: Measurement):
        """Write Measurement data as batches."""
        for channel_name, values in data.channel_data.items():
            self.writer.write(
                {
                    "channel": channel_name,
                    "timestamps": data.timestamps,
                    "values": values,
                    "tags": data.tags or {},
                }
            )

    def _write_command(self, data: Command):
        """Write Command data as a batch."""
        for channel_name, value in data.channel_data.items():
            self.writer.write(
                {
                    "channel": channel_name,
                    "timestamps": [data.timestamp],
                    "values": [value],
                    "tags": data.tags or {},
                }
            )

    def open(self):
        # Stubbing out open method for when we opt to refactor this to use a file handle
        pass

    def close(self):
        """Close the file writer."""
        if self._file and not self._file.closed:
            self.writer.flush()
            self._file.close()
