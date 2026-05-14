from __future__ import annotations

import ast
from pathlib import Path

import mkdocs_gen_files

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_DIR = REPO_ROOT / "examples"
OUTPUT_DIR = Path("examples")


def extract_title(py_path: Path) -> str:
    docstring = ast.get_docstring(ast.parse(py_path.read_text()))
    if not docstring:
        return py_path.stem
    first = docstring.strip().splitlines()[0].strip()
    if first.lower().startswith("example:"):
        first = first[len("example:") :].strip()
    return first.rstrip(".") or py_path.stem


for example_path in sorted(EXAMPLES_DIR.rglob("*.py")):
    rel_path = example_path.relative_to(EXAMPLES_DIR)
    output_path = (OUTPUT_DIR / rel_path).with_suffix(".md")
    title = extract_title(example_path)

    with mkdocs_gen_files.open(output_path, "w") as f:
        f.write(f"# {title}\n\n")
        f.write("```python\n")
        f.write(example_path.read_text())
        f.write("```\n")

with mkdocs_gen_files.open(OUTPUT_DIR / "index.md", "w") as f:
    f.write("# Examples\n\n")
    f.write("Browse examples organized by instrument type.\n")
