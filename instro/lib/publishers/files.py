"""File-based publishers for JSON, JSONL, CSV, and Avro output.

This module defines the publisher classes used to persist Measurement and Command
records to disk. Each format is handled by a dedicated writer that owns a
file-like object and writes serialized records with the appropriate data model.
"""

import csv
import json
import math
import warnings
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import IO, Any, AnyStr, Generic, Literal, cast

import fastavro

from instro.lib.types import Command, Measurement


class FileWriter(Generic[AnyStr], ABC):
    """Abstract base class for format-specific file writers.

    Concrete subclasses provide the serialization logic for a specific output
    format while sharing the same file lifecycle contract: the writer owns a
    file-like object and exposes a standard ``write``, ``open``, and ``close``
    interface.
    """

    def __init__(self, file: IO[AnyStr]) -> None:
        """Initialize the writer with an open file-like object.

        Args:
            file: The text or binary stream that will receive serialized records.
        """
        self._file: IO[AnyStr] = file

    @abstractmethod
    def write(self, data: Measurement | Command) -> None:
        """Write a Measurement or Command record to the underlying stream."""

    def open(self) -> None:
        """Open the writer's file handle.

        This base implementation is intentionally a no-op because the stream is
        expected to be opened by the publisher before the writer is constructed.
        """
        pass

    def close(self) -> None:
        """Close the underlying file-like object."""
        self._file.close()


class FilePublisher:
    """Coordinate writing Measurement and Command records to a configured output.

    The publisher can be created either from a pre-opened file-like object or from
    a directory path and filename arguments. When no directory is provided, it
    creates a persistent temporary file so callers can publish without managing a
    filesystem path manually.
    """

    def __init__(
        self,
        directory: str | Path | None = None,
        format: Literal["json", "jsonl", "csv", "avro"] = "jsonl",
        custom_file_name: str | None = None,
        *,
        file: IO[Any] | None = None,
    ) -> None:
        """Create a publisher bound to a stream, a file path, or a temporary file.

        Args:
            directory: Directory where output files should be created. If omitted,
                a temporary file is created instead.
            format: Output format, one of ``"json"``, ``"jsonl"``, ``"csv"``, or
                ``"avro"``.
            custom_file_name: Optional filename stem used when writing to disk.
            file: Optional pre-opened file-like object. When supplied, it takes
                precedence over path-based construction and cannot be combined
                with ``directory`` or ``custom_file_name``.
        """
        if file is not None and (directory is not None or custom_file_name is not None):
            raise ValueError("file cannot be combined with directory or custom_file_name")

        self.directory = Path(directory) if directory is not None else None
        self.format = format
        self.custom_file_name = custom_file_name
        self._writer: FileWriter[Any]
        self.file_path: Path | None = None

        writer_class: type[FileWriter[Any]]
        if format == "json":
            warnings.warn(
                'FilePublisher format="json" rewrites the whole file on every publish and is deprecated; '
                'use format="jsonl" instead.',
                DeprecationWarning,
                stacklevel=2,
            )
            writer_class = JsonFileWriter
            mode = "w+"
        elif format == "jsonl":
            writer_class = JsonlFileWriter
            mode = "a"
        elif format == "csv":
            writer_class = CsvFileWriter
            mode = "a"
        elif format == "avro":
            writer_class = AvroFileWriter
            mode = "wb"
        else:
            raise ValueError(f"Unsupported format: {format}")

        if file is not None:
            self._writer = writer_class(file)
            return

        newline = "" if format == "csv" else None
        if self.directory is None:
            stream = cast(
                IO[Any],
                NamedTemporaryFile(
                    mode=mode,
                    prefix=f"{custom_file_name}-" if custom_file_name is not None else "measurements-",
                    suffix=f".{format}",
                    newline=newline,
                    delete=False,
                ),
            )
            self.file_path = Path(stream.name)
            self.directory = self.file_path.parent
        else:
            file_name = custom_file_name
            if file_name is None:
                file_name = f"measurements-{datetime.now():%Y-%m-%d-%H-%M-%S}"
            self.file_path = self.directory / f"{file_name}.{format}"
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            if format == "json" and self.file_path.exists():
                mode = "r+"
            stream = open(self.file_path, mode, newline=newline)
        try:
            self._writer = writer_class(stream)
        except Exception:
            stream.close()
            raise

    def publish(self, data: Measurement | Command, **kwargs):
        """Publish one Measurement or Command using the configured writer.

        Args:
            data: The Measurement or Command instance to store.
            **kwargs: Additional arguments accepted for compatibility with the
                publisher interface.
        """
        SHARED_PUBLISHER_WARNING = (
            "If you're attempting to publish from multiple instruments, consider using SharedPublisher. "
            "See https://instro.nominal.io/instrumentation/publishers#sharedpublisher for more information."
        )

        try:
            self._writer.write(data)
        except Exception as e:
            add_note = getattr(e, "add_note", None)
            if add_note:
                add_note(SHARED_PUBLISHER_WARNING)

            elif e.args:
                e.args = (f"{e.args}\n\n{SHARED_PUBLISHER_WARNING}", *e.args[1:])

            raise

    def open(self) -> None:
        """Open the underlying writer if it owns a file handle."""
        self._writer.open()

    def close(self):
        """Close the underlying writer and flush any buffered data."""
        self._writer.close()


class JsonFileWriter(FileWriter[str]):
    """Write JSON records as a complete array on disk.

    Each publish operation reloads the file, appends the new record, and writes
    the updated list back to disk. This preserves the existing array-based JSON
    format and maintains compatibility with older consumers.
    """

    def __init__(self, file: IO[str]) -> None:
        """Initialize the JSON writer and seed an empty array when needed."""
        super().__init__(file)
        self._file.seek(0, 2)
        if self._file.tell() == 0:
            self._file.write("[]")
            self._file.flush()

    def write(self, data: Measurement | Command) -> None:
        """Append a Measurement or Command payload to the JSON array."""
        # Read existing content
        self._file.seek(0)
        try:
            content = self._file.read().strip()
            existing_data = json.loads(content) if content else []
        except json.JSONDecodeError:
            existing_data = []

        # Append new data
        if isinstance(existing_data, list):
            existing_data.append(data.__dict__)
        else:
            existing_data = [existing_data, data.__dict__]

        # Write back to file
        self._file.seek(0)
        self._file.truncate()
        json.dump(existing_data, self._file, indent=2)
        self._file.flush()


def _json_safe(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


class JsonlFileWriter(FileWriter[str]):
    """Write newline-delimited JSON records to a persistent stream."""

    def write(self, data: Measurement | Command) -> None:
        """Append one JSON object as a single newline-delimited record."""
        self._file.write(json.dumps(_json_safe(data.__dict__), allow_nan=False) + "\n")
        self._file.flush()


class CsvFileWriter(FileWriter[str]):
    """Write Measurement and Command data as CSV rows.

    One row is emitted per channel value. The writer ensures the CSV header is
    created once per file and then appends each sample in the canonical order.
    """

    def __init__(self, file: IO[str]) -> None:
        """Initialize the CSV writer and create the header when needed."""
        super().__init__(file)
        self._file.seek(0, 2)
        self._writer = csv.DictWriter(self._file, fieldnames=["timestamp", "channel", "value", "tags"])
        if self._file.tell() == 0:
            self._writer.writeheader()
            self._file.flush()

    def write(self, data: Measurement | Command) -> None:
        """Append a Measurement or Command to the CSV output."""
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
                self._writer.writerow({"timestamp": timestamp, "channel": channel_name, "value": value, "tags": tags})

    def _write_command(self, data: Command):
        """Write Command data as individual rows."""
        # Write each channel's data as separate rows
        for channel_name, value in data.channel_data.items():
            tags = json.dumps(data.tags) if data.tags else ""
            self._writer.writerow({"timestamp": data.timestamp, "channel": channel_name, "value": value, "tags": tags})


class AvroFileWriter(FileWriter[bytes]):
    """Write Avro records with a schema compatible with the ingest format.

    The writer maintains a fastavro schema and a binary stream, allowing batches
    of Measurement and Command values to be appended without rewriting the entire
    file.
    """

    def __init__(self, file: IO[bytes]) -> None:
        """Initialize the Avro writer with a binary stream and schema."""
        super().__init__(file)

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

        # Using snappy compression requires cramjam package
        self._writer = fastavro.write.Writer(self._file, self._parsed_schema, codec="snappy")

    def write(self, data: Measurement | Command) -> None:
        """Append a Measurement or Command as one or more Avro records."""
        if isinstance(data, Measurement):
            self._write_measurement(data)
        elif isinstance(data, Command):
            self._write_command(data)
        self._writer.flush()

    def _write_measurement(self, data: Measurement):
        """Write Measurement data as batches."""
        for channel_name, values in data.channel_data.items():
            self._writer.write(
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
            self._writer.write(
                {
                    "channel": channel_name,
                    "timestamps": [data.timestamp],
                    "values": [value],
                    "tags": data.tags or {},
                }
            )

    def close(self) -> None:
        """Close the file writer."""
        if not self._file.closed:
            self._writer.flush()
            super().close()
