"""Smoke test that every module under ``instro.contrib`` imports cleanly — catches transitive-dep rot."""

from __future__ import annotations

import importlib
import pkgutil

import pytest

import instro.contrib


def _walk_contrib_modules() -> list[str]:
    return sorted(info.name for info in pkgutil.walk_packages(instro.contrib.__path__, prefix="instro.contrib."))


@pytest.mark.parametrize("module_name", _walk_contrib_modules())
def test_contrib_module_imports(module_name: str) -> None:
    importlib.import_module(module_name)
