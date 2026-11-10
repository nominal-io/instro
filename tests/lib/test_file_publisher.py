"""Unit tests for FilePublisher formats, output targets, and JSON deprecation."""

import csv
import io
import json
import warnings

import fastavro
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


def test_jsonl_maps_non_finite_floats_to_null(tmp_path):
    publisher = FilePublisher(directory=tmp_path, format="jsonl", custom_file_name="capture")

    publisher.publish(
        Measurement(
            channel_data={"channel_a": [float("nan"), float("inf"), float("-inf"), 1.5]},
            timestamps=[100, 200, 300, 400],
            tags=None,
        )
    )
    publisher.close()

    record = json.loads((tmp_path / "capture.jsonl").read_text())
    assert record["channel_data"]["channel_a"] == [None, None, None, 1.5]


def test_jsonl_each_line_is_complete_before_close(tmp_path):
    publisher = FilePublisher(directory=tmp_path, format="jsonl", custom_file_name="capture")

    publisher.publish(_measurement())

    content = (tmp_path / "capture.jsonl").read_text()
    assert content.endswith("\n")
    json.loads(content)


_JSON_RECORDS = [
    {"channel_data": {"channel_a": [1.0, 2.0]}, "timestamps": [100, 200], "tags": {"unit": "volts"}},
    {"channel_data": {"channel_b": 5.0}, "timestamp": 300, "tags": None},
]

_EXPECTED_RECORDS = {
    "json": _JSON_RECORDS,
    "jsonl": _JSON_RECORDS,
    "csv": [
        {"timestamp": "100", "channel": "channel_a", "value": "1.0", "tags": '{"unit": "volts"}'},
        {"timestamp": "200", "channel": "channel_a", "value": "2.0", "tags": '{"unit": "volts"}'},
        {"timestamp": "300", "channel": "channel_b", "value": "5.0", "tags": ""},
    ],
    "avro": [
        {"channel": "channel_a", "timestamps": [100, 200], "values": [1.0, 2.0], "tags": {"unit": "volts"}},
        {"channel": "channel_b", "timestamps": [300], "values": [5.0], "tags": {}},
    ],
}


def _read_records(format: str, data: bytes) -> list:
    if format == "json":
        return json.loads(data)
    if format == "jsonl":
        return [json.loads(line) for line in data.splitlines()]
    if format == "csv":
        return list(csv.DictReader(io.StringIO(data.decode(), newline="")))
    return list(fastavro.reader(io.BytesIO(data)))


_STREAM_TARGETS = ["in_memory_stream", "opened_file"]


@pytest.mark.parametrize(
    ("format", "target"),
    [
        (format, target)
        for target in ["directory", "default_directory", *_STREAM_TARGETS]
        for format in ["json", "jsonl", "csv", "avro"]
        if not (format == "json" and target in _STREAM_TARGETS)
    ],
)
@pytest.mark.parametrize("publish_records", [False, True], ids=["empty", "records"])
def test_every_format_writes_readable_output_to_every_target(tmp_path, monkeypatch, format, target, publish_records):
    # Create the stream for the stream targets
    if target == "opened_file":
        binary = format == "avro"
        stream = open(tmp_path / f"capture.{format}", "w+b" if binary else "w+", newline=None if binary else "")
    else:
        stream = io.BytesIO() if format == "avro" else io.StringIO()

    # Create the publisher for the target, with tmp_path as the working directory
    monkeypatch.chdir(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        if target == "directory":
            publisher = FilePublisher(directory=tmp_path / "out", format=format, custom_file_name="capture")
        elif target == "default_directory":
            publisher = FilePublisher(format=format)
        else:
            publisher = FilePublisher(file=stream, format=format)

    # Publish a Measurement and a Command, then close
    if publish_records:
        publisher.publish(_measurement())
        publisher.publish(_command())
    publisher.close()

    # Read the output back: the publisher closes files it opened and leaves a caller's stream open
    if target in _STREAM_TARGETS:
        assert publisher.file_path is None
        assert not stream.closed
        stream.seek(0)
        value = stream.read()
        stream.close()
        data = value.encode() if isinstance(value, str) else value
    else:
        assert publisher._file.closed
        expected_directory = tmp_path / ("out" if target == "directory" else "instro-measurements")
        assert publisher.file_path.parent.resolve() == expected_directory.resolve()
        assert publisher.file_path.suffix == f".{format}"
        data = publisher.file_path.read_bytes()
    assert _read_records(format, data) == (_EXPECTED_RECORDS[format] if publish_records else [])


@pytest.mark.parametrize("format", ["jsonl", "csv", "avro"])
def test_every_format_appends_to_a_stream_that_already_has_data(format):
    # Publish the same records twice, each time with a new publisher on the same stream
    stream = io.BytesIO() if format == "avro" else io.StringIO()
    for _ in range(2):
        publisher = FilePublisher(file=stream, format=format)
        publisher.publish(_measurement())
        publisher.publish(_command())
        publisher.close()

    # The second publisher keeps the first one's records (and the single CSV header or Avro header)
    value = stream.getvalue()
    data = value.encode() if isinstance(value, str) else value
    assert _read_records(format, data) == _EXPECTED_RECORDS[format] * 2


def test_jsonl_close_closes_file_handle(tmp_path):
    publisher = FilePublisher(directory=tmp_path, format="jsonl", custom_file_name="capture")

    publisher.close()

    assert publisher.writer._file.closed
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


@pytest.mark.parametrize("path_args", [{"directory": "out"}, {"custom_file_name": "capture"}])
def test_file_cannot_be_combined_with_directory_or_custom_file_name(path_args):
    with pytest.raises(ValueError, match="file cannot be combined"):
        FilePublisher(file=io.StringIO(), format="jsonl", **path_args)


def test_read_only_stream_raises_clear_error(tmp_path):
    (tmp_path / "capture.jsonl").write_text("")
    with open(tmp_path / "capture.jsonl") as stream, pytest.raises(ValueError, match="writable stream"):
        FilePublisher(file=stream, format="jsonl")


def test_json_rejects_streams_and_suggests_jsonl():
    with pytest.raises(ValueError, match='format="json" does not support file; use format="jsonl"'):
        FilePublisher(file=io.StringIO(), format="json")


def test_avro_rejects_text_streams():
    with pytest.raises(ValueError, match="AvroFileWriter needs a binary stream"):
        FilePublisher(file=io.StringIO(), format="avro")


@pytest.mark.parametrize("format", ["jsonl", "csv"])
@pytest.mark.parametrize("mode", ["wb", "ab"])
def test_text_formats_reject_binary_streams(tmp_path, format, mode):
    with open(tmp_path / f"capture.{format}", mode) as stream, pytest.raises(ValueError, match="needs a text stream"):
        FilePublisher(file=stream, format=format)


def test_avro_rejects_append_only_binary_stream_with_existing_data(tmp_path):
    # Write one Avro file, then reopen it in "ab", which cannot read the existing header
    publisher = FilePublisher(directory=tmp_path, format="avro", custom_file_name="capture")
    publisher.publish(_command())
    publisher.close()

    with open(tmp_path / "capture.avro", "ab") as stream, pytest.raises(ValueError, match="'a\\+' mode"):
        FilePublisher(file=stream, format="avro")


def test_csv_writes_header_to_a_stream_that_cannot_seek():
    class NonSeekableStream(io.StringIO):
        def seekable(self) -> bool:
            return False

    stream = NonSeekableStream()
    publisher = FilePublisher(file=stream, format="csv")
    publisher.publish(_command())
    publisher.close()

    assert _read_records("csv", stream.getvalue().encode()) == _EXPECTED_RECORDS["csv"][2:]


@pytest.mark.parametrize("pass_stream", [False, True], ids=["opened_by_publisher", "caller_stream"])
def test_writer_failure_closes_only_the_file_the_publisher_opened(tmp_path, monkeypatch, pass_stream):
    # Make the Avro writer fail after the stream is open, and keep the stream it got
    streams = []

    def failing_writer(stream, *args, **kwargs):
        streams.append(stream)
        raise RuntimeError("codec missing")

    monkeypatch.setattr(fastavro.write, "Writer", failing_writer)

    # The publisher closes its own file, and leaves a caller's stream open
    caller_stream = io.BytesIO()
    with pytest.raises(RuntimeError, match="codec missing"):
        if pass_stream:
            FilePublisher(file=caller_stream, format="avro")
        else:
            FilePublisher(directory=tmp_path, format="avro", custom_file_name="capture")
    assert streams[0].closed is not pass_stream


def test_avro_appends_across_publisher_instances(tmp_path):
    for _ in range(2):
        publisher = FilePublisher(directory=tmp_path, format="avro", custom_file_name="capture")
        publisher.publish(_measurement())
        publisher.publish(_command())
        publisher.close()

    assert _read_records("avro", (tmp_path / "capture.avro").read_bytes()) == _EXPECTED_RECORDS["avro"] * 2
