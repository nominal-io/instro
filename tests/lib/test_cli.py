"""Tests for the `instro` CLI doctor command."""

from __future__ import annotations

import importlib
from io import StringIO
from unittest.mock import patch

import pytest

from instro.utils import cli


@pytest.fixture
def capture(monkeypatch):
    """Run `cli.main(...)` against an isolated rich Console + capture stdout.

    Returns a callable taking argv -> (exit_code, stdout_text).
    """

    def _run(argv: list[str]) -> tuple[int, str]:
        buf = StringIO()
        from rich.console import Console as RealConsole

        def _fixed_console(*args, **kwargs):
            kwargs.setdefault("file", buf)
            kwargs.setdefault("width", 120)
            kwargs.setdefault("force_terminal", False)
            return RealConsole(*args, **kwargs)

        monkeypatch.setattr(cli, "Console", _fixed_console)
        code = cli.main(argv)
        return code, buf.getvalue()

    return _run


def _fake_find_spec(present: set[str]):
    """Pretend only the modules in `present` exist."""

    def _impl(name: str):
        return object() if name in present else None

    return _impl


def _fake_find_native(found: dict[str, str]):
    """Pretend the listed native libs are installed, returning the given paths."""

    def _impl(names):
        for name in names:
            if name in found:
                return found[name]
        return None

    return _impl


@patch("instro.utils.cli.platform.system", return_value="Linux")
def test_doctor_all_present(_mock_os, capture, monkeypatch):
    """Every package installed + every native lib found: exit 0, 'Ready to go'."""
    extras_present = {
        "instro.daq.drivers.labjack",
        "instro.daq.drivers.mcc",
        "instro.daq.drivers.ni",
        "instro.i2c.drivers.totalphase",
    }
    monkeypatch.setattr(cli.importlib.util, "find_spec", _fake_find_spec(extras_present))
    monkeypatch.setattr(
        cli,
        "_find_native_lib",
        _fake_find_native(
            {
                "visa": "/usr/lib/libvisa.so",
                "LabJackM": "/usr/local/lib/libLabJackM.so",
                "nidaqmx": "/usr/lib/libnidaqmx.so",
                "cbw": "/usr/lib/libcbw.so",
                "aardvark": "/usr/lib/libaardvark.so",
            }
        ),
    )
    monkeypatch.setattr(cli, "_version_of", lambda _dist: "1.2.3")

    code, out = capture(["doctor"])

    assert code == 0
    assert "Found:" in out
    # Bigger emoji-width icons should be present.
    assert "✅" in out


def test_doctor_visa_missing_still_exits_zero(capture, monkeypatch):
    """The doctor is purely informational — it always exits 0 on a clean run.

    Even when VISA is missing, the doctor's job is to surface that fact via the
    table, not to fail. Reserve exit 1+ for tooling errors (see exit 2 below).
    """
    monkeypatch.setattr(cli.importlib.util, "find_spec", _fake_find_spec(set()))
    monkeypatch.setattr(cli, "_find_native_lib", _fake_find_native({}))
    monkeypatch.setattr(cli, "_version_of", lambda dist: "0.6.0" if dist == "instro" else None)

    code, out = capture(["doctor"])

    assert code == 0
    # The VISA row should still show ❌ in the table.
    assert "VISA backend" in out
    assert "❌" in out


def test_visa_python_fallback_satisfies_backend(capture, monkeypatch):
    """pyvisa-py (pure-Python backend) should count as a VISA backend."""
    monkeypatch.setattr(cli.importlib.util, "find_spec", _fake_find_spec({"pyvisa_py"}))
    monkeypatch.setattr(cli, "_find_native_lib", _fake_find_native({}))
    monkeypatch.setattr(cli, "_version_of", lambda dist: "0.6.0" if dist == "instro" else None)

    code, out = capture(["doctor"])

    assert code == 0
    assert "Using Python backend: pyvisa_py" in out


def test_doctor_optional_extras_missing(capture, monkeypatch):
    """Optional packages absent: still exit 0, each row shows pip install hint."""
    monkeypatch.setattr(cli.importlib.util, "find_spec", _fake_find_spec(set()))
    monkeypatch.setattr(cli, "_find_native_lib", _fake_find_native({"visa": "/usr/lib/libvisa.so"}))
    monkeypatch.setattr(cli, "_version_of", lambda dist: "0.6.0" if dist == "instro" else None)

    code, out = capture(["doctor"])

    assert code == 0
    # Every missing extras should show its pip install hint.
    for extras_name in ("daq-labjack", "daq-mcc", "daq-ni", "i2c-aardvark"):
        assert f"pip install 'instro[{extras_name}]'" in out


def test_native_lib_found_shows_path(capture, monkeypatch):
    """When the system driver is detected, the doctor shows the resolved path."""
    monkeypatch.setattr(
        cli.importlib.util,
        "find_spec",
        _fake_find_spec({"instro.daq.drivers.labjack"}),
    )
    monkeypatch.setattr(
        cli,
        "_find_native_lib",
        _fake_find_native(
            {
                "visa": "/Library/Frameworks/visa.framework/visa",
                "LabJackM": "/usr/local/lib/libLabJackM.dylib",
            }
        ),
    )
    monkeypatch.setattr(cli, "_version_of", lambda _dist: "1.0.0")

    _, out = capture(["doctor"])
    # The LJM row should be ✓ with the resolved path visible to the user.
    assert "/usr/local/lib/libLabJackM.dylib" in out
    # And the VISA framework path on macOS-style detection.
    assert "/Library/Frameworks/visa.framework/visa" in out


def test_native_lib_missing_shows_install_hint(capture, monkeypatch):
    """When the native lib is absent on a supported OS, the install hint must be visible."""
    monkeypatch.setattr(cli.importlib.util, "find_spec", _fake_find_spec({"pyvisa_py"}))
    monkeypatch.setattr(cli, "_find_native_lib", _fake_find_native({}))
    monkeypatch.setattr(cli, "_version_of", lambda _dist: "0.6.0")

    _, out = capture(["doctor"])
    # No native LJM → install hint surfaced
    assert "labjack.com" in out
    # No native Aardvark → install hint surfaced
    assert "totalphase.com" in out
    # Missing rows should use the ❌ glyph.
    assert "❌" in out


@patch("instro.utils.cli.platform.system", return_value="Darwin")
def test_macos_unsupported_drivers_marked_na(_mock_os, capture, monkeypatch):
    """On macOS, NI-DAQmx and MCC drivers should show 'Not supported on macOS'."""
    monkeypatch.setattr(cli.importlib.util, "find_spec", _fake_find_spec({"pyvisa_py"}))
    monkeypatch.setattr(cli, "_find_native_lib", _fake_find_native({}))
    monkeypatch.setattr(cli, "_version_of", lambda _dist: "0.6.0")

    _, out = capture(["doctor"])
    assert "Not supported on macOS" in out
    assert "NI-DAQmx runtime" in out
    assert "MCC Universal Library" in out
    # Unsupported-on-OS rows should use the ⛔ glyph.
    assert "⛔" in out


@patch("instro.utils.cli.platform.system", return_value="Linux")
def test_linux_mcc_unsupported(_mock_os, capture, monkeypatch):
    """On Linux, MCC is Windows-only."""
    monkeypatch.setattr(cli.importlib.util, "find_spec", _fake_find_spec({"pyvisa_py"}))
    monkeypatch.setattr(cli, "_find_native_lib", _fake_find_native({}))
    monkeypatch.setattr(cli, "_version_of", lambda _dist: "0.6.0")

    _, out = capture(["doctor"])
    assert "Not supported on Linux" in out


@patch("instro.utils.cli.platform.system", return_value="Windows")
def test_windows_supports_everything(_mock_os, capture, monkeypatch):
    """On Windows every driver's OS gate passes."""
    monkeypatch.setattr(cli.importlib.util, "find_spec", _fake_find_spec({"pyvisa_py"}))
    monkeypatch.setattr(cli, "_find_native_lib", _fake_find_native({}))
    monkeypatch.setattr(cli, "_version_of", lambda _dist: "0.6.0")

    _, out = capture(["doctor"])
    assert "Not supported on Windows" not in out


def test_internal_error_returns_2(capture, monkeypatch):
    """Uncaught exception inside the command must exit 2, not 1."""

    def _boom(_args):
        raise RuntimeError("simulated internal failure")

    monkeypatch.setattr(cli, "_cmd_doctor", _boom)

    code, _ = capture(["doctor"])
    assert code == 2


def test_no_command_errors(capture):
    """Argparse fails with SystemExit when no subcommand is given."""
    with pytest.raises(SystemExit):
        capture([])


def test_doctor_invokable_as_module():
    """`python -m instro.utils.cli doctor` should be a valid invocation path."""
    mod = importlib.import_module("instro.utils.cli")
    assert callable(mod.main)


def test_output_is_inside_one_outer_panel(capture, monkeypatch):
    """Everything renders inside a single outer box — runtime + table + verdict all framed."""
    monkeypatch.setattr(cli.importlib.util, "find_spec", _fake_find_spec(set()))
    monkeypatch.setattr(cli, "_find_native_lib", _fake_find_native({"visa": "/usr/lib/libvisa.so"}))
    monkeypatch.setattr(cli, "_version_of", lambda _dist: "0.6.0")

    _, out = capture(["doctor"])
    open_idx = out.index("╭")
    close_idx = out.rindex("╯")
    inner = out[open_idx:close_idx]
    assert "Python" in inner
    assert "Component" in inner


def test_no_trailing_verdict_line(capture, monkeypatch):
    """The doctor doesn't render a 'Ready to go' / 'Required driver(s) missing' verdict."""
    monkeypatch.setattr(cli.importlib.util, "find_spec", _fake_find_spec(set()))
    monkeypatch.setattr(cli, "_find_native_lib", _fake_find_native({}))
    monkeypatch.setattr(cli, "_version_of", lambda _dist: "0.6.0")
    _, out = capture(["doctor"])
    assert "Ready to go" not in out
    assert "Required driver" not in out


def test_table_has_two_columns_not_three(capture, monkeypatch):
    """Status + Notes were consolidated into one column — header should not have separate 'Notes'."""
    monkeypatch.setattr(cli.importlib.util, "find_spec", _fake_find_spec(set()))
    monkeypatch.setattr(cli, "_find_native_lib", _fake_find_native({"visa": "/usr/lib/libvisa.so"}))
    monkeypatch.setattr(cli, "_version_of", lambda _dist: "0.6.0")

    _, out = capture(["doctor"])
    # Component column should be present.
    assert "Component" in out
    # Notes column should NOT exist as a separate header — its content merged into Status.
    assert "Notes" not in out


def test_find_native_lib_uses_ctypes_find_then_load(monkeypatch):
    """`_find_native_lib` must call find_library AND verify with CDLL.

    Regression guard: find_library can return a stale-cache hit on macOS for libs
    that aren't actually loadable. CDLL is what tells us the lib really works.
    """
    calls: list[str] = []

    def _fake_find_library(name):
        calls.append(("find", name))
        return f"/fake/path/lib{name}.dylib"

    def _fake_cdll(path):
        calls.append(("load", path))
        return object()  # success

    monkeypatch.setattr(cli.ctypes.util, "find_library", _fake_find_library)
    monkeypatch.setattr(cli.ctypes, "CDLL", _fake_cdll)

    result = cli._find_native_lib(["LabJackM"])
    assert result == "/fake/path/libLabJackM.dylib"
    assert ("find", "LabJackM") in calls
    assert ("load", "/fake/path/libLabJackM.dylib") in calls


def test_find_native_lib_skips_unloadable(monkeypatch):
    """If CDLL raises OSError, try the next name rather than reporting installed."""

    def _fake_find_library(name):
        # Both names "resolve" but the first one will fail to load.
        return f"/fake/path/lib{name}.dylib"

    def _fake_cdll(path):
        if "broken" in path:
            raise OSError("simulated load failure")
        return object()

    monkeypatch.setattr(cli.ctypes.util, "find_library", _fake_find_library)
    monkeypatch.setattr(cli.ctypes, "CDLL", _fake_cdll)

    # First name is unloadable, second loads → second is reported.
    assert cli._find_native_lib(["broken", "working"]) == "/fake/path/libworking.dylib"
    # All names unloadable → None.
    assert cli._find_native_lib(["broken"]) is None
