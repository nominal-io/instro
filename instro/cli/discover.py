import warnings

import pyvisa
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from serial.tools import list_ports

from instro.lib.transports.visa import TimeoutConfig, VisaConfig, VisaDriver

MARK = "⟢"
GREEN = "#4ADE80"
YELLOW = "#FDE68A"
FOREGROUND = "#FFFFFF"
FOREGROUND_MUTED = "#A3A3A3"
FOREGROUND_ERROR = "#B91C1C"
BORDER = "#333333"


_IDN_MAP = {
    ("AGILENT TECHNOLOGIES", "34401A"): ("dmm", "Agilent34401A"),
    ("HEWLETT-PACKARD", "34401A"): ("dmm", "Agilent34401A"),
    ("KEITHLEY INSTRUMENTS", "2400"): ("dmm", "Keithley2400"),
    ("B&K PRECISION", "9115"): ("psu", "BK9115"),
    ("B&K PRECISION", "9140"): ("psu", "BK914X"),
    ("RIGOL TECHNOLOGIES", "DP811"): ("psu", "RigolDP800"),
    ("RIGOL TECHNOLOGIES", "DP821"): ("psu", "RigolDP800"),
    ("RIGOL TECHNOLOGIES", "DP831"): ("psu", "RigolDP800"),
    ("RIGOL TECHNOLOGIES", "DP832"): ("psu", "RigolDP800"),
    ("SIGLENT TECHNOLOGIES", "SPD3303"): ("psu", "SiglentSPD3303"),
    ("B&K PRECISION", "BK85"): ("eload", "BK85XXB"),
}


def discover(backend: str | None = None) -> None:
    """Scan for SCPI devices and print a discovery table."""
    console = Console()
    width = console.width
    console.print(Panel(f"[bold {FOREGROUND}]{MARK} INSTRO — DISCOVER[/]", border_style=BORDER))
    console.print("\nScanning VISA resources ... \n", style="dim")
    active_backend: str

    serial_devices = [
        ((p.device, p.manufacturer, p.product), "serial - configure manually")
        for p in list_ports.comports()
        if p.description != "n/a"
    ]

    if backend is not None:
        rm = pyvisa.ResourceManager(backend)
        active_backend = backend
    else:
        try:
            rm = pyvisa.ResourceManager("@ivi")
            active_backend = "@ivi"
        except Exception:
            rm = pyvisa.ResourceManager("@py")
            active_backend = "@py"

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        resources = rm.list_resources()

    supported_devices: list[tuple[str, str, tuple[str, str]]] = []
    unsupported_devices: list[tuple[str, str]] = []

    if not resources and not serial_devices:
        console.print(Panel(f"   [bold {FOREGROUND_ERROR}]NO DEVICES FOUND[/]", border_style=FOREGROUND_ERROR))
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
        console.print(Panel(f"   [bold {FOREGROUND_ERROR}]NO DEVICES FOUND[/]", border_style=FOREGROUND_ERROR))
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
