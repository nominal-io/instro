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
_PARTIAL = Text("⚠", style="bold yellow")
_NA = Text("·", style="dim yellow")  # not applicable on this OS

# --- Catalog: one row per hardware capability -------------------------------


@dataclass(frozen=True)
class Capability:
    """A thing the user might want to do with instro.

    Each capability needs (optionally) an `instro[<extras>]` workspace package
    plus an underlying vendor SDK. The doctor checks both and reports one
    combined status per row.
    """

    label: str
    description: str
    extras: str | None  # e.g. "daq-labjack"; None means no extras needed (PyVISA)
    extras_module: str | None  # importable module that proves the extras installed
    python_module: str  # the vendor Python wrapper (pyvisa, mcculw, ...)
    distribution: str  # PyPI distribution name for version lookup
    universally_required: bool  # if True, missing → exit 1
    supported_os: tuple[str, ...]  # platform.system() values that support this
    install_hint: Callable[[str], str]  # current OS -> what to install


def _hint_visa(os_name: str) -> str:
    if os_name == "Windows":
        return "pip install pyvisa-py  (or install NI-VISA from ni.com for hardware backends)"
    return "pip install pyvisa-py"


def _hint_labjack(_os: str) -> str:
    return (
        "pip install 'instro\\[daq-labjack]' + LJM library "
        "from labjack.com/support/software/installers/ljm"
    )


def _hint_ni(os_name: str) -> str:
    if os_name == "Windows":
        return "pip install 'instro\\[daq-ni]' + NI-DAQmx installer from ni.com/downloads"
    return "pip install 'instro\\[daq-ni]' + NI-DAQmx Linux drivers from ni.com"


def _hint_mcc(_os: str) -> str:
    return "pip install 'instro\\[daq-mcc]' + MCC Universal Library installer"


def _hint_aardvark(_os: str) -> str:
    return (
        "pip install 'instro\\[i2c-aardvark]' + Aardvark API "
        "from totalphase.com/products/aardvark-software-api"
    )


_ALL_OS = ("Darwin", "Linux", "Windows")

_CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        label="VISA / SCPI",
        description="SCPI over VISA — DMMs, PSUs, ELoads, Keysight DAQ",
        extras=None,
        extras_module=None,
        python_module="pyvisa",
        distribution="pyvisa",
        universally_required=True,
        supported_os=_ALL_OS,
        install_hint=_hint_visa,
    ),
    Capability(
        label="LabJack T-series",
        description="LabJack T4/T7/T8 USB DAQ",
        extras="daq-labjack",
        extras_module="instro.daq.drivers.labjack",
        python_module="labjack.ljm",
        distribution="labjack-ljm",
        universally_required=False,
        supported_os=_ALL_OS,
        install_hint=_hint_labjack,
    ),
    Capability(
        label="NI-DAQ",
        description="National Instruments DAQmx",
        extras="daq-ni",
        extras_module="instro.daq.drivers.ni",
        python_module="nidaqmx",
        distribution="nidaqmx",
        universally_required=False,
        supported_os=("Linux", "Windows"),  # no NI-DAQmx for macOS
        install_hint=_hint_ni,
    ),
    Capability(
        label="MCC USB-DAQ",
        description="Measurement Computing Universal Library",
        extras="daq-mcc",
        extras_module="instro.daq.drivers.mcc",
        python_module="mcculw",
        distribution="mcculw",
        universally_required=False,
        supported_os=("Windows",),  # mcculw wraps a Windows DLL
        install_hint=_hint_mcc,
    ),
    Capability(
        label="Aardvark I2C/SPI",
        description="Total Phase Aardvark host adapter",
        extras="i2c-aardvark",
        extras_module="instro.i2c.drivers.totalphase",
        python_module="pyaardvark",
        distribution="pyaardvark",
        universally_required=False,
        supported_os=_ALL_OS,
        install_hint=_hint_aardvark,
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
class _Row:
    icon: Text
    notes: str
    blocks_ready: bool  # True if this capability's failure should fail the doctor


def _resolve(capability: Capability, os_name: str) -> _Row:
    """Compute the status icon + notes for one capability."""
    # 1. OS support gate — short-circuit if the user's OS can't run this SDK.
    if os_name not in capability.supported_os:
        os_label = "macOS" if os_name == "Darwin" else os_name
        supported = ", ".join("macOS" if s == "Darwin" else s for s in capability.supported_os)
        return _Row(
            icon=_NA,
            notes=f"[dim]{capability.description}[/]\n[dim]Not supported on {os_label}; supported on {supported}.[/]",
            blocks_ready=False,
        )

    # 2. Workspace extras gate (if applicable).
    extras_present = capability.extras_module is None or _has_module(capability.extras_module)

    # 3. Vendor Python module gate (find_spec, no side effects).
    python_present = _has_module(capability.python_module)

    if not extras_present and not python_present:
        return _Row(
            icon=_MISSING,
            notes=f"[dim]{capability.description}[/]\n[yellow]{capability.install_hint(os_name)}[/]",
            blocks_ready=capability.universally_required,
        )
    if not extras_present:
        # Vendor SDK is present but the instro driver package isn't.
        install = escape(f"pip install 'instro[{capability.extras}]'")
        return _Row(
            icon=_MISSING,
            notes=f"[dim]{capability.description}[/]\n[yellow]{install}[/] (SDK already installed)",
            blocks_ready=capability.universally_required,
        )
    if not python_present:
        return _Row(
            icon=_MISSING,
            notes=f"[dim]{capability.description}[/]\n[yellow]{capability.install_hint(os_name)}[/]",
            blocks_ready=capability.universally_required,
        )

    # 4. Everything is findable — try to actually import to catch native-lib failures.
    ok, err = _try_import(capability.python_module)
    if not ok:
        msg = err or "import failed"
        return _Row(
            icon=_PARTIAL,
            notes=(
                f"[dim]{capability.description}[/]\n"
                f"[yellow]Python wrapper installed but native library failed to load[/]\n"
                f"[dim]{msg}[/]\n"
                f"[yellow]{capability.install_hint(os_name)}[/]"
            ),
            blocks_ready=capability.universally_required,
        )

    # 5. Ready. Show versions.
    sdk_version = _version_of(capability.distribution) or "installed"
    parts = [f"[dim]{capability.description}[/]", f"[dim]{capability.distribution} {sdk_version}[/]"]
    if capability.extras:
        extras_version = _version_of(f"instro-{capability.extras}") or "installed"
        parts.append(f"[dim]instro-{capability.extras} {extras_version}[/]")
    return _Row(icon=_OK, notes="\n".join(parts), blocks_ready=False)


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
    runtime.add_row("Platform", os_name)
    runtime.add_row("instro", f"[bold]{instro_version}[/]")
    console.print(runtime)
    console.print()

    # --- Capabilities -------------------------------------------------------
    table = Table(title="Capabilities", title_style="bold", title_justify="left", expand=True)
    table.add_column("Capability", style="bold", no_wrap=True)
    table.add_column("Status", justify="center", width=8)
    table.add_column("Notes")

    blocking_missing: list[str] = []
    for cap in _CAPABILITIES:
        row = _resolve(cap, os_name)
        table.add_row(cap.label, row.icon, row.notes)
        if row.blocks_ready:
            blocking_missing.append(cap.label)
    console.print(table)
    console.print()

    # --- Verdict ------------------------------------------------------------
    if blocking_missing:
        console.print(Text.assemble(
            ("⚠  ", "bold yellow"),
            (f"Missing: {', '.join(blocking_missing)}. Install before using VISA-based instruments.", "yellow"),
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
