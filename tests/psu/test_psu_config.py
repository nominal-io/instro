"""Tests for PSU JSON config-driven construction."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from instro.psu import InstroPSU, PSUConfig


@pytest.fixture
def valid_config() -> dict:
    return {
        "device": {"name": "test_psu"},
        "driver": {
            "name": "SimulatedPSU",
            "num_channels": 1,
            "connection_type": "visa",
            "visa": {"visa_resource": "TCPIP0::127.0.0.1::5025::SOCKET"},
        },
    }


def test_from_dict_returns_instropsu(valid_config):
    with patch("instro.psu.drivers.simulated.VisaDriver"):
        psu = InstroPSU.from_dict(valid_config)

    assert isinstance(psu, InstroPSU)
    assert psu.name == "test_psu"


def test_from_dict_with_timing_sets_background_interval(valid_config):
    config_with_timing = {**valid_config, "timing": {"poll_interval": 0.5}}
    with patch("instro.psu.drivers.simulated.VisaDriver"):
        psu = InstroPSU.from_dict(config_with_timing)

    assert psu.background_interval == 0.5


def test_from_dict_with_timing_rejects_unknown_field(valid_config):
    config_with_bad_timing = {**valid_config, "timing": {"poll_interval": 0.5, "pol_interval": 0.5}}
    with pytest.raises(Exception):
        InstroPSU.from_dict(config_with_bad_timing)


def test_from_dict_missing_required_field():
    with pytest.raises(Exception):
        InstroPSU.from_dict({"driver": {"name": "SimulatedPSU", "num_channels": 1, "connection_type": "visa"}})


def test_from_dict_unknown_driver_name():
    with pytest.raises(Exception):
        InstroPSU.from_dict(
            {
                "device": {"name": "test_psu"},
                "driver": {
                    "name": "not_a_real_driver",
                    "num_channels": 1,
                    "connection_type": "visa",
                    "visa": {"visa_resource": "TCPIP0::127.0.0.1::5025::SOCKET"},
                },
            }
        )


def test_from_dict_invalid_num_channels():
    with pytest.raises(Exception):
        InstroPSU.from_dict(
            {
                "device": {"name": "test_psu"},
                "driver": {
                    "name": "SimulatedPSU",
                    "num_channels": 0,
                    "connection_type": "visa",
                    "visa": {"visa_resource": "TCPIP0::127.0.0.1::5025::SOCKET"},
                },
            }
        )


def test_from_json_happy_path(valid_config, tmp_path):
    config_file = tmp_path / "psu.json"
    config_file.write_text(json.dumps(valid_config))

    with patch("instro.psu.drivers.simulated.VisaDriver"):
        psu = InstroPSU.from_json(config_file)

    assert isinstance(psu, InstroPSU)
    assert psu.name == "test_psu"


def test_from_json_str_happy_path(valid_config):
    with patch("instro.psu.drivers.simulated.VisaDriver"):
        psu = InstroPSU.from_json_str(json.dumps(valid_config))

    assert isinstance(psu, InstroPSU)
    assert psu.name == "test_psu"


def test_from_json_malformed_json(tmp_path):
    config_file = tmp_path / "psu.json"
    config_file.write_text("this is not json {{{")

    with pytest.raises(Exception):
        InstroPSU.from_json(config_file)


def test_from_dict_with_publishers(valid_config):
    config_with_publishers = {
        **valid_config,
        "publishers": [
            {"type": "NominalCorePublisher", "dataset_rid": "test_psu"},
            {"type": "FilePublisher", "directory": "test_psu_out", "format": "csv"},
        ],
    }
    with (
        patch("instro.psu.drivers.simulated.VisaDriver"),
        patch("instro.lib.publishers.NominalCorePublisher") as mock_ncp,
        patch("instro.lib.publishers.FilePublisher") as mock_fp,
    ):
        psu = InstroPSU.from_dict(config_with_publishers)

    mock_ncp.assert_called_once_with(dataset_rid="test_psu", batch_size=None, profile=None)
    mock_fp.assert_called_once_with(directory="test_psu_out", format="csv", custom_file_name=None)
    assert psu.publishers == [mock_ncp.return_value, mock_fp.return_value]


def test_file_publisher_config_accepts_jsonl_format():
    from instro.psu.config import FilePublisherConfig

    config = FilePublisherConfig(directory="out", format="jsonl")
    assert config.format == "jsonl"


def test_from_dict_unknown_publisher_type(valid_config):
    with pytest.raises(Exception):
        InstroPSU.from_dict({**valid_config, "publishers": [{"type": "NotARealPublisher"}]})


def test_vendor_registry_complete():
    import importlib

    from instro.psu.config import PSU_VENDOR_REGISTRY
    from instro.psu.psu import PSUDriverBase

    for key, path in PSU_VENDOR_REGISTRY.items():
        mod_path, cls_name = path.rsplit(".", 1)
        cls = getattr(importlib.import_module(mod_path), cls_name)
        assert issubclass(cls, PSUDriverBase), f"{key} does not point to a PSUDriverBase subclass"


def test_vendor_registry_matches_drivers_package():
    from instro.psu import drivers
    from instro.psu.config import PSU_VENDOR_REGISTRY
    from instro.psu.psu import PSUDriverBase

    exported_drivers = {
        name
        for name in drivers.__all__
        if getattr(drivers, name) is not PSUDriverBase and issubclass(getattr(drivers, name), PSUDriverBase)
    }
    assert set(PSU_VENDOR_REGISTRY) == exported_drivers, (
        "PSU_VENDOR_REGISTRY and instro.psu.drivers.__all__ have drifted apart; a new driver must be added to both."
    )
