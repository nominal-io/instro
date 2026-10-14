import subprocess
import sys
from pathlib import Path

import instro

CORE_SUBPACKAGES = sorted(p.parent.name for p in Path(instro.__file__).parent.glob("*/__init__.py"))


def test_core_subpackages_are_exposed() -> None:
    assert instro.__all__ == CORE_SUBPACKAGES
    for name in CORE_SUBPACKAGES:
        assert name in dir(instro)
        assert getattr(instro, name).__name__ == f"instro.{name}"


def test_imported_workspace_subpackage_is_listed() -> None:
    import instro.contrib  # noqa: F401

    assert "contrib" in dir(instro)


def test_import_does_not_load_subpackages() -> None:
    code = "import sys, instro; print([m for m in sys.modules if m.startswith('instro.')])"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "[]"
