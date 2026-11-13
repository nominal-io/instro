import importlib.metadata
import importlib.util
from typing import Annotated

import typer

from instro.cli.discover import discover

_WORKSPACE_PACKAGES = (
    ("instro-contrib", "contrib"),
    ("instro-unstable", "unstable"),
    ("instro-pymeasure", "pymeasure"),
    ("instro-ethernetip", "ethernetip"),
    ("instro-daq-ni", "nidaq"),
    ("instro-daq-labjack", "labjack"),
    ("instro-daq-mcc", "mccdaq"),
    ("instro-i2c-aardvark", "aardvark"),
)

app = typer.Typer()


def _version_callback(value: bool) -> None:
    if not value:
        return
    typer.echo(f"instro {importlib.metadata.version('instro')}")
    for package, extra in _WORKSPACE_PACKAGES:
        try:
            typer.echo(f"{package} {importlib.metadata.version(package)}")
        except importlib.metadata.PackageNotFoundError:
            typer.echo(f'{package} not installed (pip install "instro[{extra}]")')
    raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show installed instro package versions and exit.",
        ),
    ] = False,
) -> None:
    pass


@app.command("discover")
def discover_cmd(
    backend: Annotated[str | None, typer.Option(help="pyvisa backend, e.g. '@py' or '@ivi'")] = None,
) -> None:
    """Scan VISA resources and serial ports for instruments and print a summary table."""
    discover(backend=backend)


def _add_pymeasure_commands() -> None:
    # instro-pymeasure is optional; without it, `instro pymeasure` explains how to install it.
    if importlib.util.find_spec("instro.pymeasure") is not None:
        from instro.pymeasure.cli import app as pymeasure_app

        app.add_typer(pymeasure_app, name="pymeasure")
        return

    @app.command("pymeasure", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
    def pymeasure_missing() -> None:
        """Publish pymeasure instruments (needs the pymeasure extra)."""
        typer.echo('instro-pymeasure is not installed: pip install "instro[pymeasure]"', err=True)
        raise typer.Exit(1)


_add_pymeasure_commands()


if __name__ == "__main__":
    app()
