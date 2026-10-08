from collections.abc import Sequence

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from instro.lib.discover import DEFAULT_SOURCES, DiscoveryReport, SerialPortInfo, describe_serial_config
from instro.lib.discover import discover as discover_instruments
from instro.lib.transports.visa import DegradedInterface, backend_diagnostics

MARK = "⟢"
GREEN = "#4ADE80"
YELLOW = "#FDE68A"
RED = "#F87171"
FOREGROUND = "#FFFFFF"
FOREGROUND_MUTED = "#A3A3A3"
FOREGROUND_ERROR = "#B91C1C"
BORDER = "#333333"


def _no_devices_panel(degraded: tuple[DegradedInterface, ...]) -> Panel:
    body = f"   [bold {FOREGROUND_ERROR}]NO DEVICES FOUND[/]"
    for interface in degraded:
        body += f"\n   [dim]{interface.describe()}[/]"
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


def discover(backend: str | None = None, sources: Sequence[str] = DEFAULT_SOURCES) -> None:
    """Scan the named sources for instruments and print a discovery table."""
    console = Console()
    width = console.width
    console.print(Panel(f"[bold {FOREGROUND}]{MARK} INSTRO — DISCOVER[/]", border_style=BORDER))

    diagnostics = backend_diagnostics(backend)
    degraded = diagnostics.degraded

    console.print("\nScanning VISA resources and serial ports ... ", style="dim")
    console.print(f"   backend: {diagnostics.label}", style="dim")
    for interface in degraded:
        console.print(f"   {interface.describe()}", style="dim")
    console.print()

    report = discover_instruments(backend=backend, sources=sources)
    for note in report.skipped:
        if not note.resource.upper().startswith("ASRL"):
            console.print(f"   {note.resource}: {note.reason}", style="dim", markup=False)  # reasons contain [extras]
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
