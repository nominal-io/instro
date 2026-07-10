#!/usr/bin/env python3
"""Capture ground-truth fixtures from a real QuantusSeries device.

Stdlib only — no pip install needed. Run from any machine that can reach the
device, then send us the output folder (zip it):

    python capture_quantus.py 10.0.0.202 --stream-seconds 10

Saves REST dumps (version, item list, full settings tree, endpoints, stream
setup) as JSON plus a raw binary capture of the data stream. Read-only: it never
PUTs settings or changes device state.
"""

import argparse
import json
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REST_PORT = 8080

REST_DUMPS = {
    "version.json": "/version",
    "info_ping.json": "/info/ping",
    "endpoints.json": "/endpoints",
    "item_list.json": "/item/list",
    "system_settings.json": "/system/settings",
    "datastream_setup.json": "/dataStream/setup",
}


def fetch_json(base: str, path: str):
    with urllib.request.urlopen(base + path, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def capture_rest(base: str, out_dir: Path) -> dict:
    setup = None
    for filename, path in REST_DUMPS.items():
        try:
            payload = fetch_json(base, path)
        except (urllib.error.URLError, OSError) as exc:
            print(f"  WARN {path}: {exc}")
            continue
        (out_dir / filename).write_text(json.dumps(payload, indent=2))
        print(f"  saved {filename}")
        if path == "/dataStream/setup":
            setup = payload
    return setup or {}


def capture_stream(host: str, setup: dict, seconds: float, out_dir: Path):
    port = setup.get("TCPPort")
    if not port:
        print("  WARN no TCPPort in /dataStream/setup response; skipping stream capture")
        return
    print(f"  connecting to stream {host}:{port} for {seconds:.0f}s ...")
    deadline = time.monotonic() + seconds
    total = 0
    with socket.create_connection((host, port), timeout=10) as sock, open(
        out_dir / "stream.bin", "wb"
    ) as f:
        sock.settimeout(5)
        while time.monotonic() < deadline:
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                print("  WARN stream recv timed out (no channels streaming?)")
                break
            if not chunk:
                print("  WARN stream closed by device")
                break
            f.write(chunk)
            total += len(chunk)
    print(f"  saved stream.bin ({total} bytes)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", help="Device IP or mDNS name (e.g. 10.0.0.202)")
    parser.add_argument("--stream-seconds", type=float, default=10.0)
    parser.add_argument("--out", default=None, help="Output directory")
    args = parser.parse_args()

    out_dir = Path(args.out or f"quantus_capture_{time.strftime('%Y%m%d_%H%M%S')}")
    out_dir.mkdir(parents=True, exist_ok=True)
    base = f"http://{args.host}:{REST_PORT}"

    print(f"Capturing from {base} into {out_dir}/")
    setup = capture_rest(base, out_dir)
    if args.stream_seconds > 0:
        capture_stream(args.host, setup, args.stream_seconds, out_dir)
    print(f"Done. Please zip and send the folder: {out_dir}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
