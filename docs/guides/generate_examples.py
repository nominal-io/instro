"""Generate Mintlify example pages from ../examples/ and refresh docs.json nav.

Walks every ``*.py`` under ``examples/`` (relative to the repo root), writes a
matching ``.mdx`` page under ``docs/guides/instrumentation/examples/``, and
rewrites the "Examples" tab in ``docs/guides/docs.json``.

Run via ``just gen-examples``.
"""

from __future__ import annotations

import ast
import json
from collections import OrderedDict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
EXAMPLES_SRC = REPO_ROOT / "examples"
EXAMPLES_OUT = SCRIPT_DIR / "instrumentation" / "examples"
DOCS_JSON = SCRIPT_DIR / "docs.json"

NAV_PREFIX = "instrumentation/examples"

CATEGORY_TITLES: "OrderedDict[str, str]" = OrderedDict(
    [
        ("daq", "DAQ"),
        ("dmm", "DMM"),
        ("psu", "PSU"),
        ("eload", "Electronic Load"),
        ("i2c", "I2C"),
        ("publishers", "Publishers"),
        ("modbus", "Modbus"),
        ("ethernetip", "EtherNet/IP"),
        ("test_rack_example", "Test Rack"),
    ]
)

ROOT_GROUP_TITLE = "General"


def extract_title(py_path: Path) -> str:
    docstring = ast.get_docstring(ast.parse(py_path.read_text()))
    if not docstring:
        return py_path.stem
    first = docstring.strip().splitlines()[0].strip()
    if first.lower().startswith("example:"):
        first = first[len("example:") :].strip()
    return first.rstrip(".") or py_path.stem


def write_mdx(py_path: Path, out_path: Path) -> None:
    title = extract_title(py_path)
    body = py_path.read_text()
    if not body.endswith("\n"):
        body += "\n"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(f'---\ntitle: "{title}"\n---\n\n```python {py_path.name}\n{body}```\n')


def clean_output_dir() -> None:
    if not EXAMPLES_OUT.exists():
        return
    for mdx in EXAMPLES_OUT.rglob("*.mdx"):
        mdx.unlink()
    for d in sorted(
        (p for p in EXAMPLES_OUT.rglob("*") if p.is_dir()),
        key=lambda p: -len(p.parts),
    ):
        if not any(d.iterdir()):
            d.rmdir()


def discover() -> "tuple[OrderedDict[str, list[str]], list[str]]":
    categories: "OrderedDict[str, list[str]]" = OrderedDict()
    root_files: list[str] = []
    for py_path in sorted(EXAMPLES_SRC.rglob("*.py")):
        rel = py_path.relative_to(EXAMPLES_SRC)
        nav_path = f"{NAV_PREFIX}/{rel.with_suffix('').as_posix()}"
        if len(rel.parts) == 1:
            root_files.append(nav_path)
        else:
            categories.setdefault(rel.parts[0], []).append(nav_path)
    return categories, root_files


def reorder_by_existing(pages: list[str], existing: list[str]) -> list[str]:
    page_set = set(pages)
    kept = [p for p in existing if p in page_set]
    new = sorted(p for p in pages if p not in set(kept))
    return kept + new


def existing_examples_groups(docs: dict) -> list[dict]:
    for tab in docs["navigation"]["tabs"]:
        if tab.get("tab") == "Examples":
            return tab.get("groups", [])
    return []


def build_groups(
    categories: "OrderedDict[str, list[str]]",
    root_files: list[str],
    existing_groups: list[dict],
) -> list[dict]:
    prior_pages: dict[str, list[str]] = {
        g.get("group", ""): [p for p in g.get("pages", []) if isinstance(p, str)] for g in existing_groups
    }

    groups: list[dict] = [{"group": "Overview", "pages": [NAV_PREFIX]}]
    remaining = dict(categories)
    for folder, title in CATEGORY_TITLES.items():
        pages = remaining.pop(folder, None)
        if pages:
            groups.append({"group": title, "pages": reorder_by_existing(pages, prior_pages.get(title, []))})
    for folder, pages in remaining.items():
        title = folder.replace("_", " ").title()
        groups.append({"group": title, "pages": reorder_by_existing(pages, prior_pages.get(title, []))})
    if root_files:
        groups.append(
            {
                "group": ROOT_GROUP_TITLE,
                "pages": reorder_by_existing(root_files, prior_pages.get(ROOT_GROUP_TITLE, [])),
            }
        )
    return groups


def update_docs_json(
    categories: "OrderedDict[str, list[str]]",
    root_files: list[str],
) -> None:
    docs = json.loads(DOCS_JSON.read_text())
    groups = build_groups(categories, root_files, existing_examples_groups(docs))
    tabs = docs["navigation"]["tabs"]
    for tab in tabs:
        if tab.get("tab") == "Examples":
            tab["groups"] = groups
            break
    else:
        tabs.append({"tab": "Examples", "groups": groups})
    DOCS_JSON.write_text(json.dumps(docs, indent=2) + "\n")


def main() -> None:
    clean_output_dir()
    for py_path in sorted(EXAMPLES_SRC.rglob("*.py")):
        rel = py_path.relative_to(EXAMPLES_SRC)
        out_path = (EXAMPLES_OUT / rel).with_suffix(".mdx")
        write_mdx(py_path, out_path)
        print(f"wrote {out_path.relative_to(SCRIPT_DIR)}")
    categories, root_files = discover()
    update_docs_json(categories, root_files)
    print(f"updated {DOCS_JSON.relative_to(SCRIPT_DIR)}")


if __name__ == "__main__":
    main()
