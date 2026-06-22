"""Unit tests for EtherNet/IP tag data_type and write bounds config."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from instro.unstable.ethernetip import EtherNetIPConfig, EtherNetIPDevice
from tests.ethernetip_fakes import FakePlcKind, FakePlcValue, install_fake_native_ethernetip


def test_data_type_is_required_for_tags() -> None:
    with pytest.raises(ValidationError) as exc_info:
        EtherNetIPConfig.model_validate(
            {
                "device": {"name": "test_plc"},
                "tags": [{"alias": "speed", "tag_name": "Speed"}],
            }
        )

    assert "data_type" in str(exc_info.value)


def test_duplicate_tag_names_are_rejected() -> None:
    with pytest.raises(ValidationError, match="Duplicate tag names found: \\['Speed'\\]"):
        EtherNetIPConfig.model_validate(
            {
                "device": {"name": "test_plc"},
                "tags": [
                    {"alias": "speed", "tag_name": "Speed", "data_type": "dint"},
                    {"alias": "speed_copy", "tag_name": "Speed", "data_type": "dint"},
                ],
            }
        )


def test_required_data_type_validates_config_and_read(monkeypatch: pytest.MonkeyPatch) -> None:
    config = EtherNetIPConfig.model_validate(
        {
            "device": {"name": "test_plc"},
            "tags": [{"alias": "speed", "tag_name": "Speed", "data_type": "dint"}],
        }
    )
    assert config.get_tag("speed").data_type == "dint"

    install_fake_native_ethernetip(monkeypatch, {"Speed": FakePlcValue(FakePlcKind.DINT, 123)})
    instrument = EtherNetIPDevice(config, connection={"host": "192.0.2.10"})
    instrument.open()

    measurement = instrument.read_tag("speed")

    assert measurement.channel_data["test_plc.speed"] == [123]


def test_read_tag_and_write_tag_use_configured_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    native = install_fake_native_ethernetip(monkeypatch, {"Speed": FakePlcValue(FakePlcKind.DINT, 123)})
    instrument = EtherNetIPDevice(
        {
            "device": {"name": "test_plc"},
            "connection": {"host": "192.0.2.10"},
            "tags": [{"alias": "speed", "tag_name": "Speed", "data_type": "dint"}],
        }
    )
    instrument.open()

    measurement = instrument.read_tag("speed")
    command = instrument.write_tag("speed", 42)

    assert native.reads == ["Speed"]
    assert native.writes == [("Speed", FakePlcValue(FakePlcKind.DINT, 42))]
    assert measurement.channel_data["test_plc.speed"] == [123]
    assert command.channel_data == {"test_plc.speed.cmd": 42}


def test_bool_read_publishes_numeric_sample(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_native_ethernetip(
        monkeypatch,
        {
            "Ready": FakePlcValue(FakePlcKind.BOOL, True),
            "Faulted": FakePlcValue(FakePlcKind.BOOL, False),
        },
    )
    instrument = EtherNetIPDevice(
        {
            "device": {"name": "test_plc"},
            "connection": {"host": "192.0.2.10"},
            "tags": [
                {"alias": "ready", "tag_name": "Ready", "data_type": "bool"},
                {"alias": "faulted", "tag_name": "Faulted", "data_type": "bool"},
            ],
        }
    )
    instrument.open()

    ready = instrument.read_tag("ready")
    faulted = instrument.read_tag("faulted")

    assert ready.channel_data["test_plc.ready"] == [1]
    assert faulted.channel_data["test_plc.faulted"] == [0]


def test_typed_write_builds_native_value_without_reading(monkeypatch: pytest.MonkeyPatch) -> None:
    native = install_fake_native_ethernetip(monkeypatch)
    instrument = EtherNetIPDevice(
        {
            "device": {"name": "test_plc"},
            "connection": {"host": "192.0.2.10"},
            "tags": [{"alias": "speed", "tag_name": "Speed", "data_type": "dint"}],
        }
    )
    instrument.open()

    command = instrument.write_tag("speed", 42)

    assert native.reads == []
    assert native.writes == [("Speed", FakePlcValue(FakePlcKind.DINT, 42))]
    assert command.channel_data == {"test_plc.speed.cmd": 42}


def test_provided_data_type_validates_returned_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_native_ethernetip(monkeypatch, {"Speed": FakePlcValue(FakePlcKind.REAL, 1.25)})
    instrument = EtherNetIPDevice(
        {
            "device": {"name": "test_plc"},
            "connection": {"host": "192.0.2.10"},
            "tags": [{"alias": "speed", "tag_name": "Speed", "data_type": "dint"}],
        }
    )
    instrument.open()

    with pytest.raises(TypeError, match="expected PLC kind DINT"):
        instrument.read_tag("speed")


def test_integer_write_rejects_value_outside_plc_data_type_range(monkeypatch: pytest.MonkeyPatch) -> None:
    native = install_fake_native_ethernetip(monkeypatch)
    instrument = EtherNetIPDevice(
        {
            "device": {"name": "test_plc"},
            "connection": {"host": "192.0.2.10"},
            "tags": [{"alias": "speed", "tag_name": "Speed", "data_type": "usint"}],
        }
    )
    instrument.open()

    with pytest.raises(ValueError, match="out of range for usint"):
        instrument.write_tag("speed", 256)

    assert native.writes == []


def test_integer_write_rejects_float_value(monkeypatch: pytest.MonkeyPatch) -> None:
    native = install_fake_native_ethernetip(monkeypatch)
    instrument = EtherNetIPDevice(
        {
            "device": {"name": "test_plc"},
            "connection": {"host": "192.0.2.10"},
            "tags": [{"alias": "speed", "tag_name": "Speed", "data_type": "dint"}],
        }
    )
    instrument.open()

    with pytest.raises(TypeError, match="got float 12.0"):
        instrument.write_tag("speed", 12.0)

    assert native.writes == []


def test_write_min_max_are_optional_and_only_apply_to_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    native = install_fake_native_ethernetip(monkeypatch, {"Speed": FakePlcValue(FakePlcKind.DINT, 99)})
    instrument = EtherNetIPDevice(
        {
            "device": {"name": "test_plc"},
            "connection": {"host": "192.0.2.10"},
            "tags": [
                {
                    "alias": "speed",
                    "tag_name": "Speed",
                    "data_type": "dint",
                    "write_min": 0,
                    "write_max": 10,
                }
            ],
        }
    )
    instrument.open()

    measurement = instrument.read_tag("speed")
    assert measurement.channel_data["test_plc.speed"] == [99]

    with pytest.raises(ValueError, match="below write_min"):
        instrument.write_tag("speed", -1)
    with pytest.raises(ValueError, match="above write_max"):
        instrument.write_tag("speed", 11)

    command = instrument.write_tag("speed", 10)

    assert native.writes == [("Speed", FakePlcValue(FakePlcKind.DINT, 10))]
    assert command.channel_data == {"test_plc.speed.cmd": 10}


def test_write_min_max_must_fit_integer_data_type_range() -> None:
    with pytest.raises(ValidationError, match="out of range for usint"):
        EtherNetIPConfig.model_validate(
            {
                "device": {"name": "test_plc"},
                "tags": [{"alias": "speed", "tag_name": "Speed", "data_type": "usint", "write_min": -1}],
            }
        )


def test_tag_rejects_unsupported_write_value_map() -> None:
    with pytest.raises(ValidationError, match="write_value_map"):
        EtherNetIPConfig.model_validate(
            {
                "device": {"name": "test_plc"},
                "tags": [
                    {
                        "alias": "mode",
                        "tag_name": "Mode",
                        "data_type": "dint",
                        "write_value_map": {"run": 1},
                    }
                ],
            }
        )
