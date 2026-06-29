"""Shared cpppo-backed EtherNet/IP simulator support for integration tests."""

from __future__ import annotations

import argparse
import errno
import socket
import sys
import threading
import time
from typing import Any

STARTUP_TIMEOUT_SECONDS = 10.0
MAX_STARTUP_ATTEMPTS = 10
WINDOWS_SOCKET_ACCESS_DENIED = 10013


class CpppoTestServer:
    """Minimal in-process cpppo EtherNet/IP server for integration tests."""

    def __init__(self, tags: dict[str, tuple[str, Any]], address: str, port: int):
        self.tags = tags
        self.address = address
        self.port = port
        self._stop_event = threading.Event()
        self._ready = threading.Event()
        self._server_control: dict[str, Any] | None = None
        self._thread: threading.Thread | None = None
        self._last_error: Exception | None = None

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    @property
    def last_error(self) -> Exception | None:
        return self._last_error

    def start(self) -> None:
        self._stop_event.clear()
        self._ready.clear()
        self._server_control = None
        self._last_error = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._server_control is not None:
            try:
                self._server_control["done"] = True
            except Exception:
                pass

        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

        self._ready.clear()

    def _run(self) -> None:
        from cpppo.server.enip.main import main as enip_main
        from cpppo.server.enip.main import tags as cpppo_tags_global

        tag_args = [f"{name}={type_name}" for name, (type_name, _value) in self.tags.items()]
        initialized = False

        def idle_sync() -> None:
            nonlocal initialized

            if self._stop_event.is_set():
                raise KeyboardInterrupt()

            if initialized:
                return

            initialized = True

            try:
                from cpppo.server.enip.main import srv_ctl as server_control

                if "control" in server_control:
                    self._server_control = server_control["control"]
            except Exception:
                self._server_control = None

            for name, (_type_name, value) in self.tags.items():
                cpppo_tags_global[name].attribute[0] = value

            self._ready.set()

        try:
            enip_main(
                argv=build_cpppo_argv(tag_args, self.address, self.port),
                idle_service=idle_sync,
            )
        except KeyboardInterrupt:
            pass
        except Exception as exc:
            self._last_error = exc
            if is_retryable_bind_error(exc):
                return
            raise
        finally:
            self._ready.clear()


def reserve_local_port(address: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp_sock:
        tcp_sock.bind((address, 0))
        tcp_sock.listen(1)
        return tcp_sock.getsockname()[1]


def build_cpppo_argv(tag_args: list[str], address: str, port: int) -> list[str]:
    return tag_args + ["--address", f"{address}:{port}", "--no-udp"]


def is_retryable_bind_error(error: Exception | None) -> bool:
    if not isinstance(error, OSError):
        return False
    return error.errno == errno.EADDRINUSE or getattr(error, "winerror", None) == WINDOWS_SOCKET_ACCESS_DENIED


def parse_tag_spec(spec: str) -> tuple[str, tuple[str, Any]]:
    try:
        name, type_name, startval = spec.split(",", 2)
    except ValueError as error:
        raise ValueError(f"invalid --tag value '{spec}'; expected name,type,startval") from error

    type_name = type_name.upper()
    if type_name == "BOOL":
        value = parse_bool(startval)
    elif type_name in {"SINT", "INT", "DINT", "LINT"}:
        value = int(startval)
    elif type_name in {"USINT", "UINT", "UDINT", "ULINT"}:
        value = int(startval)
    elif type_name in {"REAL", "LREAL"}:
        value = float(startval)
    elif type_name == "STRING":
        value = startval
    else:
        raise ValueError(f"unsupported cpppo simulator tag type '{type_name}' in '{spec}'")

    return name, (type_name, value)


def parse_bool(value: str) -> bool:
    if value in {"1", "true", "TRUE", "True"}:
        return True
    if value in {"0", "false", "FALSE", "False"}:
        return False
    raise ValueError(f"invalid BOOL value '{value}'")


def start_server_with_retries(
    tags: dict[str, tuple[str, Any]],
    *,
    address: str = "127.0.0.1",
) -> tuple[CpppoTestServer, int]:
    last_error: Exception | None = None

    for _ in range(MAX_STARTUP_ATTEMPTS):
        try:
            port = reserve_local_port(address)
        except OSError as exc:
            if is_retryable_bind_error(exc):
                last_error = exc
                continue
            raise

        server = CpppoTestServer(tags=tags, address=address, port=port)
        server.start()

        deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if server.ready:
                return server, port
            if server.last_error is not None:
                break
            time.sleep(0.1)

        if is_retryable_bind_error(server.last_error):
            server.stop()
            last_error = server.last_error
            continue

        server.stop()
        raise RuntimeError(f"cpppo simulator did not become ready on {address}:{port}: {server.last_error}")

    raise RuntimeError(f"cpppo simulator failed to bind after {MAX_STARTUP_ATTEMPTS} attempts: {last_error}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--address", default="127.0.0.1")
    parser.add_argument(
        "--tag",
        action="append",
        default=[],
        metavar="NAME,TYPE,STARTVAL",
        help="tag definition to expose from the simulator",
    )
    args = parser.parse_args()

    tags = dict(parse_tag_spec(spec) for spec in args.tag)
    if not tags:
        raise ValueError("at least one --tag NAME,TYPE,STARTVAL value is required")

    server, port = start_server_with_retries(
        tags=tags,
        address=args.address,
    )

    print(f"{args.address}:{port}", flush=True)

    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()

    return 0


if __name__ == "__main__":
    sys.exit(main())
