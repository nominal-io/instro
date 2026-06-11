import pyvisa
import typer

from instro.lib.transports.visa import TimeoutConfig, VisaConfig, VisaDriver

# create an instrument mapping
_IDN_MAP = {
    "34401A": ("dmm", "AgilentA34401A"),
    "2400": ("dmm", "Keithley2400"),  # maybe make this MODEL 2400
    "9115": ("psu", "BK9115"),
    "9140": ("psu", "BK9140"),
    "DP811": ("psu", "RIGOLDP800"),
    "DP821": ("psu", "RIGOLDP800"),
    "DP831": ("psu", "RIGOLDP800"),
    "DP832": ("psu", "RIGOLDP800"),
    "SPD3303": ("psu", "SiglentSPD3303"),
    "GEN": ("psu", "TDKLambdaGenesys"),  # this feels too vague
    # "8500": ("eload", "BK85xxB"),  # not sure if this works for all 85XX series...
}

# I am a little worried about false positives, what exactly should these have? should I make the checks more complex


def discover(backend: str | None = None) -> None:
    """Function for discovering known and unknown SCPI devices with VISA."""
    # must create resource manager
    if backend:
        typer.echo("CHOOSING DEFAULT")
        rm = pyvisa.ResourceManager(backend)
    else:
        try:
            # automatically chooses a backend
            # typer.echo("CHOOSING NOT DEFAULT")

            rm = pyvisa.ResourceManager("@ivi")
        except Exception:
            rm = pyvisa.ResourceManager("@py")

    resources = rm.list_resources()
    supported_devices: list[tuple[str, str, tuple[str, str]]] = []  # put all found VISA devices here
    unsupported_devices: list[str] = []  # put all found non-supported devices here
    serial_devices: list[tuple[str, str]] = []
    typer.echo(f"\nScanning VISA resources...\n")

    # for each resource, open a visadriver, query *IDN?, then close it!, wrapped in try/except
    if not resources:
        typer.echo(typer.style("NO DEVICES FOUND", fg=typer.colors.RED))
        return

    for resource in resources:
        if resource.startswith("ASRL"):
            # serial device, set for manual config
            serial_devices.append((resource, "serial - configure manually"))
            continue
        driver = VisaDriver(
            VisaConfig(visa_resource=resource, timeout=TimeoutConfig(recv=2))
        )  # setting a 2 second timer
        try:
            driver.open()
            idn = driver.query("*IDN?").strip()
            match = next((v for k, v in _IDN_MAP.items() if k in idn), None)
            if match is not None:
                # we have valid device
                supported_devices.append((idn, resource, match))  # match is the dict value
            else:
                # implement
                unsupported_devices.append(idn)  # should separate these by color

        except pyvisa.errors.VisaIOError as e:
            msg = "permission denied - check udev rules" if "SYSTEM_ERROR" in str(e) else str(e)
            typer.echo(typer.style(f"   {resource}: no response ({msg})", fg=typer.colors.YELLOW))
        except Exception as e:
            typer.echo(typer.style(f"   {resource} unexpected error ({e})", fg=typer.colors.YELLOW))
            idn = None
        finally:
            driver.close()

    # could do v2 functionality for probing serial.

    ######################################################################### we want to test this functionality ^^

    # found devices should be filled now, show user what can be used, and what can't
    # print supported
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
        typer.echo(typer.style("~ UNSUPPORTED", fg=typer.colors.RED))
        print(f"\n")

    # now let them make a selection

    # establish connection to selected device

    # probably want to show all the different instruments available
    # this is not simply a first-come first serve basis
