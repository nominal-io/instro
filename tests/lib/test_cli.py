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
    """Return a find_spec replacement that pretends only `present` modules exist."""
    def _impl(name: str):
        return object() if name in present else None
    return _impl


@patch("instro.utils.cli.platform.system", return_value="Linux")
def test_doctor_all_present(_mock_os, capture, monkeypatch):
    """Every package + driver installed on a supported OS: exit 0, 'Ready to go'."""
    everything = {
        "instro.daq.drivers.labjack",
        "instro.daq.drivers.mcc",
        "instro.daq.drivers.ni",
        "instro.i2c.drivers.totalphase",
        "pyvisa",
        "nidaqmx",
        "labjack.ljm",
        "mcculw",
        "pyaardvark",
    }
    monkeypatch.setattr(cli.importlib.util, "find_spec", _fake_find_spec(everything))
    monkeypatch.setattr(cli, "_try_import", lambda _name: (True, None))
    monkeypatch.setattr(cli, "_version_of", lambda _dist: "1.2.3")

    code, out = capture(["doctor"])

    assert code == 0
    assert "Ready to go" in out
    # Package-down-to-driver structure: each package has a `└ driver` row below it.
    assert "instro[daq-labjack]" in out
    assert "└" in out  # tree indicator


def test_doctor_pyvisa_missing(capture, monkeypatch):
    """PyVISA is the only universally-required driver; missing → exit 1."""
    monkeypatch.setattr(cli.importlib.util, "find_spec", _fake_find_spec(set()))
    monkeypatch.setattr(cli, "_try_import", lambda _name: (False, "ModuleNotFoundError: pyvisa"))
    monkeypatch.setattr(cli, "_version_of", lambda dist: "0.6.0" if dist == "instro" else None)

    code, out = capture(["doctor"])

    assert code == 1
    assert "PyVISA" in out
    assert "Required driver" in out


def test_doctor_optional_missing_but_visa_ok(capture, monkeypatch):
    """Optional packages absent but PyVISA present: exit 0."""
    monkeypatch.setattr(cli.importlib.util, "find_spec", _fake_find_spec({"pyvisa"}))
    monkeypatch.setattr(cli, "_try_import", lambda name: (name == "pyvisa", None if name == "pyvisa" else "missing"))
    monkeypatch.setattr(cli, "_version_of", lambda dist: "1.15.0" if dist == "pyvisa" else "0.6.0")

    code, out = capture(["doctor"])

    assert code == 0
    assert "Ready to go" in out
    # Each missing extras should show its pip install hint.
    for extras_name in ("daq-labjack", "daq-mcc", "daq-ni", "i2c-aardvark"):
        assert f"pip install 'instro[{extras_name}]'" in out


@patch("instro.utils.cli.platform.system", return_value="Darwin")
def test_macos_unsupported_drivers_marked_na(_mock_os, capture, monkeypatch):
    """On macOS, NI-DAQmx and MCC drivers should show 'Not supported on macOS'."""
    monkeypatch.setattr(cli.importlib.util, "find_spec", _fake_find_spec({"pyvisa"}))
    monkeypatch.setattr(cli, "_try_import", lambda name: (name == "pyvisa", None))
    monkeypatch.setattr(cli, "_version_of", lambda dist: "1.15.0" if dist == "pyvisa" else "0.6.0")

    _, out = capture(["doctor"])
    # Both unsupported drivers should show the not-supported notice.
    assert "Not supported on macOS" in out
    assert "NI-DAQmx" in out
    assert "MCC" in out


@patch("instro.utils.cli.platform.system", return_value="Linux")
def test_linux_mcc_unsupported(_mock_os, capture, monkeypatch):
    """On Linux, MCC is Windows-only."""
    monkeypatch.setattr(cli.importlib.util, "find_spec", _fake_find_spec({"pyvisa"}))
    monkeypatch.setattr(cli, "_try_import", lambda name: (name == "pyvisa", None))
    monkeypatch.setattr(cli, "_version_of", lambda dist: "1.15.0" if dist == "pyvisa" else "0.6.0")

    _, out = capture(["doctor"])
    assert "Not supported on Linux" in out


@patch("instro.utils.cli.platform.system", return_value="Windows")
def test_windows_supports_everything(_mock_os, capture, monkeypatch):
    """On Windows every driver's OS gate passes."""
    monkeypatch.setattr(cli.importlib.util, "find_spec", _fake_find_spec({"pyvisa"}))
    monkeypatch.setattr(cli, "_try_import", lambda name: (name == "pyvisa", None))
    monkeypatch.setattr(cli, "_version_of", lambda dist: "1.15.0" if dist == "pyvisa" else "0.6.0")

    _, out = capture(["doctor"])
    assert "Not supported on Windows" not in out


@patch("instro.utils.cli.platform.system", return_value="Windows")
def test_native_lib_load_failure_shows_partial(_mock_os, capture, monkeypatch):
    """find_spec passes but real import fails → ⚠ partial state."""
    monkeypatch.setattr(
        cli.importlib.util,
        "find_spec",
        _fake_find_spec({"pyvisa", "mcculw", "instro.daq.drivers.mcc"}),
    )

    def _try(name: str) -> tuple[bool, str | None]:
        if name == "pyvisa":
            return True, None
        return False, "OSError: cannot load native library"
    monkeypatch.setattr(cli, "_try_import", _try)
    monkeypatch.setattr(cli, "_version_of", lambda dist: "1.0.0")

    code, out = capture(["doctor"])

    assert code == 0  # only PyVISA failures block; mcc partial is informational
    assert "native library failed to load" in out


def test_extras_installed_but_driver_missing(capture, monkeypatch):
    """User installed instro[daq-labjack] but never installed the LJM native lib."""
    monkeypatch.setattr(
        cli.importlib.util,
        "find_spec",
        _fake_find_spec({"pyvisa", "instro.daq.drivers.labjack"}),
    )
    monkeypatch.setattr(cli, "_try_import", lambda name: (name == "pyvisa", None))
    monkeypatch.setattr(cli, "_version_of", lambda dist: "1.0.0")

    _, out = capture(["doctor"])
    # The labjack package row should be ✓ (extras installed), but its LJM driver child should
    # explicitly call out the missing native driver.
    assert "Native driver missing" in out
    assert "labjack.com" in out


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
    monkeypatch.setattr(cli.importlib.util, "find_spec", _fake_find_spec({"pyvisa"}))
    monkeypatch.setattr(cli, "_try_import", lambda name: (name == "pyvisa", None))
    monkeypatch.setattr(cli, "_version_of", lambda dist: "1.15.0" if dist == "pyvisa" else "0.6.0")

    _, out = capture(["doctor"])
    # The outer Panel uses ╭ ╮ ╰ ╯ corner glyphs.
    # All three sections (Python label, table header, verdict text) should appear
    # between the open and close of the same outer panel.
    open_idx = out.index("╭")
    close_idx = out.rindex("╯")
    inner = out[open_idx:close_idx]
    assert "Python" in inner
    assert "Component" in inner  # table header
    assert "Ready to go" in inner  # verdict
