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
        "name": "test_psu",
        "vendor": "simulated",
        "connection": "TCPIP0::127.0.0.1::5025::SOCKET",
        "num_channels": 1,
    }


def test_from_dict_returns_instropsu(valid_config):
    with patch("instro.lib.transports.visa.VisaDriver"):
        psu = InstroPSU.from_dict(valid_config)

    assert isinstance(psu, InstroPSU)
    assert psu.name == "test_psu"


def test_from_dict_missing_required_field():
    with pytest.raises(Exception):
        InstroPSU.from_dict({"vendor": "simulated", "connection": "TCPIP0::127.0.0.1::5025::SOCKET"})


def test_from_dict_unknown_vendor():
    with pytest.raises(Exception):
        InstroPSU.from_dict(
            {
                "name": "test_psu",
                "vendor": "not_a_real_vendor",
                "connection": "TCPIP0::127.0.0.1::5025::SOCKET",
                "num_channels": 1,
            }
        )


def test_from_dict_invalid_num_channels():
    with pytest.raises(Exception):
        InstroPSU.from_dict(
            {
                "name": "test_psu",
                "vendor": "simulated",
                "connection": "TCPIP0::127.0.0.1::5025::SOCKET",
                "num_channels": 0,
            }
        )


def test_from_json_happy_path(valid_config, tmp_path):
    config_file = tmp_path / "psu.json"
    config_file.write_text(json.dumps(valid_config))

    with patch("instro.lib.transports.visa.VisaDriver"):
        psu = InstroPSU.from_json(config_file)

    assert isinstance(psu, InstroPSU)
    assert psu.name == "test_psu"


def test_from_json_malformed_json(tmp_path):
    config_file = tmp_path / "psu.json"
    config_file.write_text("this is not json {{{")

    with pytest.raises(Exception):
        InstroPSU.from_json(config_file)
