"""Unit tests for NominalCorePublisher write-stream setup and file fallback validation."""

from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from nominal_streaming import NominalDatasetStream

from instro.lib.publishers import NominalCorePublisher

_MISSING = object()


class _FakeWriteStream(NominalDatasetStream):
    """Real stream type with no live Rust stream behind it, so the isinstance check still holds."""

    def __init__(self):
        self._impl = Mock()
        self.enqueue_batch = Mock()
        self.close = Mock()


class _RecordingDataset:
    """Dataset double whose get_write_stream mirrors the Nominal Core signature."""

    def __init__(self):
        self.stream = _FakeWriteStream()
        self.call: dict[str, object] = {}

    def get_write_stream(
        self,
        batch_size: int = 250_000,
        max_wait: timedelta = timedelta(seconds=0.25),
        implementation: str | None = None,
        file_fallback: Path | None = None,
        log_level: str | None = None,
        num_workers: int | None = None,
        *,
        data_format: object = _MISSING,
    ) -> _FakeWriteStream:
        self.call = {
            "batch_size": batch_size,
            "max_wait": max_wait,
            "implementation": implementation,
            "file_fallback": file_fallback,
            "data_format": data_format,
        }
        return self.stream


def _make_publisher(dataset: _RecordingDataset, **kwargs) -> NominalCorePublisher:
    with patch(
        "instro.lib.publishers.nominal_core._resolve_nominal_client_and_dataset",
        return_value=(Mock(), dataset),
    ):
        return NominalCorePublisher(dataset_rid="ri.catalog.main.dataset.abc123", **kwargs)


def test_write_stream_is_requested_without_the_deprecated_format_selector():
    dataset = _RecordingDataset()

    _make_publisher(dataset)

    assert dataset.call["data_format"] is _MISSING
    assert dataset.call["implementation"] is None


def test_write_stream_receives_the_avro_file_fallback(tmp_path):
    dataset = _RecordingDataset()
    fallback = tmp_path / "fallback.avro"

    _make_publisher(dataset, file_fallback=fallback)

    assert dataset.call["file_fallback"] == fallback


def test_non_avro_file_fallback_is_rejected_before_the_stream_is_opened():
    dataset = _RecordingDataset()

    with pytest.raises(ValueError, match="must end with '.avro'"):
        _make_publisher(dataset, file_fallback=Path("fallback.json"))

    assert dataset.call == {}


def test_batch_size_and_max_wait_are_forwarded():
    dataset = _RecordingDataset()
    max_wait = timedelta(seconds=5)

    _make_publisher(dataset, batch_size=10, max_wait=max_wait)

    assert dataset.call["batch_size"] == 10
    assert dataset.call["max_wait"] == max_wait
