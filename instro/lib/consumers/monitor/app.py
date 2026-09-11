"""Textual app that renders a `MonitorState` as a live channel table."""

import time

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Footer, Header, Static

from instro.lib.consumers.monitor.server import ChannelState, MonitorState

MARK = "⟢"
REFRESH_INTERVAL_S = 0.25

_COLUMNS = (
    ("Instrument", "instrument"),
    ("Channel", "channel"),
    ("Kind", "kind"),
    ("Value", "value"),
    ("Age", "age"),
    ("Rate", "rate"),
    ("Count", "count"),
    ("Source", "source"),
)


def _fmt_value(value: float | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return f"{value:.6g}"


def _fmt_age(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:4.1f}s"
    minutes, secs = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m{secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


class MonitorApp(App[None]):
    """Live table of every channel published to the monitor, grouped by instrument prefix."""

    ENABLE_COMMAND_PALETTE = False
    CSS = """
    #summary {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    DataTable {
        height: 1fr;
    }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("c", "clear", "Clear"),
    ]

    def __init__(self, state: MonitorState, listen_label: str) -> None:
        super().__init__()
        self._state = state
        self._listen_label = listen_label

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(id="summary")
        yield DataTable(id="channels", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        self.title = f"{MARK} instro monitor"
        self.sub_title = f"listening on {self._listen_label}"
        table = self.query_one(DataTable)
        for label, key in _COLUMNS:
            table.add_column(label, key=key)
        self.set_interval(REFRESH_INTERVAL_S, self._refresh)
        self._refresh()

    def action_clear(self) -> None:
        self._state.clear()
        self.query_one(DataTable).clear()

    def _cells(self, state: ChannelState, now: float) -> dict[str, Text]:
        style = "" if state.connected else "dim"
        channel = state.channel.split(".", 1)[1] if "." in state.channel else ""
        source = state.source if state.connected else f"{state.source} (gone)"
        return {
            "instrument": Text(state.instrument, style=style),
            "channel": Text(channel, style=style),
            "kind": Text("C" if state.kind == "command" else "M", style=style),
            "value": Text(_fmt_value(state.value), style=style, justify="right"),
            "age": Text(_fmt_age(now - state.last_received), style=style, justify="right"),
            "rate": Text(f"{state.rate(self._state.rate_window_s, now):.1f}/s", style=style, justify="right"),
            "count": Text(str(state.count), style=style, justify="right"),
            "source": Text(source, style=style),
        }

    def _refresh(self) -> None:
        now = time.monotonic()
        table = self.query_one(DataTable)
        known = {str(key.value) for key in table.rows}
        for state in self._state.snapshot():
            cells = self._cells(state, now)
            if state.channel in known:
                for column, text in cells.items():
                    table.update_cell(state.channel, column, text)
            else:
                table.add_row(*cells.values(), key=state.channel)
        sources = self._state.sources()
        live = sum(1 for alive in sources.values() if alive)
        self.query_one("#summary", Static).update(
            f"{live} connected source(s), {len(sources) - live} gone  |  {len(table.rows)} channel(s)  |  "
            f"{self._state.frames_received} frame(s)"
        )
