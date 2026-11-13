# TEMPORARY DIAGNOSTIC for PR #733 (port 5025 collision on py3.11); remove before merge.
import gc
import os
import socket
import threading


def _holders():
    out = []
    for o in gc.get_objects():
        try:
            if isinstance(o, socket.socket) and o.fileno() != -1 and o.getsockname()[1] == 5025:
                out.append(f"{o!r}")
        except Exception:
            pass
    return out


def _listening():
    s = socket.socket()
    s.settimeout(0.2)
    try:
        s.connect(("127.0.0.1", 5025))
        return True
    except Exception:
        return False
    finally:
        s.close()


_seen = {"first": False}


def pytest_runtest_teardown(item, nextitem):
    if _seen["first"]:
        return
    h = _holders()
    if h or _listening():
        _seen["first"] = True
        threads = [t.name for t in threading.enumerate()]
        os.write(
            2,
            f"\n[DIAG5025] held after {item.nodeid}: sockets={h} listening={_listening()} threads={threads}\n".encode(),
        )
