import pyvisa
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from serial.tools import list_ports

from instro.lib.discover import scan_visa_resources
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


def discover(backend: str | None = None) -> None:
    """Scan for SCPI devices and print a discovery table."""
    console = Console()
    width = console.width
    console.print(Panel(f"[bold {FOREGROUND}]{MARK} INSTRO — DISCOVER[/]", border_style=BORDER))

    rm, active_backend, used_py_fallback = _open_resource_manager(backend)
    degraded = _degraded_interfaces(rm) if active_backend == "@py" else []

    console.print("\nScanning VISA resources ... ", style="dim")
    console.print(f"   backend: {_backend_label(active_backend, used_py_fallback)}", style="dim")
    for family, reason in degraded:
        console.print(f"   {_degraded_line(family, reason)}", style="dim")
    console.print()

    serial_devices = [
        ((p.device, p.manufacturer, p.product or "unknown"), "serial - configure manually")
        for p in list_ports.comports()
        if p.description != "n/a"
    ]

    result = scan_visa_resources(backend=active_backend, rm=rm)

    if not result.instruments and not result.unrecognized and not result.errors and not serial_devices:
        console.print(_no_devices_panel(degraded))
        return

    if result.instruments:
        table = Table(
            title=f"[bold {GREEN}]RECOGNIZED DEVICES",
            header_style=f"bold {FOREGROUND_MUTED}",
            border_style=BORDER,
            width=width,
        )
        table.add_column("Resource", style=FOREGROUND, no_wrap=False)
        table.add_column("Category", style=FOREGROUND_MUTED, no_wrap=False)
        table.add_column("Driver", style=f"bold {FOREGROUND}", no_wrap=False)
        for instrument in result.instruments:
            table.add_row(instrument.resource, instrument.category, instrument.driver_class_name)
        console.print(table)

    if serial_devices:
        table_serial = Table(
            title=f"[bold {FOREGROUND_MUTED}]SERIAL DEVICES[/]",
            border_style=BORDER,
            header_style=f"bold {FOREGROUND_MUTED}",
            width=width,
        )
        table_serial.add_column("Address", style=FOREGROUND, no_wrap=False)
        table_serial.add_column("Product", style=FOREGROUND_MUTED, no_wrap=False)
        table_serial.add_column("Message", style=FOREGROUND_MUTED, no_wrap=False)
        for serial_device in serial_devices:
            table_serial.add_row(serial_device[0][0], serial_device[0][2], serial_device[1])
        console.print(table_serial)

    if result.unrecognized:
        table_unsp = Table(
            title=f"[bold {YELLOW}]UNRECOGNIZED DEVICES[/]",
            header_style=f"bold {FOREGROUND_MUTED}",
            border_style=BORDER,
            width=width,
        )
        table_unsp.add_column("Resource", style=FOREGROUND, no_wrap=False)
        table_unsp.add_column("IDN Response", style=FOREGROUND, no_wrap=False)
        for unrecognized in result.unrecognized:
            table_unsp.add_row(unrecognized.resource, unrecognized.idn)
        console.print(table_unsp)

    if result.errors:
        table_err = Table(
            title=f"[bold {RED}]ERRORS[/]",
            header_style=f"bold {FOREGROUND_MUTED}",
            border_style=BORDER,
            width=width,
        )
        table_err.add_column("Resource", style=FOREGROUND, no_wrap=False)
        table_err.add_column("Message", style=FOREGROUND_ERROR, no_wrap=False)
        for error in result.errors:
            table_err.add_row(error.resource, error.hint or error.message)
        console.print(table_err)
