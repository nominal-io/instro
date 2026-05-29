"""Command-line interface for the instro library (``instro <command>``)."""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
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
# Wide-cell emoji glyphs so status pops next to column text; standard Unicode, no special font needed.

_OK = "✅"
_MISSING = "❌"
_NA = "⛔"  # not applicable on this OS

_ALL_OS = ("Darwin", "Linux", "Windows")


# --- Catalog: packages and the native drivers they depend on ----------------


@dataclass(frozen=True)
class Driver:
    """A system-level vendor native library an extras package depends on (checked via ctypes, not the Python wrapper)."""

    label: str  # human-readable: "LJM library", "Aardvark API"
    native_lib_names: tuple[str, ...]
    supported_os: tuple[str, ...]
    install_hint: Callable[[str], str]
    python_fallback: str | None = None  # importable module that obviates the native lib


@dataclass(frozen=True)
class Package:
    """An installable instro distribution: the core package or an extras."""

    label: str
    description: str
    extras: str | None  # None for core, "daq-labjack" etc. for extras
    distribution: str  # PyPI distribution name for version lookup
    importable_module: str | None  # path proving the package's Python files are installed
    drivers: tuple[Driver, ...]


# --- Install-hint helpers ---------------------------------------------------


def _hint_visa(os_name: str) -> str:
    if os_name == "Windows":
        return "Install NI-VISA from ni.com/downloads (or pip install pyvisa-py for a pure-Python backend)"
    if os_name == "Darwin":
        return "Install NI-VISA from ni.com/downloads (or pip install pyvisa-py for a pure-Python backend)"
    return "pip install pyvisa-py (pure-Python VISA backend; works on Linux without NI-VISA)"


def _hint_ljm(_os: str) -> str:
    return "Install LJM from labjack.com/support/software/installers/ljm"


def _hint_nidaqmx(os_name: str) -> str:
    if os_name == "Windows":
        return "Install NI-DAQmx from ni.com/downloads"
    return "Install NI-DAQmx Linux drivers from ni.com"


def _hint_mcc(_os: str) -> str:
    return "Install MCC Universal Library from measurementcomputing.com"


def _hint_aardvark(_os: str) -> str:
    return (
        "Install Aardvark API (totalphase.com/products/aardvark-software-api). "
        "Place the .dylib / .so / .dll on the system library path."
    )


# --- Drivers ---------------------------------------------------------------

_PYVISA = Driver(
    label="VISA backend",
    native_lib_names=("visa", "VISA"),
    supported_os=_ALL_OS,
    install_hint=_hint_visa,
    python_fallback="pyvisa_py",  # pyvisa-py satisfies the backend requirement on its own
)
_LJM = Driver(
    label="LJM library",
    native_lib_names=("LabJackM", "labjackm"),
    supported_os=_ALL_OS,
    install_hint=_hint_ljm,
)
_NIDAQMX = Driver(
    label="NI-DAQmx runtime",
    native_lib_names=("nidaqmx", "nicaiu"),
    supported_os=("Linux", "Windows"),
    install_hint=_hint_nidaqmx,
)
_MCC = Driver(
    label="MCC Universal Library",
    native_lib_names=("cbw", "cbw32", "cbw64"),
    supported_os=("Windows",),
    install_hint=_hint_mcc,
)
_AARDVARK = Driver(
    label="Aardvark API",
    native_lib_names=("aardvark",),
    supported_os=_ALL_OS,
    install_hint=_hint_aardvark,
)


# --- Packages: core + each extras -------------------------------------------

_PACKAGES: tuple[Package, ...] = (
    Package(
        label="instro",
        description="Core package",
        extras=None,
        distribution="instro",
        importable_module=None,  # core is always present if doctor is running
        drivers=(_PYVISA,),
    ),
    Package(
        label="instro[daq-labjack]",
        description="LabJack T-series DAQ drivers",
        extras="daq-labjack",
        distribution="instro-daq-labjack",
        importable_module="instro.daq.drivers.labjack",
        drivers=(_LJM,),
    ),
    Package(
        label="instro[daq-ni]",
        description="NI-DAQ drivers",
        extras="daq-ni",
        distribution="instro-daq-ni",
        importable_module="instro.daq.drivers.ni",
        drivers=(_NIDAQMX,),
    ),
    Package(
        label="instro[daq-mcc]",
        description="MCC USB-DAQ drivers",
        extras="daq-mcc",
        distribution="instro-daq-mcc",
        importable_module="instro.daq.drivers.mcc",
        drivers=(_MCC,),
    ),
    Package(
        label="instro[i2c-aardvark]",
        description="Aardvark I2C/SPI host adapter driver",
        extras="i2c-aardvark",
        distribution="instro-i2c-aardvark",
        importable_module="instro.i2c.drivers.totalphase",
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


def _version_of(distribution: str) -> str | None:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def _find_native_lib(names: Iterable[str]) -> str | None:
    """Locate a native library by trying each name with find_library, verifying it loads via ctypes.CDLL."""
    for name in names:
        resolved = ctypes.util.find_library(name)
        if not resolved:
            continue
        try:
            ctypes.CDLL(resolved)
        except OSError:
            # find_library returned a hit but the lib won't load (corrupt,
            # wrong arch, etc.). Treat as not-found and try the next name.
            continue
        return resolved
    return None


# --- Per-row status resolution ----------------------------------------------


def _resolve_package(pkg: Package) -> str:
    """Return the rendered notes cell for a package row (icon + explanation)."""
    if pkg.extras is None:
        version = _version_of(pkg.distribution) or "installed"
        return f"{_OK}  [dim]{pkg.description} — {pkg.distribution} {version}[/]"
    if pkg.importable_module and _has_module(pkg.importable_module):
        version = _version_of(pkg.distribution) or "installed"
        return f"{_OK}  [dim]{pkg.description} — {pkg.distribution} {version}[/]"
    install_cmd = escape(f"pip install 'instro[{pkg.extras}]'")
    return f"{_MISSING}  [dim]{pkg.description}[/]\n   [yellow]{install_cmd}[/]"


def _resolve_driver(driver: Driver, os_name: str) -> str:
    """Return the rendered notes cell for a driver row (icon + explanation)."""
    # 1. OS support gate.
    if os_name not in driver.supported_os:
        os_label = "macOS" if os_name == "Darwin" else os_name
        supported = ", ".join("macOS" if s == "Darwin" else s for s in driver.supported_os)
        return f"{_NA}  [dim]Not supported on {os_label}; supported on {supported}.[/]"

    # 2. Look for the native library on the system.
    resolved = _find_native_lib(driver.native_lib_names)
    if resolved:
        return f"{_OK}  [dim]Found: {resolved}[/]"

    # 3. Allow a Python fallback (PyVISA: pyvisa-py covers the backend role).
    if driver.python_fallback and _has_module(driver.python_fallback):
        return f"{_OK}  [dim]Using Python backend: {driver.python_fallback}[/]"

    # 4. Native lib not found.
    return f"{_MISSING}  [yellow]→ {driver.install_hint(os_name)}[/]"


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
    # Two columns only: component name on the left, status + notes mashed into
    # the right. The status glyph leads each notes cell so the eye finds it
    # before reading the explanation.
    table = Table(show_header=True, header_style="bold", expand=True)
    table.add_column("Component", no_wrap=True)
    table.add_column("Status")

    first = True
    for pkg in _PACKAGES:
        if not first:
            table.add_section()
        first = False

        # Package row + child driver rows.
        table.add_row(f"[bold]{escape(pkg.label)}[/]", _resolve_package(pkg))
        for driver in pkg.drivers:
            table.add_row(f"  └ {escape(driver.label)}", _resolve_driver(driver, os_name))

    # --- Render everything inside one outer Panel ---------------------------
    # Doctor is purely informational — the table itself tells the user what's
    # installed, what's missing, and what to install. No verdict line, and
    # `instro doctor` always exits 0 on a clean run (exit 2 is reserved for
    # uncaught internal errors).
    header = Text.assemble(
        ("🩺  ", ""),
        ("instro doctor", "bold cyan"),
        ("  —  environment check", "dim"),
    )
    body = Group(runtime, Text(""), table)
    console.print()
    console.print(Panel(body, title=header, title_align="left", border_style="cyan", padding=(1, 2)))
    console.print()
    return 0


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
