import typer

from instro.cli.discover import discover

app = typer.Typer()


@app.command("discover")
def discover_cmd(backend: str = typer.Option(None, help="pyvisa backend, e.g. '@py' or '@ivi'")) -> None:
    discover(backend=backend)


@app.command()
def doctor() -> None:
    pass


if __name__ == "__main__":
    app()
