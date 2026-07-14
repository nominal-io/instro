#!/usr/bin/env python3
"""Capture ground-truth fixtures from a real QuantusSeries device.

Stdlib only — no pip install needed. Run from any machine that can reach the
device, then send us the output folder (zip it):

    python capture_quantus.py 10.0.0.202 --stream-seconds 10

Saves REST dumps (version, item list, full settings tree, per-item settings,
stream setup) as JSON plus a raw binary capture of the data stream. Read-only:
it never PUTs settings or changes device state.
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

# Values are (filename, [candidate paths]) — the manual and the vendor's own
# examples disagree on URL casing, so ambiguous endpoints list both.
REST_DUMPS = [
    ("version.json", ["/version/"]),
    ("info_ping.json", ["/info/ping/"]),
    ("endpoints.json", ["/endpoints/"]),
    ("item_list.json", ["/item/list/"]),
    ("system_settings.json", ["/system/settings/"]),
    ("datastream_setup.json", ["/dataStream/setup/", "/datastream/setup/"]),
    ("autozero_settings.json", ["/autoZero/settings/", "/autozero/settings/"]),
    ("canfd_bus_status.json", ["/canfd/bus/status/list/"]),
]

# A LAN DAQ must never be reached through a corporate proxy.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def fetch_json(base: str, path: str):
    with OPENER.open(base + path, timeout=10) as resp:
        body = resp.read()
    if not body:
        return None  # e.g. 204 No Content
    return json.loads(body.decode("utf-8"))


def fetch_first(base: str, paths):
    """Try candidate paths in order; return (payload, error) with error set only if all failed."""
    error = None
    for path in paths:
        try:
            return fetch_json(base, path), None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            error = f"{path}: {exc}"
    return None, error


def save_json(target: Path, payload):
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def capture_rest(base: str, out_dir: Path) -> dict:
    setup = None
    item_list = None
    for filename, paths in REST_DUMPS:
        payload, error = fetch_first(base, paths)
        if error is not None:
            print(f"  WARN {error}")
            continue
        save_json(out_dir / filename, payload)
        print(f"  saved {filename}")
        if filename == "datastream_setup.json":
            setup = payload
        if filename == "item_list.json":
            item_list = payload
    capture_items(base, item_list, out_dir)
    return setup if isinstance(setup, dict) else {}


def capture_items(base: str, item_list, out_dir: Path):
    """Dump each item's settings/operationMode docs (the firmware's real names, enums, ids)."""
    if not isinstance(item_list, list):
        if item_list is not None:
            print(f"  WARN /item/list/ body is not a JSON array (got {type(item_list).__name__}); send it anyway")
        return
    items_dir = out_dir / "items"
    items_dir.mkdir(exist_ok=True)
    for item in item_list:
        if not isinstance(item, dict) or "ItemId" not in item:
            continue
        item_id = item["ItemId"]
        name = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(item.get("ItemName", "unknown")))
        for kind in ("settings", "operationMode"):
            payload, error = fetch_first(base, [f"/item/{kind}/?itemId={item_id}"])
            if error is not None:
                print(f"  WARN {error}")
                continue
            save_json(items_dir / f"item_{item_id:03d}_{name}_{kind}.json", payload)
    print(f"  saved per-item settings/operationMode docs to {items_dir.name}/")


def capture_stream(host: str, setup: dict, seconds: float, out_dir: Path):
    port = setup.get("TCPPort")
    if not port:
        print("  WARN no TCPPort in the stream setup response; skipping stream capture")
        return
    print(f"  connecting to stream {host}:{port} for {seconds:.0f}s ...")
    deadline = time.monotonic() + seconds
    total = 0
    try:
        with socket.create_connection((host, port), timeout=10) as sock, open(out_dir / "stream.bin", "wb") as f:
            sock.settimeout(2)
            while time.monotonic() < deadline:
                try:
                    chunk = sock.recv(65536)
                except socket.timeout:
                    continue  # sparse traffic (e.g. CAN-only) is normal; wait out the window
                if not chunk:
                    print("  WARN stream closed by device (another client connected, or nothing streaming)")
                    break
                f.write(chunk)
                total += len(chunk)
    except OSError as exc:
        print(f"  WARN stream connect failed: {exc}")
        return
    print(f"  saved stream.bin ({total} bytes){' - empty is fine, send it anyway' if total == 0 else ''}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", help="Device IP or mDNS name (e.g. 10.0.0.202)")
    parser.add_argument("--stream-seconds", type=float, default=10.0)
    parser.add_argument("--port", type=int, default=REST_PORT, help="QServer REST port")
    parser.add_argument("--out", default=None, help="Output directory")
    args = parser.parse_args()

    out_dir = Path(args.out or f"quantus_capture_{time.strftime('%Y%m%d_%H%M%S')}")
    out_dir.mkdir(parents=True, exist_ok=True)
    base = f"http://{args.host}:{args.port}"

    print(f"Capturing from {base} into {out_dir}/")
    setup = capture_rest(base, out_dir)
    if args.stream_seconds > 0:
        capture_stream(args.host, setup, args.stream_seconds, out_dir)
    print(f"Done. Please zip and send the folder: {out_dir}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
