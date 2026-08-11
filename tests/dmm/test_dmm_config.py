"""Tests for DMM JSON config-driven construction."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from instro.dmm import DMMConfig, DMMDriverBase, InstroDMM, MeasurementFunction


@pytest.fixture
def valid_config() -> dict:
    return {
        "device": {"name": "test_dmm"},
        "driver": {
            "name": "SimulatedDMM",
            "connection_type": "visa",
            "visa": {"visa_resource": "TCPIP0::127.0.0.1::5025::SOCKET"},
        },
    }


def _make_dmm_with_mock_driver(config: dict) -> tuple[InstroDMM, MagicMock]:
    with patch("instro.dmm.drivers.simulated.VisaDriver"):
        dmm = InstroDMM(config=config)
    mock_driver = MagicMock(spec=DMMDriverBase)
    dmm._driver = mock_driver
    return dmm, mock_driver


def test_init_with_config_dict(valid_config):
    with patch("instro.dmm.drivers.simulated.VisaDriver"):
        dmm = InstroDMM(config=valid_config)

    assert isinstance(dmm, InstroDMM)
    assert dmm.name == "test_dmm"
    assert dmm._config is not None
    assert dmm._config.driver.name == "SimulatedDMM"


def test_init_with_config_dict_and_timing_sets_background_interval(valid_config):
    config_with_timing = {
        **valid_config,
        "measurement": {"function": "DC_VOLTAGE"},
        "timing": {"poll_interval": 0.5},
    }
    with patch("instro.dmm.drivers.simulated.VisaDriver"):
        dmm = InstroDMM(config=config_with_timing)

    assert dmm.background_interval == 0.5


def test_init_with_config_dict_timing_without_measurement_raises(valid_config):
    with pytest.raises(Exception, match="timing requires a measurement block"):
        InstroDMM(config={**valid_config, "timing": {"poll_interval": 0.5}})


def test_init_with_config_dict_aperture_nplc_and_seconds_raises(valid_config):
    config_with_both = {
        **valid_config,
        "measurement": {"function": "DC_VOLTAGE", "aperture_nplc": 10, "aperture_seconds": 0.1},
    }
    with pytest.raises(Exception, match="mutually exclusive"):
        InstroDMM(config=config_with_both)


def test_open_applies_measurement_config_in_order(valid_config):
    dmm, mock_driver = _make_dmm_with_mock_driver(
        {
            **valid_config,
            "measurement": {"function": "DC_VOLTAGE", "digits": 6, "aperture_nplc": 10, "range": 10.0},
        }
    )

    dmm.open()

    method_order = [name for name, _, _ in mock_driver.mock_calls]
    assert method_order == [
        "open",
        "set_measurement_function",
        "set_digits",
        "set_dc_voltage_nplc",
        "set_dc_voltage_range",
    ]
    mock_driver.set_measurement_function.assert_called_once_with(MeasurementFunction.DC_VOLTAGE)
    mock_driver.set_digits.assert_called_once_with(6)
    mock_driver.set_dc_voltage_nplc.assert_called_once_with(10.0)
    mock_driver.set_dc_voltage_range.assert_called_once_with(10.0)


def test_open_applies_range_auto_as_none(valid_config):
    dmm, mock_driver = _make_dmm_with_mock_driver(
        {**valid_config, "measurement": {"function": "AC_CURRENT", "range": "auto"}}
    )

    dmm.open()

    mock_driver.set_ac_current_range.assert_called_once_with(None)


def test_open_with_omitted_measurement_fields_applies_only_function(valid_config):
    dmm, mock_driver = _make_dmm_with_mock_driver({**valid_config, "measurement": {"function": "DC_VOLTAGE"}})

    dmm.open()

    method_order = [name for name, _, _ in mock_driver.mock_calls]
    assert method_order == ["open", "set_measurement_function"]


def test_open_without_measurement_block_touches_nothing(valid_config):
    dmm, mock_driver = _make_dmm_with_mock_driver(valid_config)

    dmm.open()

    method_order = [name for name, _, _ in mock_driver.mock_calls]
    assert method_order == ["open"]


def test_init_with_config_dict_missing_required_field():
    with pytest.raises(Exception):
        InstroDMM(config={"driver": {"name": "SimulatedDMM", "connection_type": "visa"}})


def test_init_with_config_dict_unknown_driver_name(valid_config):
    with pytest.raises(Exception):
        InstroDMM(config={**valid_config, "driver": {**valid_config["driver"], "name": "not_a_real_driver"}})


def test_init_with_config_file_path_malformed_json(tmp_path):
    config_file = tmp_path / "dmm.json"
    config_file.write_text("this is not json {{{")

    with pytest.raises(Exception):
        InstroDMM(config=config_file)


def test_init_with_config_file_path_object(valid_config, tmp_path):
    config_file = tmp_path / "dmm.json"
    config_file.write_text(json.dumps(valid_config))

    with patch("instro.dmm.drivers.simulated.VisaDriver"):
        dmm = InstroDMM(config=config_file)

    assert dmm.name == "test_dmm"


def test_init_with_config_dmmconfig_object_does_not_alias_caller_instance(valid_config):
    dmm_config = DMMConfig.model_validate(valid_config)
    with patch("instro.dmm.drivers.simulated.VisaDriver"):
        dmm = InstroDMM(config=dmm_config)

    dmm_config.device.name = "mutated"

    assert dmm._config is not dmm_config
    assert dmm._config.device.name == "test_dmm"


def test_init_with_config_explicit_empty_name_is_not_overwritten(valid_config):
    # name="" is falsy but explicitly chosen; it must win over config.device.name, not
    # get silently replaced by it (same regression class as the PSU truthy-`or` bug).
    with patch("instro.dmm.drivers.simulated.VisaDriver"):
        dmm = InstroDMM(config=valid_config, name="")

    assert dmm.name == ""


def test_init_with_config_and_driver_raises(valid_config):
    with pytest.raises(ValueError, match="cannot be combined"):
        InstroDMM(config=valid_config, driver=MagicMock())


def test_init_with_no_config_and_missing_direct_args_raises():
    with pytest.raises(ValueError, match="requires either config"):
        InstroDMM(name="only_name")


def test_init_with_autostart_opens_and_starts(valid_config):
    config_with_timing = {
        **valid_config,
        "measurement": {"function": "DC_VOLTAGE"},
        "timing": {"poll_interval": 0.5},
    }
    with (
        patch("instro.dmm.drivers.simulated.VisaDriver"),
        patch.object(InstroDMM, "open") as mock_open,
        patch.object(InstroDMM, "start") as mock_start,
    ):
        InstroDMM(config=config_with_timing, autostart=True)

    mock_open.assert_called_once()
    mock_start.assert_called_once()


def test_init_without_autostart_does_not_open_or_start(valid_config):
    with (
        patch("instro.dmm.drivers.simulated.VisaDriver"),
        patch.object(InstroDMM, "open") as mock_open,
        patch.object(InstroDMM, "start") as mock_start,
    ):
        InstroDMM(config=valid_config)

    mock_open.assert_not_called()
    mock_start.assert_not_called()


def test_init_with_config_dict_with_publishers(valid_config):
    config_with_publishers = {
        **valid_config,
        "publishers": [
            {"type": "NominalCorePublisher", "dataset_rid": "test_dmm"},
            {"type": "FilePublisher", "directory": "test_dmm_out", "format": "csv"},
        ],
    }
    with (
        patch("instro.dmm.drivers.simulated.VisaDriver"),
        patch("instro.lib.publishers.NominalCorePublisher") as mock_ncp,
        patch("instro.lib.publishers.FilePublisher") as mock_fp,
    ):
        dmm = InstroDMM(config=config_with_publishers)

    mock_ncp.assert_called_once_with(dataset_rid="test_dmm", batch_size=None, profile=None)
    mock_fp.assert_called_once_with(directory="test_dmm_out", format="csv", custom_file_name=None)
    assert dmm.publishers == [mock_ncp.return_value, mock_fp.return_value]


def test_vendor_registry_complete():
    import importlib

    from instro.dmm.config import DMM_VENDOR_REGISTRY
    from instro.dmm.dmm import DMMDriverBase

    for key, path in DMM_VENDOR_REGISTRY.items():
        mod_path, cls_name = path.rsplit(".", 1)
        cls = getattr(importlib.import_module(mod_path), cls_name)
        assert issubclass(cls, DMMDriverBase), f"{key} does not point to a DMMDriverBase subclass"


def test_vendor_registry_matches_drivers_package():
    from instro.dmm import drivers
    from instro.dmm.config import DMM_VENDOR_REGISTRY
    from instro.dmm.dmm import DMMDriverBase

    exported_drivers = {
        name
        for name in drivers.__all__
        if getattr(drivers, name) is not DMMDriverBase and issubclass(getattr(drivers, name), DMMDriverBase)
    }
    assert set(DMM_VENDOR_REGISTRY) == exported_drivers, (
        "DMM_VENDOR_REGISTRY and instro.dmm.drivers.__all__ have drifted apart; a new driver must be added to both."
    )
