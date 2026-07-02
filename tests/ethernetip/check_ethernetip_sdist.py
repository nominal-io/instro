from __future__ import annotations

import argparse
import glob
import tarfile
from pathlib import Path, PurePosixPath

# the things we expect to be contained in the sdist
REQUIRED_PATHS = {
    "instro-ethernetip/src/lib.rs",
    "instro-ethernetip/src/sync_session.rs",
    "instro-ethernetip-rs/src/lib.rs",
    "instro-ethernetip-rs/src/blocking.rs",
    "instro-ethernetip-rs/Cargo.toml",
    "instro-ethernetip/Cargo.lock",
    "instro/ethernetip/__init__.py",
    "instro/ethernetip/ethernetip.py",
    "instro/ethernetip/ethernetip_types.py",
    "instro/ethernetip/_ethernetip.pyi",
    "instro/ethernetip/py.typed",
}


# Locate the sdist file
# Accept CI-friendly directory inputs, shell globs, and direct archive paths.
def _find_sdist(path: Path) -> Path:
    if path.is_dir():
        sdists = sorted(path.glob("instro_ethernetip-*.tar.gz"))
    elif any(char in str(path) for char in "*?["):
        sdists = sorted(Path(match) for match in glob.glob(str(path)))
    else:
        sdists = [path]

    if len(sdists) != 1:
        raise SystemExit(f"Expected exactly one instro-ethernetip sdist, found {len(sdists)}")
    if not sdists[0].is_file():
        raise SystemExit(f"EtherNet/IP sdist does not exist: {sdists[0]}")
    return sdists[0]


# Return archive entries without the generated instro_ethernetip-<version> prefix.
# e.g. instro_ethernetip-1.0.0/instro-ethernetip/src/lib.rs -> instro-ethernetip/src/lib.rs
def _archive_relative_names(sdist: Path) -> set[str]:
    with tarfile.open(sdist) as archive:
        names = archive.getnames()

    relative_names = set()
    for name in names:
        parts = PurePosixPath(name).parts
        if len(parts) > 1:
            relative_names.add(str(PurePosixPath(*parts[1:])))
    return relative_names


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify the EtherNet/IP sdist includes source-build inputs.")
    parser.add_argument("sdist", type=Path, help="Sdist archive, glob, or directory containing one sdist")
    args = parser.parse_args()

    sdist = _find_sdist(args.sdist)
    missing = sorted(REQUIRED_PATHS - _archive_relative_names(sdist))
    if missing:
        raise SystemExit(f"EtherNet/IP sdist is missing: {', '.join(missing)}")


if __name__ == "__main__":
    main()
