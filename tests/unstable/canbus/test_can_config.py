"""Tests for CANConfig: DBC loading, path resolution, and message-name validation (issue #641)."""

import json
import shutil
from pathlib import Path

import pytest

from instro.unstable.canbus import CANConfig

DBC = Path(__file__).parent / "vehicle.dbc"


def _config(**overrides: object) -> CANConfig:
    return CANConfig.model_validate({"device": {"name": "vehicle_bus"}, "dbc": DBC, **overrides})


def test_from_json_resolves_dbc_next_to_the_json_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shutil.copy(DBC, tmp_path / "vehicle.dbc")
    config_path = tmp_path / "bench.json"
    config_path.write_text(json.dumps({"device": {"name": "vehicle_bus"}, "dbc": "vehicle.dbc"}))
    monkeypatch.chdir(Path(__file__).parent.parent)  # somewhere with no vehicle.dbc

    config = CANConfig.from_json(config_path)

    assert config.dbc == tmp_path / "vehicle.dbc"
    assert config.get_message("BMS_Status").frame_id == 0x123


def test_rx_defaults_to_every_dbc_message_and_tx_to_none() -> None:
    config = _config()

    assert [m.name for m in config.rx_dbc_messages] == [
        "BMS_Status",
        "Motor_Feedback",
        "Motor_Command",
        "Diagnostics",
    ]
    assert config.tx_dbc_messages == []


def test_unknown_message_name_fails_at_load_with_the_closest_match() -> None:
    with pytest.raises(KeyError, match="Did you mean 'BMS_Status'"):
        _config(rx_messages=["BMS_Stats"])


def test_multiplexed_tx_message_is_rejected() -> None:
    with pytest.raises(ValueError, match="multiplexed"):
        _config(tx_messages=["Diagnostics"])
