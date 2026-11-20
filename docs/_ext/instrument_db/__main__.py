"""Refresh the instrument snapshot: ``just update-instrument-db``.

Discovers every source live and rewrites ``docs/_data/instrument_snapshot.json``, which ``release`` mode reads.
Run it when cutting a release (or after bumping pymeasure), and commit the result.
"""

from __future__ import annotations

import argparse
import runpy
import sys
from collections import Counter
from pathlib import Path

from . import collect, write_snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="instrument_db", description=__doc__)
    parser.add_argument("--source", action="append", help="refresh only this source (repeatable); default: all")
    args = parser.parse_args(argv)

    docs = Path(__file__).resolve().parents[2]
    conf = runpy.run_path(str(docs / "conf.py"))  # the same source options the build uses
    options = conf["instrument_db_sources"]
    path = docs / conf.get("instrument_db_snapshot", "_data/instrument_snapshot.json")
    chosen = set(args.source or options)
    # release mode everywhere, so only the chosen sources are discovered; the others keep their snapshot
    records, live = collect("release", options, path, refresh=chosen)
    changed = write_snapshot(path, records)
    for name, recs in records.items():
        by_type = Counter(r.category for r in recs)
        print(f"{name}: {len(recs)} instruments in {len(by_type)} categories{'' if name in live else ' (kept)'}")
    print(f"{'Updated' if changed else 'No change to'} {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
