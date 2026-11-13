# TEMPORARY DIAGNOSTIC for PR #733 (port 5025 collision on py3.11); remove before merge.
import gc
import os
import socket as _socket
import subprocess
import sys
import threading

_orig_bind = _socket.socket.bind


def _dump(addr):
    lines = [f"[DIAG5025] bind{addr} failed. python={sys.version.split()[0]} pid={os.getpid()}"]
    lines.append("threads=" + repr([t.name for t in threading.enumerate()]))
    for o in gc.get_objects():
        try:
            if isinstance(o, _socket.socket) and o.fileno() != -1:
                lines.append(f"  sock {o!r}")
        except Exception:
            pass
    cmds = (["netstat", "-ano"],) if sys.platform == "win32" else (["netstat", "-an"],)
    for cmd in cmds:
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout
            lines += ["  net: " + ln for ln in out.splitlines() if "5025" in ln]
        except Exception as e:
            lines.append(f"  netstat failed: {e}")
    try:
        import psutil  # noqa: F401
    except Exception:
        pass
    os.write(2, ("\n".join(lines) + "\n").encode())


def _bind(self, address):
    try:
        return _orig_bind(self, address)
    except OSError:
        if isinstance(address, tuple) and len(address) > 1 and address[1] == 5025:
            _dump(address)
        raise


_socket.socket.bind = _bind  # type: ignore[method-assign]
