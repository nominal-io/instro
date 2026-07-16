from __future__ import annotations

import argparse
import json
import subprocess
import tarfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER_PACKAGE = REPO_ROOT / "packages" / "instro-ethernetip"
WRAPPER_ARCHIVE_DIR = WRAPPER_PACKAGE.relative_to(REPO_ROOT).as_posix()


@dataclass(frozen=True)
class ExpectedPath:
    group: str
    source_path: str
    archive_path: str


def _sdist_archive(path: Path) -> Path:
    if not path.is_file():
        raise SystemExit(f"EtherNet/IP sdist does not exist: {path}")
    return path


def _paths_under(root: Path, patterns: Iterable[str]) -> set[Path]:
    paths: set[Path] = set()
    for pattern in patterns:
        paths.update(path for path in root.glob(pattern) if path.is_file())
    return paths


def _archive_path(*parts: str) -> str:
    return str(PurePosixPath(*[part for part in parts if part]))


def _relative_archive_paths(
    group: str,
    source_root: Path,
    archive_root: str,
    paths: Iterable[Path],
) -> set[ExpectedPath]:
    return {
        ExpectedPath(
            group,
            _repo_archive_root(path, path.as_posix()),
            _archive_path(archive_root, path.relative_to(source_root).as_posix()),
        )
        for path in paths
    }


def _repo_archive_root(path: Path, fallback: str) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return fallback


def _cargo_metadata() -> dict[str, object]:
    command = [
        "cargo",
        "metadata",
        "--locked",
        "--format-version",
        "1",
        "--no-deps",
        "--manifest-path",
        str(WRAPPER_PACKAGE / "Cargo.toml"),
    ]
    result = subprocess.run(command, cwd=REPO_ROOT, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        raise SystemExit(f"Failed to inspect EtherNet/IP Cargo metadata: {message}")
    return json.loads(result.stdout)


def _local_path_dependencies() -> dict[str, Path]:
    packages = _cargo_metadata()["packages"]
    if not isinstance(packages, list):
        raise SystemExit("Expected Cargo metadata for EtherNet/IP wrapper package")

    dependencies = packages[0]["dependencies"]
    return {
        dependency.get("rename") or dependency["name"]: Path(dependency["path"])
        for dependency in dependencies
        if dependency.get("source") is None and dependency.get("path") is not None
    }


def _expected_paths() -> set[ExpectedPath]:
    expected = {
        ExpectedPath(
            "wrapper manifest",
            _archive_path(WRAPPER_ARCHIVE_DIR, filename),
            _archive_path(WRAPPER_ARCHIVE_DIR, filename),
        )
        for filename in ("Cargo.toml", "Cargo.lock")
    }
    expected.add(ExpectedPath("workspace manifest", "Cargo.toml", "Cargo.toml"))
    expected.add(
        ExpectedPath("wrapper manifest", _archive_path(WRAPPER_ARCHIVE_DIR, "pyproject.toml"), "pyproject.toml")
    )
    rust_toolchain = WRAPPER_PACKAGE / "rust-toolchain.toml"
    if rust_toolchain.is_file():
        expected.add(
            ExpectedPath(
                "wrapper manifest",
                _repo_archive_root(rust_toolchain, rust_toolchain.as_posix()),
                _archive_path(WRAPPER_ARCHIVE_DIR, rust_toolchain.name),
            )
        )

    python_files = _paths_under(WRAPPER_PACKAGE / "instro" / "ethernetip", ("**/*.py", "**/*.pyi", "**/py.typed"))
    expected.update(_relative_archive_paths("python package", WRAPPER_PACKAGE, "", python_files))

    wrapper_rust_files = _paths_under(WRAPPER_PACKAGE / "src", ("**/*.rs",))
    expected.update(
        _relative_archive_paths("wrapper Rust source", WRAPPER_PACKAGE, WRAPPER_ARCHIVE_DIR, wrapper_rust_files)
    )

    for dependency_name, dependency_path in _local_path_dependencies().items():
        archive_root = _repo_archive_root(dependency_path, dependency_name)
        dependency_manifest = dependency_path / "Cargo.toml"
        expected.add(
            ExpectedPath(
                f"path dependency {dependency_name}",
                _repo_archive_root(dependency_manifest, dependency_manifest.as_posix()),
                _archive_path(archive_root, "Cargo.toml"),
            )
        )
        rust_files = _paths_under(dependency_path / "src", ("**/*.rs",))
        expected.update(
            _relative_archive_paths(f"path dependency {dependency_name}", dependency_path, archive_root, rust_files)
        )

    return expected


# Return archive entries without the generated instro_ethernetip-<version> prefix.
# e.g. instro_ethernetip-1.0.0/crates/instro-ethernetip/src/lib.rs -> crates/instro-ethernetip/src/lib.rs
def _archive_relative_names(sdist: Path) -> set[str]:
    with tarfile.open(sdist) as archive:
        names = archive.getnames()

    relative_names = set()
    top_level_dirs = set()
    for name in names:
        parts = PurePosixPath(name).parts
        if parts:
            top_level_dirs.add(parts[0])
        if len(parts) > 1:
            relative_names.add(str(PurePosixPath(*parts[1:])))
    if len(top_level_dirs) != 1:
        raise SystemExit(f"Expected one top-level directory in {sdist.name}, found {sorted(top_level_dirs)}")
    return relative_names


def _check_sdist(sdist_path: Path, verbose: bool) -> None:
    sdist = _sdist_archive(sdist_path)
    archive_names = _archive_relative_names(sdist)
    expected_paths = sorted(_expected_paths(), key=lambda item: (item.group, item.archive_path))
    missing_by_group: dict[str, list[str]] = {}
    for expected_path in expected_paths:
        if expected_path.archive_path not in archive_names:
            missing_by_group.setdefault(expected_path.group, []).append(expected_path.archive_path)

    missing = [f"{group}: {', '.join(paths)}" for group, paths in missing_by_group.items()]
    if missing:
        raise SystemExit(f"EtherNet/IP sdist is missing: {'; '.join(missing)}")

    if verbose:
        for expected_path in expected_paths:
            print(f"{expected_path.source_path} -> {expected_path.archive_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify EtherNet/IP package artifacts.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sdist_parser = subparsers.add_parser("sdist", help="Verify an EtherNet/IP source distribution")
    sdist_parser.add_argument("archive", type=Path, help="Sdist archive to verify")
    sdist_parser.add_argument("--verbose", action="store_true", help="Print every sdist path verified")

    args = parser.parse_args()
    if args.command == "sdist":
        _check_sdist(args.archive, args.verbose)


if __name__ == "__main__":
    main()
