"""Instrument discovery: enumerate candidates, identify each one, match it to a registered driver.

The layers are usable on their own and the CLI is one consumer of them:

* :func:`list_serial_ports` and :func:`enumerate_candidates` list what could be probed. They send nothing.
* :func:`identify` runs one ``*IDN?`` probe; a serial port is tried once at the default serial settings.
* :func:`match_idn` is the pure lookup against the vendor registries (:mod:`instro.lib.registry`).
* :class:`DiscoveryProvider` is a source of discovery records, selected by name through
  :data:`SOURCES`. :class:`VisaProvider` covers SCPI over VISA (USB, LAN, GPIB, serial) and is the
  default; vendor-SDK sources for DAQs register under their own names so callers opt in to them.
* :func:`discover` runs the selected providers and assembles a :class:`DiscoveryReport`.
"""

from __future__ import annotations

import dataclasses
import enum
import re
import warnings
from collections.abc import Callable, Iterable, Sequence
from typing import Any, Literal, Protocol

import pyvisa
from serial.tools import list_ports

from instro.lib.registry import driver_registry, iter_driver_entries
from instro.lib.transports.visa import (
    ControlFlow,
    SerialConfig,
    TimeoutConfig,
    VisaConfig,
    VisaDriver,
    _open_resource_manager,
)

VISA_TRANSPORT = "visa"
DEFAULT_TIMEOUT_S = 2

SerialProbePolicy = Literal["usb", "all", "none"]


# --- identity -----------------------------------------------------------------------------------


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


# --- records ------------------------------------------------------------------------------------


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

        report = discover()
        psu = report.by_category("psu")[0]
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


def _serial_config_block(serial: SerialConfig) -> dict[str, Any]:
    """``SerialConfig`` as the JSON config schema spells it: every field, enums by value."""
    block: dict[str, Any] = {}
    for field in dataclasses.fields(serial):
        value = getattr(serial, field.name)
        block[field.name] = value.value if isinstance(value, enum.Enum) else value
    return block


def describe_serial_config(serial: SerialConfig) -> str:
    """Short human form, e.g. ``9600 baud, 8N1, no flow control``."""
    stop = {1: "1", 1.5: "1.5", 2: "2"}[serial.stop_bits.value]
    flow = "no flow control" if serial.flow_control == ControlFlow.NONE else f"{serial.flow_control.name} flow control"
    return f"{serial.baud_rate} baud, {serial.data_bits}{serial.parity.value}{stop}, {flow}"


@dataclasses.dataclass(frozen=True)
class UnrecognizedInstrument:
    """Answered the identity query, but no registered driver claims the identity."""

    resource: str
    idn: str
    serial_config: SerialConfig | None = None


@dataclasses.dataclass(frozen=True)
class ScanError:
    """A resource that was probed but could not be identified: the probe failed or timed out.

    ``message`` is the raw ``str(exc)`` so headless callers get the unfiltered cause; ``hint`` is
    an actionable interpretation for the known-bad cases, which the CLI prefers when present.
    """

    resource: str
    message: str
    hint: str | None = None


@dataclasses.dataclass(frozen=True)
class SkippedResource:
    """A candidate discovery chose not to probe (excluded, or a serial port the policy rules out), and why."""

    resource: str
    reason: str


@dataclasses.dataclass(frozen=True)
class SerialPortInfo:
    """A serial port as the OS reports it. The identity is the USB adapter's, not the instrument's."""

    device: str
    resource: str
    description: str
    manufacturer: str | None = None
    product: str | None = None
    usb_vid: int | None = None
    usb_pid: int | None = None
    adapter_serial: str | None = None

    @property
    def is_usb(self) -> bool:
        """True for a USB serial adapter; motherboard ports report no VID."""
        return self.usb_vid is not None


@dataclasses.dataclass(frozen=True)
class Candidate:
    """A resource discovery may probe, and where it came from."""

    resource: str
    source: Literal["visa", "serial", "extra"]
    port: SerialPortInfo | None = None

    @property
    def is_serial(self) -> bool:
        return self.resource.upper().startswith("ASRL")


@dataclasses.dataclass(frozen=True)
class Identity:
    """Result of :func:`identify`: the reply and, for a serial port, the settings it answered with."""

    idn: str
    serial_config: SerialConfig | None = None


class IdentifyError(RuntimeError):
    """A serial port answered, but not with anything that reads as an ``*IDN?`` reply."""


@dataclasses.dataclass(frozen=True)
class DiscoveryEvent:
    """Progress notification passed to ``on_event`` as discovery runs."""

    status: Literal["probing", "found", "unrecognized", "unreachable", "skipped"]
    resource: str
    detail: str = ""


Record = DiscoveredInstrument | UnrecognizedInstrument | ScanError | SkippedResource
EmitFn = Callable[[DiscoveryEvent], None]


@dataclasses.dataclass(frozen=True)
class DiscoveryOptions:
    """Knobs shared by every provider. ``discover()`` builds one from its keyword arguments.

    Attributes:
        backend: pyvisa backend, or ``None`` for the default (``@ivi`` with ``@py`` fallback).
        timeout: Seconds to wait for each identity reply.
        extra_resources: Addresses VISA does not enumerate but should be probed, e.g. LAN sockets.
        exclude: Resources or serial device names (case-insensitive) never to probe.
        probe_serial: ``"usb"`` probes USB serial adapters only (motherboard ports cost a timeout each
            and may front non-SCPI equipment), ``"all"`` probes every port, ``"none"`` skips serial.
        serial_config: The one serial configuration every serial port is probed with. A port that does
            not answer at it is reported as an error asking for manual serial settings; discovery does
            not sweep baud rates or framings.
        serial_ports: The OS serial ports, enumerated once per scan so providers don't repeat it.
    """

    backend: str | None = None
    timeout: int = DEFAULT_TIMEOUT_S
    extra_resources: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    probe_serial: SerialProbePolicy = "usb"
    serial_config: SerialConfig = dataclasses.field(default_factory=SerialConfig)
    serial_ports: tuple[SerialPortInfo, ...] = ()
    """The OS serial ports, enumerated once by :func:`discover` and shared with every provider."""

    def is_excluded(self, resource: str, port: SerialPortInfo | None = None) -> bool:
        excluded = {e.upper() for e in self.exclude}
        return resource.upper() in excluded or (port is not None and port.device.upper() in excluded)


class DiscoveryProvider(Protocol):
    """A source of discovery records.

    :func:`discover` runs the providers named by its ``sources`` argument, looked up in
    :data:`SOURCES`. A provider yields :class:`DiscoveredInstrument` records with its own
    ``transport`` token and a ``resource`` the driver constructor accepts,
    :class:`UnrecognizedInstrument` for devices it saw but has no driver for, :class:`ScanError` for
    probes that failed, and :class:`SkippedResource` for devices it chose not to probe, calling
    ``emit`` before each probe so CLIs can show progress. Pass ``providers=`` to :func:`discover` to
    run a specific set, which is how tests substitute one.
    """

    name: str

    def discover(self, options: DiscoveryOptions, emit: EmitFn) -> Iterable[Record]: ...


@dataclasses.dataclass
class DiscoveryReport:
    """Everything :func:`discover` learned, grouped by outcome."""

    instruments: list[DiscoveredInstrument]
    unrecognized: list[UnrecognizedInstrument]
    errors: list[ScanError]
    skipped: list[SkippedResource] = dataclasses.field(default_factory=list)
    serial_ports: list[SerialPortInfo] = dataclasses.field(default_factory=list)

    def by_category(self, category: str) -> list[DiscoveredInstrument]:
        """Discovered instruments of one category, in discovery order."""
        return [i for i in self.instruments if i.category == category]


# Names from the VISA-only API, kept importable.
VisaInstrumentInfo = DiscoveredInstrument
VisaUnrecognizedInstrument = UnrecognizedInstrument
VisaScanError = ScanError
VisaScanResult = DiscoveryReport


# --- enumerate ----------------------------------------------------------------------------------


def _asrl_resource(device: str) -> str:
    com = re.fullmatch(r"COM(\d+)", device, re.IGNORECASE)
    return f"ASRL{com.group(1)}::INSTR" if com else f"ASRL{device}::INSTR"


def list_serial_ports() -> list[SerialPortInfo]:
    """Every serial port the OS knows about, keyed to the VISA resource that opens it. Sends nothing."""
    ports = []
    for p in list_ports.comports():
        ports.append(
            SerialPortInfo(
                device=p.device,
                resource=_asrl_resource(p.device),
                description=p.description,
                manufacturer=p.manufacturer,
                product=p.product,
                usb_vid=p.vid,
                usb_pid=p.pid,
                adapter_serial=p.serial_number,
            )
        )
    return ports


def enumerate_candidates(
    backend: str | None = None,
    extra_resources: Iterable[str] = (),
    *,
    rm: pyvisa.ResourceManager | None = None,
    serial_ports: Sequence[SerialPortInfo] | None = None,
) -> list[Candidate]:
    """Merge VISA's resource list, the OS serial ports and ``extra_resources`` into one de-duplicated list.

    VISA resources come first, in the order the backend lists them. Nothing is opened or queried.
    """
    if rm is None:
        rm, _, _ = _open_resource_manager(backend)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        visa_resources = list(rm.list_resources())
    ports = {p.resource: p for p in (list_serial_ports() if serial_ports is None else serial_ports)}

    candidates: dict[str, Candidate] = {}
    for resource in visa_resources:
        candidates[resource] = Candidate(resource, "visa", ports.get(resource))
    for resource, port in ports.items():
        candidates.setdefault(resource, Candidate(resource, "serial", port))
    for resource in extra_resources:
        candidates.setdefault(resource, Candidate(resource, "extra", ports.get(resource)))
    return list(candidates.values())


# --- identify -----------------------------------------------------------------------------------


def _query_idn(config: VisaConfig) -> str:
    driver = VisaDriver(config)
    try:
        driver.open()
        return driver.query("*IDN?").strip()
    finally:
        driver.close()


def _looks_like_idn(reply: str) -> bool:
    """A wrong-baud reply is empty or garbage; a real ``*IDN?`` is printable with at least manufacturer and model."""
    return reply.isprintable() and len([f for f in reply.split(",") if f.strip()]) >= 2


def identify(
    resource: str,
    *,
    backend: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_S,
    serial_config: SerialConfig | None = None,
) -> Identity:
    """Query ``*IDN?`` on one resource.

    A serial (``ASRL``) resource is queried once at ``serial_config`` (the :class:`SerialConfig`
    defaults when ``None``) and the settings that answered are returned with the reply. Other
    resources are queried once with ``timeout``.

    Raises:
        IdentifyError: a serial port replied, but not with anything that reads as an ``*IDN?`` reply,
            which is what a framing or baud mismatch looks like.
        Exception: whatever the transport raised (timeout, permission denied, ...).
    """
    if not resource.upper().startswith("ASRL"):
        config = VisaConfig(visa_resource=resource, visa_backend=backend, timeout=TimeoutConfig(recv=timeout))
        return Identity(_query_idn(config))

    serial = serial_config if serial_config is not None else SerialConfig()
    config = VisaConfig(
        visa_resource=resource, visa_backend=backend, serial_config=serial, timeout=TimeoutConfig(recv=timeout)
    )
    reply = _query_idn(config)
    if not _looks_like_idn(reply):
        raise IdentifyError(f"unreadable reply {reply[:20]!r} at {describe_serial_config(serial)}")
    # Each identity owns its settings; the caller's object is probed with for every port.
    return Identity(reply, dataclasses.replace(serial))


def serial_settings_hint(serial: SerialConfig) -> str:
    """What to tell a user whose serial port did not answer at the probed settings."""
    return f"no *IDN? reply at {describe_serial_config(serial)}; set this port's serial_config manually"


def _classify_error_hint(exc: Exception) -> str | None:
    """Return an actionable hint for a known-bad scan exception, or None if there isn't one."""
    if isinstance(exc, pyvisa.errors.VisaIOError) and "SYSTEM_ERROR" in str(exc):
        return "permission denied - check udev rules"
    err_str = str(exc)
    if "No backend available" in err_str or "PyUSB" in err_str:
        return "USB backend missing - install libusb"
    return None


# --- providers ----------------------------------------------------------------------------------


class VisaProvider:
    """SCPI-over-VISA discovery: VISA resources, OS serial ports and extra addresses, identified by ``*IDN?``.

    Pass an already-open ``rm`` to reuse a resource manager a caller opened for its own diagnostics.

    Records carry ``options.backend`` as requested, not the backend it resolved to, so a default
    request stays ``None`` and the :class:`VisaConfig` built from a record keeps the fallback behavior.
    """

    name = VISA_TRANSPORT

    def __init__(self, rm: pyvisa.ResourceManager | None = None) -> None:
        self._rm = rm

    def discover(self, options: DiscoveryOptions, emit: EmitFn) -> Iterable[Record]:
        # Resolve once for probing; pyvisa caches one ResourceManager per backend, so this is free
        # even when rm was given, and it saves a failed @ivi attempt per resource on a @py bench.
        resolved_rm, backend, _ = _open_resource_manager(options.backend)
        rm = self._rm if self._rm is not None else resolved_rm

        for candidate in enumerate_candidates(
            backend, options.extra_resources, rm=rm, serial_ports=options.serial_ports
        ):
            reason = _skip_reason(candidate, options)
            if reason is not None:
                yield SkippedResource(candidate.resource, reason)
                continue
            detail = f"serial, {describe_serial_config(options.serial_config)}" if candidate.is_serial else ""
            emit(DiscoveryEvent("probing", candidate.resource, detail))
            try:
                identity = identify(
                    candidate.resource, backend=backend, timeout=options.timeout, serial_config=options.serial_config
                )
            except Exception as exc:
                hint = _classify_error_hint(exc)
                if hint is None and candidate.is_serial:
                    hint = serial_settings_hint(options.serial_config)
                yield ScanError(candidate.resource, str(exc), hint)
                continue
            match = match_idn(identity.idn)
            if match is None:
                yield UnrecognizedInstrument(candidate.resource, identity.idn, identity.serial_config)
                continue
            yield DiscoveredInstrument(
                resource=candidate.resource,
                idn=identity.idn,
                category=match.category,
                driver_name=match.driver_name,
                num_channels=match.num_channels,
                backend=options.backend,
                serial_config=identity.serial_config,
            )


def _skip_reason(candidate: Candidate, options: DiscoveryOptions) -> str | None:
    if options.is_excluded(candidate.resource, candidate.port):
        return "excluded"
    if not candidate.is_serial:
        return None
    if options.probe_serial == "none":
        return "serial probing disabled"
    if options.probe_serial == "usb" and (candidate.port is None or not candidate.port.is_usb):
        name = f"{candidate.port.device} ({candidate.port.description})" if candidate.port else candidate.resource
        return f"{name} is not a USB serial port and was not probed"
    return None


SOURCES: dict[str, Callable[[], DiscoveryProvider]] = {VISA_TRANSPORT: VisaProvider}
"""Discovery sources by name: what ``discover(sources=...)`` and ``instro discover --source`` can run.

``"visa"`` is the built-in SCPI-over-VISA scan, which also covers serial ports. Packages that discover
through a vendor SDK add their own entry so callers opt in by name; ``"all"`` selects every entry.
"""

DEFAULT_SOURCES: tuple[str, ...] = (VISA_TRANSPORT,)


def providers_for(sources: Iterable[str]) -> list[DiscoveryProvider]:
    """Instantiate the providers named in ``sources`` (``"all"`` expands to every :data:`SOURCES` entry), in order and without duplicates.

    Raises:
        ValueError: a name is not a known source.
    """
    names = list(dict.fromkeys(sources))
    if "all" in names:
        names = list(SOURCES)
    unknown = [name for name in names if name not in SOURCES]
    if unknown:
        raise ValueError(f"unknown discovery source(s) {unknown}; choose from {list(SOURCES)} or 'all'")
    return [SOURCES[name]() for name in names]


# --- orchestrate --------------------------------------------------------------------------------


def discover(
    backend: str | None = None,
    *,
    extra_resources: Iterable[str] = (),
    exclude: Iterable[str] = (),
    probe_serial: SerialProbePolicy = "usb",
    timeout: int = DEFAULT_TIMEOUT_S,
    serial_config: SerialConfig | None = None,
    on_event: EmitFn | None = None,
    sources: Iterable[str] = DEFAULT_SOURCES,
    providers: Sequence[DiscoveryProvider] | None = None,
) -> DiscoveryReport:
    """Run the selected discovery providers and return what was found.

    Args:
        backend: pyvisa backend, e.g. ``"@py"``; ``None`` uses ``@ivi`` and falls back to ``@py``.
        extra_resources: Addresses to probe that VISA does not list, e.g. ``TCPIP0::10.0.0.5::5025::SOCKET``.
        exclude: Resources or serial device names never to probe.
        probe_serial: ``"usb"`` (default) probes USB serial adapters only, ``"all"`` every port, ``"none"`` skips serial.
        timeout: Seconds per identity query.
        serial_config: Serial settings every serial port is probed with; ``None`` means the
            :class:`SerialConfig` defaults (9600 baud, 8N1, no flow control). There is no sweep: a
            port that does not answer lands in ``errors`` with a hint to configure it manually.
        on_event: Called with a :class:`DiscoveryEvent` before each probe and after each outcome.
        sources: Names from :data:`SOURCES` to run, default VISA only (which includes serial ports);
            ``"all"`` runs every registered source.
        providers: Explicit providers to run instead of ``sources``; how tests substitute one.

    Example::

        from instro.lib.discover import discover

        report = discover(extra_resources=["TCPIP0::10.0.0.5::5025::SOCKET"])
        for found in report.instruments:
            print(found.resource, found.category, found.driver_name)
    """
    serial_ports = list_serial_ports()
    options = DiscoveryOptions(
        backend=backend,
        timeout=timeout,
        extra_resources=tuple(extra_resources),
        exclude=tuple(exclude),
        probe_serial=probe_serial,
        serial_config=serial_config if serial_config is not None else SerialConfig(),
        serial_ports=tuple(serial_ports),
    )
    emit: EmitFn = on_event if on_event is not None else (lambda event: None)
    report = DiscoveryReport(instruments=[], unrecognized=[], errors=[], serial_ports=serial_ports)

    for provider in providers_for(sources) if providers is None else providers:
        for record in provider.discover(options, emit):
            if isinstance(record, DiscoveredInstrument):
                report.instruments.append(record)
                emit(DiscoveryEvent("found", record.resource, f"{record.category} {record.driver_name}"))
            elif isinstance(record, UnrecognizedInstrument):
                report.unrecognized.append(record)
                emit(DiscoveryEvent("unrecognized", record.resource, record.idn))
            elif isinstance(record, ScanError):
                report.errors.append(record)
                emit(DiscoveryEvent("unreachable", record.resource, record.hint or record.message))
            else:
                report.skipped.append(record)
                emit(DiscoveryEvent("skipped", record.resource, record.reason))
    return report


def scan_visa_resources(
    backend: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_S,
    *,
    rm: pyvisa.ResourceManager | None = None,
) -> DiscoveryReport:
    """VISA-only discovery with serial ports skipped: the pre-provider API, now a wrapper over :func:`discover`.

    Pass an already-open ``rm`` with the ``backend`` it was opened under to reuse it.
    """
    return discover(backend=backend, timeout=timeout, probe_serial="none", providers=[VisaProvider(rm=rm)])
