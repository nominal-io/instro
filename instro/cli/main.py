import typer

from instro.cli.discover import discover

app = typer.Typer()


@app.callback()
def main() -> None:
    pass


@app.command("discover")
def discover_cmd(backend: str = typer.Option(None, help="pyvisa backend, e.g. '@py' or '@ivi'")) -> None:
    discover(backend=backend)


if __name__ == "__main__":
    app()
