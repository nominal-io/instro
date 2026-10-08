"""Discovery for instruments reached through a vendor SDK instead of VISA: NI-DAQmx, LabJack LJM, MCC UL.

Each provider is a named source in :data:`instro.lib.discover.SOURCES` (``nidaq``, ``labjack``,
``mccdaq``) that callers opt in to; the default scan is VISA only. A provider imports its SDK only
when it runs, so ``instro`` stays importable without any of them. Selecting a source whose instro
package is not installed yields one :class:`~instro.lib.discover.SkippedResource` naming the extra to
install; an SDK or runtime that fails to load yields a :class:`~instro.lib.discover.ScanError` with a
hint. The records resolve their drivers through ``DAQ_VENDOR_REGISTRY`` like any other.
"""

from __future__ import annotations

import dataclasses
import importlib.util
from collections.abc import Iterable

from instro.lib.discover import (
    DiscoveredInstrument,
    DiscoveryEvent,
    DiscoveryOptions,
    DiscoveryProvider,
    EmitFn,
    Record,
    ScanError,
    SkippedResource,
)

__all__ = [
    "VENDOR_PACKAGES",
    "LabJackProvider",
    "MCCProvider",
    "MissingPackageProvider",
    "NIDAQProvider",
    "VendorPackage",
    "vendor_provider",
]


def _idn(manufacturer: str, model: object, serial: object) -> str:
    """Spell a vendor device's identity the way an ``*IDN?`` reply would, so records read alike."""
    return f"{manufacturer},{model},{serial},"


class NIDAQProvider:
    """NI-DAQmx devices from ``System.local().devices``. The resource is the DAQmx device name (``Dev1``, ``cDAQ1Mod1``)."""

    name = "nidaqmx"

    def discover(self, options: DiscoveryOptions, emit: EmitFn) -> Iterable[Record]:
        try:
            from nidaqmx.system import System  # type: ignore[import-not-found, import-untyped, unused-ignore]

            devices = list(System.local().devices)
        except Exception as exc:
            yield ScanError(self.name, str(exc), "NI-DAQmx runtime not available; install NI-DAQmx")
            return
        for device in devices:
            emit(DiscoveryEvent("probing", device.name, "NI-DAQmx"))
            yield DiscoveredInstrument(
                resource=device.name,
                idn=_idn(
                    "National Instruments", getattr(device, "product_type", ""), getattr(device, "serial_num", "")
                ),
                category="daq",
                driver_name="NIDAQDriver",
                transport=self.name,
            )


# ljm.constants.dtT4 / dtT7 / dtT8
_LJM_DEVICE_TYPES = {4: "T4", 7: "T7", 8: "T8"}


class LabJackProvider:
    """LabJack T-series devices from ``ljm.listAll``. The resource is the serial number, which ``ljm.openS`` accepts."""

    name = "ljm"

    def discover(self, options: DiscoveryOptions, emit: EmitFn) -> Iterable[Record]:
        try:
            from labjack import ljm  # type: ignore[import-not-found, import-untyped, unused-ignore]

            found, device_types, _connections, serials, _ips = ljm.listAll(ljm.constants.dtANY, ljm.constants.ctANY)
        except Exception as exc:
            yield ScanError(self.name, str(exc), "LabJack LJM library not available; install LJM")
            return
        for device_type, serial in zip(device_types[:found], serials[:found], strict=False):
            resource = str(serial)
            emit(DiscoveryEvent("probing", resource, "LabJack LJM"))
            model = _LJM_DEVICE_TYPES.get(device_type, f"device type {device_type}")
            yield DiscoveredInstrument(
                resource=resource,
                idn=_idn("LabJack", model, serial),
                category="daq",
                driver_name="LabJackTSeriesDriver",
                transport=self.name,
            )


class MCCProvider:
    """MCC devices from ``ul.get_daq_device_inventory``. The resource is the unique id ``MCCDriver`` takes as ``device_id``."""

    name = "mcculw"

    def discover(self, options: DiscoveryOptions, emit: EmitFn) -> Iterable[Record]:
        try:
            from mcculw import ul  # type: ignore[import-not-found, import-untyped, unused-ignore]
            from mcculw.enums import InterfaceType  # type: ignore[import-not-found, import-untyped, unused-ignore]

            ul.ignore_instacal()
            devices = list(ul.get_daq_device_inventory(InterfaceType.ANY))
        except Exception as exc:
            yield ScanError(self.name, str(exc), "MCC Universal Library not available (Windows only); install InstaCal")
            return
        for device in devices:
            emit(DiscoveryEvent("probing", device.unique_id, "MCC UL"))
            yield DiscoveredInstrument(
                resource=device.unique_id,
                idn=_idn("Measurement Computing", getattr(device, "product_name", ""), device.unique_id),
                category="daq",
                driver_name="MCCDriver",
                transport=self.name,
            )


@dataclasses.dataclass(frozen=True)
class VendorPackage:
    """A vendor package core knows how to discover for, and what to install when it is missing."""

    label: str
    distribution: str
    module: str
    extra: str
    provider: type[DiscoveryProvider]


VENDOR_PACKAGES: dict[str, VendorPackage] = {
    "nidaq": VendorPackage("NI-DAQmx", "instro-daq-ni", "instro.daq.drivers.ni", "nidaq", NIDAQProvider),
    "labjack": VendorPackage("LabJack", "instro-daq-labjack", "instro.daq.drivers.labjack", "labjack", LabJackProvider),
    "mccdaq": VendorPackage("MCC", "instro-daq-mcc", "instro.daq.drivers.mcc", "mccdaq", MCCProvider),
}
"""Vendor sources by the name used in ``discover(sources=...)`` and ``instro discover --source``."""


class MissingPackageProvider:
    """Stands in for a selected vendor source whose instro package is not installed: yields one skipped record."""

    def __init__(self, package: VendorPackage) -> None:
        self.name = package.extra
        self._package = package

    def discover(self, options: DiscoveryOptions, emit: EmitFn) -> Iterable[Record]:
        package = self._package
        yield SkippedResource(
            package.label, f'{package.distribution} not installed (pip install "instro[{package.extra}]")'
        )


def _installed(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except ModuleNotFoundError:
        return False


def vendor_provider(source: str) -> DiscoveryProvider:
    """The provider for a vendor source name, or a :class:`MissingPackageProvider` when its package is absent."""
    package = VENDOR_PACKAGES[source]
    return package.provider() if _installed(package.module) else MissingPackageProvider(package)
