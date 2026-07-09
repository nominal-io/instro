"""mkdocs-macros module: auto-generated vendor driver lists for the Instruments pages."""

import importlib
import inspect
from pathlib import Path

REPO_BLOB_URL = "https://github.com/nominal-io/instro/blob/main"
REPO_ROOT = Path(__file__).resolve().parents[2]


def _driver_classes(category: str) -> list[type]:
    module = importlib.import_module(f"instro.{category}.drivers")
    base = next(getattr(module, name) for name in module.__all__ if name.endswith("DriverBase"))
    return [
        obj
        for name in module.__all__
        if inspect.isclass(obj := getattr(module, name)) and obj is not base and issubclass(obj, base)
    ]


def driver_list(category: str) -> str:
    """Render one heading + one-line summary per concrete driver in instro.<category>.drivers."""
    classes = _driver_classes(category)
    if not classes:
        return "*No vendor drivers ship in the core `instro` package for this category.*"

    entries = []
    for cls in classes:
        summary = (inspect.getdoc(cls) or "").strip().splitlines()[:1]
        source = Path(inspect.getfile(cls)).resolve().relative_to(REPO_ROOT)
        link = f"{REPO_BLOB_URL}/{source}"
        entries.append(f"### {cls.__name__}\n\n{summary[0] if summary else ''} ([source]({link}))")
    return "\n\n".join(entries)


def define_env(env):
    env.macro(driver_list)
