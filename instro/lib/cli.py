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

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# --- Status icons ------------------------------------------------------------

_OK = Text("✓", style="bold green")
_MISSING = Text("✗", style="bold red")
_INFO = Text("—", style="dim")
_NA = Text("·", style="dim yellow")  # not applicable on this OS

# --- Catalog of things we check ---------------------------------------------


@dataclass(frozen=True)
class Extra:
    """An `instro[<name>]` optional dependency, backed by a workspace package."""

    extras_name: str
    pypi_name: str
    description: str
    importable_module: str  # used to confirm the workspace files actually installed


@dataclass(frozen=True)
class VendorSDK:
    """A vendor-supplied SDK that's not a pure-Python pip install.

    `python_module` is the Python wrapper (e.g. `nidaqmx`). `os_hint` returns
    an install instruction tailored to the current platform.
    """

    label: str
    python_module: str
    distribution: str | None  # PyPI distribution name for version lookup; None = same as python_module
    os_hint: Callable[[str], str]  # current OS ("Darwin" / "Linux" / "Windows") -> hint


_EXTRAS: tuple[Extra, ...] = (
    Extra("daq-labjack", "instro-daq-labjack", "LabJack T-series DAQ", "instro.daq.drivers.labjack"),
    Extra("daq-mcc", "instro-daq-mcc", "Measurement Computing DAQ (Windows-only)", "instro.daq.drivers.mcc"),
    Extra("daq-ni", "instro-daq-ni", "NI-DAQmx DAQ", "instro.daq.drivers.ni"),
    Extra("i2c-aardvark", "instro-i2c-aardvark", "Total Phase Aardvark I2C/SPI host adapter", "instro.i2c.drivers.totalphase"),
)


def _visa_hint(os_name: str) -> str:
    if os_name == "Windows":
        return "pip install pyvisa-py  (or install NI-VISA from ni.com for hardware backends)"
    return "pip install pyvisa-py"


def _nidaq_hint(os_name: str) -> str:
    if os_name == "Darwin":
        return "macOS not supported — try instro\\[daq-labjack] for cross-platform DAQ"
    if os_name == "Windows":
        return "NI-DAQmx installer: ni.com/downloads — then  pip install 'instro\\[daq-ni]'"
    return "NI-DAQmx Linux drivers from ni.com — then  pip install 'instro\\[daq-ni]'"


def _ljm_hint(os_name: str) -> str:
    base = "labjack.com/support/software/installers/ljm"
    return f"Install LJM library from {base} — then  pip install 'instro\\[daq-labjack]'"


def _mcc_hint(os_name: str) -> str:
    if os_name != "Windows":
        return f"{os_name} not supported — MCC Universal Library is Windows-only"
    return "Install MCC Universal Library, then  pip install 'instro\\[daq-mcc]'"


def _aardvark_hint(os_name: str) -> str:
    return "Aardvark API: totalphase.com/products/aardvark-software-api — then  pip install 'instro\\[i2c-aardvark]'"


_VENDOR_SDKS: tuple[VendorSDK, ...] = (
    VendorSDK("PyVISA",       "pyvisa",      "pyvisa",         _visa_hint),
    VendorSDK("NI-DAQmx",     "nidaqmx",     "nidaqmx",        _nidaq_hint),
    VendorSDK("LabJack LJM",  "labjack.ljm", "labjack-ljm",    _ljm_hint),
    VendorSDK("MCC UL",       "mcculw",      "mcculw",         _mcc_hint),
    VendorSDK("Aardvark",     "pyaardvark",  "pyaardvark",     _aardvark_hint),
)


# --- Detection helpers -------------------------------------------------------


def _has_module(dotted: str) -> bool:
    """True if Python could import `dotted` without triggering side effects.

    Uses `find_spec` so we don't actually execute the module — important for
    SDKs whose import has side effects (e.g. mcculw loads a Windows DLL at
    import time and crashes on non-Windows).
    """
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


# --- Doctor command ---------------------------------------------------------


def _cmd_doctor(_args: argparse.Namespace) -> int:
    console = Console()
    os_name = platform.system()
    py_version = ".".join(str(x) for x in sys.version_info[:3])
    instro_version = _version_of("instro") or "unknown"

    console.print()
    console.print(Panel.fit(
        Text.assemble(
            ("🩺  ", ""),
            ("instro doctor", "bold cyan"),
            ("  —  environment check", "dim"),
        ),
        border_style="cyan",
    ))
    console.print()

    # --- Runtime block ------------------------------------------------------
    runtime = Table.grid(padding=(0, 2))
    runtime.add_column(style="dim", justify="right")
    runtime.add_column()
    runtime.add_row("Python", f"[bold]{py_version}[/]  [dim]({platform.machine()})[/]")
    runtime.add_row("Platform", f"{os_name}")
    runtime.add_row("instro", f"[bold]{instro_version}[/]")
    console.print(runtime)
    console.print()

    # --- Extras -------------------------------------------------------------
    user_missing: list[str] = []
    extras_table = Table(title="Workspace extras", title_style="bold", title_justify="left", expand=True)
    extras_table.add_column("Extra", style="bold", no_wrap=True)
    extras_table.add_column("Status", justify="center", width=8)
    extras_table.add_column("Notes")
    for extra in _EXTRAS:
        # Escape brackets so Rich doesn't interpret `[daq-labjack]` as markup.
        extra_label = escape(f"instro[{extra.extras_name}]")
        install_cmd = escape(f"pip install 'instro[{extra.extras_name}]'")
        if _has_module(extra.importable_module):
            version = _version_of(extra.pypi_name) or "installed"
            extras_table.add_row(
                extra_label,
                _OK,
                f"[dim]{extra.pypi_name}[/] {version}  — {extra.description}",
            )
        else:
            extras_table.add_row(
                extra_label,
                _MISSING,
                f"[dim]not installed[/] — {extra.description}\n[yellow]{install_cmd}[/]",
            )
    console.print(extras_table)
    console.print()

    # --- Vendor SDKs --------------------------------------------------------
    sdk_table = Table(title="Vendor SDKs", title_style="bold", title_justify="left", expand=True)
    sdk_table.add_column("SDK", style="bold")
    sdk_table.add_column("Status", justify="center", width=8)
    sdk_table.add_column("Notes")
    for sdk in _VENDOR_SDKS:
        present = _has_module(sdk.python_module)
        if present:
            # find_spec passes; try a real import to catch native-lib load failures.
            ok, err = _try_import(sdk.python_module)
            if ok:
                version = _version_of(sdk.distribution or sdk.python_module) or "installed"
                sdk_table.add_row(sdk.label, _OK, f"[dim]{sdk.distribution or sdk.python_module}[/] {version}")
            else:
                # The Python wrapper installed but its native lib didn't load.
                msg = err or "import failed"
                sdk_table.add_row(
                    sdk.label,
                    _MISSING,
                    f"[yellow]Python wrapper present but native library missing[/]\n[dim]{msg}[/]\n{sdk.os_hint(os_name)}",
                )
                if sdk.label == "PyVISA":  # only PyVISA is universally required
                    user_missing.append(sdk.label)
        else:
            sdk_table.add_row(
                sdk.label,
                _MISSING if sdk.label == "PyVISA" else _NA,
                f"[dim]not installed[/]\n[yellow]{sdk.os_hint(os_name)}[/]",
            )
            if sdk.label == "PyVISA":
                user_missing.append(sdk.label)
    console.print(sdk_table)
    console.print()

    # --- Verdict ------------------------------------------------------------
    if user_missing:
        console.print(Text.assemble(
            ("⚠  ", "bold yellow"),
            (f"Missing: {', '.join(user_missing)}. Install before using VISA-based instruments.", "yellow"),
        ))
        return 1
    console.print(Text("✓  Ready to go.", style="bold green"))
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
