"""VISA instrument discovery: scan resources, query identity, match to known drivers."""

# the difficult thing is that this only works reliably for VISA instruments atm (sometimes)
# we will need to eventually expand this to cover non-visa instruments!
from __future__ import annotations

import dataclasses
import warnings

import pyvisa

from instro.lib.transports.visa import TimeoutConfig, VisaConfig, VisaDriver, _open_resource_manager


@dataclasses.dataclass
class VisaInstrumentInfo:
    resource: str
    idn: str
    category: str
    driver_class_name: str
    num_channels: int | None


@dataclasses.dataclass
class VisaScanError:
    resource: str
    message: str
    hint: str | None = None


@dataclasses.dataclass
class VisaUnrecognizedInstrument:
    resource: str
    idn: str


@dataclasses.dataclass
class VisaScanResult:
    instruments: list[VisaInstrumentInfo]
    unrecognized: list[VisaUnrecognizedInstrument]
    errors: list[VisaScanError]


# Key: (vendor, model) substrings matched case-insensitively against the *IDN? response.
# Value: (category, driver_class_name, num_channels). num_channels is the instrument's real
# channel count where known; left None where it isn't tracked (dmm, eload).
_IDN_MAP: dict[tuple[str, str], tuple[str, str, int | None]] = {
    ("AGILENT TECHNOLOGIES", "34401A"): ("dmm", "Agilent34401A", None),
    ("HEWLETT-PACKARD", "34401A"): ("dmm", "Agilent34401A", None),
    ("KEITHLEY INSTRUMENTS", "2400"): ("dmm", "Keithley2400", None),
    ("KEYSIGHT TECHNOLOGIES", "34461A"): ("dmm", "Keysight34461A", None),
    ("AGILENT TECHNOLOGIES", "34461A"): ("dmm", "Keysight34461A", None),
    ("B&K PRECISION", "9115"): ("psu", "BK9115", 1),
    ("B&K PRECISION", "9140"): ("psu", "BK914X", 3),
    ("RIGOL TECHNOLOGIES", "DP811"): ("psu", "RigolDP800", 1),
    ("RIGOL TECHNOLOGIES", "DP821"): ("psu", "RigolDP800", 2),
    ("RIGOL TECHNOLOGIES", "DP831"): ("psu", "RigolDP800", 3),
    ("RIGOL TECHNOLOGIES", "DP832"): ("psu", "RigolDP800", 3),
    ("SIGLENT TECHNOLOGIES", "SPD3303"): ("psu", "SiglentSPD3303", 2),
    ("B&K PRECISION", "BK85"): ("eload", "BK85XXB", None),
    ("KEYSIGHT TECHNOLOGIES", "DSOX120"): ("scope", "Keysight1200X", 2),
    ("KEYSIGHT TECHNOLOGIES", "EDUX105"): ("scope", "Keysight1200X", 2),
    ("TEKTRONIX", "MSO22"): ("scope", "Tektronix2SeriesMSO", 4),
    ("TEKTRONIX", "MSO24"): ("scope", "Tektronix2SeriesMSO", 4),
    ("SIGLENT TECHNOLOGIES", "SDS1104X-E"): ("scope", "SiglentSDS1000XE", 4),
    ("SIGLENT TECHNOLOGIES", "SDS1202X-E"): ("scope", "SiglentSDS1000XE", 2),
    ("SIGLENT TECHNOLOGIES", "SDS1204X-E"): ("scope", "SiglentSDS1000XE", 4),
}


def _classify_error_hint(exc: Exception) -> str | None:
    """Return an actionable hint for a known-bad scan exception, or None if there isn't one.

    ``VisaScanError.message`` always stays the raw ``str(exc)`` so callers that don't want this
    interpretation layer (e.g. a headless integration that just wants results) aren't forced
    into it; callers that do want it (the CLI) can prefer ``hint`` when it's present.
    """
    if isinstance(exc, pyvisa.errors.VisaIOError) and "SYSTEM_ERROR" in str(exc):
        return "permission denied - check udev rules"
    err_str = str(exc)
    if "No backend available" in err_str or "PyUSB" in err_str:
        return "USB backend missing - install libusb"
    return None


def scan_visa_resources(
    backend: str | None = None,
    timeout: int = 2,
    *,
    rm: pyvisa.ResourceManager | None = None,
) -> VisaScanResult:
    """Scan VISA resources, query each for identity, and return matched instruments.

    Pass an already-open ``rm`` (e.g. one a caller opened via ``_open_resource_manager`` for its
    own backend diagnostics) with the ``backend`` string used to open it, to reuse that resource
    manager instead of opening a second one.
    """
    if rm is None:
        rm, backend, _ = _open_resource_manager(backend)
    active_backend = backend

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        resources = rm.list_resources()

    instruments: list[VisaInstrumentInfo] = []
    unrecognized: list[VisaUnrecognizedInstrument] = []
    errors: list[VisaScanError] = []

    for resource in resources:
        if resource.startswith("ASRL"):
            continue

        driver = VisaDriver(
            VisaConfig(visa_resource=resource, timeout=TimeoutConfig(recv=timeout), visa_backend=active_backend),
        )
        try:
            driver.open()
            idn = driver.query("*IDN?").strip()
            parts = [p.strip().lower() for p in idn.split(",")]
            vendor = parts[0] if len(parts) > 0 else ""
            model = parts[1] if len(parts) > 1 else ""

            match = next(
                (
                    v
                    for (k_vendor, k_model), v in _IDN_MAP.items()
                    if k_vendor.lower() in vendor and k_model.lower() in model
                ),
                None,
            )
            if match is None:
                unrecognized.append(VisaUnrecognizedInstrument(resource=resource, idn=idn))
            else:
                category, driver_class_name, num_channels = match
                instruments.append(
                    VisaInstrumentInfo(
                        resource=resource,
                        idn=idn,
                        category=category,
                        driver_class_name=driver_class_name,
                        num_channels=num_channels,
                    )
                )
        except Exception as e:
            errors.append(VisaScanError(resource=resource, message=str(e), hint=_classify_error_hint(e)))
        finally:
            driver.close()

    return VisaScanResult(instruments=instruments, unrecognized=unrecognized, errors=errors)
