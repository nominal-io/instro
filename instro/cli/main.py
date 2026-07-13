import importlib.metadata
from typing import Annotated

import typer

from instro.cli.discover import discover

_WORKSPACE_PACKAGES = (
    ("instro-contrib", "contrib"),
    ("instro-unstable", "unstable"),
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


if __name__ == "__main__":
    app()
