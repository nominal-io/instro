"""File-based publishers (JSON, CSV, Avro)."""

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Protocol

import fastavro

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
        format: Literal["json", "csv", "avro"] = "avro",
        custom_file_name: str | None = None,
    ):
        """Write Measurement/Command data to a file.

        Args:
            directory: Output directory.
            format: ``"json"``, ``"csv"``, or ``"avro"`` (default).
            custom_file_name: Filename without extension; defaults to ``measurements-<UTC-timestamp>``.
        """
        self.directory = Path(directory)
        self.format = format
        self.custom_file_name = custom_file_name
        self._writer: FileWriter

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
            self._writer = JsonFileWriter(self.file_path)
        elif format == "csv":
            self._writer = CsvFileWriter(self.file_path)
        elif format == "avro":
            self._writer = AvroFileWriter(self.file_path)
        else:
            raise ValueError(f"Unsupported format: {format}")

    def publish(self, data: Measurement | Command, **kwargs):
        """Publish data to file using the appropriate writer."""
        self._writer.write(data)

    def open(self):
        # Stubbing out open method for when we opt to refactor this to use a file handle
        pass

    def close(self):
        """Close the publisher and ensure all data is written."""
        self._writer.close()


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


class CsvFileWriter:
    """Handles CSV format writing with proper file management."""

    def __init__(self, file_path: Path):
        self.file_path = file_path
        self._headers_written = False
        self._ensure_file_exists()

    def _ensure_file_exists(self):
        """Create directory and file if they don't exist."""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        # CSV file will be created when first write occurs

    def write(self, data: Measurement | Command):
        """Append data to CSV file."""
        mode = "a" if self.file_path.exists() else "w"

        with open(self.file_path, mode, newline="") as f:
            writer = csv.writer(f)

            if isinstance(data, Measurement):
                self._write_measurement(writer, data)
            elif isinstance(data, Command):
                self._write_command(writer, data)

    def _write_measurement(self, writer: Any, data: Measurement):
        """Write Measurement data as individual rows."""
        # Write headers if not already written
        if not self._headers_written:
            headers = ["timestamp", "channel", "value", "tags"]
            writer.writerow(headers)
            self._headers_written = True

        # Write each channel's data as separate rows
        for channel_name, values in data.channel_data.items():
            for i, value in enumerate(values):
                # Each channel should have the same number of values as timestamps
                # but handle edge case where they don't match
                timestamp = data.timestamps[i] if i < len(data.timestamps) else data.timestamps[-1]
                tags = json.dumps(data.tags) if data.tags else ""
                writer.writerow([timestamp, channel_name, value, tags])

    def _write_command(self, writer: Any, data: Command):
        """Write Command data as individual rows."""
        # Write headers if not already written
        if not self._headers_written:
            headers = ["timestamp", "channel", "value", "tags"]
            writer.writerow(headers)
            self._headers_written = True

        # Write each channel's data as separate rows
        for channel_name, value in data.channel_data.items():
            tags = json.dumps(data.tags) if data.tags else ""
            writer.writerow([data.timestamp, channel_name, value, tags])

    def open(self):
        # Stubbing out open method for when we opt to refactor this to use a file handle
        pass

    def close(self):
        """Close the file writer."""
        pass


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
        self._writer = fastavro.write.Writer(self._file, self._parsed_schema, codec="snappy")

    def _ensure_file_exists(self):
        """Create directory if it doesn't exist."""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, data: Measurement | Command):
        """Append data to Avro file."""
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

    def open(self):
        # Stubbing out open method for when we opt to refactor this to use a file handle
        pass

    def close(self):
        """Close the file writer."""
        if self._file and not self._file.closed:
            self._writer.flush()
            self._file.close()
