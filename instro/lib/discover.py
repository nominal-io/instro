"""VISA instrument discovery: scan resources, query identity, match to registered drivers."""

from __future__ import annotations

import dataclasses
import enum
import warnings
from typing import Any

import pyvisa

from instro.lib.registry import driver_registry, iter_driver_entries
from instro.lib.transports.visa import SerialConfig, TimeoutConfig, VisaConfig, VisaDriver, _open_resource_manager

VISA_TRANSPORT = "visa"


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


@dataclasses.dataclass(frozen=True)
class DiscoveredInstrument:
    """An instrument that discovery identified and matched to a registered driver.

    The record is enough to construct the driver (:meth:`make_driver`) or to emit the ``driver``
    block of an instro JSON config (:meth:`config_block`), so scripts and fixtures don't re-derive
    either from the resource string.

    Attributes:
        resource: Address the driver opens: a VISA resource string, or for other transports whatever
            the driver's constructor takes (an NI-DAQmx device name, an MCC serial number, ...).
        idn: Identity reply the match was made from (the raw ``*IDN?`` string for SCPI instruments).
        category: Instrument category (``"psu"``, ``"dmm"``, ``"daq"``, ...).
        driver_name: Key of the matched driver in the category's registry
            (:func:`~instro.lib.registry.driver_registry`), e.g. ``"BK9115"``.
        num_channels: Programmable channel count where the category tracks it, else ``None``.
        transport: ``"visa"`` for SCPI-over-VISA instruments. Discovery providers for vendor SDKs
            set their own token; only ``"visa"`` records can build a :class:`VisaConfig`.
        backend: pyvisa backend the scan was asked for (``None`` for the default with fallback), so
            :meth:`visa_config` resolves it the same way the scan did.
        serial_config: Serial settings a serial (``ASRL``) instrument answered with, else ``None``.

    Example::

        report = scan_visa_resources()
        psu = next(i for i in report.instruments if i.category == "psu")
        instro_psu = InstroPSU(name="psu", driver=psu.make_driver(), num_channels=psu.num_channels)
    """

    resource: str
    idn: str
    category: str
    driver_name: str
    num_channels: int | None = None
    transport: str = VISA_TRANSPORT
    backend: str | None = None
    serial_config: SerialConfig | None = None

    # Identified by `resource`, not used as a set member or dict key; declaring this keeps a
    # record with a (mutable, unhashable) SerialConfig behaving like one without.
    __hash__ = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        """Copy ``serial_config`` so the record owns it; the caller may keep mutating the object it passed in."""
        if self.serial_config is not None:
            object.__setattr__(self, "serial_config", dataclasses.replace(self.serial_config))

    @property
    def driver_class_name(self) -> str:
        """Deprecated alias of :attr:`driver_name`."""
        return self.driver_name

    def visa_config(self) -> VisaConfig:
        """The :class:`VisaConfig` that reaches this instrument, carrying the backend and serial settings it answered with.

        Raises:
            ValueError: the instrument is not on the VISA transport.
        """
        if self.transport != VISA_TRANSPORT:
            raise ValueError(f"{self.resource} is on the {self.transport!r} transport, not VISA")
        # A copy, so adjusting the returned config cannot reach back into this frozen record.
        serial = dataclasses.replace(self.serial_config) if self.serial_config is not None else SerialConfig()
        return VisaConfig(visa_resource=self.resource, visa_backend=self.backend, serial_config=serial)

    def driver_class(self) -> type:
        """The matched driver class, loaded from the category's driver registry on demand.

        Raises:
            KeyError: ``driver_name`` is not registered for ``category``.
        """
        try:
            entry = driver_registry(self.category)[self.driver_name]
        except KeyError:
            raise KeyError(f"no driver named {self.driver_name!r} registered for category {self.category!r}") from None
        return entry.load()

    def make_driver(self) -> Any:
        """A new, unopened driver for this instrument.

        VISA instruments get a :class:`VisaConfig`; drivers on other transports receive
        :attr:`resource` directly, which is the ``device_id`` convention the DAQ vendor drivers use.
        """
        cls = self.driver_class()
        if self.transport == VISA_TRANSPORT:
            return cls(self.visa_config())
        return cls(self.resource)

    def config_block(self) -> dict[str, Any]:
        """The ``driver`` block of an instro JSON config for this instrument.

        ``num_channels`` is included when discovery knows it, which is exactly the set of categories
        whose driver config requires it (psu, scope, awg).

        Raises:
            ValueError: the instrument is not on the VISA transport, which is the only one with a
                JSON config schema today.
        """
        if self.transport != VISA_TRANSPORT:
            raise ValueError(f"no JSON config schema for the {self.transport!r} transport")
        visa: dict[str, Any] = {"visa_resource": self.resource}
        if self.backend is not None:
            visa["visa_backend"] = self.backend
        if self.serial_config is not None:
            visa["serial_config"] = _serial_config_block(self.serial_config)
        block: dict[str, Any] = {"name": self.driver_name, "visa": visa}
        if self.num_channels is not None:
            block["num_channels"] = self.num_channels
        return block


VisaInstrumentInfo = DiscoveredInstrument
"""Deprecated alias of :class:`DiscoveredInstrument`."""


def _serial_config_block(serial: SerialConfig) -> dict[str, Any]:
    """``SerialConfig`` as the JSON config schema spells it: every field, enums by value."""
    block: dict[str, Any] = {}
    for field in dataclasses.fields(serial):
        value = getattr(serial, field.name)
        block[field.name] = value.value if isinstance(value, enum.Enum) else value
    return block


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
    instruments: list[DiscoveredInstrument]
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

    Pass an already-open ``rm`` (e.g. one a caller opened for its own backend diagnostics) to reuse
    it instead of opening a second one. ``backend`` is always the backend the caller *asked for*:
    ``None`` for the default with ``@py`` fallback, or an explicit specifier.

    Each result records that requested ``backend``, not the one it resolved to, so a ``None`` stays
    ``None`` and the :class:`VisaConfig` and config block built from the result keep the
    default-then-fallback behavior instead of pinning whichever backend this machine happened to use.
    """
    # pyvisa caches one ResourceManager per backend, so resolving here is free even when rm is given.
    resolved_rm, active_backend, _ = _open_resource_manager(backend)
    if rm is None:
        rm = resolved_rm

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        resources = rm.list_resources()

    instruments: list[DiscoveredInstrument] = []
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
                    DiscoveredInstrument(
                        resource=resource,
                        idn=idn,
                        category=match.category,
                        driver_name=match.driver_name,
                        num_channels=match.num_channels,
                        backend=backend,
                    )
                )
        except Exception as e:
            errors.append(VisaScanError(resource=resource, message=str(e), hint=_classify_error_hint(e)))
        finally:
            driver.close()

    return VisaScanResult(instruments=instruments, unrecognized=unrecognized, errors=errors)
