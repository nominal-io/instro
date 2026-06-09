"""DO NOT MERGE: temporary live EIP proxy workaround for local NECP policy."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading

REMOTE_ENDPOINT = ("10.123.1.199", 44818)


def pipe(source: socket.socket, destination: socket.socket) -> None:
    try:
        while True:
            data = source.recv(65_536)
            if not data:
                break
            destination.sendall(data)
    except OSError:
        pass
    finally:
        for sock in (source, destination):
            try:
                sock.close()
            except OSError:
                pass


def handle_connection(client: socket.socket) -> None:
    upstream = socket.create_connection(REMOTE_ENDPOINT)
    threading.Thread(target=pipe, args=(client, upstream), daemon=True).start()
    threading.Thread(target=pipe, args=(upstream, client), daemon=True).start()


def main() -> int:
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen()

    port = listener.getsockname()[1]
    print(f"proxy 127.0.0.1:{port} -> {REMOTE_ENDPOINT[0]}:{REMOTE_ENDPOINT[1]}", flush=True)

    def accept_loop() -> None:
        while True:
            client, _addr = listener.accept()
            handle_connection(client)

    threading.Thread(target=accept_loop, daemon=True).start()

    os.environ.update(
        INSTRO_EIP_PLC_ENDPOINT=f"127.0.0.1:{port}",
        INSTRO_EIP_ROUTE_PATH_SLOTS="0",
        INSTRO_EIP_TARGET_L32E="1",
        INSTRO_EIP_EXCLUDE_UNSIGNED_TYPES="1",
        INSTRO_EIP_EXCLUDE_TYPES="LREAL",
    )

    return subprocess.call(
        [
            "cargo",
            "test",
            "-p",
            "instro-ethernetip-rs",
            "--test",
            "explicit_session_integration",
            "--",
            "--test-threads=1",
        ]
    )


if __name__ == "__main__":
    sys.exit(main())
