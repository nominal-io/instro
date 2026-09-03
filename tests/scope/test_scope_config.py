"""Tests for scope JSON config-driven construction."""

from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock, patch

import pytest

from instro.scope import (
    AcquisitionMode,
    Coupling,
    InstroScope,
    ScopeConfig,
    ScopeDriverBase,
    ScopeMeasurementType,
    TriggerMode,
    TriggerSlope,
    TriggerType,
)

VISA_PATCH = "instro.scope.drivers.keysight_1200x.VisaDriver"


@pytest.fixture
def valid_config() -> dict:
    return {
        "device": {"name": "test_scope"},
        "driver": {
            "name": "Keysight1200X",
            "connection_type": "visa",
            "num_channels": 2,
            "visa": {"visa_resource": "TCPIP0::127.0.0.1::INSTR"},
        },
    }


@pytest.fixture
def full_config(valid_config: dict) -> dict:
    return {
        **valid_config,
        "channels": {
            "1": {
                "vertical_scale": 1.0,
                "vertical_offset": 0.0,
                "coupling": "DC",
                "probe_attenuation": 10.0,
                "measurements": ["VRMS", "FREQUENCY"],
            },
            "2": {"measurements": ["VPP"]},
        },
        "acquisition": {
            "mode": "AVERAGE",
            "average_count": 16,
            "horizontal_scale": 0.001,
            "start_acquisition_on_open": True,
        },
        "trigger": {"source": 1, "type": "EDGE", "level": 0.5, "slope": "RISING", "mode": "NORMAL"},
    }


def _make_scope_with_mock_driver(config: dict) -> tuple[InstroScope, MagicMock]:
    """Build an InstroScope from ``config`` and swap in a mock driver whose read-backs echo ``full_config``."""
    with patch(VISA_PATCH):
        scope = InstroScope(config=config)
    mock_driver = MagicMock(spec=ScopeDriverBase)
    mock_driver.get_vertical_scale.return_value = 1.0
    mock_driver.get_vertical_offset.return_value = 0.0
    mock_driver.get_coupling.return_value = Coupling.DC
    mock_driver.get_probe_attenuation.return_value = 10.0
    mock_driver.get_horizontal_scale.return_value = 0.001
    mock_driver.get_acquisition_mode.return_value = AcquisitionMode.AVERAGE
    mock_driver.get_average_count.return_value = 16
    scope._driver = mock_driver
    return scope, mock_driver


def _driver_calls(mock_driver: MagicMock) -> list[str]:
    return [name for name, _, _ in mock_driver.mock_calls if name != "check_errors"]


def test_init_with_config_dict_coerces_channel_keys(full_config):
    with patch(VISA_PATCH):
        scope = InstroScope(config=full_config)

    assert scope.name == "test_scope"
    assert scope._num_channels == 2
    assert scope._config is not None
    assert set(scope._config.channels) == {1, 2}


def test_init_with_config_registers_measurements_as_daemon_functions(full_config):
    with patch(VISA_PATCH):
        scope = InstroScope(config=full_config)

    assert scope._background_methods == [
        (scope.measure, (ScopeMeasurementType.VRMS,), {"channel": 1}),
        (scope.measure, (ScopeMeasurementType.FREQUENCY,), {"channel": 1}),
        (scope.measure, (ScopeMeasurementType.VPP,), {"channel": 2}),
    ]


def test_init_with_config_dict_and_timing_sets_background_interval(valid_config):
    config = {**valid_config, "channels": {"1": {"measurements": ["VPP"]}}, "timing": {"poll_interval": 0.5}}
    with patch(VISA_PATCH):
        scope = InstroScope(config=config)

    assert scope.background_interval == 0.5


def test_init_with_config_dict_timing_without_measurements_raises(valid_config):
    config = {**valid_config, "channels": {"1": {"vertical_scale": 1.0}}, "timing": {"poll_interval": 0.5}}
    with pytest.raises(Exception, match="timing requires at least one channel"):
        InstroScope(config=config)


def test_init_with_config_dict_channel_out_of_range_raises(valid_config):
    with pytest.raises(Exception, match=r"channels \[3\] are outside 1..2"):
        InstroScope(config={**valid_config, "channels": {"3": {"vertical_scale": 1.0}}})


def test_init_with_config_dict_trigger_source_out_of_range_raises(valid_config):
    with pytest.raises(Exception, match="trigger.source 3 is outside 1..2"):
        InstroScope(config={**valid_config, "trigger": {"source": 3}})


def test_init_with_config_dict_average_count_without_average_mode_raises(valid_config):
    with pytest.raises(Exception, match="average_count requires mode 'AVERAGE'"):
        InstroScope(config={**valid_config, "acquisition": {"mode": "NORMAL", "average_count": 16}})


def test_init_with_config_dict_duplicate_measurements_raises(valid_config):
    with pytest.raises(Exception, match="duplicate measurement types"):
        InstroScope(config={**valid_config, "channels": {"1": {"measurements": ["VPP", "VPP"]}}})


def test_open_applies_config_in_hardware_order(full_config, caplog):
    scope, mock_driver = _make_scope_with_mock_driver(full_config)

    with caplog.at_level(logging.WARNING, logger="instro.scope.scope"):
        scope.open()

    # Probe before scale (probe rescales the channel), trigger before run() (trigger in place when
    # acquisition starts), run() before the acquisition block (Siglent applies ACQW only while
    # running), count before mode (Siglent's mode command carries the count), slots before sync.
    assert _driver_calls(mock_driver) == [
        "open",
        "set_coupling",
        "set_probe_attenuation",
        "set_vertical_scale",
        "set_vertical_offset",
        "set_trigger_source",
        "set_trigger_type",
        "set_trigger_slope",
        "set_trigger_level",
        "set_trigger_mode",
        "run",
        "set_average_count",
        "set_acquisition_mode",
        "set_horizontal_scale",
        *["setup_measurement"] * 3,
        *["get_vertical_scale", "get_vertical_offset", "get_coupling", "get_probe_attenuation"] * 2,
        "get_horizontal_scale",
        "get_acquisition_mode",
        "get_average_count",
    ]
    mock_driver.set_vertical_scale.assert_called_once_with(1.0, channel=1)
    mock_driver.set_probe_attenuation.assert_called_once_with(10.0, channel=1)
    mock_driver.set_acquisition_mode.assert_called_once_with(AcquisitionMode.AVERAGE)
    mock_driver.set_trigger_source.assert_called_once_with(1)
    mock_driver.set_trigger_type.assert_called_once_with(TriggerType.EDGE)
    mock_driver.set_trigger_slope.assert_called_once_with(TriggerSlope.RISING)
    mock_driver.set_trigger_mode.assert_called_once_with(TriggerMode.NORMAL)
    assert [c.args + (c.kwargs["channel"],) for c in mock_driver.setup_measurement.call_args_list] == [
        (ScopeMeasurementType.VRMS, 1),
        (ScopeMeasurementType.FREQUENCY, 1),
        (ScopeMeasurementType.VPP, 2),
    ]
    assert caplog.records == []


def test_open_without_start_acquisition_on_open_does_not_run(full_config):
    full_config["acquisition"]["start_acquisition_on_open"] = False
    scope, mock_driver = _make_scope_with_mock_driver(full_config)

    scope.open()

    mock_driver.run.assert_not_called()


def test_open_with_config_but_no_blocks_only_syncs(valid_config):
    scope, mock_driver = _make_scope_with_mock_driver(valid_config)

    scope.open()

    calls = _driver_calls(mock_driver)
    assert calls[0] == "open"
    assert not any(name.startswith("set_") for name in calls)
    assert "run" not in calls


def test_open_warns_when_instrument_snaps_requested_values(full_config, caplog):
    scope, mock_driver = _make_scope_with_mock_driver(full_config)
    mock_driver.get_vertical_scale.return_value = 2.0
    mock_driver.get_acquisition_mode.return_value = AcquisitionMode.NORMAL

    with caplog.at_level(logging.WARNING, logger="instro.scope.scope"):
        scope.open()

    assert [r.getMessage() for r in caplog.records] == [
        "config requested ch1.vertical_scale=1.0 but instrument reports 2.0",
        "config requested acquisition.mode=AVERAGE but instrument reports NORMAL",
    ]


def test_open_does_not_warn_on_three_significant_figure_readback_rounding(full_config, caplog):
    full_config["channels"]["1"]["vertical_scale"] = 0.1236
    scope, mock_driver = _make_scope_with_mock_driver(full_config)
    mock_driver.get_vertical_scale.return_value = float("1.24E-01")

    with caplog.at_level(logging.WARNING, logger="instro.scope.scope"):
        scope.open()

    assert caplog.records == []


def test_open_closes_driver_when_config_apply_fails_and_retries_on_reopen(full_config):
    scope, mock_driver = _make_scope_with_mock_driver(full_config)
    mock_driver.set_acquisition_mode.side_effect = NotImplementedError("no AVERAGE mode")

    with pytest.raises(NotImplementedError):
        scope.open()

    mock_driver.close.assert_called_once()
    mock_driver.get_vertical_scale.assert_not_called()

    # The failed apply must not leave the flag set, or this reopen would skip the config entirely.
    mock_driver.set_acquisition_mode.side_effect = None
    scope.open()
    assert mock_driver.set_acquisition_mode.call_count == 2
    mock_driver.get_vertical_scale.assert_called()


def test_start_without_measurements_warns(valid_config, caplog):
    scope, _ = _make_scope_with_mock_driver(valid_config)

    with caplog.at_level(logging.WARNING, logger="instro.scope.scope"):
        scope.start()
    scope.stop()

    assert [r.getMessage() for r in caplog.records] == [
        "Background daemon for scope 'test_scope' has no measurements to poll; declare channels.<n>.measurements "
        "in the config or call add_background_daemon_function to register some."
    ]


def test_reopen_without_close_does_not_reapply_config(full_config):
    scope, mock_driver = _make_scope_with_mock_driver(full_config)

    scope.open()
    scope.open()
    assert mock_driver.set_vertical_scale.call_count == 1

    scope.close()
    scope.open()
    assert mock_driver.set_vertical_scale.call_count == 2


def test_init_with_autostart_without_measurements_raises_before_opening(valid_config):
    with patch(VISA_PATCH) as mock_visa_cls:
        with pytest.raises(ValueError, match="measurements entry"):
            InstroScope(config=valid_config, autostart=True)

    mock_visa_cls.return_value.open.assert_not_called()


def test_init_with_autostart_opens_and_starts(full_config):
    with (
        patch(VISA_PATCH),
        patch.object(InstroScope, "open") as mock_open,
        patch.object(InstroScope, "start") as mock_start,
    ):
        InstroScope(config=full_config, autostart=True)

    mock_open.assert_called_once()
    mock_start.assert_called_once()


def test_init_with_autostart_open_failure_closes_config_publishers(full_config):
    config = {**full_config, "publishers": [{"type": "NominalCorePublisher", "dataset_rid": "test_scope"}]}
    with (
        patch(VISA_PATCH) as mock_visa_cls,
        patch("instro.lib.publishers.NominalCorePublisher") as mock_ncp,
    ):
        mock_visa_cls.return_value.open.side_effect = OSError("unreachable")
        with pytest.raises(OSError):
            InstroScope(config=config, autostart=True)

    mock_ncp.return_value.close.assert_called_once()


def test_init_with_config_dict_missing_required_field():
    with pytest.raises(Exception):
        InstroScope(config={"driver": {"name": "Keysight1200X", "connection_type": "visa", "num_channels": 2}})


def test_init_with_config_dict_unknown_driver_name(valid_config):
    with pytest.raises(Exception, match="unknown driver"):
        InstroScope(config={**valid_config, "driver": {**valid_config["driver"], "name": "not_a_real_driver"}})


def test_init_with_config_dict_unknown_field_rejected(valid_config):
    with pytest.raises(Exception):
        InstroScope(config={**valid_config, "channels": {"1": {"vertical_scael": 1.0}}})


def test_init_with_config_file_path_malformed_json(tmp_path):
    config_file = tmp_path / "scope.json"
    config_file.write_text("this is not json {{{")

    with pytest.raises(Exception):
        InstroScope(config=config_file)


def test_init_with_config_file_path_object(full_config, tmp_path):
    config_file = tmp_path / "scope.json"
    config_file.write_text(json.dumps(full_config))

    with patch(VISA_PATCH):
        scope = InstroScope(config=config_file)

    assert scope.name == "test_scope"
    assert scope._config is not None
    assert scope._config.channels[1].coupling == Coupling.DC


def test_init_with_config_scopeconfig_object_does_not_alias_caller_instance(valid_config):
    scope_config = ScopeConfig.model_validate(valid_config)
    with patch(VISA_PATCH):
        scope = InstroScope(config=scope_config)

    scope_config.device.name = "mutated"

    assert scope._config is not scope_config
    assert scope._config is not None
    assert scope._config.device.name == "test_scope"


def test_init_with_config_and_driver_raises(valid_config):
    with pytest.raises(ValueError, match="cannot be combined"):
        InstroScope(config=valid_config, driver=MagicMock())


def test_init_with_no_config_and_missing_direct_args_raises():
    with pytest.raises(ValueError, match="requires either config"):
        InstroScope(name="only_name")


def test_init_with_config_dict_with_publishers(valid_config):
    config = {
        **valid_config,
        "publishers": [
            {"type": "NominalCorePublisher", "dataset_rid": "test_scope"},
            {"type": "FilePublisher", "directory": "test_scope_out", "format": "csv"},
        ],
    }
    with (
        patch(VISA_PATCH),
        patch("instro.lib.publishers.NominalCorePublisher") as mock_ncp,
        patch("instro.lib.publishers.FilePublisher") as mock_fp,
    ):
        scope = InstroScope(config=config)

    mock_ncp.assert_called_once_with(dataset_rid="test_scope", batch_size=None, profile=None)
    mock_fp.assert_called_once_with(directory="test_scope_out", format="csv", custom_file_name=None)
    assert scope.publishers == [mock_ncp.return_value, mock_fp.return_value]


def test_vendor_registry_complete():
    import importlib

    from instro.scope.config import SCOPE_VENDOR_REGISTRY

    for key, path in SCOPE_VENDOR_REGISTRY.items():
        mod_path, cls_name = path.rsplit(".", 1)
        cls = getattr(importlib.import_module(mod_path), cls_name)
        assert issubclass(cls, ScopeDriverBase), f"{key} does not point to a ScopeDriverBase subclass"


def test_vendor_registry_matches_drivers_package():
    from instro.scope import drivers
    from instro.scope.config import SCOPE_VENDOR_REGISTRY

    exported_drivers = {
        name
        for name in drivers.__all__
        if getattr(drivers, name) is not ScopeDriverBase and issubclass(getattr(drivers, name), ScopeDriverBase)
    }
    assert set(SCOPE_VENDOR_REGISTRY) == exported_drivers, (
        "SCOPE_VENDOR_REGISTRY and instro.scope.drivers.__all__ have drifted apart; a new driver must be added to both."
    )
