"""Discover the rack's instruments with ``instro.lib.discover.discover()``.

instro enumerates VISA resources and OS serial ports, identifies each by ``*IDN?`` (serial ports once,
at 9600 baud 8N1 unless told otherwise), and matches the reply against the vendor registries. This
module maps the rack's environment knobs onto it and logs progress:

- ``RACK_PSU_RESOURCE`` / ``RACK_DMM_RESOURCE`` / ``RACK_ELOAD_RESOURCE`` pin a category to one
  resource; pinned resources are also probed when VISA doesn't list them (e.g. LAN sockets).
- ``RACK_SERIAL_PROBE_ALL=1`` probes motherboard serial ports too (default: USB adapters only).
- ``RACK_SERIAL_EXCLUDE=COM5,ASRL7::INSTR`` never probes those ports.

A serial instrument that isn't at 9600 baud 8N1 lands in ``errors`` with a hint to set its
``serial_config`` manually; discovery doesn't sweep baud rates.
"""

from __future__ import annotations

import dataclasses
import enum
import json
import logging
import os
from collections.abc import Iterable

from instro.lib.discover import DiscoveredInstrument, DiscoveryEvent, DiscoveryReport, discover, parse_idn

logger = logging.getLogger("rack.discovery")

CATEGORIES = ("psu", "dmm", "eload")


def pinned_resource(category: str) -> str | None:
    return os.environ.get(f"RACK_{category.upper()}_RESOURCE")


def serial_excludes() -> list[str]:
    return [e.strip() for e in os.environ.get("RACK_SERIAL_EXCLUDE", "").split(",") if e.strip()]


def model_of(instrument: DiscoveredInstrument) -> str:
    """The model field of the instrument's ``*IDN?`` reply, e.g. ``"DP832A"``."""
    return parse_idn(instrument.idn).model


def to_json(report: DiscoveryReport) -> str:
    """The report as JSON; enums (in serial settings) are written by value."""

    def default(value: object) -> object:
        return value.value if isinstance(value, enum.Enum) else str(value)

    return json.dumps(dataclasses.asdict(report), indent=2, default=default)


def _log_event(event: DiscoveryEvent) -> None:
    level = logging.WARNING if event.status in ("unrecognized", "unreachable") else logging.INFO
    logger.log(level, "  %-12s %-45s %s", event.status, event.resource, event.detail)


def discover_rack(extra_resources: Iterable[str] = ()) -> DiscoveryReport:
    """Run instro discovery with the rack's pins, serial policy, and excludes applied."""
    pinned = [r for c in CATEGORIES if (r := pinned_resource(c))]
    report = discover(
        extra_resources=[*pinned, *extra_resources],
        exclude=serial_excludes(),
        probe_serial="all" if os.environ.get("RACK_SERIAL_PROBE_ALL") == "1" else "usb",
        on_event=_log_event,
    )
    logger.info(
        "Discovered %d supported instrument(s), %d unrecognized, %d error(s), %d skipped",
        len(report.instruments),
        len(report.unrecognized),
        len(report.errors),
        len(report.skipped),
    )
    return report


def main() -> int:
    """Run rack discovery standalone; ``-v`` adds pyvisa/instro DEBUG logs, ``--json`` prints the report."""
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

    report = discover_rack(args.resources)
    if args.json:
        print(to_json(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
