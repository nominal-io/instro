"""The flat namespaces (``instro``, ``instro.drivers``, ``instro.lib``) stay in step with the category paths.

Each test fails when a name exported from a category path is missing from its flat namespace, so a new driver,
type, or publisher registered in the usual place cannot silently skip the shorter import.
"""

from __future__ import annotations

import ast
import importlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

import instro
import instro.drivers
import instro.lib

_CATEGORIES = ("awg", "daq", "daq.scaling", "dmm", "eload", "i2c", "modbus", "psu", "scope")
# Ambiguous at the top level (modbus TimingConfig, i2c RegisterDef vs modbus RegisterDef) or already in instro.lib.
_TOP_LEVEL_EXCLUDED = {"modbus": {"TimingConfig", "DeviceInfo", "LinearScale"}, "i2c": {"RegisterDef"}}
_DRIVER_CATEGORIES = ("awg", "daq", "dmm", "eload", "i2c", "psu", "scope")
_LIB_SUBPACKAGES = ("config", "publishers", "registry", "transports")


@pytest.mark.parametrize("category", _CATEGORIES)
def test_top_level_exports_every_category_name(category: str) -> None:
    module = importlib.import_module(f"instro.{category}")
    for name in set(module.__all__) - _TOP_LEVEL_EXCLUDED.get(category, set()):
        assert name in instro.__all__, f"instro.{category}.{name} missing from instro"
        assert getattr(instro, name) is getattr(module, name)


def test_top_level_type_checking_imports_match_lazy_exports() -> None:
    # mypy and IDEs read the TYPE_CHECKING block; runtime reads _EXPORTS. They must name the same objects.
    tree = ast.parse(Path(instro.__file__).read_text())
    block = next(n for n in tree.body if isinstance(n, ast.If) and getattr(n.test, "id", None) == "TYPE_CHECKING")
    static = {
        alias.asname or alias.name: node.module
        for node in block.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert static == instro._EXPORTS


def test_top_level_import_is_lazy() -> None:
    code = "import sys, instro; print(any(m.startswith('instro.') for m in sys.modules))"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "False"


@pytest.mark.parametrize("module", [instro, instro.drivers], ids=lambda m: m.__name__)
def test_dir_lists_only_public_names(module: object) -> None:
    # dir() feeds IPython tab completion; the modules' own helper imports must not show up there.
    public = {n for n in dir(module) if not n.startswith("_")}
    assert public.isdisjoint({"Any", "TYPE_CHECKING", "annotations", "extend_path", "importlib"})


def test_top_level_unknown_name_raises_attribute_error() -> None:
    with pytest.raises(AttributeError, match="no attribute 'NotAThing'"):
        _ = instro.NotAThing  # type: ignore[attr-defined]


@pytest.mark.parametrize("category", _DRIVER_CATEGORIES)
def test_drivers_exports_every_category_driver(category: str) -> None:
    module = importlib.import_module(f"instro.{category}.drivers")
    for name in module.__all__:
        assert name in instro.drivers.__all__, f"instro.{category}.drivers.{name} missing from instro.drivers"
        assert getattr(instro.drivers, name) is getattr(module, name)


@pytest.mark.parametrize("name", sorted(instro.drivers._VENDOR_DRIVERS))
def test_drivers_exports_vendor_package_driver(name: str) -> None:
    module_name, _extra = instro.drivers._VENDOR_DRIVERS[name]
    assert name in dir(instro.drivers)
    try:
        expected = getattr(importlib.import_module(module_name), name)
    except ImportError:  # vendor package or its native SDK (e.g. Windows-only mcculw) absent here
        with pytest.raises(ImportError):
            getattr(instro.drivers, name)
    else:
        assert getattr(instro.drivers, name) is expected


def test_drivers_star_import_skips_vendor_package_drivers() -> None:
    # A vendor name in __all__ would make `import *` raise wherever that extra isn't installed.
    namespace: dict[str, object] = {}
    exec("from instro.drivers import *", namespace)
    assert "VirtualPSU" in namespace
    assert not set(instro.drivers._VENDOR_DRIVERS) & set(instro.drivers.__all__)


def test_drivers_missing_vendor_package_names_the_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(instro.drivers._VENDOR_DRIVERS, "FakeDriver", ("instro.daq.drivers.not_installed", "fake"))
    with pytest.raises(ImportError, match=r"FakeDriver needs the 'fake' extra: pip install 'instro\[fake\]'"):
        _ = instro.drivers.FakeDriver  # type: ignore[attr-defined]


@pytest.mark.parametrize("subpackage", _LIB_SUBPACKAGES)
def test_lib_exports_every_subpackage_name(subpackage: str) -> None:
    module = importlib.import_module(f"instro.lib.{subpackage}")
    for name in module.__all__:
        assert name in instro.lib.__all__, f"instro.lib.{subpackage}.{name} missing from instro.lib"
        assert getattr(instro.lib, name) is getattr(module, name)


@pytest.mark.parametrize("category", ("daq", "dmm", "eload", "i2c", "psu", "scope"))
def test_contrib_drivers_exports_every_contrib_driver(category: str) -> None:
    contrib_drivers = pytest.importorskip("instro.contrib.drivers")
    module = importlib.import_module(f"instro.contrib.{category}.drivers")
    for name in module.__all__:
        assert name in contrib_drivers.__all__, (
            f"instro.contrib.{category}.drivers.{name} missing from instro.contrib.drivers"
        )
        assert getattr(contrib_drivers, name) is getattr(module, name)


_REPO = Path(__file__).resolve().parents[1]
_IMPORT = re.compile(r"from (instro(?:\.\w+)*) import (\([^)]*\)|[^\n#]+)")
_USER_DOCS = sorted(
    p
    for p in [_REPO / "README.md", *(_REPO / "docs").rglob("*.md"), *(_REPO / "examples").rglob("*.py")]
    if not {"guides", "ref", "_build", "sdk", "examples"} & set(p.relative_to(_REPO).parts[1:-1])
    and p.name not in {"AGENTS.md", "CONTRIBUTING.md", "DOCS_MIGRATION.md"}
)


def _shallowest(module: str, name: str) -> str | None:
    """The shallowest instro module that exports the same object as ``module.name``, or None if unresolvable."""
    try:
        obj = getattr(importlib.import_module(module), name)
    except (ImportError, AttributeError):  # vendor package absent, or a placeholder name in prose
        return None
    parts = module.split(".")
    candidates = [".".join(parts[:i]) for i in range(1, len(parts))] + ["instro.drivers", "instro.lib"]
    if module.startswith("instro.contrib."):
        candidates.append("instro.contrib.drivers")
    best = module
    for candidate in candidates:
        try:
            namespace = importlib.import_module(candidate)
        except ImportError:
            continue
        if name in dir(namespace) and getattr(namespace, name, None) is obj and candidate.count(".") < best.count("."):
            best = candidate
    return best


@pytest.mark.parametrize("path", _USER_DOCS, ids=lambda p: str(p.relative_to(_REPO)))
def test_docs_import_from_shallowest_module(path: Path) -> None:
    # Docs and examples recommend one place to import each name from: the shallowest module that has it.
    text = path.read_text()
    deeper = []
    for match in _IMPORT.finditer(text):
        module = match.group(1)
        names = [n.strip().split(" as ")[0] for n in match.group(2).strip("()").replace("\n", " ").split(",")]
        for name in filter(None, names):
            best = _shallowest(module, name)
            if best is not None and best != module:
                deeper.append(
                    f"line {text[: match.start()].count(chr(10)) + 1}: import {name} from {best}, not {module}"
                )
    assert not deeper, "\n".join(deeper)
