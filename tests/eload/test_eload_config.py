"""Tests for E-Load JSON config-driven construction."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from instro.eload import ELoadConfig, ELoadDriverBase, InstroELoad, LoadMode, SlewRateDirection


@pytest.fixture
def valid_config() -> dict:
    return {
        "device": {"name": "test_eload"},
        "driver": {
            "name": "BK85XXB",
            "connection_type": "visa",
            "visa": {"visa_resource": "ASRL3::INSTR"},
        },
    }


def _make_eload_with_mock_driver(config: dict) -> tuple[InstroELoad, MagicMock]:
    with patch("instro.eload.drivers.bk_85xxb.VisaDriver"):
        eload = InstroELoad(config=config)
    mock_driver = MagicMock(spec=ELoadDriverBase)
    eload._driver = mock_driver
    return eload, mock_driver


def test_init_with_config_dict(valid_config):
    with patch("instro.eload.drivers.bk_85xxb.VisaDriver"):
        eload = InstroELoad(config=valid_config)

    assert isinstance(eload, InstroELoad)
    assert eload.name == "test_eload"
    assert eload._config is not None
    assert eload._config.driver.name == "BK85XXB"


def test_init_with_config_dict_and_timing_sets_background_interval(valid_config):
    config_with_timing = {**valid_config, "timing": {"poll_interval": 0.5}}
    with patch("instro.eload.drivers.bk_85xxb.VisaDriver"):
        eload = InstroELoad(config=config_with_timing)

    assert eload.background_interval == 0.5


def test_init_with_config_dict_timing_is_valid_without_load_block(valid_config):
    # Decision 3 in INSTRO-566: polled measurements work regardless of mode,
    # so timing standalone must stay valid for passive monitoring.
    with patch("instro.eload.drivers.bk_85xxb.VisaDriver"):
        eload = InstroELoad(config={**valid_config, "timing": {"poll_interval": 0.5}})

    assert eload._config.load is None


def test_init_with_config_dict_curr_limit_outside_cv_raises(valid_config):
    with pytest.raises(Exception, match="only meaningful in CV mode"):
        InstroELoad(config={**valid_config, "load": {"mode": "CC", "level": 1.0, "curr_limit": 2.0}})


def test_init_with_config_dict_curr_limit_without_level_raises(valid_config):
    with pytest.raises(Exception, match="curr_limit requires a level"):
        InstroELoad(config={**valid_config, "load": {"mode": "CV", "curr_limit": 2.0}})


def test_init_with_config_dict_load_block_without_mode_raises(valid_config):
    with pytest.raises(Exception):
        InstroELoad(config={**valid_config, "load": {"level": 1.0}})


def test_open_applies_load_config_in_order(valid_config):
    eload, mock_driver = _make_eload_with_mock_driver(
        {
            **valid_config,
            "load": {"mode": "CC", "level": 1.5, "range": 5.0, "slew_rate": {"direction": "BOTH", "rate": 0.5}},
        }
    )

    eload.open()

    method_order = [name for name, _, _ in mock_driver.mock_calls]
    assert method_order == ["open", "set_mode", "set_level", "set_range", "set_slewrate"]
    mock_driver.set_mode.assert_called_once_with(mode=LoadMode.CC, channel=1)
    mock_driver.set_level.assert_called_once_with(mode=LoadMode.CC, value=1.5, channel=1, curr_limit=None)
    mock_driver.set_range.assert_called_once_with(mode=LoadMode.CC, value=5.0, channel=1)
    mock_driver.set_slewrate.assert_called_once_with(direction=SlewRateDirection.BOTH, rate=0.5, channel=1)


def test_open_applies_cv_level_with_curr_limit(valid_config):
    eload, mock_driver = _make_eload_with_mock_driver(
        {**valid_config, "load": {"mode": "CV", "level": 12.0, "curr_limit": 2.0}}
    )

    eload.open()

    mock_driver.set_level.assert_called_once_with(mode=LoadMode.CV, value=12.0, channel=1, curr_limit=2.0)


def test_open_with_mode_only_load_block_applies_only_mode(valid_config):
    eload, mock_driver = _make_eload_with_mock_driver({**valid_config, "load": {"mode": "CR"}})

    eload.open()

    method_order = [name for name, _, _ in mock_driver.mock_calls]
    assert method_order == ["open", "set_mode"]


def test_open_without_load_block_touches_nothing(valid_config):
    eload, mock_driver = _make_eload_with_mock_driver(valid_config)

    eload.open()

    method_order = [name for name, _, _ in mock_driver.mock_calls]
    assert method_order == ["open"]


def test_open_never_enables_input_or_short_from_config(valid_config):
    # Decision 2 in INSTRO-566: loading a config must never start sinking
    # current or short the input.
    eload, mock_driver = _make_eload_with_mock_driver(
        {
            **valid_config,
            "load": {"mode": "CC", "level": 1.5, "range": 5.0, "slew_rate": {"direction": "BOTH", "rate": 0.5}},
        }
    )

    eload.open()

    mock_driver.output_enable.assert_not_called()
    mock_driver.short_output.assert_not_called()


def test_open_closes_driver_and_rolls_back_state_when_load_apply_fails(valid_config):
    eload, mock_driver = _make_eload_with_mock_driver({**valid_config, "load": {"mode": "CP", "range": 5.0}})
    mock_driver.set_range.side_effect = NotImplementedError("only exposes :RANGe for CC and CV")

    with pytest.raises(NotImplementedError):
        eload.open()

    mock_driver.close.assert_called_once()
    # The partial apply must not leave a stale mode that lets set_level run against a closed driver.
    with pytest.raises(ValueError, match="Mode must be set"):
        eload.set_level(1.0)


def test_reopen_without_close_does_not_reapply_load_config(valid_config):
    eload, mock_driver = _make_eload_with_mock_driver({**valid_config, "load": {"mode": "CC"}})

    eload.open()
    eload.open()
    assert mock_driver.set_mode.call_count == 1

    eload.close()
    eload.open()
    assert mock_driver.set_mode.call_count == 2


def test_init_with_config_dict_missing_required_field():
    with pytest.raises(Exception):
        InstroELoad(config={"driver": {"name": "BK85XXB", "connection_type": "visa"}})


def test_init_with_config_dict_unknown_version_rejected(valid_config):
    with pytest.raises(Exception):
        InstroELoad(config={**valid_config, "version": 2})


def test_init_with_config_dict_unknown_driver_name(valid_config):
    with pytest.raises(Exception):
        InstroELoad(config={**valid_config, "driver": {**valid_config["driver"], "name": "not_a_real_driver"}})


def test_init_with_config_file_path_malformed_json(tmp_path):
    config_file = tmp_path / "eload.json"
    config_file.write_text("this is not json {{{")

    with pytest.raises(Exception):
        InstroELoad(config=config_file)


def test_init_with_config_file_path_object(valid_config, tmp_path):
    config_file = tmp_path / "eload.json"
    config_file.write_text(json.dumps(valid_config))

    with patch("instro.eload.drivers.bk_85xxb.VisaDriver"):
        eload = InstroELoad(config=config_file)

    assert eload.name == "test_eload"


def test_init_with_config_eloadconfig_object_does_not_alias_caller_instance(valid_config):
    eload_config = ELoadConfig.model_validate(valid_config)
    with patch("instro.eload.drivers.bk_85xxb.VisaDriver"):
        eload = InstroELoad(config=eload_config)

    eload_config.device.name = "mutated"

    assert eload._config is not eload_config
    assert eload._config.device.name == "test_eload"


def test_init_with_config_explicit_empty_name_is_not_overwritten(valid_config):
    # name="" is falsy but explicitly chosen; it must win over config.device.name, not
    # get silently replaced by it (same regression class as the PSU truthy-`or` bug).
    with patch("instro.eload.drivers.bk_85xxb.VisaDriver"):
        eload = InstroELoad(config=valid_config, name="")

    assert eload.name == ""


def test_init_with_config_and_driver_raises(valid_config):
    with pytest.raises(ValueError, match="cannot be combined"):
        InstroELoad(config=valid_config, driver=MagicMock())


def test_init_with_no_config_and_missing_direct_args_raises():
    with pytest.raises(ValueError, match="requires either config"):
        InstroELoad(name="only_name")


def test_init_with_autostart_opens_and_starts(valid_config):
    config_with_timing = {**valid_config, "timing": {"poll_interval": 0.5}}
    with (
        patch("instro.eload.drivers.bk_85xxb.VisaDriver"),
        patch.object(InstroELoad, "open") as mock_open,
        patch.object(InstroELoad, "start") as mock_start,
    ):
        InstroELoad(config=config_with_timing, autostart=True)

    mock_open.assert_called_once()
    mock_start.assert_called_once()


def test_init_without_autostart_does_not_open_or_start(valid_config):
    with (
        patch("instro.eload.drivers.bk_85xxb.VisaDriver"),
        patch.object(InstroELoad, "open") as mock_open,
        patch.object(InstroELoad, "start") as mock_start,
    ):
        InstroELoad(config=valid_config)

    mock_open.assert_not_called()
    mock_start.assert_not_called()


def test_init_with_autostart_open_failure_closes_config_publishers(valid_config):
    config = {
        **valid_config,
        "publishers": [{"type": "NominalCorePublisher", "dataset_rid": "test_eload"}],
    }
    with (
        patch("instro.eload.drivers.bk_85xxb.VisaDriver") as mock_visa_cls,
        patch("instro.lib.publishers.NominalCorePublisher") as mock_ncp,
    ):
        mock_visa_cls.return_value.open.side_effect = OSError("unreachable")
        with pytest.raises(OSError):
            InstroELoad(config=config, autostart=True)

    mock_ncp.return_value.close.assert_called_once()


def test_init_with_config_dict_with_publishers(valid_config):
    config_with_publishers = {
        **valid_config,
        "publishers": [
            {"type": "NominalCorePublisher", "dataset_rid": "test_eload"},
            {"type": "FilePublisher", "directory": "test_eload_out", "format": "csv"},
        ],
    }
    with (
        patch("instro.eload.drivers.bk_85xxb.VisaDriver"),
        patch("instro.lib.publishers.NominalCorePublisher") as mock_ncp,
        patch("instro.lib.publishers.FilePublisher") as mock_fp,
    ):
        eload = InstroELoad(config=config_with_publishers)

    mock_ncp.assert_called_once_with(dataset_rid="test_eload", batch_size=None, profile=None)
    mock_fp.assert_called_once_with(directory="test_eload_out", format="csv", custom_file_name=None)
    assert eload.publishers == [mock_ncp.return_value, mock_fp.return_value]


def test_vendor_registry_complete():
    import importlib

    from instro.eload.config import ELOAD_VENDOR_REGISTRY
    from instro.eload.eload import ELoadDriverBase

    for key, path in ELOAD_VENDOR_REGISTRY.items():
        mod_path, cls_name = path.rsplit(".", 1)
        cls = getattr(importlib.import_module(mod_path), cls_name)
        assert issubclass(cls, ELoadDriverBase), f"{key} does not point to an ELoadDriverBase subclass"


def test_vendor_registry_matches_drivers_package():
    from instro.eload import drivers
    from instro.eload.config import ELOAD_VENDOR_REGISTRY
    from instro.eload.eload import ELoadDriverBase

    exported_drivers = {
        name
        for name in drivers.__all__
        if getattr(drivers, name) is not ELoadDriverBase and issubclass(getattr(drivers, name), ELoadDriverBase)
    }
    assert set(ELOAD_VENDOR_REGISTRY) == exported_drivers, (
        "ELOAD_VENDOR_REGISTRY and instro.eload.drivers.__all__ have drifted apart; a new driver must be added to both."
    )
