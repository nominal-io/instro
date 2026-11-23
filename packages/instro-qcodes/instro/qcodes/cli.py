"""``instro qcodes`` commands: inspect a QCoDeS instrument and generate its channel map."""

from __future__ import annotations

import ast
import importlib
import json
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from instro.qcodes.channel_map import ChannelMap
from instro.qcodes.inspection import describe

app = typer.Typer(help="Publish QCoDeS instruments through instro.", no_args_is_help=True)

_INSTRUMENT_HELP = (
    "QCoDeS instrument class, as a full dotted path (qcodes.instrument_drivers.Keithley.Keithley2450) "
    "or relative to qcodes.instrument_drivers (Keithley.Keithley2450)."
)
_RESOURCE_HELP = (
    "Address passed to the driver as its second argument, usually a VISA resource. Leave it out for "
    "drivers that don't take one, such as simulated ones."
)
_ARG_HELP = "Extra driver argument as NAME=VALUE; repeat for several. VALUE is a Python literal, else a string."
_INSPECT_NAME = "instro_inspect"


def load_instrument_class(dotted: str) -> type:
    """Import a QCoDeS instrument class from ``pkg.module.Class`` or ``Vendor.Class``.

    Raises:
        typer.BadParameter: The class can't be imported.
    """
    module_name, _, class_name = dotted.rpartition(".")
    if not module_name:
        raise typer.BadParameter(f"{dotted!r} is not a dotted path to a class")
    for candidate in (module_name, f"qcodes.instrument_drivers.{module_name}"):
        try:
            module = importlib.import_module(candidate)
        except ImportError:
            continue
        cls = getattr(module, class_name, None)
        if isinstance(cls, type):
            return cls
    raise typer.BadParameter(f"Could not import QCoDeS instrument class {dotted!r}")


def _parse_args(args: list[str]) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for item in args:
        key, separator, raw = item.partition("=")
        if not separator or not key.isidentifier():
            raise typer.BadParameter(f"{item!r} is not NAME=VALUE", param_hint="--arg")
        try:
            parsed[key] = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            parsed[key] = raw
    return parsed


def _connect(instrument: str, resource: str | None, args: list[str]) -> Any:
    cls = load_instrument_class(instrument)
    positional = [resource] if resource else []
    try:
        return cls(_INSPECT_NAME, *positional, **_parse_args(args))
    except TypeError as exc:
        raise typer.BadParameter(f"Could not construct {instrument}: {exc}. Pass --resource or --arg.") from exc


@app.command("map")
def map_cmd(
    instrument: Annotated[str, typer.Argument(help=_INSTRUMENT_HELP)],
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Write the map here instead of stdout.")] = None,
    resource: Annotated[str | None, typer.Option(help=_RESOURCE_HELP)] = None,
    arg: Annotated[list[str] | None, typer.Option(help=_ARG_HELP)] = None,
) -> None:
    """Generate an editable JSON channel map for a QCoDeS instrument.

    QCoDeS adds parameters when a driver is constructed, so this connects to the instrument.
    Edit each entry's "channel" to rename it, or set it to null to stop publishing it, then pass
    the file as channel_map= to QcodesInstrument or attach().
    """
    target = _connect(instrument, resource, arg or [])
    try:
        channel_map = ChannelMap.generate(target)
        text = json.dumps(channel_map.to_dict(describe(target)), indent=2) + "\n"
    finally:
        target.close()
    if output is None:
        typer.echo(text, nl=False)
    else:
        output.write_text(text)
        typer.echo(f"Wrote {len(channel_map.channels)} channels to {output}", err=True)


@app.command("describe")
def describe_cmd(
    instrument: Annotated[str, typer.Argument(help=_INSTRUMENT_HELP)],
    resource: Annotated[str | None, typer.Option(help=_RESOURCE_HELP)] = None,
    arg: Annotated[list[str] | None, typer.Option(help=_ARG_HELP)] = None,
) -> None:
    """Print each publishable parameter with its unit and its default command/measurement channels."""
    target = _connect(instrument, resource, arg or [])
    try:
        specs = describe(target)
        rows = ChannelMap.defaults().preview(target)
    finally:
        target.close()
    table = Table(title=instrument)
    for column in ("parameter", "kind", "unit", "command channel", "measurement channel"):
        table.add_column(column)
    for spec, row in zip(specs, rows):
        table.add_row(spec.path, spec.kind, spec.unit, row["command"] or "", row["measurement"] or "")
    Console().print(table)
