"""Typed Python API for test-and-measurement instruments."""

import importlib as _importlib
import pkgutil as _pkgutil

# Workspace packages (instro-contrib, instro-unstable, ...) add subpackages under instro/.
__path__ = _pkgutil.extend_path(__path__, __name__)

__all__ = ["awg", "cli", "daq", "dmm", "eload", "i2c", "lib", "modbus", "psu", "scope"]


def __getattr__(name: str) -> object:
    if name in __all__:
        return _importlib.import_module(f"{__name__}.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    # Include subpackages already imported, such as instro.contrib.
    return sorted({*globals(), *__all__})
