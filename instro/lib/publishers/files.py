"""File-based publishers (JSON, JSONL, CSV, Avro)."""

import csv
import json
import math
import warnings
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from tempfile import mkdtemp
from typing import IO, Any, AnyStr, Generic, Literal

import fastavro

from instro.lib.types import Command, Measurement


class FileWriter(Generic[AnyStr], ABC):
    """Base class for format writers.

    Every writer needs a writable stream. The JSONL, CSV, and Avro writers write at the stream's
    current position. `JsonFileWriter` rewrites the whole stream on each write.

    Attributes:
        mode (str): Mode `FilePublisher` uses to open this writer's file.
        newline (str | None): ``newline`` argument `FilePublisher` uses to open this writer's file.
    """

    mode: str
    newline: str | None = None

    def __init__(self, file: IO[AnyStr]) -> None:
        """Wrap an open stream.

        Args:
            file (IO[AnyStr]): Stream that receives the records. Text or binary, per the format.

        Raises:
            ValueError: If the stream is not writable.
        """
        if not file.writable():
            raise ValueError("FilePublisher needs a writable stream")
        self._file: IO[AnyStr] = file

    @abstractmethod
    def write(self, data: Measurement | Command) -> None:
        """Write one Measurement or Command to the stream."""


class FilePublisher:
    def __init__(
        self,
        directory: str | Path | None = None,
        format: Literal["json", "jsonl", "csv", "avro"] = "avro",
        custom_file_name: str | None = None,
        *,
        file: IO[Any] | None = None,
    ) -> None:
        """Write Measurement/Command data to a file or to an open stream.

        Args:
            directory: Output directory. Defaults to a new temporary directory.
            format: ``"jsonl"``, ``"csv"``, ``"avro"`` (default), or ``"json"`` (deprecated).
            custom_file_name: Filename without extension; defaults to ``measurements-<local-timestamp>``.
            file: Open stream to write to instead of a file (binary for ``"avro"``, text otherwise); ``close()``
                leaves it open. ``"csv"`` needs a seekable stream; ``"json"`` needs a readable, seekable one.

        Raises:
            ValueError: If ``file`` is combined with ``directory`` or ``custom_file_name``, the format is unknown,
                or ``file`` does not support what the format needs.
        """
        # Check the arguments and pick the writer for the format
        if file is not None and (directory is not None or custom_file_name is not None):
            raise ValueError("file cannot be combined with directory or custom_file_name")
        if format not in _WRITERS:
            raise ValueError(f"Unsupported format: {format}")
        if format == "json":
            warnings.warn(
                'FilePublisher format="json" rewrites the whole file on every publish and is deprecated; '
                'use format="jsonl" instead.',
                DeprecationWarning,
                stacklevel=2,
            )
        writer_class = _WRITERS[format]
        self.format = format
        self.custom_file_name = custom_file_name
        self.directory: Path | None = None
        self.file_path: Path | None = None

        # Open the output file, unless the caller gave a stream
        if file is None:
            self.directory = Path(directory) if directory is not None else Path(mkdtemp())
            if custom_file_name is None:
                custom_file_name = f"measurements-{datetime.now():%Y-%m-%d-%H-%M-%S}"
            self.file_path = self.directory / f"{custom_file_name}.{format}"
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            file = open(self.file_path, writer_class.mode, newline=writer_class.newline)

        # Attach the writer to the stream, and close the file this publisher opened if that fails
        self._file = file
        try:
            self._writer = writer_class(file)
        except Exception:
            self.close()
            raise

    def publish(self, data: Measurement | Command, **kwargs) -> None:
        """Publish data to file using the appropriate writer."""
        try:
            self._writer.write(data)
        except Exception as e:
            e.add_note("If you're attempting to publish from multiple instruments, consider using SharedPublisher.")
            raise

    def open(self) -> None:
        # The file opens in __init__; kept so callers of open() do not break
        pass

    def close(self) -> None:
        """Close the file this publisher opened. A stream passed as ``file`` stays open."""
        if self.file_path is not None:
            self._file.close()


class JsonFileWriter(FileWriter[str]):
    """Write records as one JSON array.

    Each write reads the whole stream from position 0 and rewrites it. Content that is not valid JSON is erased.
    """

    # "a+" creates a missing file, keeps existing records, and allows reads
    mode = "a+"

    def __init__(self, file: IO[str]) -> None:
        """Wrap the stream and write an empty array if the stream is empty.

        Raises:
            ValueError: If the stream is not writable, readable, and seekable.
        """
        super().__init__(file)
        if not (file.readable() and file.seekable()):
            raise ValueError('FilePublisher format="json" needs a readable, seekable stream, such as open(path, "w+")')
        self._file.seek(0, 2)
        if self._file.tell() == 0:
            self._file.write("[]")
            self._file.flush()

    def write(self, data: Measurement | Command) -> None:
        """Append data to the JSON array."""
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
    """Write newline-delimited JSON, one record per line."""

    mode = "a"

    def write(self, data: Measurement | Command) -> None:
        """Append data as one JSON line, mapping non-finite floats to null."""
        self._file.write(json.dumps(_json_safe(data.__dict__), allow_nan=False) + "\n")
        self._file.flush()


class CsvFileWriter(FileWriter[str]):
    """Write one CSV row per channel value, with a header at the start of the stream."""

    mode = "a"
    newline = ""

    def __init__(self, file: IO[str]) -> None:
        """Wrap the stream and write the header if the stream is at its start.

        Raises:
            ValueError: If the stream is not writable and seekable.
        """
        super().__init__(file)
        if not file.seekable():
            raise ValueError('FilePublisher format="csv" needs a seekable stream to decide whether to write the header')
        self._writer = csv.DictWriter(self._file, fieldnames=["timestamp", "channel", "value", "tags"])
        if self._file.tell() == 0:
            self._writer.writeheader()
            self._file.flush()

    def write(self, data: Measurement | Command) -> None:
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
                self._writer.writerow({"timestamp": timestamp, "channel": channel_name, "value": value, "tags": tags})

    def _write_command(self, data: Command):
        """Write Command data as individual rows."""
        # Write each channel's data as separate rows
        for channel_name, value in data.channel_data.items():
            tags = json.dumps(data.tags) if data.tags else ""
            self._writer.writerow({"timestamp": data.timestamp, "channel": channel_name, "value": value, "tags": tags})


class AvroFileWriter(FileWriter[bytes]):
    """Write snappy-compressed Avro records in the Nominal Core ingest schema."""

    mode = "wb"

    def __init__(self, file: IO[bytes]) -> None:
        """Wrap the stream and write the Avro header."""
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
        """Append data as Avro records and flush them to the stream."""
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


_WRITERS: dict[str, type[FileWriter[Any]]] = {
    "json": JsonFileWriter,
    "jsonl": JsonlFileWriter,
    "csv": CsvFileWriter,
    "avro": AvroFileWriter,
}
