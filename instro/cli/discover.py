import warnings

import pyvisa
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from serial.tools import list_ports

from instro.lib.transports.visa import TimeoutConfig, VisaConfig, VisaDriver, _open_resource_manager

MARK = "⟢"
GREEN = "#4ADE80"
YELLOW = "#FDE68A"
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


_IDN_MAP = {
    ("AGILENT TECHNOLOGIES", "34401A"): ("dmm", "Agilent34401A"),
    ("HEWLETT-PACKARD", "34401A"): ("dmm", "Agilent34401A"),
    ("KEITHLEY INSTRUMENTS", "2400"): ("dmm", "Keithley2400"),
    ("KEYSIGHT TECHNOLOGIES", "34461A"): ("dmm", "Keysight34461A"),
    ("AGILENT TECHNOLOGIES", "34461A"): ("dmm", "Keysight34461A"),
    ("B&K PRECISION", "9115"): ("psu", "BK9115"),
    ("B&K PRECISION", "9140"): ("psu", "BK914X"),
    ("RIGOL TECHNOLOGIES", "DP811"): ("psu", "RigolDP800"),
    ("RIGOL TECHNOLOGIES", "DP821"): ("psu", "RigolDP800"),
    ("RIGOL TECHNOLOGIES", "DP831"): ("psu", "RigolDP800"),
    ("RIGOL TECHNOLOGIES", "DP832"): ("psu", "RigolDP800"),
    ("SIGLENT TECHNOLOGIES", "SPD3303"): ("psu", "SiglentSPD3303"),
    ("B&K PRECISION", "BK85"): ("eload", "BK85XXB"),
}


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

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        resources = rm.list_resources()

    supported_devices: list[tuple[str, str, tuple[str, str]]] = []
    unsupported_devices: list[tuple[str, str]] = []

    if not resources and not serial_devices:
        console.print(_no_devices_panel(degraded))
        return

    for resource in resources:
        if resource.startswith("ASRL"):
            continue

        driver = VisaDriver(
            VisaConfig(visa_resource=resource, timeout=TimeoutConfig(recv=2), visa_backend=active_backend),
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
            if match is not None:
                supported_devices.append((idn, resource, match))
            else:
                unsupported_devices.append((idn, resource))

        except pyvisa.errors.VisaIOError as e:
            msg = "permission denied - check udev rules" if "SYSTEM_ERROR" in str(e) else str(e)
            console.print(f"   [{FOREGROUND_ERROR}]{resource}: no response: ({msg})[/]")
        except Exception as e:
            err_str = str(e)
            if "No backend available" in err_str or "PyUSB" in err_str:
                msg = "USB backend missing - install libusb"
            else:
                msg = err_str
            console.print(f"   [{FOREGROUND_ERROR}]{resource}: unexpected error: ({msg})[/]")
        finally:
            driver.close()

    if not supported_devices and not unsupported_devices and not serial_devices:
        console.print(_no_devices_panel(degraded))
    else:
        if supported_devices:
            table = Table(
                title=f"[bold {GREEN}]RECOGNIZED DEVICES",
                header_style=f"bold {FOREGROUND_MUTED}",
                border_style=BORDER,
                width=width,
            )
            table.add_column("Resource", style=FOREGROUND, no_wrap=False)
            table.add_column("Category", style=FOREGROUND_MUTED, no_wrap=False)
            table.add_column("Driver", style=f"bold {FOREGROUND}", no_wrap=False)
            for supported in supported_devices:
                table.add_row(supported[1], supported[2][0], supported[2][1])
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

        if unsupported_devices:
            table_unsp = Table(
                title=f"[bold {YELLOW}]UNRECOGNIZED DEVICES[/]",
                header_style=f"bold {FOREGROUND_MUTED}",
                border_style=BORDER,
                width=width,
            )
            table_unsp.add_column("Resource", style=FOREGROUND, no_wrap=False)
            table_unsp.add_column("IDN Response", style=FOREGROUND, no_wrap=False)
            for unsupported in unsupported_devices:
                table_unsp.add_row(unsupported[1], unsupported[0])
            console.print(table_unsp)
