"""File-based consumers for reading data produced by FilePublisher."""

import json
import time
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

from instro.lib.types import Command, Measurement
from instro.unstable.lib.consumers import Consumer


class FileFormats(Enum):
    JSON = "json"
    JSONL = "jsonl"
    CSV = "csv"
    AVRO = "avro"


class FileConsumer(Consumer):
    """File-based consumer for reading data produced by FilePublisher."""

    def __init__(self, file_path: Path, poll_s: float = 1.0, format: FileFormats = FileFormats.JSONL):
        self.file_path = Path(file_path)
        self.poll_s = poll_s
        self.format = format
        self._is_open = False

    def open(self) -> Any:
        self._fh = open(self.file_path, "r")
        self._is_open = True
        return self._fh

    def close(self) -> None:
        if self._is_open:
            self._fh.close()
            self._is_open = False

    def consume(self) -> Iterable[Measurement | Command]:
        with self.open() as fh:
            while True:
                line = fh.readline()
                if not line:
                    time.sleep(self.poll_s)
                    continue
                if self.format == FileFormats.JSONL:
                    yield super().record_to_object(json.loads(line))
                elif self.format == FileFormats.JSON:
                    yield super().record_to_object(json.loads(line))
                else:
                    raise ValueError(f"Unsupported file format: {self.format.name}")
