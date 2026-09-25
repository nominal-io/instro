"""Fail if a guides/README link into the SDK reference points at a page or anchor the build lacks.

The Mintlify guides link to the SDK site by absolute URL, so a renamed class, moved
page, or dropped heading in docs/sdk would otherwise 404 silently after deploy.

Run after `just build-docs`: `uv run python docs/sdk/check_links.py`.
"""

import re
import subprocess
import sys
from pathlib import Path

BASE = "https://nominal-io.github.io/instro/"
BUILD = Path(__file__).parent / "_build" / "dirhtml"
URL = re.compile(re.escape(BASE) + r"([^\s)\"'<>`\]]*)")


def main() -> int:
    if not (BUILD / "index.html").is_file():
        print(f"no build at {BUILD}; run `just build-docs` first", file=sys.stderr)
        return 2
    files = subprocess.run(
        ["git", "ls-files", "docs/guides", "README.md"], capture_output=True, text=True, check=True
    ).stdout.split()
    errors = []
    checked = 0
    for name in files:
        path = Path(name)
        if path.suffix not in {".md", ".mdx", ".json"}:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in URL.finditer(line):
                if line[match.end() : match.end() + 1] == "<":
                    continue  # a documented URL template, e.g. .../generated/<dotted.path>/
                target, _, anchor = match.group(1).partition("#")
                page = BUILD / target / "index.html" if not target or target.endswith("/") else BUILD / target
                checked += 1
                if not page.is_file():
                    errors.append(f"{name}:{lineno}: no page for {match.group(0)}")
                elif anchor and f'id="{anchor}"' not in page.read_text(encoding="utf-8"):
                    errors.append(f"{name}:{lineno}: no #{anchor} on {BASE}{target}")
    for error in errors:
        print(error, file=sys.stderr)
    print(f"checked {checked} SDK links, {len(errors)} broken")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
