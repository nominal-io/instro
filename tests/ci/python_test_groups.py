import json
from pathlib import Path


def main() -> None:
    """Print up to four nonempty groups of whole test directories and root files."""
    paths = sorted(
        path.as_posix()
        for path in Path("tests").iterdir()
        if (path.is_file() and path.match("test_*.py"))
        or (path.is_dir() and any(test.is_file() for test in path.rglob("test_*.py")))
    )
    if not paths:
        raise SystemExit("No Python test paths found")
    groups = [{"name": f"group-{index + 1}", "paths": paths[index::4]} for index in range(min(4, len(paths)))]
    print(json.dumps(groups, separators=(",", ":")))


if __name__ == "__main__":
    main()
