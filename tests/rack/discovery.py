"""Discover the rack's instruments, on top of ``instro.lib.discover``.

instro's ``scan_visa_resources()`` identifies every non-serial VISA resource by ``*IDN?`` and matches
it against the vendor registries (``match_idn()``), returning ``DiscoveredInstrument`` records that
build their own drivers and config blocks. It skips serial ports, so this module adds them:

1. Lists serial ports through the OS (pyserial) without sending anything.
2. Probes only USB serial ports by default; motherboard ports (no USB VID) are skipped because
   probing them costs a timeout each and could send bytes to non-SCPI equipment.
   ``RACK_SERIAL_PROBE_ALL=1`` probes them too; ``RACK_SERIAL_EXCLUDE=COM5,COM7`` skips ports.
3. Identifies what's behind each port by ``*IDN?``, trying ``SERIAL_BAUDS`` in order and stopping
   at the first well-formed reply, then matches it with ``match_idn()`` like any other instrument.

The report lists supported instruments, instruments that answered but have no in-tree driver,
and resources that couldn't be identified (with why).
"""

from __future__ import annotations

import dataclasses
import enum
import json
import logging
import os
import re
import warnings
from collections.abc import Iterable

from serial.tools import list_ports

from instro.lib.discover import DiscoveredInstrument, match_idn, parse_idn, scan_visa_resources
from instro.lib.transports import SerialConfig, VisaConfig
from instro.lib.transports.visa import TimeoutConfig, VisaDriver, _open_resource_manager

logger = logging.getLogger("rack.discovery")

CATEGORIES = ("psu", "dmm", "eload")
VISA_TIMEOUT_S = 2
SERIAL_TIMEOUT_S = 1.0
SERIAL_BAUDS = (9600, 19200, 38400, 57600, 115200)


@dataclasses.dataclass(frozen=True)
class SerialPortInfo:
    device: str
    usb_vid: int | None
    usb_pid: int | None
    adapter_serial: str | None
    description: str

    @property
    def is_usb(self) -> bool:
        return self.usb_vid is not None


@dataclasses.dataclass(frozen=True)
class UnsupportedInstrument:
    """Answered *IDN?, but no in-tree driver matches it."""

    resource: str
    idn: str
    baud_rate: int | None


@dataclasses.dataclass(frozen=True)
class UnreachableResource:
    """Couldn't be identified: the probe failed, timed out, or the port was skipped."""

    resource: str
    reason: str


@dataclasses.dataclass(frozen=True)
class DiscoveryReport:
    instruments: list[DiscoveredInstrument]
    unsupported: list[UnsupportedInstrument]
    unreachable: list[UnreachableResource]


def model_of(instrument: DiscoveredInstrument) -> str:
    """The model field of the instrument's ``*IDN?`` reply, e.g. ``"DP832A"``."""
    return parse_idn(instrument.idn).model


def to_json(report: DiscoveryReport) -> str:
    """The report as JSON; enums (in serial settings) are written by value."""

    def default(value: object) -> object:
        return value.value if isinstance(value, enum.Enum) else str(value)

    return json.dumps(dataclasses.asdict(report), indent=2, default=default)


def _looks_like_idn(reply: str) -> bool:
    """Wrong-baud replies are garbage or empty; a real *IDN? has printable text and 3+ commas-separated fields."""
    return reply.isprintable() and len([f for f in reply.split(",") if f.strip()]) >= 3


def _asrl_resource(device: str) -> str:
    com = re.fullmatch(r"COM(\d+)", device, re.IGNORECASE)
    return f"ASRL{com.group(1)}::INSTR" if com else f"ASRL{device}::INSTR"


def list_serial_ports() -> dict[str, SerialPortInfo]:
    """VISA resource -> port info for every serial port the OS knows about. Sends nothing."""
    ports = {}
    for p in list_ports.comports():
        ports[_asrl_resource(p.device)] = SerialPortInfo(
            device=p.device,
            usb_vid=p.vid,
            usb_pid=p.pid,
            adapter_serial=p.serial_number,
            description=p.description,
        )
    return ports


def _query_idn(config: VisaConfig) -> str:
    driver = VisaDriver(config)
    try:
        driver.open()
        return driver.query("*IDN?").strip()
    finally:
        driver.close()


def _identify_serial(resource: str, backend: str) -> tuple[str, int]:
    """(*IDN? reply, baud rate) at the first baud that answers intelligibly; raises with every attempt otherwise."""
    failures: dict[str, list[int]] = {}
    for baud in SERIAL_BAUDS:
        config = VisaConfig(
            visa_resource=resource,
            visa_backend=backend,
            serial_config=SerialConfig(baud_rate=baud),
            timeout=TimeoutConfig(recv=SERIAL_TIMEOUT_S),
        )
        try:
            reply = _query_idn(config)
        except Exception as exc:
            failures.setdefault(f"{type(exc).__name__}: {exc}", []).append(baud)
            continue
        if _looks_like_idn(reply):
            return reply, baud
        failures.setdefault(f"unreadable reply {reply[:20]!r}", []).append(baud)
    summary = "; ".join(f"{reason} (baud {', '.join(map(str, bauds))})" for reason, bauds in failures.items())
    raise RuntimeError(f"no *IDN? reply: {summary}")


def _serial_skip_reason(resource: str, port: SerialPortInfo | None) -> str | None:
    excluded = {e.strip().upper() for e in os.environ.get("RACK_SERIAL_EXCLUDE", "").split(",") if e.strip()}
    if resource.upper() in excluded or (port is not None and port.device.upper() in excluded):
        return "excluded by RACK_SERIAL_EXCLUDE"
    if os.environ.get("RACK_SERIAL_PROBE_ALL") != "1" and (port is None or not port.is_usb):
        name = f"{port.device} ({port.description})" if port else "serial port"
        return f"{name} is not a USB serial port; skipped (set RACK_SERIAL_PROBE_ALL=1 to probe)"
    return None


def _record(
    resource: str, idn: str, report: DiscoveryReport, serial_config: SerialConfig | None = None
) -> DiscoveredInstrument | None:
    """Match ``idn`` and file the result as supported or unsupported."""
    match = match_idn(idn)
    baud = serial_config.baud_rate if serial_config is not None else None
    at_baud = f"  (@ {baud} baud)" if baud else ""
    if match is None:
        logger.warning("        *IDN? -> %s%s  [no in-tree driver]", idn, at_baud)
        report.unsupported.append(UnsupportedInstrument(resource, idn, baud))
        return None
    logger.info("        *IDN? -> %s%s  [%s %s]", idn, at_baud, match.category, match.driver_name)
    instrument = DiscoveredInstrument(
        resource=resource,
        idn=idn,
        category=match.category,
        driver_name=match.driver_name,
        num_channels=match.num_channels,
        serial_config=serial_config,
    )
    report.instruments.append(instrument)
    return instrument


def discover(extra_resources: Iterable[str] = ()) -> DiscoveryReport:
    """Identify every VISA resource and OS serial port, plus ``extra_resources`` (e.g. LAN addresses VISA doesn't list)."""
    report = DiscoveryReport(instruments=[], unsupported=[], unreachable=[])

    scan = scan_visa_resources()
    logger.info(
        "VISA scan (non-serial): %d resource(s) answered or failed",
        len(scan.instruments) + len(scan.unrecognized) + len(scan.errors),
    )
    for found in scan.instruments:
        logger.info("  probe %-45s", found.resource)
        logger.info("        *IDN? -> %s  [%s %s]", found.idn, found.category, found.driver_name)
        report.instruments.append(found)
    for other in scan.unrecognized:
        logger.info("  probe %-45s", other.resource)
        logger.warning("        *IDN? -> %s  [no in-tree driver]", other.idn)
        report.unsupported.append(UnsupportedInstrument(other.resource, other.idn, None))
    for error in scan.errors:
        reason = f"{error.message} ({error.hint})" if error.hint else error.message
        logger.warning("  probe %-45s unreachable: %s", error.resource, reason)
        report.unreachable.append(UnreachableResource(error.resource, reason))

    rm, backend, _ = _open_resource_manager(None)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        listed = set(rm.list_resources())
    serial_ports = list_serial_ports()
    logger.info(
        "OS serial ports: %s",
        ", ".join(f"{p.device} ({p.description})" for p in serial_ports.values()) or "none",
    )
    serial_resources = {r for r in listed | set(serial_ports) | set(extra_resources) if r.startswith("ASRL")}
    for resource in sorted(serial_resources):
        if (skip := _serial_skip_reason(resource, serial_ports.get(resource))) is not None:
            logger.info("  skip  %-45s %s", resource, skip)
            report.unreachable.append(UnreachableResource(resource, skip))
            continue
        logger.info("  probe %-45s serial, bauds %s", resource, SERIAL_BAUDS)
        try:
            idn, baud = _identify_serial(resource, backend)
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            logger.warning("        unreachable: %s", reason)
            report.unreachable.append(UnreachableResource(resource, reason))
            continue
        _record(resource, idn, report, SerialConfig(baud_rate=baud))

    for resource in sorted(r for r in set(extra_resources) - listed if not r.startswith("ASRL")):
        logger.info("  probe %-45s (not listed by VISA)", resource)
        try:
            idn = _query_idn(VisaConfig(visa_resource=resource, timeout=TimeoutConfig(recv=VISA_TIMEOUT_S)))
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            logger.warning("        unreachable: %s", reason)
            report.unreachable.append(UnreachableResource(resource, reason))
            continue
        _record(resource, idn, report)

    logger.info(
        "Discovered %d supported instrument(s), %d unsupported, %d unreachable",
        len(report.instruments),
        len(report.unsupported),
        len(report.unreachable),
    )
    return report


def main() -> int:
    """Run discovery standalone; ``-v`` adds pyvisa/instro DEBUG logs, ``--json`` prints the report."""
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-v", "--verbose", action="store_true", help="also show DEBUG logs from pyvisa and instro")
    parser.add_argument("--json", action="store_true", help="print the full report as JSON")
    parser.add_argument("resources", nargs="*", help="extra VISA resources to probe, e.g. LAN addresses")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s.%(msecs)03d %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logger.setLevel(logging.INFO)

    report = discover(args.resources)
    if args.json:
        print(to_json(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
