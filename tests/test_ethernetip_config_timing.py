"""Unit tests for unstable EtherNet/IP timing config."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from instro.unstable.ethernetip import EtherNetIPConfig, EtherNetIPDevice
from tests.ethernetip_fakes import FakePlcKind, FakePlcValue, install_fake_native_ethernetip


def test_tag_poll_defaults_true_and_accepts_false() -> None:
    config = EtherNetIPConfig.model_validate(
        {
            "device": {"name": "test_plc"},
            "tags": [
                {"alias": "speed", "tag_name": "Speed", "data_type": "dint"},
                {"alias": "setpoint", "tag_name": "Setpoint", "data_type": "dint", "poll": False},
            ],
        }
    )

    assert config.get_tag("speed").poll is True
    assert config.get_tag("setpoint").poll is False


def test_timing_config_is_optional_and_accepts_poll_interval() -> None:
    no_timing = EtherNetIPConfig.model_validate({"device": {"name": "test_plc"}})
    assert no_timing.timing is None

    polling = EtherNetIPConfig.model_validate(
        {
            "device": {"name": "test_plc"},
            "timing": {"poll_interval": 1.0},
        }
    )
    assert polling.timing is not None
    assert polling.timing.poll_interval == 1.0


def test_config_rejects_extra_top_level_keys() -> None:
    with pytest.raises(ValidationError) as exc_info:
        EtherNetIPConfig.model_validate({"device": {"name": "test_plc"}, "metadata": {"owner": "controls"}})

    assert "Extra inputs are not permitted" in str(exc_info.value)


def test_background_daemon_only_reads_poll_enabled_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    state = install_fake_native_ethernetip(
        monkeypatch,
        {
            "Speed": FakePlcValue(FakePlcKind.DINT, 123),
            "Setpoint": FakePlcValue(FakePlcKind.DINT, 456),
        },
    )
    instrument = EtherNetIPDevice(
        {
            "device": {"name": "test_plc"},
            "connection": {"host": "192.0.2.10"},
            "timing": {"poll_interval": 1.0},
            "tags": [
                {"alias": "speed", "tag_name": "Speed", "data_type": "dint"},
                {"alias": "setpoint", "tag_name": "Setpoint", "data_type": "dint", "poll": False},
            ],
        }
    )
    instrument.open()

    instrument._background_daemon()

    assert state.reads == []
    assert state.batch_reads == [["Speed"]]


def test_background_daemon_batches_all_polled_tags_into_one_request(monkeypatch: pytest.MonkeyPatch) -> None:
    state = install_fake_native_ethernetip(
        monkeypatch,
        {
            "Speed": FakePlcValue(FakePlcKind.DINT, 1),
            "Pressure": FakePlcValue(FakePlcKind.DINT, 2),
            "Temperature": FakePlcValue(FakePlcKind.DINT, 3),
        },
    )
    instrument = EtherNetIPDevice(
        {
            "device": {"name": "test_plc"},
            "connection": {"host": "192.0.2.10"},
            "timing": {"poll_interval": 1.0},
            "tags": [
                {"alias": "speed", "tag_name": "Speed", "data_type": "dint"},
                {"alias": "pressure", "tag_name": "Pressure", "data_type": "dint"},
                {"alias": "temperature", "tag_name": "Temperature", "data_type": "dint"},
            ],
        }
    )
    instrument.open()

    instrument._background_daemon()

    assert state.reads == []
    assert state.batch_reads == [["Speed", "Pressure", "Temperature"]]


def test_timing_with_empty_tags_warns_but_configures_device() -> None:
    with pytest.warns(RuntimeWarning, match="No EtherNet/IP tags are configured"):
        instrument = EtherNetIPDevice(
            {
                "device": {"name": "test_plc"},
                "connection": {"host": "192.0.2.10"},
                "timing": {"poll_interval": 1.0},
                "tags": [],
            }
        )

    assert instrument._background_methods == []


def test_autostart_rejects_empty_tags() -> None:
    with pytest.raises(ValueError, match="No EtherNet/IP tags are configured"):
        EtherNetIPDevice(
            {
                "device": {"name": "test_plc"},
                "connection": {"host": "192.0.2.10"},
                "timing": {"poll_interval": 1.0},
                "tags": [],
            },
            autostart=True,
        )


def test_autostart_rejects_all_poll_disabled_tags() -> None:
    with pytest.raises(ValueError, match="all 2 configured tags have poll=false"):
        EtherNetIPDevice(
            {
                "device": {"name": "test_plc"},
                "connection": {"host": "192.0.2.10"},
                "timing": {"poll_interval": 1.0},
                "tags": [
                    {"alias": "setpoint", "tag_name": "Setpoint", "data_type": "dint", "poll": False},
                    {"alias": "enabled", "tag_name": "Enabled", "data_type": "bool", "poll": False},
                ],
            },
            autostart=True,
        )


def test_manual_start_rejects_all_poll_disabled_tags() -> None:
    with pytest.warns(RuntimeWarning, match="all 1 configured tags have poll=false"):
        instrument = EtherNetIPDevice(
            {
                "device": {"name": "test_plc"},
                "connection": {"host": "192.0.2.10"},
                "timing": {"poll_interval": 1.0},
                "tags": [{"alias": "setpoint", "tag_name": "Setpoint", "data_type": "dint", "poll": False}],
            }
        )

    with pytest.raises(RuntimeError, match="all 1 configured tags have poll=false"):
        instrument.start()

    assert instrument._background_thread is None


def test_string_data_type_is_not_part_of_python_config_surface() -> None:
    with pytest.raises(ValidationError) as exc_info:
        EtherNetIPConfig.model_validate(
            {
                "device": {"name": "test_plc"},
                "tags": [{"alias": "recipe_name", "tag_name": "RecipeName", "data_type": "string"}],
            }
        )

    assert "data_type" in str(exc_info.value)
    assert "string" in str(exc_info.value)


def test_write_tag_rejects_string_value(monkeypatch: pytest.MonkeyPatch) -> None:
    state = install_fake_native_ethernetip(monkeypatch)
    instrument = EtherNetIPDevice(
        {
            "device": {"name": "test_plc"},
            "connection": {"host": "192.0.2.10"},
            "tags": [{"alias": "speed", "tag_name": "Speed", "data_type": "dint"}],
        }
    )
    instrument.open()

    with pytest.raises(TypeError, match="got str"):
        instrument.write_tag("speed", "startup")  # type: ignore[arg-type]

    assert state.writes == []


def test_close_tolerates_broken_native_session(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    state = install_fake_native_ethernetip(monkeypatch, close_error=RuntimeError("broken pipe"))
    instrument = EtherNetIPDevice(
        {
            "device": {"name": "test_plc"},
            "connection": {"host": "192.0.2.10"},
            "tags": [{"alias": "speed", "tag_name": "Speed", "data_type": "dint"}],
        }
    )
    instrument.open()

    with caplog.at_level("WARNING", logger="instro.unstable.ethernetip.ethernetip"):
        instrument.close()

    assert state.closes == 1
    assert instrument._client is None
    assert "Failed to close EtherNet/IP session cleanly" in caplog.text
