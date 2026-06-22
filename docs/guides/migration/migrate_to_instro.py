"""Migrate a customer codebase from `nominal-instro` (<=0.6.x) to `instro` (>=0.7).

Usage:
    python migrate_to_instro.py [TARGET_DIR] [--dry-run]

TARGET_DIR defaults to the current directory. Pass `--dry-run` to preview
changes without writing files.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPLACEMENTS: list[tuple[str, str]] = [
    # --- structural import-path rewrites (INSTRO-51) ---
    # `nominal_instro.instruments.<cat>` collapses to `instro.<cat>`. Each
    # category's instrument class and its driver base now live together in
    # `instro/<cat>/<cat>.py` (no separate driver module). Concrete vendor
    # drivers move from `drivers/<cat>/<vendor>/<file>` to `<cat>/drivers/<file>`
    # in the main repo (vendor sub-namespacing only survives where a workspace
    # package owns the vendor folder). protocols/modbus and protocols/common_types
    # lift out of `protocols/`.
    ("nominal_instro.protocols.modbus.modbus_types", "instro.modbus.types"),
    ("nominal_instro.protocols.modbus.modbus", "instro.modbus.modbus"),
    ("nominal_instro.protocols.modbus.sim_server", "instro.modbus.sim_server"),
    ("nominal_instro.protocols.modbus", "instro.modbus"),
    ("nominal_instro.protocols.common_types", "instro.lib.types"),
    # `nominal_instro.lib` maps to `instro.lib`, which holds the
    # shared building blocks outside the category packages.
    # Files that *moved* within lib (rather than just being renamed) need
    # explicit entries — the broader `nominal_instro.lib` substring rewrite
    # would otherwise produce stale paths that no longer exist:
    #   lib/visa.py            → lib/transports/visa.py (moved into transports/)
    #   lib/logging.py         → lib/nominal.py (merged with Nominal helpers)
    #   lib/utils.py           → lib/nominal.py (renamed + merged)
    #   lib/util/nominal.py    → lib/nominal.py (folder collapsed)
    ("nominal_instro.lib.visa", "instro.lib.transports.visa"),
    ("nominal_instro.lib.logging", "instro.lib.nominal"),
    ("nominal_instro.lib.utils", "instro.lib.nominal"),
    ("nominal_instro.lib.util.nominal", "instro.lib.nominal"),
    ("nominal_instro.lib", "instro.lib"),
    ("nominal_instro.instruments.daq.scaling", "instro.daq.scaling"),
    # Per-category driver bases are re-exported at the category root, so
    # rewrite to the public path rather than the underscore-prefixed module.
    ("nominal_instro.instruments.daq.driver", "instro.daq"),
    ("nominal_instro.instruments.dmm.driver", "instro.dmm"),
    ("nominal_instro.instruments.eload.driver", "instro.eload"),
    ("nominal_instro.instruments.psu.driver", "instro.psu"),
    ("nominal_instro.instruments.i2c.driver", "instro.i2c"),
    ("nominal_instro.instruments.psu.scpi_sim_server", "instro.psu.scpi_sim_server"),
    ("nominal_instro.instruments.daq", "instro.daq"),
    ("nominal_instro.instruments.dmm", "instro.dmm"),
    ("nominal_instro.instruments.eload", "instro.eload"),
    ("nominal_instro.instruments.psu", "instro.psu"),
    ("nominal_instro.instruments.i2c", "instro.i2c"),
    ("nominal_instro.drivers.daq.keysight.keysight_34980a", "instro.daq.drivers.keysight_34980a"),
    ("nominal_instro.drivers.daq.keysight", "instro.daq.drivers"),
    ("nominal_instro.drivers.daq.labjack", "instro.daq.drivers.labjack"),
    ("nominal_instro.drivers.daq.mcc", "instro.daq.drivers.mcc"),
    ("nominal_instro.drivers.daq.ni", "instro.daq.drivers.ni"),
    ("nominal_instro.drivers.dmm.agilent.agilent_a34401a", "instro.dmm.drivers.agilent_a34401a"),
    ("nominal_instro.drivers.dmm.agilent", "instro.dmm.drivers"),
    ("nominal_instro.drivers.dmm.keithley.keithley_2400", "instro.dmm.drivers.keithley_2400"),
    ("nominal_instro.drivers.dmm.keithley", "instro.dmm.drivers"),
    ("nominal_instro.drivers.dmm", "instro.dmm.drivers"),
    ("nominal_instro.drivers.eload.bk.bk_85xxb", "instro.eload.drivers.bk_85xxb"),
    ("nominal_instro.drivers.eload.bk", "instro.eload.drivers"),
    ("nominal_instro.drivers.eload", "instro.eload.drivers"),
    ("nominal_instro.drivers.psu.bk.bk_9115", "instro.psu.drivers.bk_9115"),
    ("nominal_instro.drivers.psu.bk", "instro.psu.drivers"),
    ("nominal_instro.drivers.psu.rigol.rigol_dp800", "instro.psu.drivers.rigol_dp800"),
    ("nominal_instro.drivers.psu.rigol", "instro.psu.drivers"),
    ("nominal_instro.drivers.psu.siglent.siglent_spd3303", "instro.psu.drivers.siglent_spd3303"),
    ("nominal_instro.drivers.psu.siglent", "instro.psu.drivers"),
    ("nominal_instro.drivers.psu.simulated.simulated", "instro.psu.drivers.simulated"),
    ("nominal_instro.drivers.psu.simulated", "instro.psu.drivers"),
    ("nominal_instro.drivers.psu.tdk_lambda.tdk_lambda_genesys", "instro.psu.drivers.tdk_lambda_genesys"),
    ("nominal_instro.drivers.psu.tdk_lambda", "instro.psu.drivers"),
    ("nominal_instro.drivers.psu", "instro.psu.drivers"),
    ("nominal_instro.drivers.i2c.totalphase.aardvark", "instro.i2c.drivers.totalphase.aardvark"),
    ("nominal_instro.drivers.i2c.totalphase", "instro.i2c.drivers.totalphase"),
    ("nominal_instro.drivers.i2c", "instro.i2c.drivers"),
    # --- class renames (Nominal → Instro / bare) ---
    ("NominalInstrumentationErrorCodes", "InstrumentationErrorCodes"),
    # `NominalDAQFacade`, `NominalDMMFacade`, `NominalI2CFacade`, and
    # `NominalPSUFacade` were removed in INSTRO-311, INSTRO-158, INSTRO-156,
    # and INSTRO-157 respectively. Keep these rows as passthroughs so the
    # shorter category-class alternatives below (NominalDAQ, NominalDMM,
    # NominalI2C, NominalPSU) cannot match inside the facade names and
    # silently produce a now-nonexistent symbol. Users keep the original
    # name and hit a clear ImportError they must resolve by migrating away
    # from the facade pattern.
    ("NominalDAQFacade", "NominalDAQFacade"),
    ("NominalDMMFacade", "NominalDMMFacade"),
    ("NominalI2CFacade", "NominalI2CFacade"),
    ("NominalPSUFacade", "NominalPSUFacade"),
    ("NominalScopeFacade", "InstroScopeFacade"),
    ("NominalDAQ", "InstroDAQ"),
    ("NominalDMM", "InstroDMM"),
    ("NominalELoad", "InstroELoad"),
    ("NominalI2C", "I2CInterface"),
    ("NominalModbus", "ModbusDevice"),
    ("NominalPSU", "InstroPSU"),
    ("NominalScope", "InstroScope"),
    ("NominalInstrument", "Instrument"),
    # --- package rename fallback (catches any remaining nominal-instro) ---
    ("nominal-instro", "instro"),
    ("nominal_instro", "instro"),
]

# Python regex alternation is leftmost-first, not longest-match. Sort by
# descending old-token length so longer alternatives always match before any
# shorter prefix they contain (e.g. NominalI2CFacade before NominalI2C).
PATTERN = re.compile("|".join(re.escape(old) for old, _ in sorted(REPLACEMENTS, key=lambda kv: -len(kv[0]))))
LOOKUP = dict(REPLACEMENTS)

EXTENSIONS = {".py", ".pyi", ".toml", ".md", ".mdx", ".yml", ".yaml", ".txt", ".cfg", ".ini"}
EXCLUDED_DIRS = {".venv", "venv", ".git", "target", "dist", "build", "__pycache__", ".mypy_cache", "node_modules", ".tox", ".pytest_cache"}


def iter_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in EXTENSIONS:
            continue
        if any(part in EXCLUDED_DIRS for part in path.parts):
            continue
        yield path


def migrate_file(path: Path, dry_run: bool) -> tuple[int, dict[str, int]]:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return 0, {}

    per_token: dict[str, int] = {}

    def sub(match: re.Match[str]) -> str:
        old = match.group(0)
        per_token[old] = per_token.get(old, 0) + 1
        return LOOKUP[old]

    new_text = PATTERN.sub(sub, text)
    total = sum(per_token.values())

    if total and not dry_run:
        path.write_text(new_text, encoding="utf-8")

    return total, per_token


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", nargs="?", default=".", help="Directory to migrate (default: cwd)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    args = parser.parse_args()

    root = Path(args.target).resolve()
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 1

    print(f"{'[DRY-RUN] ' if args.dry_run else ''}Migrating {root}")

    total_files = 0
    total_subs = 0
    grand_totals: dict[str, int] = {}

    for path in iter_files(root):
        count, per_token = migrate_file(path, args.dry_run)
        if count:
            total_files += 1
            total_subs += count
            for tok, n in per_token.items():
                grand_totals[tok] = grand_totals.get(tok, 0) + n
            print(f"  {path.relative_to(root)}  ({count} replacements)")

    print()
    print(f"Files changed:  {total_files}")
    print(f"Total subs:     {total_subs}")
    if grand_totals:
        print("By token:")
        for tok, n in sorted(grand_totals.items(), key=lambda kv: -kv[1]):
            print(f"  {tok:<40} {n}")

    if args.dry_run:
        print("\nDry run - no files written. Re-run without --dry-run to apply.")
    else:
        print("\nDone. Next steps:")
        print("  1. Update your pyproject.toml / requirements: 'nominal-instro' -> 'instro'")
        print("  2. Recreate your venv or run `uv sync` / `pip install -e .`")
        print("  3. Run your test suite to verify")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
