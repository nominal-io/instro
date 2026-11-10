"""Cross-category building blocks: base instrument, transports, publishers, shared scaling types."""

from typing import TYPE_CHECKING, Any

from instro.lib.discover import (
    DiscoveredInstrument,
    DriverMatch,
    IdnFields,
    VisaInstrumentInfo,
    VisaScanError,
    VisaScanResult,
    VisaUnrecognizedInstrument,
    match_idn,
    parse_idn,
    scan_visa_resources,
)
from instro.lib.exceptions import FeatureNotSupportedError, InstroError, InstrumentNotOpenError
from instro.lib.instrument import Instrument
from instro.lib.transports.visa import VisaConfig, VisaDriver
from instro.lib.types import Command, DeviceInfo, LinearScale, Measurement, ScaleType

if TYPE_CHECKING:
    from instro.lib.nominal import install_nominal_core_log_handler


# Loaded on first access so importing instro does not require the `nominal` package (#626).
def __getattr__(name: str) -> Any:
    if name == "install_nominal_core_log_handler":
        from instro.lib.nominal import install_nominal_core_log_handler

        return install_nominal_core_log_handler
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "Command",
    "DeviceInfo",
    "DiscoveredInstrument",
    "DriverMatch",
    "FeatureNotSupportedError",
    "IdnFields",
    "InstroError",
    "Instrument",
    "InstrumentNotOpenError",
    "LinearScale",
    "Measurement",
    "ScaleType",
    "VisaConfig",
    "VisaDriver",
    "VisaInstrumentInfo",
    "VisaScanError",
    "VisaScanResult",
    "VisaUnrecognizedInstrument",
    "install_nominal_core_log_handler",
    "match_idn",
    "parse_idn",
    "scan_visa_resources",
]
