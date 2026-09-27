"""Generate the Examples section (``docs/examples/``, gitignored) from the example scripts.

Each ``examples/<category>/*.py`` becomes a page showing the script, titled from the
first line of its module docstring; each category gets an index. Examples inside the
``instro-unstable`` and ``instro-contrib`` packages go under ``examples/packages/<pkg>/``
with the package's caveat. Nothing here is hand-maintained: add a script and it appears.
Files are only rewritten when their content changes, so live preview doesn't loop.
"""

import ast
from pathlib import Path

from sphinx.application import Sphinx

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


def _page(py: Path, repo: Path, callout: str = "") -> str:
    rel = py.relative_to(repo).as_posix()
    return (
        f"# {_script_title(py)}\n\n{callout}"
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
        folder = rel.parts[0] if len(rel.parts) > 1 else "general"
        files[out / folder / f"{py.stem}.md"] = _page(py, repo)
        categories.setdefault(folder, []).append((_script_title(py), py.stem))
    for folder, entries in categories.items():
        files[out / folder / "index.md"] = _index(_title(folder), entries)

    for slug, (title, dist, kind, callout) in PACKAGES.items():
        src = repo / "packages" / dist / "instro" / slug
        sections: dict[str, list[tuple[str, str]]] = {}
        for py in sorted(src.rglob("examples/*.py")):
            sub = py.parent.parent.name
            note = f":::{{{kind}}}\n{callout.format(subject='This example uses')}\n:::\n\n"
            files[out / "packages" / slug / sub / f"{py.stem}.md"] = _page(py, repo, note)
            sections.setdefault(sub, []).append((_script_title(py), f"{sub}/{py.stem}"))
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


def setup(app: Sphinx) -> dict[str, bool]:
    app.connect("builder-inited", generate)
    return {"parallel_read_safe": True}
