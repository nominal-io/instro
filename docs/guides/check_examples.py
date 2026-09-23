"""Check that the generated Mintlify example pages and Examples tab nav are up to date.

Runs ``generate_examples.main`` into a temporary directory and compares the
result against the committed ``docs/guides/examples/`` tree. It reports:

- generated pages that are missing from the repo, differ from it, or exist in
  the repo but are no longer generated (fix with ``just gen-examples``);
- generated ``index.mdx`` pages that aren't listed in ``docs.json``'s Examples
  tab, and Examples tab entries with no page behind them (fix by editing
  ``docs.json``).

Exits non-zero if anything is reported. This is what docs CI runs.

Run via ``just check-examples``.
"""

import json
import sys
import tempfile
from pathlib import Path

from generate_examples import EXAMPLES_OUT as REPO_EXAMPLES_DIR
from generate_examples import SCRIPT_DIR
from generate_examples import main as gen_examples_main

DOCS_JSON = SCRIPT_DIR / "docs.json"


def read_text_universal(path: Path) -> str:
    """Read file with universal newline mode (all line endings converted to LF)."""
    with open(path, encoding="utf-8", newline=None) as f:
        return f.read()


def examples_tab_pages() -> set[str]:
    """Every page path listed under docs.json's Examples tab, including nested groups."""
    tabs = json.loads(DOCS_JSON.read_text())["navigation"]["tabs"]
    examples_tab = next(tab for tab in tabs if tab["tab"] == "Examples")
    pages: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, str):
            pages.add(node)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            walk(node.get("groups", []))
            walk(node.get("pages", []))

    walk(examples_tab)
    return pages


def check_nav(output_directory: Path) -> list[str]:
    """Generated index pages must be in the Examples tab, and every tab entry must resolve to a page."""
    errors = []
    pages = examples_tab_pages()
    for index in sorted(output_directory.rglob("index.mdx")):
        nav_path = f"examples/{index.relative_to(output_directory).with_suffix('').as_posix()}"
        if nav_path not in pages:
            errors.append(f"Generated index missing from docs.json Examples tab: {nav_path}")
    for page in sorted(pages):
        rel = Path(page).relative_to("examples") if page != "examples" else None
        generated = rel is not None and (output_directory / rel).with_suffix(".mdx").exists()
        if not generated and not (SCRIPT_DIR / page).with_suffix(".mdx").exists():
            errors.append(f"docs.json Examples tab entry has no page: {page}")
    return errors


def check_examples(output_directory: Path) -> bool:
    gen_examples_main(output_path=output_directory)
    errors = []
    checked_files = []

    # Check generated files exist in repo and match content
    for generated_file in sorted(output_directory.rglob("*")):
        if generated_file.is_dir():
            continue

        rel_path = generated_file.relative_to(output_directory)
        repo_file = REPO_EXAMPLES_DIR / rel_path

        if not repo_file.exists():
            errors.append(f"Generated file does not exist in repo: {rel_path}")
            continue

        try:
            generated_content = read_text_universal(generated_file)
            repo_content = read_text_universal(repo_file)
        except Exception as e:
            errors.append(f"Error reading {rel_path}: {e}")
            continue

        checked_files.append(rel_path)

        if generated_content != repo_content:
            errors.append(f"Content mismatch: {rel_path}")

    # Check for files in repo that weren't generated
    for repo_file in sorted(REPO_EXAMPLES_DIR.rglob("*")):
        if repo_file.is_dir():
            continue
        rel_path = repo_file.relative_to(REPO_EXAMPLES_DIR)
        generated_file = output_directory / rel_path
        if not generated_file.exists():
            errors.append(f"Expected generated file missing: {rel_path}")

    errors += check_nav(output_directory)

    # Report all errors
    for error in errors:
        print(f"ERROR: {error}")

    print(f"Checked {len(checked_files)} example files against the repository working directory")
    if errors:
        print(f"\n{len(errors)} problem(s) found — run `just gen-examples` and check docs.json's Examples tab")
    return not errors


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as temp_dir:
        TEMP_EXAMPLE_DIRECTORY = Path(temp_dir)
        if not check_examples(TEMP_EXAMPLE_DIRECTORY):
            sys.exit(1)
