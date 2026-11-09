"""Importing instro must not need nominal or its transitive tree (#626).

Each test runs in a subprocess whose import system refuses the blocked packages, so a new
top-level ``import nominal`` (or numpy) anywhere on an import path fails here.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import textwrap

BLOCKER = textwrap.dedent(
    """
    import importlib.abc, sys

    BLOCKED = {"nominal", "nominal_streaming", "polars", "pandas", "numpy"}

    class _Blocker(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path, target=None):
            if name.split(".")[0] in BLOCKED:
                raise ModuleNotFoundError(f"No module named {name!r} (blocked by test)", name=name)
            return None

    sys.meta_path.insert(0, _Blocker())
    """
)

CORE_MODULES = [
    "instro",
    "instro.lib",
    "instro.lib.publishers",
    "instro.cli",
    *(
        f"instro.{category}{sub}"
        for category in ("awg", "daq", "dmm", "eload", "i2c", "psu", "scope")
        for sub in ("", ".drivers")
    ),
    "instro.modbus",
]

VENDOR_MODULES = [
    "instro.contrib",
    "instro.ethernetip",
    "instro.daq.drivers.labjack",
    "instro.daq.drivers.mcc",
    "instro.daq.drivers.ni",
    "instro.i2c.drivers.totalphase",
]


def _run_blocked(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", BLOCKER + textwrap.dedent(code)],
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_categories_import_without_nominal() -> None:
    installed_vendors = [name for name in VENDOR_MODULES if importlib.util.find_spec(name) is not None]
    result = _run_blocked(
        f"""
        import importlib
        for name in {CORE_MODULES + installed_vendors!r}:
            importlib.import_module(name)
        """
    )
    assert result.returncode == 0, result.stderr


def test_nominal_core_publisher_names_missing_package() -> None:
    result = _run_blocked(
        """
        try:
            from instro.lib.publishers import NominalCorePublisher
        except ImportError as e:
            print(e)
        else:
            raise SystemExit("import unexpectedly succeeded")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "pip install nominal" in result.stdout
