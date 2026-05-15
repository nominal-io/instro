"""Command-line interface for the instro library.

Entry point: ``instro <command>``. Currently the only command is ``doctor``,
which prints a health report so users can debug missing extras or vendor
SDKs before they hit a confusing import error.

Adding new subcommands is a matter of writing another `_cmd_*` function
and adding a parser in `main()`.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import platform
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Callable

from rich.console import Console, Group
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# --- Status icons ------------------------------------------------------------

_OK = Text("✓", style="bold green")
_MISSING = Text("✗", style="bold red")
_PARTIAL = Text("⚠", style="bold yellow")
_NA = Text("·", style="dim yellow")  # not applicable on this OS

_ALL_OS = ("Darwin", "Linux", "Windows")


# --- Catalog: packages and the native drivers they depend on ----------------


@dataclass(frozen=True)
class Driver:
    """A vendor SDK a package depends on, with two parts.

    * **Python wrapper** — the PyPI distribution that exposes
      `python_module` (e.g. `labjack-ljm` exposes `labjack.ljm`). Installed
      automatically by `pip install 'instro[<extras>]'`.
    * **Native library** — the system-level binary (`.dylib`/`.so`/`.dll`)
      installed via the vendor's standalone installer.

    Both must be present for the driver to actually work. The doctor
    distinguishes "Python wrapper missing" from "wrapper present but
    native lib failed to load" so install hints can point at the right
    thing.
    """

    label: str           # short identifier (matches PyPI package name)
    description: str
    python_module: str   # importable wrapper (e.g. "pyvisa", "labjack.ljm")
    distribution: str    # PyPI distribution for version lookup
    supported_os: tuple[str, ...]
    native_name: str     # human name for the native lib (e.g. "LJM library")
    native_install_hint: Callable[[str], str]  # how to install the native lib


@dataclass(frozen=True)
class Package:
    """An installable instro distribution: the core package or an extras."""

    label: str
    description: str
    extras: str | None        # None for core, "daq-labjack" etc. for extras
    distribution: str         # "instro" or "instro-daq-labjack"
    importable_module: str | None  # path proving the package's Python files are installed; None for core
    universally_required: bool     # if any of its drivers fails → exit 1
    drivers: tuple[Driver, ...]


# --- Install-hint helpers ---------------------------------------------------


def _hint_pyvisa(os_name: str) -> str:
    if os_name == "Windows":
        return "pip install pyvisa-py  (or install NI-VISA from ni.com for hardware backends)"
    return "pip install pyvisa-py"


def _hint_ljm(_os: str) -> str:
    return "Install LJM library from labjack.com/support/software/installers/ljm"


def _hint_nidaqmx(os_name: str) -> str:
    if os_name == "Windows":
        return "Install NI-DAQmx from ni.com/downloads"
    return "Install NI-DAQmx Linux drivers from ni.com"


def _hint_mcc(_os: str) -> str:
    return "Install MCC Universal Library from measurementcomputing.com"


def _hint_aardvark(_os: str) -> str:
    return "Install Aardvark API from totalphase.com/products/aardvark-software-api"


# --- Drivers ---------------------------------------------------------------

_PYVISA = Driver(
    label="pyvisa",
    description="SCPI over VISA — DMMs, PSUs, ELoads, Keysight DAQ",
    python_module="pyvisa",
    distribution="pyvisa",
    supported_os=_ALL_OS,
    native_name="VISA backend",
    native_install_hint=_hint_pyvisa,
)
_LJM = Driver(
    label="labjack-ljm",
    description="LabJack T4/T7/T8 USB DAQ",
    python_module="labjack.ljm",
    distribution="labjack-ljm",
    supported_os=_ALL_OS,
    native_name="LJM library",
    native_install_hint=_hint_ljm,
)
_NIDAQMX = Driver(
    label="nidaqmx",
    description="National Instruments DAQmx",
    python_module="nidaqmx",
    distribution="nidaqmx",
    supported_os=("Linux", "Windows"),
    native_name="NI-DAQmx runtime",
    native_install_hint=_hint_nidaqmx,
)
_MCC = Driver(
    label="mcculw",
    description="Measurement Computing USB-DAQ",
    python_module="mcculw",
    distribution="mcculw",
    supported_os=("Windows",),
    native_name="MCC Universal Library",
    native_install_hint=_hint_mcc,
)
_AARDVARK = Driver(
    label="pyaardvark",
    description="Total Phase Aardvark I2C/SPI host adapter",
    python_module="pyaardvark",
    distribution="pyaardvark",
    supported_os=_ALL_OS,
    native_name="Aardvark API",
    native_install_hint=_hint_aardvark,
)


# --- Packages: core + each extras -------------------------------------------

_PACKAGES: tuple[Package, ...] = (
    Package(
        label="instro",
        description="Core package",
        extras=None,
        distribution="instro",
        importable_module=None,  # core is always present if doctor is running
        universally_required=True,
        drivers=(_PYVISA,),
    ),
    Package(
        label="instro[daq-labjack]",
        description="LabJack T-series DAQ drivers",
        extras="daq-labjack",
        distribution="instro-daq-labjack",
        importable_module="instro.daq.drivers.labjack",
        universally_required=False,
        drivers=(_LJM,),
    ),
    Package(
        label="instro[daq-ni]",
        description="NI-DAQ drivers",
        extras="daq-ni",
        distribution="instro-daq-ni",
        importable_module="instro.daq.drivers.ni",
        universally_required=False,
        drivers=(_NIDAQMX,),
    ),
    Package(
        label="instro[daq-mcc]",
        description="MCC USB-DAQ drivers",
        extras="daq-mcc",
        distribution="instro-daq-mcc",
        importable_module="instro.daq.drivers.mcc",
        universally_required=False,
        drivers=(_MCC,),
    ),
    Package(
        label="instro[i2c-aardvark]",
        description="Aardvark I2C/SPI host adapter driver",
        extras="i2c-aardvark",
        distribution="instro-i2c-aardvark",
        importable_module="instro.i2c.drivers.totalphase",
        universally_required=False,
        drivers=(_AARDVARK,),
    ),
)


# --- Detection helpers -------------------------------------------------------


def _has_module(dotted: str) -> bool:
    """True if Python could import `dotted` without triggering side effects."""
    try:
        return importlib.util.find_spec(dotted) is not None
    except (ImportError, ValueError):
        return False


def _try_import(dotted: str) -> tuple[bool, str | None]:
    """Try to actually import `dotted`. Returns (ok, error_message)."""
    try:
        importlib.import_module(dotted)
        return True, None
    except Exception as e:  # noqa: BLE001 — vendor SDKs raise NameError/OSError/etc.
        return False, f"{type(e).__name__}: {e}"


def _version_of(distribution: str) -> str | None:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


# --- Per-row status resolution ----------------------------------------------


@dataclass
class _PackageRow:
    icon: Text
    notes: str
    installed: bool


@dataclass
class _DriverRow:
    icon: Text
    notes: str
    blocks_ready: bool  # True if this driver's failure should fail the doctor


def _resolve_package(pkg: Package) -> _PackageRow:
    if pkg.extras is None:
        # Core. If doctor is running at all, the core package is installed.
        version = _version_of(pkg.distribution) or "installed"
        return _PackageRow(
            icon=_OK,
            notes=f"[dim]{pkg.description} — {pkg.distribution} {version}[/]",
            installed=True,
        )
    if pkg.importable_module and _has_module(pkg.importable_module):
        version = _version_of(pkg.distribution) or "installed"
        return _PackageRow(
            icon=_OK,
            notes=f"[dim]{pkg.description} — {pkg.distribution} {version}[/]",
            installed=True,
        )
    install_cmd = escape(f"pip install 'instro[{pkg.extras}]'")
    return _PackageRow(
        icon=_MISSING,
        notes=f"[dim]{pkg.description}[/]\n[yellow]{install_cmd}[/]",
        installed=False,
    )


def _resolve_driver(driver: Driver, parent: Package, parent_installed: bool, os_name: str) -> _DriverRow:
    # 1. OS support gate.
    if os_name not in driver.supported_os:
        os_label = "macOS" if os_name == "Darwin" else os_name
        supported = ", ".join("macOS" if s == "Darwin" else s for s in driver.supported_os)
        return _DriverRow(
            icon=_NA,
            notes=f"[dim]Not supported on {os_label}; supported on {supported}.[/]",
            blocks_ready=False,
        )

    # 2. Python wrapper missing (find_spec returns None).
    if not _has_module(driver.python_module):
        # The user might have the native library installed already — common
        # source of confusion ("but I installed LJM!"). Be explicit: this row
        # is checking the *Python wrapper*, separate from the native binary.
        if parent.extras is not None:
            pip_cmd = escape(f"pip install 'instro[{parent.extras}]'")
            wrapper_action = f"[yellow]→ {pip_cmd}[/] [dim](installs {driver.distribution})[/]"
        else:
            wrapper_action = f"[yellow]→ pip install {driver.distribution}[/]"
        return _DriverRow(
            icon=_MISSING,
            notes=(
                f"[yellow]Python wrapper [bold]{driver.distribution}[/bold] not installed[/]\n"
                f"{wrapper_action}\n"
                f"[dim]Native {driver.native_name} is also required (separate install):[/]\n"
                f"[dim]→ {driver.native_install_hint(os_name)}[/]"
            ),
            blocks_ready=parent.universally_required,
        )

    # 3. Python wrapper present but actual import fails — almost always the
    #    native library failing to load.
    ok, err = _try_import(driver.python_module)
    if not ok:
        msg = err or "import failed"
        return _DriverRow(
            icon=_PARTIAL,
            notes=(
                f"[yellow]{driver.distribution} installed, but native {driver.native_name} failed to load[/]\n"
                f"[dim]{msg}[/]\n"
                f"[yellow]→ {driver.native_install_hint(os_name)}[/]"
            ),
            blocks_ready=parent.universally_required,
        )

    # 4. Both parts working.
    version = _version_of(driver.distribution) or "installed"
    return _DriverRow(
        icon=_OK,
        notes=f"[dim]{driver.description} — {driver.distribution} {version}[/]",
        blocks_ready=False,
    )


# --- Doctor command ---------------------------------------------------------


def _cmd_doctor(_args: argparse.Namespace) -> int:
    console = Console()
    os_name = platform.system()
    py_version = ".".join(str(x) for x in sys.version_info[:3])
    instro_version = _version_of("instro") or "unknown"

    # --- Runtime grid (goes inside the outer panel) -------------------------
    runtime = Table.grid(padding=(0, 2))
    runtime.add_column(style="dim", justify="right")
    runtime.add_column()
    runtime.add_row("Python", f"[bold]{py_version}[/]  [dim]({platform.machine()})[/]")
    runtime.add_row("Platform", os_name)
    runtime.add_row("instro", f"[bold]{instro_version}[/]")

    # --- Packages-and-drivers table -----------------------------------------
    table = Table(show_header=True, header_style="bold", expand=True)
    table.add_column("Component", no_wrap=True)
    table.add_column("Status", justify="center", width=8)
    table.add_column("Notes")

    blocking: list[str] = []
    first = True
    for pkg in _PACKAGES:
        if not first:
            table.add_section()
        first = False

        # Package row.
        pkg_row = _resolve_package(pkg)
        table.add_row(f"[bold]{escape(pkg.label)}[/]", pkg_row.icon, pkg_row.notes)

        # Child driver rows.
        for driver in pkg.drivers:
            child_row = _resolve_driver(driver, pkg, pkg_row.installed, os_name)
            table.add_row(f"  └ {escape(driver.label)}", child_row.icon, child_row.notes)
            if child_row.blocks_ready:
                blocking.append(f"{pkg.label} → {driver.label}")

    # --- Verdict line -------------------------------------------------------
    if blocking:
        verdict = Text.assemble(
            ("⚠  ", "bold yellow"),
            (f"Required driver(s) missing: {', '.join(blocking)}", "yellow"),
        )
        exit_code = 1
    else:
        verdict = Text("✓  Ready to go.", style="bold green")
        exit_code = 0

    # --- Render everything inside one outer Panel ---------------------------
    header = Text.assemble(
        ("🩺  ", ""),
        ("instro doctor", "bold cyan"),
        ("  —  environment check", "dim"),
    )
    body = Group(runtime, Text(""), table, Text(""), verdict)
    console.print()
    console.print(Panel(body, title=header, title_align="left", border_style="cyan", padding=(1, 2)))
    console.print()
    return exit_code


# --- argparse wiring --------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="instro", description="instro command-line tools")
    subparsers = parser.add_subparsers(dest="command", required=True)
    doctor = subparsers.add_parser("doctor", help="Print an environment health report")
    doctor.set_defaults(func=_cmd_doctor)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return int(args.func(args))
    except Exception as e:  # noqa: BLE001 — surface any internal error with exit 2
        Console(stderr=True).print(f"[bold red]instro: internal error:[/] {type(e).__name__}: {e}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
