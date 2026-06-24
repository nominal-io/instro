r"""Manual SCPI responder — operator acts as the connected instrument.

Starts a TCP socket server and presents a Textual TUI. Incoming SCPI commands
are displayed; the operator types the response that gets sent back to the driver.
Works with any instro driver that accepts a ``TCPIP0::...::SOCKET`` resource.

Usage::

    uv run python -m instro.lib.scpi_manual_sim           # default port 5026
    uv run python -m instro.lib.scpi_manual_sim --port 0  # OS-assigned port

Connect any VISA driver::

    from instro.dmm.drivers.agilent_a34401a import Agilent34401A
    from instro.lib.transports.visa import TerminatorConfig, VisaConfig

    driver = Agilent34401A(
        VisaConfig(
            visa_resource="TCPIP0::127.0.0.1::5026::SOCKET",
            terminator=TerminatorConfig(write="\\n", read="\\n"),
        )
    )
    driver.open()

Write-only commands (no ``?``) are auto-acknowledged with no response.
Queries pause until the operator types a response and presses Enter.
"""

from __future__ import annotations

import argparse
import socket
import threading
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Footer, Header, Input, Label, Log

DEFAULT_PORT = 5026

# ---- Nominal colour palette ----

NOMINAL_MARK = "⟢"
NOMINAL_BACKGROUND = "#121212"
NOMINAL_SURFACE = "#0C0C0C"
NOMINAL_SURFACE_MUTED = "#1A1A1A"
NOMINAL_FOREGROUND = "#FFFFFF"
NOMINAL_FOREGROUND_MUTED = "#A3A3A3"
NOMINAL_FOREGROUND_HIGHLIGHT = "#4ADE80"
NOMINAL_BORDER = "#333333"
NOMINAL_BORDER_MUTED = "#242424"

_CSS_TOKENS = {
    "@background@": NOMINAL_BACKGROUND,
    "@border@": NOMINAL_BORDER,
    "@border-muted@": NOMINAL_BORDER_MUTED,
    "@foreground@": NOMINAL_FOREGROUND,
    "@foreground-highlight@": NOMINAL_FOREGROUND_HIGHLIGHT,
    "@foreground-muted@": NOMINAL_FOREGROUND_MUTED,
    "@surface@": NOMINAL_SURFACE,
    "@surface-muted@": NOMINAL_SURFACE_MUTED,
}


def _css(source: str) -> str:
    for token, value in _CSS_TOKENS.items():
        source = source.replace(token, value)
    return source


# ---- Command model ----


class CommandKind(Enum):
    WRITE = "write"  # no ? — acknowledged silently
    QUERY = "query"  # contains ? — needs a human response


@dataclass
class PendingCommand:
    text: str
    kind: CommandKind
    response_event: threading.Event = field(default_factory=threading.Event)
    response: str = ""

    @staticmethod
    def from_text(text: str) -> "PendingCommand":
        kind = CommandKind.QUERY if "?" in text else CommandKind.WRITE
        return PendingCommand(text=text, kind=kind)


# ---- TCP server ----


class ManualSCPIServer:
    """TCP socket server that routes SCPI commands to the TUI operator."""

    def __init__(self, host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> None:
        self._host = host
        self._port = port
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._socket: socket.socket | None = None
        self._pending: PendingCommand | None = None
        self._lock = threading.Lock()
        self.on_command: list[Callable[[PendingCommand], None]] = []

    @property
    def port(self) -> int:
        return self._port

    @property
    def host(self) -> str:
        return self._host

    def start(self) -> None:
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind((self._host, self._port))
        self._port = self._socket.getsockname()[1]
        self._socket.listen(1)
        self._socket.settimeout(0.5)
        self._thread = threading.Thread(target=self._run, daemon=True, name="scpi-manual-sim")
        self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        with self._lock:
            if self._pending is not None:
                self._pending.response = ""
                self._pending.response_event.set()
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def submit_response(self, response: str) -> None:
        """Called from the TUI when the operator submits a response."""
        with self._lock:
            if self._pending is not None:
                self._pending.response = response
                self._pending.response_event.set()

    def _run(self) -> None:
        assert self._socket is not None
        while not self._stop.is_set():
            try:
                conn, _ = self._socket.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                self._handle_client(conn)
            except Exception:
                pass
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    def _handle_client(self, conn: socket.socket) -> None:
        conn.settimeout(0.5)
        buf = b""
        while not self._stop.is_set():
            try:
                data = conn.recv(1024)
            except socket.timeout:
                continue
            except OSError:
                return
            if not data:
                return
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                text = line.decode(errors="replace").strip()
                if not text:
                    continue
                cmd = PendingCommand.from_text(text)

                with self._lock:
                    self._pending = cmd

                for cb in self.on_command:
                    cb(cmd)

                if cmd.kind == CommandKind.QUERY:
                    cmd.response_event.wait()
                    try:
                        conn.sendall((cmd.response + "\n").encode())
                    except OSError:
                        return

                with self._lock:
                    self._pending = None


# ---- Textual TUI ----


class _CommandReceived(Message):
    def __init__(self, cmd: PendingCommand) -> None:
        super().__init__()
        self.cmd = cmd


class ManualSCPIApp(App[None]):
    """TUI where the operator manually responds to SCPI queries from a driver."""

    ENABLE_COMMAND_PALETTE = False

    CSS = _css("""
    Screen {
        layout: vertical;
        background: @background@;
        color: @foreground@;
    }

    Header {
        background: @surface@;
        color: @foreground@;
    }

    Footer {
        background: @surface@;
        color: @foreground-muted@;
    }

    #body {
        height: 1fr;
        padding: 0 1;
    }

    #log-pane {
        height: 1fr;
        border: solid @border@;
        padding: 0 1;
    }

    #log-pane Log {
        height: 1fr;
        background: @background@;
        color: @foreground-muted@;
        scrollbar-background: @background@;
        scrollbar-color: @border@;
    }

    #prompt-pane {
        height: auto;
        border: solid @border@;
        padding: 0 1;
        margin-top: 1;
    }

    #prompt-pane.waiting {
        border: solid @foreground-highlight@;
    }

    #pending-label {
        color: @foreground-muted@;
        padding: 0 0 1 0;
    }

    #pending-label.query {
        color: @foreground-highlight@;
    }

    #pending-command {
        color: @foreground@;
        text-style: bold;
        padding: 0 0 1 0;
    }

    #response-input {
        border: solid @border-muted@;
        background: @surface-muted@;
        color: @foreground@;
    }

    #response-input:focus {
        border: solid @foreground@;
    }

    #hint {
        color: @foreground-muted@;
        padding: 1 0 0 0;
    }
    """)

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, server: ManualSCPIServer) -> None:
        super().__init__()
        self._server = server
        self._waiting_for_query = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="body"):
            with Vertical(id="log-pane"):
                yield Label("Command log", markup=False)
                yield Log(id="log", auto_scroll=True)
            with Vertical(id="prompt-pane"):
                yield Label("No command yet", id="pending-label", markup=False)
                yield Label("", id="pending-command", markup=False)
                yield Input(placeholder="Type response and press Enter…", id="response-input")
                yield Label(
                    "Write-only commands are auto-acknowledged. "
                    'Type a response for queries (e.g. +1.234E+00 or 0,"No error").',
                    id="hint",
                    markup=False,
                )
        yield Footer()

    def on_mount(self) -> None:
        self.title = f"{NOMINAL_MARK} Nominal instro"
        self.sub_title = f"SCPI manual sim | {self._server.host}:{self._server.port}"
        self._server.on_command.append(self._on_server_command)
        self.query_one("#response-input", Input).disabled = True

    def _on_server_command(self, cmd: PendingCommand) -> None:
        self.call_from_thread(self.post_message, _CommandReceived(cmd))

    def on__command_received(self, event: _CommandReceived) -> None:
        cmd = event.cmd
        log = self.query_one("#log", Log)
        label = self.query_one("#pending-label", Label)
        command_display = self.query_one("#pending-command", Label)
        inp = self.query_one("#response-input", Input)
        prompt_pane = self.query_one("#prompt-pane")

        if cmd.kind == CommandKind.WRITE:
            log.write_line(f"W  {cmd.text}")
            label.update("Write (auto-acknowledged)")
            label.remove_class("query")
            command_display.update(cmd.text)
            prompt_pane.remove_class("waiting")
            inp.disabled = True
        else:
            log.write_line(f"Q  {cmd.text}")
            label.update("Query — type response below:")
            label.add_class("query")
            command_display.update(cmd.text)
            prompt_pane.add_class("waiting")
            inp.disabled = False
            inp.clear()
            inp.focus()
            self._waiting_for_query = True

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if not self._waiting_for_query:
            return
        response = event.value.strip()
        log = self.query_one("#log", Log)
        inp = self.query_one("#response-input", Input)
        prompt_pane = self.query_one("#prompt-pane")
        label = self.query_one("#pending-label", Label)

        log.write_line(f"R  {response}")
        self._server.submit_response(response)
        self._waiting_for_query = False
        inp.clear()
        inp.disabled = True
        prompt_pane.remove_class("waiting")
        label.update("Waiting for next command…")
        label.remove_class("query")


# ---- Entry point ----


def main() -> None:
    parser = argparse.ArgumentParser(description="Manual SCPI responder — operator acts as the instrument")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    server = ManualSCPIServer(host=args.host, port=args.port)
    server.start()
    try:
        ManualSCPIApp(server).run()
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
