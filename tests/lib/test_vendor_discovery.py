"""Built-in vendor-SDK discovery, run against stand-in SDK modules so no vendor runtime is needed."""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from instro.lib.discover import DiscoveredInstrument, DiscoveryEvent, DiscoveryOptions, ScanError
from instro.lib.vendor_discovery import (
    VENDOR_PACKAGES,
    LabJackProvider,
    MCCProvider,
    MissingPackageProvider,
    NIDAQProvider,
    vendor_provider,
)


def _run(provider):
    events: list[DiscoveryEvent] = []
    records = list(provider.discover(DiscoveryOptions(), events.append))
    return records, events


def _fake_sdk(monkeypatch: pytest.MonkeyPatch, **modules: object) -> None:
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)


def test_nidaq_provider_lists_daqmx_devices_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    system = MagicMock()
    system.local.return_value.devices = [
        SimpleNamespace(name="Dev1", product_type="USB-6002", serial_num=0x1A2B3C),
        SimpleNamespace(name="cDAQ1Mod1", product_type="NI 9219", serial_num=0),
    ]
    _fake_sdk(monkeypatch, nidaqmx=MagicMock(), **{"nidaqmx.system": SimpleNamespace(System=system)})

    records, events = _run(NIDAQProvider())

    assert records == [
        DiscoveredInstrument(
            "Dev1", "National Instruments,USB-6002,1715004,", "daq", "NIDAQDriver", transport="nidaqmx"
        ),
        DiscoveredInstrument("cDAQ1Mod1", "National Instruments,NI 9219,0,", "daq", "NIDAQDriver", transport="nidaqmx"),
    ]
    assert [(e.status, e.resource) for e in events] == [("probing", "Dev1"), ("probing", "cDAQ1Mod1")]


def test_nidaq_provider_reports_missing_runtime_as_one_error(monkeypatch: pytest.MonkeyPatch) -> None:
    system = MagicMock()
    system.local.side_effect = OSError("Could not find an installation of NI-DAQmx")
    _fake_sdk(monkeypatch, nidaqmx=MagicMock(), **{"nidaqmx.system": SimpleNamespace(System=system)})

    records, _ = _run(NIDAQProvider())

    assert records == [
        ScanError(
            "nidaqmx", "Could not find an installation of NI-DAQmx", "NI-DAQmx runtime not available; install NI-DAQmx"
        )
    ]


def test_labjack_provider_lists_serials_with_model_names(monkeypatch: pytest.MonkeyPatch) -> None:
    ljm = MagicMock()
    ljm.constants.dtANY, ljm.constants.ctANY = 0, 0
    ljm.listAll.return_value = (2, [7, 4], [1, 3], [470010001, 440020002], ["0.0.0.0", "192.168.1.5"])
    _fake_sdk(monkeypatch, labjack=SimpleNamespace(ljm=ljm))

    records, _ = _run(LabJackProvider())

    assert records == [
        DiscoveredInstrument("470010001", "LabJack,T7,470010001,", "daq", "LabJackTSeriesDriver", transport="ljm"),
        DiscoveredInstrument("440020002", "LabJack,T4,440020002,", "daq", "LabJackTSeriesDriver", transport="ljm"),
    ]
    ljm.listAll.assert_called_once_with(0, 0)


def test_mcc_provider_lists_unique_ids_and_bypasses_instacal(monkeypatch: pytest.MonkeyPatch) -> None:
    ul = MagicMock()
    ul.get_daq_device_inventory.return_value = [SimpleNamespace(unique_id="1F8C2A3", product_name="USB-1608G")]
    enums = SimpleNamespace(InterfaceType=SimpleNamespace(ANY=7))
    _fake_sdk(monkeypatch, mcculw=SimpleNamespace(ul=ul), **{"mcculw.enums": enums})

    records, _ = _run(MCCProvider())

    assert records == [
        DiscoveredInstrument(
            "1F8C2A3", "Measurement Computing,USB-1608G,1F8C2A3,", "daq", "MCCDriver", transport="mcculw"
        )
    ]
    ul.ignore_instacal.assert_called_once_with()
    ul.get_daq_device_inventory.assert_called_once_with(7)


def test_vendor_sources_are_named_in_discover_sources() -> None:
    from instro.lib.discover import SOURCES

    assert set(VENDOR_PACKAGES) <= set(SOURCES)
    assert set(VENDOR_PACKAGES) == {"nidaq", "labjack", "mccdaq"}


def test_selecting_a_source_whose_package_is_missing_yields_one_skipped_record() -> None:
    with patch("instro.lib.vendor_discovery._installed", return_value=False):
        provider = vendor_provider("nidaq")
    assert isinstance(provider, MissingPackageProvider)
    records, _ = _run(provider)
    assert [(r.resource, r.reason) for r in records] == [
        ("NI-DAQmx", 'instro-daq-ni not installed (pip install "instro[nidaq]")')
    ]  # type: ignore[union-attr]


def test_selecting_an_installed_source_builds_its_provider() -> None:
    with patch("instro.lib.vendor_discovery._installed", return_value=True):
        assert isinstance(vendor_provider("labjack"), LabJackProvider)


def test_every_vendor_driver_name_is_a_daq_registry_key() -> None:
    """The driver names the providers emit are registry keys, so make_driver() works once the package is installed."""
    from instro.lib.registry import driver_registry

    assert {"NIDAQDriver", "LabJackTSeriesDriver", "MCCDriver"} <= set(driver_registry("daq"))
