"""Unit tests for FilePublisher JSONL support and JSON deprecation."""

import json
import warnings

import pytest

from instro.lib.publishers import FilePublisher
from instro.lib.types import Command, Measurement


def _measurement() -> Measurement:
    return Measurement(
        channel_data={"channel_a": [1.0, 2.0]},
        timestamps=[100, 200],
        tags={"unit": "volts"},
    )


def _command() -> Command:
    return Command(channel_data={"channel_b": 5.0}, timestamp=300, tags=None)


def test_jsonl_writes_one_json_object_per_line(tmp_path):
    publisher = FilePublisher(directory=tmp_path, format="jsonl", custom_file_name="capture")

    publisher.publish(_measurement())
    publisher.publish(_command())
    publisher.close()

    lines = (tmp_path / "capture.jsonl").read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == {
        "channel_data": {"channel_a": [1.0, 2.0]},
        "timestamps": [100, 200],
        "tags": {"unit": "volts"},
    }
    assert json.loads(lines[1]) == {"channel_data": {"channel_b": 5.0}, "timestamp": 300, "tags": None}


def test_jsonl_record_shape_matches_json_writer(tmp_path):
    jsonl_publisher = FilePublisher(directory=tmp_path, format="jsonl", custom_file_name="capture")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        json_publisher = FilePublisher(directory=tmp_path, format="json", custom_file_name="capture")

    jsonl_publisher.publish(_measurement())
    json_publisher.publish(_measurement())
    jsonl_publisher.close()
    json_publisher.close()

    jsonl_record = json.loads((tmp_path / "capture.jsonl").read_text())
    json_record = json.loads((tmp_path / "capture.json").read_text())[0]
    assert jsonl_record == json_record


def test_jsonl_each_line_is_complete_before_close(tmp_path):
    publisher = FilePublisher(directory=tmp_path, format="jsonl", custom_file_name="capture")

    publisher.publish(_measurement())

    content = (tmp_path / "capture.jsonl").read_text()
    assert content.endswith("\n")
    json.loads(content)


def test_jsonl_close_closes_file_handle(tmp_path):
    publisher = FilePublisher(directory=tmp_path, format="jsonl", custom_file_name="capture")

    publisher.close()

    assert publisher._writer._file.closed
    publisher.close()  # idempotent


def test_json_format_emits_deprecation_warning(tmp_path):
    with pytest.warns(DeprecationWarning, match="jsonl"):
        FilePublisher(directory=tmp_path, format="json", custom_file_name="capture")


def test_json_format_still_writes_array(tmp_path):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        publisher = FilePublisher(directory=tmp_path, format="json", custom_file_name="capture")

    publisher.publish(_measurement())
    publisher.publish(_command())
    publisher.close()

    records = json.loads((tmp_path / "capture.json").read_text())
    assert isinstance(records, list)
    assert len(records) == 2


def test_jsonl_does_not_warn(tmp_path):
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        FilePublisher(directory=tmp_path, format="jsonl", custom_file_name="capture")
