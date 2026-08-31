"""Tests for AWG JSON config-driven construction."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from instro.unstable.awg import AWGConfig, InstroAWG
from instro.unstable.awg.types import Arbitrary, Sine


@pytest.fixture
def valid_config() -> dict:
    return {
        "device": {"name": "test_awg"},
        "driver": {
            "name": "RigolDG1022Z",
            "num_channels": 2,
            "connection_type": "visa",
            "visa": {"visa_resource": "TCPIP0::127.0.0.1::5025::SOCKET"},
        },
        "channels": {
            "1": {"waveform": {"shape": "sine", "frequency_hz": 1000.0, "phase_deg": 0.0}},
        },
    }


def _patch_driver():
    return patch("instro.unstable.awg.drivers.rigol_dg1022z.RigolDG1022Z")


def test_init_with_config_dict(valid_config):
    with _patch_driver():
        awg = InstroAWG(config=valid_config)

    assert isinstance(awg, InstroAWG)
    assert awg.name == "test_awg"
    assert awg._config is not None
    assert awg._config.driver.name == "RigolDG1022Z"


def test_init_with_config_dict_and_timing_sets_background_interval(valid_config):
    config_with_timing = {**valid_config, "timing": {"poll_interval": 0.5}}
    with _patch_driver():
        awg = InstroAWG(config=config_with_timing)

    assert awg.background_interval == 0.5


def test_init_with_config_dict_missing_channels():
    with pytest.raises(Exception):
        InstroAWG(
            config={
                "device": {"name": "test_awg"},
                "driver": {
                    "name": "RigolDG1022Z",
                    "num_channels": 2,
                    "connection_type": "visa",
                    "visa": {"visa_resource": "TCPIP0::127.0.0.1::5025::SOCKET"},
                },
            }
        )


def test_init_with_config_dict_unknown_driver_name(valid_config):
    with pytest.raises(Exception):
        InstroAWG(config={**valid_config, "driver": {**valid_config["driver"], "name": "not_a_real_driver"}})


def test_init_with_config_dict_invalid_num_channels(valid_config):
    with pytest.raises(Exception):
        InstroAWG(config={**valid_config, "driver": {**valid_config["driver"], "num_channels": 0}})


def test_init_with_config_file_path_malformed_json(tmp_path):
    config_file = tmp_path / "awg.json"
    config_file.write_text("this is not json {{{")

    with pytest.raises(Exception):
        InstroAWG(config=config_file)


def test_init_with_config_awgconfig_object(valid_config):
    awg_config = AWGConfig.model_validate(valid_config)
    with _patch_driver():
        awg = InstroAWG(config=awg_config)

    assert awg.name == "test_awg"


def test_init_with_config_json_string_raises_not_a_path(valid_config):
    # A raw JSON string is always treated as a file path (matches InstroPSU/InstroDMM),
    # not auto-detected as JSON text.
    with pytest.raises(OSError):
        InstroAWG(config=json.dumps(valid_config))


def test_init_with_config_file_path_str(valid_config, tmp_path):
    config_file = tmp_path / "awg.json"
    config_file.write_text(json.dumps(valid_config))

    with _patch_driver():
        awg = InstroAWG(config=str(config_file))

    assert awg.name == "test_awg"


def test_init_with_config_name_override(valid_config):
    with _patch_driver():
        awg = InstroAWG(config=valid_config, name="overridden")

    assert awg.name == "overridden"


def test_init_with_config_explicit_empty_name_is_not_overwritten(valid_config):
    with _patch_driver():
        awg = InstroAWG(config=valid_config, name="")

    assert awg.name == ""


def test_init_with_config_awgconfig_object_does_not_alias_caller_instance(valid_config):
    awg_config = AWGConfig.model_validate(valid_config)
    with _patch_driver():
        awg = InstroAWG(config=awg_config)

    awg_config.device.name = "mutated"

    assert awg._config is not awg_config
    assert awg._config.device.name == "test_awg"


def test_init_with_config_and_driver_raises(valid_config):
    with pytest.raises(ValueError, match="cannot be combined"):
        InstroAWG(config=valid_config, driver=MagicMock(), num_channels=1)


def test_init_with_no_config_and_missing_direct_args_raises():
    with pytest.raises(ValueError, match="requires either config"):
        InstroAWG(name="only_name")


def test_init_with_autostart_requires_config():
    with pytest.raises(ValueError, match="autostart=True requires config"):
        InstroAWG(name="direct", driver=MagicMock(), num_channels=1, autostart=True)


def test_init_with_autostart_opens_and_starts(valid_config):
    with (
        _patch_driver(),
        patch.object(InstroAWG, "open") as mock_open,
        patch.object(InstroAWG, "start") as mock_start,
    ):
        InstroAWG(config=valid_config, autostart=True)

    mock_open.assert_called_once()
    mock_start.assert_called_once()


def test_init_with_autostart_open_failure_closes_config_publishers(valid_config):
    config = {
        **valid_config,
        "publishers": [{"type": "NominalCorePublisher", "dataset_rid": "test_awg"}],
    }
    with (
        patch("instro.unstable.awg.drivers.rigol_dg1022z.RigolDG1022Z") as mock_cls,
        patch("instro.lib.publishers.NominalCorePublisher") as mock_ncp,
    ):
        mock_cls.return_value.open.side_effect = OSError("unreachable")
        with pytest.raises(OSError):
            InstroAWG(config=config, autostart=True)

    mock_ncp.return_value.close.assert_called_once()


def test_init_without_autostart_does_not_open_or_start(valid_config):
    with (
        _patch_driver(),
        patch.object(InstroAWG, "open") as mock_open,
        patch.object(InstroAWG, "start") as mock_start,
    ):
        InstroAWG(config=valid_config)

    mock_open.assert_not_called()
    mock_start.assert_not_called()


def test_init_with_config_dict_with_publishers(valid_config):
    config_with_publishers = {
        **valid_config,
        "publishers": [
            {"type": "NominalCorePublisher", "dataset_rid": "test_awg"},
            {"type": "FilePublisher", "directory": "test_awg_out", "format": "csv"},
        ],
    }
    with (
        _patch_driver(),
        patch("instro.lib.publishers.NominalCorePublisher") as mock_ncp,
        patch("instro.lib.publishers.FilePublisher") as mock_fp,
    ):
        awg = InstroAWG(config=config_with_publishers)

    mock_ncp.assert_called_once_with(dataset_rid="test_awg", batch_size=None, profile=None)
    mock_fp.assert_called_once_with(directory="test_awg_out", format="csv", custom_file_name=None)
    assert awg.publishers == [mock_ncp.return_value, mock_fp.return_value]


def test_init_with_config_dict_unknown_publisher_type(valid_config):
    with pytest.raises(Exception):
        InstroAWG(config={**valid_config, "publishers": [{"type": "NotARealPublisher"}]})


def test_vendor_registry_complete():
    import importlib

    from instro.unstable.awg import AWGDriverBase
    from instro.unstable.awg.config import AWG_VENDOR_REGISTRY

    for key, path in AWG_VENDOR_REGISTRY.items():
        mod_path, cls_name = path.rsplit(".", 1)
        cls = getattr(importlib.import_module(mod_path), cls_name)
        assert issubclass(cls, AWGDriverBase), f"{key} does not point to an AWGDriverBase subclass"


def test_vendor_registry_matches_drivers_package():
    from instro.unstable.awg import AWGDriverBase, drivers
    from instro.unstable.awg.config import AWG_VENDOR_REGISTRY

    exported_drivers = {
        name
        for name in drivers.__all__
        if getattr(drivers, name) is not AWGDriverBase and issubclass(getattr(drivers, name), AWGDriverBase)
    }
    assert set(AWG_VENDOR_REGISTRY) == exported_drivers, (
        "AWG_VENDOR_REGISTRY and instro.unstable.awg.drivers.__all__ have drifted apart; "
        "a new driver must be added to both."
    )


def test_channels_required_and_non_empty(valid_config):
    with pytest.raises(Exception):
        InstroAWG(config={**valid_config, "channels": {}})


def test_channels_rejects_duplicate_channel_numbers(valid_config):
    channel_config = valid_config["channels"]["1"]
    with pytest.raises(Exception, match="duplicate"):
        InstroAWG(config={**valid_config, "channels": {"1": channel_config, "01": channel_config}})


def test_channels_rejects_channel_number_outside_num_channels(valid_config):
    channel_config = valid_config["channels"]["1"]
    for key in ("3", "0", "-1"):
        with pytest.raises(Exception, match="out of range"):
            InstroAWG(config={**valid_config, "channels": {key: channel_config}})


def test_channel_config_rejects_output_enable_field(valid_config):
    channel_config = {**valid_config["channels"]["1"], "output_enable": True}
    with pytest.raises(Exception):
        InstroAWG(config={**valid_config, "channels": {"1": channel_config}})


def test_open_applies_waveform_amplitude_and_offset(valid_config):
    config = {
        **valid_config,
        "channels": {
            "1": {
                "waveform": {"shape": "sine", "frequency_hz": 1000.0, "phase_deg": 0.0},
                "amplitude": {"value": 2.0, "unit": "VPP"},
                "offset": 0.1,
            }
        },
    }
    with _patch_driver() as mock_cls:
        awg = InstroAWG(config=config)
        awg.open()

    mock_driver = mock_cls.return_value
    mock_driver.set_waveform.assert_called_once_with(channel=1, waveform=Sine(frequency_hz=1000.0, phase_deg=0.0))
    mock_driver.set_amplitude.assert_called_once()
    mock_driver.set_offset.assert_called_once_with(1, 0.1)


def test_arbitrary_waveform_samples_inline(valid_config):
    config = {
        **valid_config,
        "channels": {
            "1": {"waveform": {"shape": "arbitrary", "samples": [-1.0, 0.0, 1.0, 0.0], "sample_rate_sas": 50.0}}
        },
    }
    with _patch_driver() as mock_cls:
        awg = InstroAWG(config=config)
        awg.open()

    mock_driver = mock_cls.return_value
    mock_driver.set_waveform.assert_called_once_with(
        channel=1, waveform=Arbitrary(samples=(-1.0, 0.0, 1.0, 0.0), sample_rate_hz=50.0)
    )


def test_arbitrary_waveform_samples_from_csv_file(valid_config, tmp_path):
    samples_file = tmp_path / "samples.csv"
    samples_file.write_text("-1.0\n0.0\n1.0\n0.0\n")
    config = {
        **valid_config,
        "channels": {
            "1": {
                "waveform": {"shape": "arbitrary", "samples": str(samples_file), "sample_rate_sas": 50.0},
            }
        },
    }
    with _patch_driver() as mock_cls:
        awg = InstroAWG(config=config)
        awg.open()

    mock_driver = mock_cls.return_value
    mock_driver.set_waveform.assert_called_once_with(
        channel=1, waveform=Arbitrary(samples=(-1.0, 0.0, 1.0, 0.0), sample_rate_hz=50.0)
    )


def test_arbitrary_samples_relative_path_resolves_against_config_file(valid_config, tmp_path, monkeypatch):
    (tmp_path / "samples.csv").write_text("-1.0\n0.0\n1.0\n0.0\n")
    config_path = tmp_path / "awg.json"
    config_path.write_text(
        json.dumps(
            {
                **valid_config,
                "channels": {
                    "1": {"waveform": {"shape": "arbitrary", "samples": "samples.csv", "sample_rate_sas": 50.0}}
                },
            }
        )
    )
    monkeypatch.chdir(tmp_path.parent)

    with _patch_driver() as mock_cls:
        awg = InstroAWG(config=str(config_path))
        awg.open()

    mock_cls.return_value.set_waveform.assert_called_once_with(
        channel=1, waveform=Arbitrary(samples=(-1.0, 0.0, 1.0, 0.0), sample_rate_hz=50.0)
    )


def test_arbitrary_samples_missing_file_rejected_at_validation(valid_config):
    config = {
        **valid_config,
        "channels": {"1": {"waveform": {"shape": "arbitrary", "samples": "no_such.csv", "sample_rate_sas": 50.0}}},
    }
    with pytest.raises(Exception, match="samples file not found"):
        InstroAWG(config=config)


def test_arbitrary_samples_path_round_trips_verbatim(valid_config, tmp_path, monkeypatch):
    (tmp_path / "samples.csv").write_text("-1.0\n0.0\n1.0\n0.0\n")
    config_path = tmp_path / "awg.json"
    config_path.write_text(
        json.dumps(
            {
                **valid_config,
                "channels": {
                    "1": {"waveform": {"shape": "arbitrary", "samples": "samples.csv", "sample_rate_sas": 50.0}}
                },
            }
        )
    )
    monkeypatch.chdir(tmp_path.parent)

    with _patch_driver():
        awg = InstroAWG(config=str(config_path))

    dumped = awg._config.model_dump(mode="json")
    assert dumped["channels"]["1"]["waveform"]["samples"] == "samples.csv"


def test_malformed_waveform_rejected_at_validation(valid_config):
    """types.py's shape bounds must apply when the config is parsed, not partway through open()."""
    bad_waveforms = [
        ({"shape": "sine", "frequency_hz": -5.0, "phase_deg": 0.0}, "frequency_hz must be positive"),
        (
            {"shape": "square", "frequency_hz": 1.0, "duty_cycle_pct": 500.0, "phase_deg": 0.0},
            "duty_cycle_pct must be between 0 and 100",
        ),
        ({"shape": "pulse", "frequency_hz": 1000.0, "width_s": 1.0, "delay_s": 0.0}, "must fit within the period"),
        ({"shape": "arbitrary", "samples": [0.1, 0.2], "sample_rate_sas": 0.0}, "sample_rate_hz must be positive"),
        ({"shape": "arbitrary", "samples": [0.5], "sample_rate_sas": 50.0}, "at least 2 samples"),
        ({"shape": "arbitrary", "samples": [5.0, -9.0], "sample_rate_sas": 50.0}, "normalized to"),
    ]
    for waveform, expected in bad_waveforms:
        with pytest.raises(Exception, match=expected):
            InstroAWG(config={**valid_config, "channels": {"1": {"waveform": waveform}}})


def test_malformed_csv_samples_error_names_the_file(valid_config, tmp_path):
    samples_file = tmp_path / "samples.csv"
    samples_file.write_text("0.1,0.2\n0.3,oops\n")
    config = {
        **valid_config,
        "channels": {"1": {"waveform": {"shape": "arbitrary", "samples": str(samples_file), "sample_rate_sas": 50.0}}},
    }
    with pytest.raises(Exception, match=r"samples\.csv: could not convert string to float"):
        InstroAWG(config=config)
