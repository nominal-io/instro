import pyvisa
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from instro.lib.discover import DiscoveryReport, SerialPortInfo, describe_serial_config
from instro.lib.discover import discover as discover_instruments
from instro.lib.transports.visa import _open_resource_manager

MARK = "⟢"
GREEN = "#4ADE80"
YELLOW = "#FDE68A"
RED = "#F87171"
FOREGROUND = "#FFFFFF"
FOREGROUND_MUTED = "#A3A3A3"
FOREGROUND_ERROR = "#B91C1C"
BORDER = "#333333"


_INTERFACE_HINTS = {
    "GPIB": "install NI-488.2 or linux-gpib",
    "USB": "install libusb",
    "ASRL": "install pyserial",
}

_INTERFACE_SUFFIXES = (" INSTR", " INTFC", " SOCKET", " RAW")


def _degraded_interfaces(rm: pyvisa.ResourceManager) -> list[tuple[str, str]]:
    """Return (interface family, reason) for pyvisa-py interfaces that are not Available."""
    get_debug_info = getattr(rm.visalib, "get_debug_info", None)
    if get_debug_info is None:
        return []
    degraded: dict[str, str] = {}
    for key, value in get_debug_info().items():
        if not key.endswith(_INTERFACE_SUFFIXES):
            continue
        lines = value if isinstance(value, list) else str(value).splitlines()
        reason = lines[0].strip() if lines else ""
        if reason.startswith("Available"):
            continue
        degraded.setdefault(key.split(" ", 1)[0], reason.rstrip("."))
    return sorted(degraded.items())


def _degraded_line(family: str, reason: str) -> str:
    hint = _INTERFACE_HINTS.get(family)
    suffix = f" ({hint})" if hint else ""
    return f"{family}: unavailable — {reason}{suffix}"


def _backend_label(active_backend: str, used_py_fallback: bool) -> str:
    if active_backend == "@ivi":
        return "@ivi (system IVI VISA)"
    if active_backend == "@py":
        return "@py (pyvisa-py — no IVI VISA found)" if used_py_fallback else "@py (pyvisa-py)"
    return active_backend


def _no_devices_panel(degraded: list[tuple[str, str]]) -> Panel:
    body = f"   [bold {FOREGROUND_ERROR}]NO DEVICES FOUND[/]"
    for family, reason in degraded:
        body += f"\n   [dim]{_degraded_line(family, reason)}[/]"
    return Panel(body, border_style=FOREGROUND_ERROR)


def _serial_rows(report: DiscoveryReport) -> list[tuple[str, str, str]]:
    """(address, product, message) for every serial port discovery did not identify."""
    ports: dict[str, SerialPortInfo] = {p.resource: p for p in report.serial_ports}

    def row(resource: str, message: str) -> tuple[str, str, str]:
        port = ports.get(resource)
        return (port.device if port else resource, (port.product if port else None) or "unknown", message)

    rows = [row(s.resource, s.reason) for s in report.skipped if s.resource.upper().startswith("ASRL")]
    rows += [row(e.resource, e.hint or e.message) for e in report.errors if e.resource.upper().startswith("ASRL")]
    return rows


def discover(backend: str | None = None) -> None:
    """Scan for instruments and print a discovery table."""
    console = Console()
    width = console.width
    console.print(Panel(f"[bold {FOREGROUND}]{MARK} INSTRO — DISCOVER[/]", border_style=BORDER))

    rm, active_backend, used_py_fallback = _open_resource_manager(backend)
    degraded = _degraded_interfaces(rm) if active_backend == "@py" else []

    console.print("\nScanning VISA resources and serial ports ... ", style="dim")
    console.print(f"   backend: {_backend_label(active_backend, used_py_fallback)}", style="dim")
    for family, reason in degraded:
        console.print(f"   {_degraded_line(family, reason)}", style="dim")
    console.print()

    report = discover_instruments(backend=backend)
    serial_rows = _serial_rows(report)
    other_errors = [e for e in report.errors if not e.resource.upper().startswith("ASRL")]

    if not report.instruments and not report.unrecognized and not other_errors and not serial_rows:
        console.print(_no_devices_panel(degraded))
        return

    if report.instruments:
        table = Table(
            title=f"[bold {GREEN}]RECOGNIZED DEVICES",
            header_style=f"bold {FOREGROUND_MUTED}",
            border_style=BORDER,
            width=width,
        )
        table.add_column("Resource", style=FOREGROUND, no_wrap=False)
        table.add_column("Category", style=FOREGROUND_MUTED, no_wrap=False)
        table.add_column("Driver", style=f"bold {FOREGROUND}", no_wrap=False)
        for instrument in report.instruments:
            resource = instrument.resource
            if instrument.serial_config is not None:
                resource = f"{resource} ({describe_serial_config(instrument.serial_config)})"
            table.add_row(resource, instrument.category, instrument.driver_name)
        console.print(table)

    if serial_rows:
        table_serial = Table(
            title=f"[bold {FOREGROUND_MUTED}]SERIAL PORTS NOT IDENTIFIED[/]",
            border_style=BORDER,
            header_style=f"bold {FOREGROUND_MUTED}",
            width=width,
        )
        table_serial.add_column("Address", style=FOREGROUND, no_wrap=False)
        table_serial.add_column("Product", style=FOREGROUND_MUTED, no_wrap=False)
        table_serial.add_column("Message", style=FOREGROUND_MUTED, no_wrap=False)
        for address, product, message in serial_rows:
            table_serial.add_row(address, product, message)
        console.print(table_serial)

    if report.unrecognized:
        table_unsp = Table(
            title=f"[bold {YELLOW}]UNRECOGNIZED DEVICES[/]",
            header_style=f"bold {FOREGROUND_MUTED}",
            border_style=BORDER,
            width=width,
        )
        table_unsp.add_column("Resource", style=FOREGROUND, no_wrap=False)
        table_unsp.add_column("IDN Response", style=FOREGROUND, no_wrap=False)
        for unrecognized in report.unrecognized:
            table_unsp.add_row(unrecognized.resource, unrecognized.idn)
        console.print(table_unsp)

    if other_errors:
        table_err = Table(
            title=f"[bold {RED}]ERRORS[/]",
            header_style=f"bold {FOREGROUND_MUTED}",
            border_style=BORDER,
            width=width,
        )
        table_err.add_column("Resource", style=FOREGROUND, no_wrap=False)
        table_err.add_column("Message", style=FOREGROUND_ERROR, no_wrap=False)
        for error in other_errors:
            table_err.add_row(error.resource, error.hint or error.message)
        console.print(table_err)
