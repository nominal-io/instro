"""Hardware regression suite for DAQ digital line/port I/O across all drivers.

Verifies that the digital channel configuration split (configure_digital_line vs
configure_digital_port) works end-to-end on real hardware. For each enabled driver
it runs a DO->DI loopback for the digital mode(s) its hardware supports, asserts the
read value matches what was written, publishes the data to a per-driver Nominal Core
dataset, and creates an asset event plus a workbook per example script.

Environment-specific values (device IDs, loopback wiring, the shared workbook template
RID) and the generated Nominal RIDs live in the gitignored tests/daq/regression_config.json.
Copy tests/daq/regression_config.example.json and fill it in before running.

    uv run pytest -m hardware -v -s tests/daq/test_daq_digital_regression.py

Capability matrix (which modes each driver's hardware supports end-to-end):
    labjack  -> line   (port raises NotImplementedError)
    ni       -> line   (port read/write not yet implemented -> NotImplementedError)
    keysight -> line   (port read/write not yet implemented -> NotImplementedError)
    mcc      -> port   (line/d_config_bit unsupported on USB-1616HS-4)
"""

import json
import time
import warnings
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from instro.daq import InstroDAQ
from instro.daq.types import DigitalPortWidth, Direction, Logic

_CONFIG_PATH = Path(__file__).parent / "regression_config.json"

# DO->DI propagation settle time before each read.
_SETTLE_S = 0.05

# Digital modes each driver's hardware can exercise.
_CAPABILITIES: dict[str, list[str]] = {
    "labjack": ["line"],
    "ni": ["line"],
    "keysight": ["line"],
    "mcc": ["port"],
}

# Example scripts a workbook is created for, per mode.
_SCRIPTS_BY_MODE: dict[str, list[str]] = {
    "line": ["read_digital_line", "write_digital_line"],
    "port": ["read_digital_port", "write_digital_port"],
}

# Human-facing driver names used in asset names (e.g. "LabJack 480010992").
_DISPLAY_NAMES: dict[str, str] = {
    "labjack": "LabJack",
    "ni": "NI",
    "keysight": "Keysight",
    "mcc": "MCC",
}

_PARAMS = [(driver, mode) for driver, modes in _CAPABILITIES.items() for mode in modes]


def _resolve_device_id(driver_key: str, daq: InstroDAQ, cfg: dict[str, Any]) -> str:
    """Best-effort hardware serial for the asset name; falls back to the configured id/address."""
    configured = cfg.get("device_id") or cfg.get("address") or driver_key
    if driver_key == "labjack":
        try:
            return str(daq.driver.get_info()[2])  # LabJack serial number
        except Exception:
            return configured
    return configured


def _build_driver(driver_key: str, cfg: dict[str, Any]) -> Any:
    """Construct the concrete vendor driver (lazy import keeps optional SDKs out of collection)."""
    if driver_key == "labjack":
        from instro.daq.drivers.labjack import LabJackTSeriesDriver

        return LabJackTSeriesDriver(device_id=cfg["device_id"])
    if driver_key == "mcc":
        from instro.daq.drivers.mcc import MCCDriver

        return MCCDriver(device_id=cfg["device_id"])
    if driver_key == "ni":
        from instro.daq.drivers.ni import NIDAQDriver

        return NIDAQDriver(device_id=cfg["device_id"])
    if driver_key == "keysight":
        from instro.daq.drivers import Keysight34980A

        return Keysight34980A(cfg["address"])
    raise ValueError(f"Unknown driver key: {driver_key}")


def _run_line_loopback(daq: InstroDAQ, wiring: dict[str, Any]) -> None:
    """Configure a DO/DI line pair, then assert each written bit reads back."""
    daq.configure_digital_line(
        direction=Direction.OUTPUT, physical_channel=wiring["do"], alias="do_line", logic=Logic.HIGH
    )
    daq.configure_digital_line(
        direction=Direction.INPUT, physical_channel=wiring["di"], alias="di_line", logic=Logic.HIGH
    )
    for value in (1, 0):
        daq.write_digital_line(channel="do_line", data=value)
        time.sleep(_SETTLE_S)
        read = daq.read_digital_line(channel="di_line")
        got = int(round(float(read.latest)))
        assert got == value, f"line loopback {wiring['do']}->{wiring['di']}: wrote {value}, read {got}"


def _run_port_loopback(daq: InstroDAQ, wiring: dict[str, Any]) -> None:
    """Configure a DO/DI port pair, then assert each written pattern reads back under the wiring mask."""
    width = DigitalPortWidth(int(wiring.get("width", 8)))
    mask = int(wiring.get("mask", (1 << int(width)) - 1))
    daq.configure_digital_port(
        direction=Direction.OUTPUT, physical_channel=wiring["do"], port_width=width, logic=Logic.HIGH, alias="do_port"
    )
    daq.configure_digital_port(
        direction=Direction.INPUT, physical_channel=wiring["di"], port_width=width, logic=Logic.HIGH, alias="di_port"
    )
    for pattern in (mask, 0):
        daq.write_digital_port(channel="do_port", data=pattern)
        time.sleep(_SETTLE_S)
        read = daq.read_digital_port(channel="di_port")
        got = int(round(float(read.latest))) & mask
        assert got == (pattern & mask), (
            f"port loopback {wiring['do']}->{wiring['di']}: wrote {pattern:#b} (mask {mask:#b}), read {got:#b}"
        )


class _NominalResources:
    """Creates/reuses per-driver Nominal Core dataset, asset, events, and workbooks."""

    def __init__(self, config: dict[str, Any], config_path: Path):
        from nominal.core import NominalClient

        self._config = config
        self._config_path = config_path
        self._client = NominalClient.from_profile(config.get("profile", "default"))
        self._template_rid = config["template_rid"]
        self._scope = config.get("data_scope_name", "daq")
        self._generated: dict[str, Any] = config.setdefault("generated", {})
        self._workbook_urls: list[tuple[str, str]] = []

    @property
    def config(self) -> dict[str, Any]:
        return self._config

    def setup_driver(self, driver_key: str, device_id: str) -> tuple[Any, str]:
        """Get-or-create the asset and dataset for a driver, linking the dataset into the asset."""
        gen = self._generated.setdefault(driver_key, {})
        device_name = f"{_DISPLAY_NAMES.get(driver_key, driver_key)} {device_id}"
        asset = self._client.get_or_create_asset_by_properties(
            properties={"device": device_id, "driver": driver_key, "purpose": "digital-regression"},
            name=device_name,
            description="DAQ digital I/O loopback regression",
            labels=["instro", "daq", "digital-regression", driver_key],
        )
        gen["asset_rid"] = asset.rid

        if gen.get("dataset_rid"):
            dataset = self._client.get_dataset(gen["dataset_rid"])
        else:
            dataset = self._client.create_dataset(
                name=f"{device_name} digital regression",
                description="DAQ digital I/O loopback regression data",
                labels=["instro", "daq", "digital-regression", driver_key],
            )
            gen["dataset_rid"] = dataset.rid

        existing_scopes = {name for name, _ in asset.list_data_scopes()}
        if self._scope not in existing_scopes:
            asset.add_dataset(self._scope, dataset)

        self._save()
        return asset, dataset.rid

    def ensure_workbook(self, driver_key: str, asset: Any, script_name: str) -> str:
        """Instantiate the shared template against the asset once per driver/script, reusing on later runs."""
        gen = self._generated.setdefault(driver_key, {})
        workbooks: dict[str, str] = gen.setdefault("workbooks", {})
        if workbooks.get(script_name):
            return workbooks[script_name]
        template = self._client.get_workbook_template(self._template_rid)
        workbook = template.create_workbook(asset=asset, title=f"{driver_key} - {script_name}")
        workbooks[script_name] = workbook.rid
        self._workbook_urls.append((f"{driver_key}/{script_name}", workbook.nominal_url))
        self._save()
        return workbook.rid

    def record_event(
        self,
        asset: Any,
        name: str,
        start_ns: int,
        end_ns: int,
        passed: bool,
        driver_key: str,
        mode: str,
        description: str = "",
    ) -> None:
        """Create a SUCCESS/ERROR event for a loopback case on the driver's asset."""
        from nominal.core import EventType

        self._client.create_event(
            name=name,
            type=EventType.SUCCESS if passed else EventType.ERROR,
            start=start_ns,
            duration=timedelta(microseconds=(end_ns - start_ns) / 1_000),
            description=description,
            assets=[asset],
            properties={"status": "PASS" if passed else "FAIL", "driver": driver_key, "mode": mode},
            labels=["instro", "daq", "digital-regression"],
        )

    def _save(self) -> None:
        with self._config_path.open("w") as f:
            json.dump(self._config, f, indent=2)
            f.write("\n")

    def finish(self) -> None:
        if self._workbook_urls:
            print("\nWorkbooks created this run:")
            for label, url in self._workbook_urls:
                print(f"  {label}: {url}")


def _load_config() -> dict[str, Any] | None:
    if not _CONFIG_PATH.exists():
        return None
    with _CONFIG_PATH.open() as f:
        return json.load(f)


@pytest.fixture(scope="module")
def nominal_resources() -> Any:
    config = _load_config()
    if config is None:
        pytest.skip(
            f"regression config not found at {_CONFIG_PATH}; copy regression_config.example.json and fill it in"
        )
    template_rid = config.get("template_rid", "")
    if not template_rid or template_rid.startswith("<"):
        pytest.skip("template_rid is not set in regression config")
    resources = _NominalResources(config, _CONFIG_PATH)
    yield resources
    resources.finish()


@pytest.mark.hardware
@pytest.mark.parametrize("driver_key,mode", _PARAMS)
def test_digital_loopback(driver_key: str, mode: str, nominal_resources: Any) -> None:
    cfg = nominal_resources.config["drivers"].get(driver_key)
    if not cfg or not cfg.get("enabled"):
        pytest.skip(f"{driver_key} not enabled in regression config")
    wiring = cfg.get(mode)
    if not wiring:
        pytest.skip(f"{driver_key} has no {mode} wiring configured")

    from instro.utils.publishers import NominalCorePublisher

    daq = InstroDAQ(name=f"{driver_key}_digital_regression", driver=_build_driver(driver_key, cfg))
    asset = None
    passed = True
    description = ""
    start_ns = time.time_ns()
    try:
        daq.open()
        device_id = _resolve_device_id(driver_key, daq, cfg)
        asset, dataset_rid = nominal_resources.setup_driver(driver_key, device_id)
        daq.add_publisher(NominalCorePublisher(dataset_rid=dataset_rid))

        start_ns = time.time_ns()
        if mode == "line":
            _run_line_loopback(daq, wiring)
        else:
            _run_port_loopback(daq, wiring)
    except Exception as exc:
        passed = False
        description = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        end_ns = time.time_ns()
        try:
            daq.close()
        except Exception:
            pass
        if asset is not None:
            # Workbook creation is a best-effort artifact; never let it flip the hardware verdict.
            if passed:
                for script in _SCRIPTS_BY_MODE[mode]:
                    try:
                        nominal_resources.ensure_workbook(driver_key, asset, script)
                    except Exception as exc:
                        warnings.warn(f"workbook creation failed for {driver_key}/{script}: {exc}", stacklevel=2)
            nominal_resources.record_event(
                asset,
                name=f"{driver_key} {mode} digital loopback",
                start_ns=start_ns,
                end_ns=end_ns,
                passed=passed,
                driver_key=driver_key,
                mode=mode,
                description=description,
            )
