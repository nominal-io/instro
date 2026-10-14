"""Warn when a driver exported by ``instro.<category>.drivers`` is missing from the docs.

For each ``sdk/instruments/<category>.md`` whose category has a core ``drivers``
module, every name in that module's ``__all__`` (except ``*DriverBase``) must pass
two checks:

``sdk``
    The SDK page references the class by a dotted ``instro.`` path.
``guide``
    A driver page ``<category>/<Name>.md`` exists.

``driver_docs_exempt`` in ``conf.py`` maps ``"<category>.<Name>"`` to the checks
that name skips. An exemption that no longer applies is also a warning.
"""

import importlib
import re
from pathlib import Path

from sphinx.application import Sphinx
from sphinx.util import logging

logger = logging.getLogger(__name__)

CHECKS = ("sdk", "guide")


def _exported_drivers(category: str) -> list[str] | None:
    """Names in ``instro.<category>.drivers.__all__``; None when there is no such module."""
    try:
        module = importlib.import_module(f"instro.{category}.drivers")
    except ModuleNotFoundError as exc:
        if exc.name is not None and f"instro.{category}.drivers".startswith(exc.name):
            return None
        raise
    return [name for name in module.__all__ if not name.endswith("DriverBase")]


def _check_driver_docs(app: Sphinx) -> None:
    srcdir = Path(app.srcdir)
    exempt: dict[str, set[str]] = {key: set(checks) for key, checks in app.config.driver_docs_exempt.items()}
    for key, checks in exempt.items():
        if unknown := checks - set(CHECKS):
            logger.warning(f"driver_docs: {key}: unknown checks {sorted(unknown)} in driver_docs_exempt")

    seen: set[str] = set()
    for sdk_page in sorted((srcdir / "sdk" / "instruments").glob("*.md")):
        category = sdk_page.stem
        names = _exported_drivers(category)
        if names is None:
            continue
        sdk_text = sdk_page.read_text(encoding="utf-8")
        for name in names:
            key = f"{category}.{name}"
            seen.add(key)
            missing = {
                "sdk": not re.search(rf"\binstro(?:\.\w+)+\.{name}\b", sdk_text),
                "guide": not (srcdir / category / f"{name}.md").is_file(),
            }
            skipped = exempt.get(key, set())
            for check in CHECKS:
                if missing[check] and check not in skipped:
                    where = sdk_page.relative_to(srcdir) if check == "sdk" else f"{category}/{name}.md"
                    logger.warning(f"driver_docs: {key} is exported but missing from {where}")
                elif check in skipped and not missing[check]:
                    logger.warning(f"driver_docs: {key} passes {check!r}; remove it from driver_docs_exempt")

    for key in sorted(exempt.keys() - seen):
        logger.warning(f"driver_docs: {key} in driver_docs_exempt is not an exported driver")


def setup(app: Sphinx) -> dict[str, bool]:
    app.add_config_value("driver_docs_exempt", {}, "env")
    app.connect("builder-inited", _check_driver_docs)
    return {"parallel_read_safe": True}
