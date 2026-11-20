"""Instrument database: the Instruments tab, generated from the drivers instro and third-party libraries ship.

On every build (or from a release snapshot, see ``instrument_db_refresh``) this finds the drivers, then writes
into ``docs/instruments/`` (gitignored, like ``docs/examples/``):

- ``index.md``: the searchable, browsable home
- ``<driver_type>/<id>.md``: one stub per instrument, with a code example for that driver type
- ``type/`` and ``manufacturer/``: a page per category and per manufacturer

Sources live in ``sources.py`` and page templates in ``render.py``; see ``docs/AGENTS.md`` for how to add a library.
Files are only rewritten when their content changes, so live preview doesn't loop.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .records import InstrumentRecord
from .render import ROOT, category_pages, home_page, manufacturer_pages, stub_page
from .sources import SOURCES, make_source

if TYPE_CHECKING:  # Sphinx is only in the docs group; tests import this package without it
    from sphinx.application import Sphinx
    from sphinx.config import Config

logger = logging.getLogger(__name__)

REFRESH_MODES = ("build", "release")
ENV_VAR = "INSTRO_DOCS_INSTRUMENT_DB"
RESERVED_IDS = {"type", "manufacturer", "index", "images"}


def refresh_mode(setting: str | dict[str, str], source: str) -> str:
    """When ``source`` is re-discovered: ``"build"`` or ``"release"``.

    ``INSTRO_DOCS_INSTRUMENT_DB`` overrides the setting for every source. A dict sets modes per source, with
    ``"default"`` for the rest (``"build"`` if absent).
    """
    mode = os.environ.get(ENV_VAR) or (
        setting if isinstance(setting, str) else setting.get(source, setting.get("default", "build"))
    )
    if mode not in REFRESH_MODES:
        raise ValueError(f"instrument_db_refresh: {mode!r} is not one of {REFRESH_MODES}")
    return mode


def read_snapshot(path: Path) -> dict[str, list[InstrumentRecord]]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {name: [InstrumentRecord.from_json(r) for r in recs] for name, recs in data.get("sources", {}).items()}


def write_snapshot(path: Path, sources: dict[str, list[InstrumentRecord]]) -> bool:
    """Write the snapshot, sorted so a refresh diffs cleanly. Returns whether the file changed."""
    payload = {
        "sources": {
            name: [r.to_json() for r in sorted(recs, key=lambda r: (r.category, r.import_path))]
            for name, recs in sorted(sources.items())
        }
    }
    text = json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def collect(
    setting: str | dict[str, str],
    source_options: dict[str, dict[str, Any]],
    snapshot_path: Path,
    refresh: Iterable[str] | None = None,
) -> tuple[dict[str, list[InstrumentRecord]], set[str]]:
    """Records per source, and which sources were discovered live.

    A source in ``"release"`` mode is read from the snapshot (and discovered live, with a warning, when the
    snapshot has none). ``refresh`` forces the named sources live regardless of mode.
    """
    snapshot = read_snapshot(snapshot_path)
    forced = set(refresh or ())
    records: dict[str, list[InstrumentRecord]] = {}
    live: set[str] = set()
    for name, options in source_options.items():
        if name not in SOURCES:
            raise ValueError(f"instrument_db_sources: unknown source {name!r}; known: {sorted(SOURCES)}")
        if name not in forced and refresh_mode(setting, name) == "release":
            if name in snapshot:
                records[name] = snapshot[name]
                continue
            logger.warning("instrument_db: no snapshot for %r in %s; discovering it live", name, snapshot_path)
        records[name] = list(make_source(name, **options).discover())
        live.add(name)
    return records, live


def _unique(records: list[InstrumentRecord]) -> list[InstrumentRecord]:
    """Drop records whose URL slug repeats within a driver type, and reject reserved slugs."""
    seen: set[tuple[str, str]] = set()
    out = []
    for r in records:
        key = (r.driver_type, r.id)
        if r.id in RESERVED_IDS or key in seen:
            logger.warning(
                "instrument_db: skipping %s %s: duplicate or reserved page name %r", r.driver_type, r.name, r.id
            )
            continue
        seen.add(key)
        out.append(r)
    return out


def build_pages(records: list[InstrumentRecord], srcdir: Path) -> dict[str, str]:
    """Docname to page text for every generated page."""
    if not records:
        return {}
    pages = {f"{ROOT}/{r.driver_type}/{r.id}": stub_page(r, srcdir) for r in records}
    pages |= category_pages(records) | manufacturer_pages(records)
    pages[f"{ROOT}/index"] = home_page(records)
    return pages


def write_pages(srcdir: Path, pages: dict[str, str]) -> None:
    """Write the pages, only where the text changed, and delete generated pages that are no longer produced."""
    root = srcdir / ROOT
    wanted = {srcdir / f"{doc}.md": text for doc, text in pages.items()}
    for path, text in wanted.items():
        if path.is_file() and path.read_text(encoding="utf-8") == text:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    if root.is_dir():
        for stale in root.rglob("*.md"):
            if stale not in wanted and "images" not in stale.relative_to(root).parts:
                stale.unlink()


def _generate(app: Sphinx, config: Config) -> None:
    srcdir = Path(app.srcdir)
    snapshot_path = srcdir / config.instrument_db_snapshot
    sources, live = collect(config.instrument_db_refresh, config.instrument_db_sources, snapshot_path)
    if config.instrument_db_write_snapshot and write_snapshot(snapshot_path, sources):
        logger.info("instrument_db: wrote %s", snapshot_path)
    records = _unique([r for recs in sources.values() for r in recs])
    logger.info(
        "instrument_db: %d instruments (%s)",
        len(records),
        ", ".join(f"{n}: {len(r)}{'' if n in live else ' from snapshot'}" for n, r in sources.items()),
    )
    write_pages(srcdir, build_pages(records, srcdir))


def setup(app: Sphinx) -> dict[str, bool]:
    app.add_config_value("instrument_db_refresh", "build", "env")
    app.add_config_value("instrument_db_snapshot", "_data/instrument_snapshot.json", "env")
    app.add_config_value("instrument_db_sources", {"instro": {}}, "env")
    app.add_config_value("instrument_db_write_snapshot", False, "env")
    app.connect("config-inited", _generate)
    app.add_js_file("instrument-db.js", loading_method="defer")
    return {"parallel_read_safe": True}
