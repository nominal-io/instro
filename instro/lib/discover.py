"""VISA instrument discovery: scan resources, query identity, match to registered drivers."""

# the difficult thing is that this only works reliably for VISA instruments atm (sometimes)
# we will need to eventually expand this to cover non-visa instruments!
from __future__ import annotations

import dataclasses
import warnings

import pyvisa

from instro.lib.registry import iter_driver_entries
from instro.lib.transports.visa import TimeoutConfig, VisaConfig, VisaDriver, _open_resource_manager


@dataclasses.dataclass(frozen=True)
class IdnFields:
    """The four comma-separated fields of an IEEE 488.2 ``*IDN?`` reply; missing fields are ``""``."""

    manufacturer: str
    model: str
    serial: str
    firmware: str


@dataclasses.dataclass(frozen=True)
class DriverMatch:
    """The registered driver an ``*IDN?`` reply maps to."""

    category: str
    driver_name: str
    num_channels: int | None


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


def parse_idn(idn: str) -> IdnFields:
    """Split an ``*IDN?`` reply into its fields, tolerating missing trailing fields."""
    fields = [f.strip() for f in idn.split(",")]
    fields += [""] * (4 - len(fields))
    return IdnFields(*fields[:4])


def match_idn(idn: str) -> DriverMatch | None:
    """Match an ``*IDN?`` reply against every registered driver's :class:`~instro.lib.registry.IdnPattern`.

    Returns the first match in registry order, or ``None`` when no in-tree driver claims the identity.
    """
    fields = parse_idn(idn)
    for category, driver_name, entry in iter_driver_entries():
        for pattern in entry.idn_patterns:
            if pattern.matches(fields.manufacturer, fields.model):
                return DriverMatch(category=category, driver_name=driver_name, num_channels=pattern.num_channels)
    return None


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
            match = match_idn(idn)
            if match is None:
                unrecognized.append(VisaUnrecognizedInstrument(resource=resource, idn=idn))
            else:
                instruments.append(
                    VisaInstrumentInfo(
                        resource=resource,
                        idn=idn,
                        category=match.category,
                        driver_class_name=match.driver_name,
                        num_channels=match.num_channels,
                    )
                )
        except Exception as e:
            errors.append(VisaScanError(resource=resource, message=str(e), hint=_classify_error_hint(e)))
        finally:
            driver.close()

    return VisaScanResult(instruments=instruments, unrecognized=unrecognized, errors=errors)
