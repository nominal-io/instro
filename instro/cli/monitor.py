import typer

from instro.lib.consumers.monitor.protocol import DEFAULT_HOST, DEFAULT_PORT


def monitor(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    """Listen for `MonitorPublisher` connections and show every channel's latest value in a TUI."""
    from instro.lib.consumers.monitor.app import MonitorApp
    from instro.lib.consumers.monitor.server import MonitorServer, MonitorState

    state = MonitorState()
    try:
        server = MonitorServer(state, host=host, port=port)
    except OSError as e:
        typer.echo(f"Could not listen on {host}:{port}: {e}", err=True)
        raise typer.Exit(code=1)
    server.start()
    try:
        MonitorApp(state, f"{server.host}:{server.port}").run()
    finally:
        server.shutdown()
