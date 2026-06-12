import warnings

import pyvisa
import typer
from serial.tools import list_ports

from instro.lib.transports.visa import TimeoutConfig, VisaConfig, VisaDriver

# create an instrument mapping - false positives currently likely

# should make the mapping into having company name and also having part #
# and then checking both of them! I really need more hardware tests for reliable
# usage from company names... can a dict key be a tuple?
# currently this mapping is not robust

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
    # create list of real devices:
    real_serial_ports = {p.device for p in list_ports.comports() if p.description != "n/a"}

    # this section of code is quite inefficient
    rm_py = pyvisa.ResourceManager("@py")
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
        py_resources = rm_py.list_resources()

    supported_devices: list[tuple[str, str, tuple[str, str]]] = []  # put all found VISA devices here
    unsupported_devices: list[str] = []  # put all found non-supported devices here
    serial_devices: list[tuple[str, str]] = []
    typer.echo(f"\nScanning VISA resources...\n")

    # for each resource, open a visadriver, query *IDN?, then close it!, wrapped in try/except
    if not resources:
        typer.echo(typer.style("NO DEVICES FOUND", fg=typer.colors.RED))
        return

    # print(len(py_resources))
    # print(len(resources))
    # for p in list_ports.comports():
    #     print(f"{p.device}, description: {p.description}")

    # let's just use a separate loop for all the serial devices

    for pr in py_resources or ():
        if pr.startswith("ASRL"):
            try:
                res_info = rm_py.resource_info(pr)
                # print("PRINTING NAME")
                # print(res_info.resource_name, real_serial_ports)
                # print("NOT A REAL PORT")
                for rp in real_serial_ports:
                    if res_info.resource_name and rp in res_info.resource_name:  # probably because this can be a None
                        serial_devices.append((pr, "serial - configure manually"))
                        break
            except Exception:
                pass

    for resource in resources:
        if resource.startswith("ASRL"):
            continue

        driver = VisaDriver(
            VisaConfig(visa_resource=resource, timeout=TimeoutConfig(recv=2))
        )  # setting a 2 second timer
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
            # match = next((v for k, v in _IDN_MAP.items() if k in idn), None)
            if match is not None:
                # we have valid device
                supported_devices.append((idn, resource, match))  # match is the dict value
            else:
                unsupported_devices.append(idn)

        except pyvisa.errors.VisaIOError as e:
            msg = "permission denied - check udev rules" if "SYSTEM_ERROR" in str(e) else str(e)
            typer.echo(typer.style(f"   {resource}: no response: ({msg})\n", fg=typer.colors.YELLOW))
        except Exception as e:
            typer.echo(typer.style(f"   {resource} unexpected error ({e})", fg=typer.colors.YELLOW))
            idn = None
        finally:
            driver.close()
    # could do v2 functionality for probing serial.

    ######################################################################### we want to test this functionality ^^

    # found devices should be filled now, show user what can be used, and what can't

    if not supported_devices and not unsupported_devices and not serial_devices:
        # not a single device found
        typer.echo(typer.style(f"NO DEVICES FOUND", fg=typer.colors.RED, bold=True))
    else:
        for supported in supported_devices:
            print(f"{supported[1]}")
            print(f"{supported[0]}")
            print(f"{supported[2][1]}")
            typer.echo(typer.style("✓ SUPPORTED", fg=typer.colors.GREEN))
            print(f"\n")

        for serial_device in serial_devices:
            print(f"{serial_device[0]}")
            typer.echo(typer.style(f"{serial_device[1]}", fg=typer.colors.YELLOW))
            print(f"\n")

        for unsupported in unsupported_devices:
            print(f"{unsupported}")
            typer.echo(typer.style("~ UNSUPPORTED", fg=typer.colors.RED))  # is unsupported the right word?
            print(f"\n")

    # now let them make a selection

    # establish connection to selected device

    # probably want to show all the different instruments available
    # this is not simply a first-come first serve basis
