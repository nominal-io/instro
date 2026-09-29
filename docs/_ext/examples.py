"""Generate the Examples section (``docs/examples/``, gitignored) from the example scripts.

Each ``examples/<category>/*.py`` becomes a page showing the script, titled from the
first line of its module docstring; each category gets an index. Examples inside the
``instro-unstable`` and ``instro-contrib`` packages go under ``examples/packages/<pkg>/``
with the package's caveat. Nothing here is hand-maintained: add a script and it appears.
Files are only rewritten when their content changes, so live preview doesn't loop.
"""

import ast
from pathlib import Path
from typing import Any

from sphinx.application import Sphinx

# pagename -> repo-relative script path, filled by generate() for the edit link
_SOURCES: dict[str, str] = {}

# folder -> (title, octicon) for categories under examples/; others are title-cased
CATEGORIES = {
    "daq": ("DAQ", "graph"),
    "dmm": ("DMM", "meter"),
    "psu": ("PSU", "zap"),
    "eload": ("Electronic Load", "plug"),
    "awg": ("AWG", "pulse"),
    "scope": ("Oscilloscope", "pulse"),
    "i2c": ("I2C", "cpu"),
    "publishers": ("Publishers", "rocket"),
    "modbus": ("Modbus", "globe"),
    "ethernetip": ("EtherNet/IP", "globe"),
    "test_rack_example": ("Test Rack", "server"),
    "flowcontroller": ("Flow Controller", "meter"),
    "motorcontroller": ("Motor Controller", "gear"),
    "general": ("General", "code"),
}
PACKAGES = {
    "unstable": (
        "Unstable",
        "instro-unstable",
        "warning",
        "{subject} `instro-unstable`. This code is new and may change without notice.",
    ),
    "contrib": (
        "Contrib",
        "instro-contrib",
        "note",
        "{subject} `instro-contrib`. Contrib drivers are verified on hardware by their contributor, "
        "not by Nominal. See [Contrib drivers](/library/contrib.md).",
    ),
}


def _title(folder: str) -> str:
    return CATEGORIES.get(folder, (folder.replace("_", " ").title(), "code"))[0]


def _script_title(py: Path) -> str:
    doc = ast.get_docstring(ast.parse(py.read_text(encoding="utf-8"))) or ""
    first = doc.strip().splitlines()[0].strip() if doc.strip() else py.stem
    if first.lower().startswith("example:"):
        first = first[len("example:") :].strip()
    return first.rstrip(".") or py.stem


def _page(py: Path, repo: Path, title: str, callout: str = "") -> str:
    rel = py.relative_to(repo).as_posix()
    return (
        f"# {title}\n\n{callout}"
        f"```{{literalinclude}} /../{rel}\n:caption: {py.name}\n:language: python\n```\n\n"
        f"Source: [`{rel}`](https://github.com/nominal-io/instro/blob/main/{rel})\n"
    )


def _index(title: str, entries: list[tuple[str, str]], intro: str = "") -> str:
    links = "".join(f"- [{t}]({name}.md)\n" for t, name in entries)
    toc = "".join(f"{name}\n" for _, name in entries)
    return f"# {title}\n\n{intro}{links}\n```{{toctree}}\n:hidden:\n\n{toc}```\n"


def generate(app: Sphinx) -> None:
    repo = Path(app.srcdir).parent
    out = Path(app.srcdir) / "examples"
    files: dict[Path, str] = {}

    categories: dict[str, list[tuple[str, str]]] = {}
    for py in sorted((repo / "examples").rglob("*.py")):
        rel = py.relative_to(repo / "examples")
        # pages keep the script's path under its category, so same-named scripts in
        # different subfolders don't collide
        folder, name = (rel.parts[0], Path(*rel.parts[1:])) if len(rel.parts) > 1 else ("general", rel)
        name = name.with_suffix("").as_posix()
        title = _script_title(py)
        files[out / folder / f"{name}.md"] = _page(py, repo, title)
        _SOURCES[f"examples/{folder}/{name}"] = py.relative_to(repo).as_posix()
        categories.setdefault(folder, []).append((title, name))
    for folder, entries in categories.items():
        files[out / folder / "index.md"] = _index(_title(folder), entries)

    for slug, (title, dist, kind, callout) in PACKAGES.items():
        src = repo / "packages" / dist / "instro" / slug
        sections: dict[str, list[tuple[str, str]]] = {}
        for py in sorted(src.rglob("examples/*.py")):
            sub = py.parent.parent.name
            note = f":::{{{kind}}}\n{callout.format(subject='This example uses')}\n:::\n\n"
            script_title = _script_title(py)
            files[out / "packages" / slug / sub / f"{py.stem}.md"] = _page(py, repo, script_title, note)
            _SOURCES[f"examples/packages/{slug}/{sub}/{py.stem}"] = py.relative_to(repo).as_posix()
            sections.setdefault(sub, []).append((script_title, f"{sub}/{py.stem}"))
        body = f"# {title}\n\n:::{{{kind}}}\n{callout.format(subject='These examples use')}\n:::\n\n"
        body += (
            "".join(
                f"## {_title(sub)}\n\n" + "".join(f"- [{t}]({name}.md)\n" for t, name in entries) + "\n"
                for sub, entries in sorted(sections.items())
            )
            or "No examples yet.\n\n"
        )
        toc = "".join(f"{name}\n" for entries in sections.values() for _, name in entries)
        files[out / "packages" / slug / "index.md"] = body + f"```{{toctree}}\n:hidden:\n\n{toc}```\n"

    cards = "".join(
        f":::{{grid-item-card}} {{octicon}}`{CATEGORIES.get(f, ('', 'code'))[1]}` {_title(f)}\n"
        f":link: /examples/{f}/index\n:link-type: doc\n:::\n\n"
        for f in sorted(categories)
    )
    packages = "".join(
        f":::{{grid-item-card}} {title}\n:link: /examples/packages/{slug}/index\n:link-type: doc\n:::\n\n"
        for slug, (title, *_) in PACKAGES.items()
    )
    files[out / "index.md"] = (
        "# Examples\n\n{.lead}\nFull, runnable examples, organized by instrument type. The source is in "
        "[`examples/`](https://github.com/nominal-io/instro/tree/main/examples).\n\n"
        f"## By category\n\n::::{{grid}} 1 2 3 3\n:gutter: 2\n\n{cards}::::\n\n"
        f"## Additional packages\n\n::::{{grid}} 1 2 3 3\n:gutter: 2\n\n{packages}::::\n"
    )

    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
    for stale in out.rglob("*.md"):
        if stale not in files:
            stale.unlink()


def _edit_link(app: Sphinx, pagename: str, templatename: str, context: dict[str, Any], doctree: Any) -> None:
    """Point the sidebar's "Edit this page" at the script; the generated .md isn't in the repo."""
    if not pagename.startswith("examples/"):
        return
    script = _SOURCES.get(pagename)
    if script is None:  # index pages have no single source
        context["page_source_suffix"] = ""
        return
    ctx = app.config.html_context
    url = f"https://github.com/{ctx['source_user']}/{ctx['source_repo']}/blob/{ctx['source_version']}/{script}"
    context["edit_source_link"] = lambda filename: url


def setup(app: Sphinx) -> dict[str, bool]:
    app.connect("builder-inited", generate)
    # after the theme's own hook (priority 500), which installs edit_source_link
    app.connect("html-page-context", _edit_link, priority=600)
    return {"parallel_read_safe": True}
