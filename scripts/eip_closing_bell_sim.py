"""Run the closing bell EtherNet/IP simulator."""

from __future__ import annotations

import sys
import time
from pathlib import Path

# Allow direct execution from scripts/ while reusing the existing test helper.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tests.cpppo_sim_server import CpppoTestServer

ADDRESS = "127.0.0.1"
PORT = 44818
STARTUP_TIMEOUT_SECONDS = 10.0
TAGS = {
    "test_bool": ("BOOL", False),
    "test_sint": ("SINT", -3),
    "test_int": ("INT", -12),
    "test_dint": ("DINT", 10),
    "test_lint": ("LINT", -5678),
    "test_usint": ("USINT", 7),
    "test_uint": ("UINT", 42),
    "test_udint": ("UDINT", 99),
    "test_ulint": ("ULINT", 123456),
    "test_real": ("REAL", 1.25),
    "test_lreal": ("LREAL", -9.5),
}


class TerminalStyle:
    def __init__(self) -> None:
        if sys.stdout.isatty():
            self.bold = "\033[1m"
            self.green = "\033[32m"
            self.cyan = "\033[36m"
            self.yellow = "\033[33m"
            self.dim = "\033[2m"
            self.reset = "\033[0m"
        else:
            self.bold = self.green = self.cyan = self.yellow = self.dim = self.reset = ""


def wait_for_server(server: CpppoTestServer) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while not server.ready and server.last_error is None and time.monotonic() < deadline:
        time.sleep(0.1)

    if server.last_error is not None:
        raise RuntimeError(f"cpppo simulator failed to start on {ADDRESS}:{PORT}: {server.last_error}")
    if not server.ready:
        raise RuntimeError(f"cpppo simulator did not become ready on {ADDRESS}:{PORT}")


def print_startup_message(style: TerminalStyle) -> None:
    print(
        f"{style.green}{style.bold}EtherNet/IP simulator running{style.reset} "
        f"{style.cyan}{ADDRESS}:{PORT}{style.reset}",
        flush=True,
    )
    print(f"{style.bold}Route path:{style.reset} {style.dim}none{style.reset}", flush=True)
    print(f"{style.bold}Tags:{style.reset}", flush=True)
    for name, (type_name, initial_value) in TAGS.items():
        print(
            f"  {style.cyan}{name}{style.reset}: "
            f"{style.yellow}{type_name}{style.reset} = {initial_value!r}",
            flush=True,
        )
    print(f"{style.dim}Press Ctrl-C to stop.{style.reset}", flush=True)


def main() -> int:
    server = CpppoTestServer(tags=TAGS, address=ADDRESS, port=PORT)
    server.start()

    try:
        wait_for_server(server)
        print_startup_message(TerminalStyle())

        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        return 0
    finally:
        server.stop()


if __name__ == "__main__":
    raise SystemExit(main())
