"""Data base class and its Measurement/Command subclasses."""

import json

import pytest

from instro.lib.publishers.channel_buffer import DequeInMemoryPublisher
from instro.lib.publishers.files import JsonlFileWriter
from instro.lib.types import Command, Data, DataType, Measurement


def test_subclasses_are_data_and_pin_type() -> None:
    measurement = Measurement(channel_data={"x": [1.0]}, timestamps=[0])
    command = Command(channel_data={"x.cmd": [1.0]}, timestamps=[0])
    assert isinstance(measurement, Data) and isinstance(command, Data)
    assert measurement.type is DataType.MEASUREMENT
    assert command.type is DataType.COMMAND


def test_command_accepts_legacy_scalar_and_timestamp_form() -> None:
    command = Command(channel_data={"x.cmd": 5.0}, timestamp=300)
    assert command.channel_data == {"x.cmd": [5.0]}
    assert command.timestamps == [300]
    assert command.timestamp == 300


def test_command_rejects_more_than_one_point_per_channel() -> None:
    with pytest.raises(ValueError, match="one point per channel"):
        Command(channel_data={"x.cmd": [1.0, 2.0]}, timestamps=[1, 2])


def test_channel_buffer_receives_commands() -> None:
    publisher = DequeInMemoryPublisher(maxlen=4)
    publisher.publish(Command(channel_data={"x.cmd": 2.0}, timestamp=7))
    assert publisher.get("x.cmd").latest == 2.0


def test_jsonl_writer_keeps_type_off_the_wire(tmp_path) -> None:
    writer = JsonlFileWriter(tmp_path / "out.jsonl")
    writer.write(Command(channel_data={"x.cmd": 1.0}, timestamp=1))
    writer.close()
    record = json.loads((tmp_path / "out.jsonl").read_text().splitlines()[0])
    assert "type" not in record
