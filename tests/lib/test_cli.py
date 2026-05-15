"""Tests for the `instro` CLI doctor command."""

from __future__ import annotations

import importlib
from io import StringIO
from unittest.mock import patch

import pytest

from instro.utils import cli


@pytest.fixture
def capture(monkeypatch):
    """Run `cli.main(...)` against an isolated rich Console + capture stdout/stderr.

    Returns a callable taking argv -> (exit_code, stdout_text).
    """
    def _run(argv: list[str]) -> tuple[int, str]:
        buf = StringIO()
        # Force Rich to render to our buffer with a fixed width so output is
        # stable across terminal sizes / TTY presence.
        from rich.console import Console as RealConsole

        def _fixed_console(*args, **kwargs):
            kwargs.setdefault("file", buf)
            kwargs.setdefault("width", 100)
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


def test_doctor_all_present(capture, monkeypatch):
    """Everything installed and importable: exit 0, ✓ marks, 'Ready to go'."""
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
    # Make `_try_import` claim every SDK loads cleanly.
    monkeypatch.setattr(cli, "_try_import", lambda _name: (True, None))
    # Stub version lookups so we don't depend on what's actually pip-installed.
    monkeypatch.setattr(cli, "_version_of", lambda dist: "1.2.3")

    code, out = capture(["doctor"])

    assert code == 0
    assert "Ready to go" in out
    assert "instro doctor" in out
    # PyVISA must be present in every healthy report.
    assert "PyVISA" in out


def test_doctor_pyvisa_missing(capture, monkeypatch):
    """PyVISA is the only universally-required SDK; its absence is exit 1."""
    monkeypatch.setattr(cli.importlib.util, "find_spec", _fake_find_spec(set()))
    monkeypatch.setattr(cli, "_try_import", lambda _name: (False, "ModuleNotFoundError: pyvisa"))
    monkeypatch.setattr(cli, "_version_of", lambda dist: None)

    code, out = capture(["doctor"])

    assert code == 1
    assert "PyVISA" in out
    assert "Missing" in out


def test_doctor_extras_missing_but_pyvisa_ok(capture, monkeypatch):
    """Extras absent but PyVISA present: exit 0 (extras are optional)."""
    monkeypatch.setattr(cli.importlib.util, "find_spec", _fake_find_spec({"pyvisa"}))
    monkeypatch.setattr(cli, "_try_import", lambda name: (name == "pyvisa", None if name == "pyvisa" else "missing"))
    monkeypatch.setattr(cli, "_version_of", lambda dist: "1.15.0" if dist == "pyvisa" else None)

    code, out = capture(["doctor"])

    assert code == 0
    assert "Ready to go" in out
    # Each missing extra should show its install hint.
    for extras_name in ("daq-labjack", "daq-mcc", "daq-ni", "i2c-aardvark"):
        assert f"pip install 'instro[{extras_name}]'" in out


def test_doctor_native_lib_load_failure(capture, monkeypatch):
    """find_spec passes but actual import fails (e.g. mcculw on macOS)."""
    monkeypatch.setattr(cli.importlib.util, "find_spec", _fake_find_spec({"pyvisa", "mcculw"}))

    def _try(name: str) -> tuple[bool, str | None]:
        if name == "pyvisa":
            return True, None
        return False, "NameError: name 'WinDLL' is not defined"

    monkeypatch.setattr(cli, "_try_import", _try)
    monkeypatch.setattr(cli, "_version_of", lambda dist: "1.0.0" if dist == "pyvisa" else None)

    code, out = capture(["doctor"])

    assert code == 0  # only PyVISA is fatal-required; mcculw failing is informational
    assert "native library missing" in out
    assert "WinDLL" in out


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


def test_doctor_invokable_as_module(monkeypatch):
    """`python -m instro.utils.cli doctor` should be a valid invocation path."""
    # Just confirm the module is loadable as a script and `main` is callable.
    mod = importlib.import_module("instro.utils.cli")
    assert callable(mod.main)


@patch("instro.utils.cli.platform.system", return_value="Darwin")
def test_macos_nidaqmx_hint(_mock_system, capture, monkeypatch):
    """On macOS, the NI-DAQmx hint should explicitly call out that macOS is unsupported."""
    monkeypatch.setattr(cli.importlib.util, "find_spec", _fake_find_spec({"pyvisa"}))
    monkeypatch.setattr(cli, "_try_import", lambda name: (name == "pyvisa", None))
    monkeypatch.setattr(cli, "_version_of", lambda dist: "1.15.0" if dist == "pyvisa" else None)
    _, out = capture(["doctor"])
    assert "macOS not supported" in out


@patch("instro.utils.cli.platform.system", return_value="Linux")
def test_linux_mcc_hint(_mock_system, capture, monkeypatch):
    """On Linux, the MCC hint should say Linux is unsupported (it's Windows-only)."""
    monkeypatch.setattr(cli.importlib.util, "find_spec", _fake_find_spec({"pyvisa"}))
    monkeypatch.setattr(cli, "_try_import", lambda name: (name == "pyvisa", None))
    monkeypatch.setattr(cli, "_version_of", lambda dist: "1.15.0" if dist == "pyvisa" else None)
    _, out = capture(["doctor"])
    assert "Linux not supported" in out
