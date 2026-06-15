import warnings

import pyvisa
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from serial.tools import list_ports

from instro.lib.transports.visa import TimeoutConfig, VisaConfig, VisaDriver

# create an instrument mapping - false positives currently likely

# should make the mapping into having company name and also having part #
# and then checking both of them! I really need more hardware tests for reliable
# usage from company names... can a dict key be a tuple?
# currently this mapping is not robust

MARK = "⟢"
# BACKGROUND = "#000000"
GREEN = "#4ADE80"
YELLOW = "#FDE68A"
SURFACE = "#0C0C0C"
FOREGROUND = "#FFFFFF"
FOREGROUND_MUTED = "#A3A3A3"
FOREGROUND_ERROR = "#B91C1C"
BORDER = "#333333"


_IDN_MAP = {
    ("AGILENT TECHNOLOGIES", "34401A"): ("dmm", "AgilentA34401A"),
    ("HEWLETT-PACKARD", "34401A"): ("dmm", "AgilentA34401A"),
    ("KEITHLEY INSTRUMENTS", "2400"): ("dmm", "Keithley2400"),  # maybe make this MODEL 2400 # OLD ONES US "HP"
    ("B&K PRECISION", "9115"): ("psu", "BK9115"),
    ("B&K PRECISION", "9140"): ("psu", "BK9140"),
    ("RIGOL TECHNOLOGIES", "DP811"): ("psu", "RIGOLDP800"),
    ("RIGOL TECHNOLOGIES", "DP821"): ("psu", "RIGOLDP800"),
    ("RIGOL TECHNOLOGIES", "DP831"): ("psu", "RIGOLDP800"),
    ("RIGOL TECHNOLOGIES", "DP832"): ("psu", "RIGOLDP800"),
    ("SIGLENT TECHNOLOGIES", "SPD3303"): ("psu", "SiglentSPD3303"),
    ("GENESYS", "GEN"): ("psu", "TDKLambdaGenesys"),  # this feels too vague
    ("B&K PRECISION", "BK85"): ("eload", "BK85xxB"),  # not sure if this works for all 85XX series...
    # ^^ added this one back in, need to tune all the names though!
}

# let's get the keys for this _IDN_MAP to be a tuple, then we can check if the vendor & device num are in the name


def discover(backend: str | None = None) -> None:
    """Function for discovering known and unknown SCPI devices with VISA."""
    console = Console()
    console.print(Panel(f"[bold {FOREGROUND}]{MARK} INSTRO — DISCOVER[/]", border_style=BORDER))
    console.print("\nScanning VISA resources ... \n", style="dim")
    # create list of real devices:
    serial_devices = [
        ((p.device, p.manufacturer, p.product), "serial - configure manually")
        for p in list_ports.comports()
        if p.description != "n/a"
    ]

    # this section of code is quite inefficient
    if backend:
        rm = pyvisa.ResourceManager(backend)

    else:
        try:
            # automatically chooses a backend
            rm = pyvisa.ResourceManager("@ivi")
        except Exception:
            rm = pyvisa.ResourceManager("@py")

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        resources = rm.list_resources()

    supported_devices: list[tuple[str, str, tuple[str, str]]] = []  # put all found VISA devices here
    unsupported_devices: list[str] = []  # put all found non-supported devices here

    # for each resource, open a visadriver, query *IDN?, then close it!, wrapped in try/except
    if not resources:
        console.print(Panel(f"   [bold {FOREGROUND_ERROR}]NO DEVICES FOUND[/]", border_style=FOREGROUND_ERROR))
        return

    for resource in resources:
        if resource.startswith("ASRL"):
            continue

        driver = VisaDriver(
            VisaConfig(visa_resource=resource, timeout=TimeoutConfig(recv=2))
        )  # setting a 2-second timer
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
                # we have valid device
                supported_devices.append((idn, resource, match))  # match is the dict value
            else:
                unsupported_devices.append(idn)

        except pyvisa.errors.VisaIOError as e:
            msg = "permission denied - check udev rules" if "SYSTEM_ERROR" in str(e) else str(e)
            console.print(f"   [{FOREGROUND_ERROR}]{resource}: no response: ({msg})[/]")
        except Exception as e:
            err_str = str(e)
            if "No backend available" in err_str or "PyUSB" in err_str:
                msg = "USB backend missing - install libusb: brew install libusb (Mac) or apt install libusb-1.0.0 (Linux)"
            else:
                msg = err_str
            console.print(f"   [{FOREGROUND_ERROR}]{resource}: unexpected error: ({msg})[/]")
            idn = None
        finally:
            driver.close()

    # found devices should be filled now, show user what can be used, and what can't

    if not supported_devices and not unsupported_devices and not serial_devices:
        # not a single device found
        console.print(Panel(f"   [bold {FOREGROUND_ERROR}]NO DEVICES FOUND[/]", border_style=FOREGROUND_ERROR))

        # typer.echo(typer.style(f"NO DEVICES FOUND", fg=typer.colors.RED, bold=True))
    else:
        # now adding table support
        if supported_devices:
            table = Table(
                title=f"[bold {GREEN}]SUPPORTED DEVICES", header_style=f"bold {FOREGROUND_MUTED}", border_style=BORDER
            )
            table.add_column("Resource", style=FOREGROUND, no_wrap=False)
            table.add_column("Category", style=FOREGROUND_MUTED, no_wrap=False)
            table.add_column("Driver", style=f"bold {FOREGROUND}", no_wrap=False)
            for supported in supported_devices:
                table.add_row(supported[1], supported[2][0], supported[2][1])
            console.print(table)

        if serial_devices:
            table_serial = Table(
                title=f"[bold {YELLOW}]SERIAL DEVICES[/]", border_style=BORDER, header_style=f"bold {FOREGROUND_MUTED}"
            )
            table_serial.add_column("Address", style=FOREGROUND, no_wrap=False)
            table_serial.add_column("Product", style=FOREGROUND_MUTED, no_wrap=False)
            table_serial.add_column("Message", style=FOREGROUND_MUTED, no_wrap=False)
            for serial_device in serial_devices:
                table_serial.add_row(serial_device[0][0], serial_device[0][2], serial_device[1])
            console.print(table_serial)

        if unsupported_devices:
            table_unsp = Table(
                title=f"[bold {FOREGROUND_ERROR}] UNSUPPORTED DEVICES[/]",
                header_style=f"bold {FOREGROUND_MUTED}",
                border_style=BORDER,
            )
            table_unsp.add_column("IDN Response", style=FOREGROUND, no_wrap=False)
            for unsupported in unsupported_devices:
                table_unsp.add_row(unsupported)
            console.print(table_unsp)
