"""Unit tests for CsvFileWriter's persistent file handle."""

import csv

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


def _rows(path) -> list[list[str]]:
    with open(path, newline="") as f:
        return list(csv.reader(f))


def test_csv_writes_header_once_and_one_row_per_value(tmp_path):
    publisher = FilePublisher(directory=tmp_path, format="csv", custom_file_name="capture")

    publisher.publish(_measurement())
    publisher.publish(_command())
    publisher.close()

    rows = _rows(tmp_path / "capture.csv")
    assert rows[0] == ["timestamp", "channel", "value", "tags"]
    assert rows[1] == ["100", "channel_a", "1.0", '{"unit": "volts"}']
    assert rows[2] == ["200", "channel_a", "2.0", '{"unit": "volts"}']
    assert rows[3] == ["300", "channel_b", "5.0", ""]
    assert len(rows) == 4


def test_csv_rows_are_flushed_per_publish(tmp_path):
    publisher = FilePublisher(directory=tmp_path, format="csv", custom_file_name="capture")

    publisher.publish(_measurement())

    rows = _rows(tmp_path / "capture.csv")
    assert len(rows) == 3  # header + two values, readable before close()
    publisher.close()


def test_csv_uses_one_handle_across_writes(tmp_path):
    publisher = FilePublisher(directory=tmp_path, format="csv", custom_file_name="capture")
    handle = publisher._writer._file

    publisher.publish(_measurement())
    publisher.publish(_command())

    assert publisher._writer._file is handle
    publisher.close()
    assert handle.closed
    publisher.close()  # idempotent


def test_csv_appends_across_publisher_instances_with_one_header(tmp_path):
    first = FilePublisher(directory=tmp_path, format="csv", custom_file_name="capture")
    first.publish(_command())
    first.close()

    second = FilePublisher(directory=tmp_path, format="csv", custom_file_name="capture")
    second.publish(_command())
    second.close()

    rows = _rows(tmp_path / "capture.csv")
    assert rows.count(["timestamp", "channel", "value", "tags"]) == 1
    assert rows.count(["300", "channel_b", "5.0", ""]) == 2


def test_csv_construction_writes_header_exactly_once_per_file(tmp_path):
    first = FilePublisher(directory=tmp_path, format="csv", custom_file_name="capture")
    first.close()  # never published

    second = FilePublisher(directory=tmp_path, format="csv", custom_file_name="capture")
    second.close()

    assert _rows(tmp_path / "capture.csv") == [["timestamp", "channel", "value", "tags"]]
