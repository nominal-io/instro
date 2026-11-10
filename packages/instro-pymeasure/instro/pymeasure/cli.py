"""``instro pymeasure`` commands: inspect a pymeasure instrument and generate its channel map."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from instro.pymeasure.channel_map import ChannelMap
from instro.pymeasure.inspection import describe

app = typer.Typer(help="Publish pymeasure instruments through instro.", no_args_is_help=True)

_INSTRUMENT_HELP = (
    "pymeasure instrument class, as a full dotted path (pymeasure.instruments.keithley.Keithley2400) "
    "or relative to pymeasure.instruments (keithley.Keithley2400)."
)
_RESOURCE_HELP = (
    "Connect to this VISA resource and inspect the live instance. Needed for drivers that add "
    "channels in __init__ rather than with ChannelCreator."
)


def load_instrument_class(dotted: str) -> type:
    """Import a pymeasure instrument class from ``pkg.module.Class`` or ``vendor.Class``.

    Raises:
        typer.BadParameter: The class can't be imported.
    """
    module_name, _, class_name = dotted.rpartition(".")
    if not module_name:
        raise typer.BadParameter(f"{dotted!r} is not a dotted path to a class")
    for candidate in (module_name, f"pymeasure.instruments.{module_name}"):
        try:
            module = importlib.import_module(candidate)
        except ImportError:
            continue
        cls = getattr(module, class_name, None)
        if isinstance(cls, type):
            return cls
    raise typer.BadParameter(f"Could not import pymeasure instrument class {dotted!r}")


def _target(instrument: str, resource: str | None) -> Any:
    cls = load_instrument_class(instrument)
    return cls(resource) if resource else cls


def _close(target: Any) -> None:
    if not isinstance(target, type):
        target.adapter.close()


@app.command("map")
def map_cmd(
    instrument: Annotated[str, typer.Argument(help=_INSTRUMENT_HELP)],
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Write the map here instead of stdout.")] = None,
    resource: Annotated[str | None, typer.Option(help=_RESOURCE_HELP)] = None,
) -> None:
    """Generate an editable JSON channel map for a pymeasure instrument.

    Edit each entry's "channel" to rename it, or set it to null to stop publishing it, then pass
    the file as channel_map= to PymeasureInstrument or attach().
    """
    target = _target(instrument, resource)
    try:
        channel_map = ChannelMap.generate(target)
        text = json.dumps(channel_map.to_dict(describe(target)), indent=2) + "\n"
    finally:
        _close(target)
    if output is None:
        typer.echo(text, nl=False)
    else:
        output.write_text(text)
        typer.echo(f"Wrote {len(channel_map.channels)} channels to {output}", err=True)


@app.command("describe")
def describe_cmd(
    instrument: Annotated[str, typer.Argument(help=_INSTRUMENT_HELP)],
    resource: Annotated[str | None, typer.Option(help=_RESOURCE_HELP)] = None,
) -> None:
    """Print each publishable property with its SCPI commands and its default command/measurement channels."""
    target = _target(instrument, resource)
    try:
        specs = describe(target)
        rows = ChannelMap.defaults().preview(target)
    finally:
        _close(target)
    table = Table(title=instrument)
    for column in ("property", "kind", "get", "set", "command channel", "measurement channel"):
        table.add_column(column)
    for spec, row in zip(specs, rows):
        table.add_row(
            spec.path,
            spec.kind,
            spec.get_command or "",
            spec.set_command or "",
            row["command"] or "",
            row["measurement"] or "",
        )
    Console().print(table)
