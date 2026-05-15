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


@patch("instro.utils.cli.platform.system", return_value="Linux")
def test_doctor_all_present(_mock_os, capture, monkeypatch):
    """Everything installed and importable on a supported OS: exit 0, 'Ready to go'."""
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
    # Single merged "Capabilities" table now, not two separate tables.
    assert "Capabilities" in out
    assert "Workspace extras" not in out
    assert "Vendor SDKs" not in out


def test_doctor_pyvisa_missing(capture, monkeypatch):
    """PyVISA is the only universally-required capability; missing → exit 1."""
    monkeypatch.setattr(cli.importlib.util, "find_spec", _fake_find_spec(set()))
    monkeypatch.setattr(cli, "_try_import", lambda _name: (False, "ModuleNotFoundError: pyvisa"))
    monkeypatch.setattr(cli, "_version_of", lambda _dist: None)

    code, out = capture(["doctor"])

    assert code == 1
    assert "VISA / SCPI" in out
    assert "Missing" in out


def test_doctor_optional_missing_but_visa_ok(capture, monkeypatch):
    """Optional capabilities absent but PyVISA present: exit 0."""
    monkeypatch.setattr(cli.importlib.util, "find_spec", _fake_find_spec({"pyvisa"}))
    monkeypatch.setattr(cli, "_try_import", lambda name: (name == "pyvisa", None if name == "pyvisa" else "missing"))
    monkeypatch.setattr(cli, "_version_of", lambda dist: "1.15.0" if dist == "pyvisa" else None)

    code, out = capture(["doctor"])

    assert code == 0
    assert "Ready to go" in out


@patch("instro.utils.cli.platform.system", return_value="Darwin")
def test_macos_filters_unsupported(_mock_os, capture, monkeypatch):
    """On macOS, NI-DAQ and MCC rows should show 'Not supported on macOS'."""
    monkeypatch.setattr(cli.importlib.util, "find_spec", _fake_find_spec({"pyvisa"}))
    monkeypatch.setattr(cli, "_try_import", lambda name: (name == "pyvisa", None))
    monkeypatch.setattr(cli, "_version_of", lambda dist: "1.15.0" if dist == "pyvisa" else None)

    _, out = capture(["doctor"])
    assert "Not supported on macOS" in out
    # NI-DAQ row should be in the output as not-supported.
    assert "NI-DAQ" in out
    assert "MCC USB-DAQ" in out


@patch("instro.utils.cli.platform.system", return_value="Linux")
def test_linux_filters_mcc(_mock_os, capture, monkeypatch):
    """On Linux, MCC is Windows-only and should show 'Not supported on Linux'."""
    monkeypatch.setattr(cli.importlib.util, "find_spec", _fake_find_spec({"pyvisa"}))
    monkeypatch.setattr(cli, "_try_import", lambda name: (name == "pyvisa", None))
    monkeypatch.setattr(cli, "_version_of", lambda dist: "1.15.0" if dist == "pyvisa" else None)

    _, out = capture(["doctor"])
    assert "Not supported on Linux" in out
    assert "MCC USB-DAQ" in out


@patch("instro.utils.cli.platform.system", return_value="Windows")
def test_windows_supports_everything(_mock_os, capture, monkeypatch):
    """On Windows every capability is supported (only MCC unsupported elsewhere)."""
    monkeypatch.setattr(cli.importlib.util, "find_spec", _fake_find_spec({"pyvisa"}))
    monkeypatch.setattr(cli, "_try_import", lambda name: (name == "pyvisa", None))
    monkeypatch.setattr(cli, "_version_of", lambda dist: "1.15.0" if dist == "pyvisa" else None)

    _, out = capture(["doctor"])
    assert "Not supported on Windows" not in out


@patch("instro.utils.cli.platform.system", return_value="Windows")
def test_native_lib_load_failure_shows_partial(_mock_os, capture, monkeypatch):
    """find_spec passes but real import fails → ⚠ partial state, not exit 1."""
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

    assert code == 0  # only PyVISA failures block; mcculw partial is informational
    assert "native library failed to load" in out
    assert "cannot load native library" in out


def test_extras_missing_but_sdk_present(capture, monkeypatch):
    """User has the vendor Python SDK but forgot `pip install instro[daq-labjack]`."""
    monkeypatch.setattr(cli.importlib.util, "find_spec", _fake_find_spec({"pyvisa", "labjack.ljm"}))
    monkeypatch.setattr(cli, "_try_import", lambda name: (name in {"pyvisa", "labjack.ljm"}, None))
    monkeypatch.setattr(cli, "_version_of", lambda dist: "1.0.0")

    _, out = capture(["doctor"])
    # Should show a hint to install the extras specifically, since the SDK is already there.
    assert "pip install 'instro[daq-labjack]'" in out
    assert "SDK already installed" in out


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
