"""Discover the rack's instruments: enumerate VISA and serial ports, identify each by *IDN?, match a driver.

Serial ports carry no identity of their own: the OS reports the USB-serial *adapter* (VID/PID,
adapter serial number), not the instrument behind it. So discovery:

1. Lists serial ports through the OS (pyserial) without sending anything.
2. Probes only USB serial ports by default; motherboard ports (no USB VID) are skipped because
   probing them costs a timeout each and could send bytes to non-SCPI equipment.
   ``RACK_SERIAL_PROBE_ALL=1`` probes them too; ``RACK_SERIAL_EXCLUDE=COM5,COM7`` skips ports.
3. Identifies what's behind each port by ``*IDN?``, trying ``SERIAL_BAUDS`` in order and stopping
   at the first well-formed reply.
4. Matches the reply against ``KNOWN_INSTRUMENTS``, whose driver names are the keys of instro's
   own config registries, so a discovered instrument builds the same driver a JSON config would.

The report lists supported instruments, instruments that answered but have no in-tree driver,
and resources that couldn't be identified (with why).
"""

from __future__ import annotations

import dataclasses
import importlib
import logging
import os
import re
import warnings
from collections.abc import Iterable
from typing import Any

from serial.tools import list_ports

from instro.dmm.config import DMM_VENDOR_REGISTRY
from instro.eload.config import ELOAD_VENDOR_REGISTRY
from instro.lib.transports import SerialConfig, VisaConfig
from instro.lib.transports.visa import TimeoutConfig, VisaDriver, _open_resource_manager
from instro.psu.config import PSU_VENDOR_REGISTRY

logger = logging.getLogger("rack.discovery")

CATEGORIES = ("psu", "dmm", "eload")
REGISTRIES: dict[str, dict[str, str]] = {
    "psu": PSU_VENDOR_REGISTRY,
    "dmm": DMM_VENDOR_REGISTRY,
    "eload": ELOAD_VENDOR_REGISTRY,
}
VISA_TIMEOUT_S = 2
SERIAL_TIMEOUT_S = 1.0
SERIAL_BAUDS = (9600, 19200, 38400, 57600, 115200)
# ±(fraction of reading, amperes) for a PSU's own current readback, used when comparing it to an
# independent meter. Models without a datasheet value in the registry get this conservative default.
GENERIC_CURRENT_READBACK = (0.01, 0.010)


@dataclasses.dataclass(frozen=True)
class KnownInstrument:
    """An *IDN? pattern and the in-tree driver that handles it."""

    category: str
    vendors: tuple[str, ...]  # substrings of the IDN manufacturer field, case-insensitive
    model: str  # regex matched against the IDN model field, case-insensitive
    driver_name: str  # key in the category's instro config registry
    num_channels: int | None = None
    current_readback: tuple[float, float] = GENERIC_CURRENT_READBACK


# DP832/DP832A current readback: ±(0.15% + 5 mA) on every channel (DP800 datasheet, annual, 25 °C ± 5 °C).
_DP83X_CURRENT_READBACK = (0.0015, 0.005)

KNOWN_INSTRUMENTS: tuple[KnownInstrument, ...] = (
    KnownInstrument("psu", ("RIGOL",), r"^DP811", "RigolDP800", 1),
    KnownInstrument("psu", ("RIGOL",), r"^DP821", "RigolDP800", 2),
    KnownInstrument("psu", ("RIGOL",), r"^DP83[12]", "RigolDP800", 3, _DP83X_CURRENT_READBACK),
    KnownInstrument("psu", ("SIGLENT",), r"^SPD3303", "SiglentSPD3303", 2),
    KnownInstrument("psu", ("B&K",), r"^9115", "BK9115", 1),
    KnownInstrument("psu", ("B&K",), r"^914\d", "BK914X", 3),
    KnownInstrument("psu", ("KEYSIGHT", "AGILENT"), r"^E361\d\d", "KeysightE36100", 1),
    KnownInstrument("dmm", ("KEYSIGHT", "AGILENT"), r"^34461A", "Keysight34461A"),
    KnownInstrument("dmm", ("AGILENT", "HEWLETT-PACKARD"), r"^34401A", "Agilent34401A"),
    KnownInstrument("dmm", ("KEITHLEY",), r"^2400", "Keithley2400"),
    KnownInstrument("eload", ("B&K",), r"^85\d\dB$", "BK85XXB"),
)


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
class DiscoveredInstrument:
    category: str
    resource: str
    idn: str
    driver_name: str
    num_channels: int | None
    baud_rate: int | None
    current_readback: tuple[float, float] = GENERIC_CURRENT_READBACK

    @property
    def model(self) -> str:
        return _idn_fields(self.idn)[1]

    def visa_config(self) -> VisaConfig:
        if self.baud_rate is None:
            return VisaConfig(visa_resource=self.resource)
        return VisaConfig(visa_resource=self.resource, serial_config=SerialConfig(baud_rate=self.baud_rate))

    def driver_class(self) -> type:
        module_path, _, class_name = REGISTRIES[self.category][self.driver_name].rpartition(".")
        cls: type = getattr(importlib.import_module(module_path), class_name)
        return cls

    def make_driver(self) -> Any:
        """A new, unopened driver for this instrument."""
        return self.driver_class()(self.visa_config())

    def config_driver_block(self) -> dict:
        """The ``driver`` block of an instro JSON config for this instrument."""
        visa: dict = {"visa_resource": self.resource}
        if self.baud_rate is not None:
            visa["serial_config"] = {"baud_rate": self.baud_rate}
        block: dict = {"name": self.driver_name, "visa": visa}
        if self.category == "psu":
            block["num_channels"] = self.num_channels
        return block


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


def _idn_fields(idn: str) -> list[str]:
    fields = [f.strip() for f in idn.split(",")]
    return fields + [""] * (4 - len(fields))


def _looks_like_idn(reply: str) -> bool:
    """Wrong-baud replies are garbage or empty; a real *IDN? has printable text and 3+ commas-separated fields."""
    return reply.isprintable() and len([f for f in reply.split(",") if f.strip()]) >= 3


def match_known(idn: str) -> KnownInstrument | None:
    vendor, model = (f.lower() for f in _idn_fields(idn)[:2])
    for known in KNOWN_INSTRUMENTS:
        if any(v.lower() in vendor for v in known.vendors) and re.search(known.model, model, re.IGNORECASE):
            return known
    return None


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


def _identify_visa(resource: str, backend: str) -> str:
    return _query_idn(
        VisaConfig(visa_resource=resource, visa_backend=backend, timeout=TimeoutConfig(recv=VISA_TIMEOUT_S))
    )


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


def discover(extra_resources: Iterable[str] = ()) -> DiscoveryReport:
    """Probe every VISA resource and OS serial port, plus ``extra_resources`` (e.g. LAN addresses VISA doesn't list)."""
    rm, backend, _ = _open_resource_manager(None)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        visa_resources = set(rm.list_resources())
    serial_ports = list_serial_ports()
    resources = sorted(visa_resources | set(serial_ports) | set(extra_resources))
    logger.info(
        "VISA backend %r: %d resource(s); OS serial ports: %s",
        backend,
        len(resources),
        ", ".join(f"{p.device} ({p.description})" for p in serial_ports.values()) or "none",
    )

    instruments: list[DiscoveredInstrument] = []
    unsupported: list[UnsupportedInstrument] = []
    unreachable: list[UnreachableResource] = []
    for resource in resources:
        baud: int | None = None
        try:
            if resource.startswith("ASRL"):
                if (reason := _serial_skip_reason(resource, serial_ports.get(resource))) is not None:
                    logger.info("  skip  %-45s %s", resource, reason)
                    unreachable.append(UnreachableResource(resource, reason))
                    continue
                logger.info("  probe %-45s serial, bauds %s", resource, SERIAL_BAUDS)
                idn, baud = _identify_serial(resource, backend)
            else:
                logger.info("  probe %-45s", resource)
                idn = _identify_visa(resource, backend)
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            logger.warning("        unreachable: %s", reason)
            unreachable.append(UnreachableResource(resource, reason))
            continue

        at_baud = f"  (@ {baud} baud)" if baud else ""
        known = match_known(idn)
        if known is None:
            logger.warning("        *IDN? -> %s%s  [no in-tree driver]", idn, at_baud)
            unsupported.append(UnsupportedInstrument(resource, idn, baud))
            continue
        logger.info("        *IDN? -> %s%s  [%s %s]", idn, at_baud, known.category, known.driver_name)
        instruments.append(
            DiscoveredInstrument(
                category=known.category,
                resource=resource,
                idn=idn,
                driver_name=known.driver_name,
                num_channels=known.num_channels,
                baud_rate=baud,
                current_readback=known.current_readback,
            )
        )

    logger.info(
        "Discovered %d supported instrument(s), %d unsupported, %d unreachable",
        len(instruments),
        len(unsupported),
        len(unreachable),
    )
    return DiscoveryReport(instruments=instruments, unsupported=unsupported, unreachable=unreachable)


def main() -> int:
    """Run discovery standalone; ``-v`` adds pyvisa/instro DEBUG logs, ``--json`` prints the report."""
    import argparse
    import json

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
        print(json.dumps(dataclasses.asdict(report), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
