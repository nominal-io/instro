from __future__ import annotations

import argparse
import json
import subprocess
import tarfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class ExpectedPath:
    group: str
    source_path: str
    archive_path: str


def _sdist_archive(path: Path) -> Path:
    if not path.is_file():
        raise SystemExit(f"sdist does not exist: {path}")
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


def _cargo_metadata(wrapper_package: Path) -> dict[str, object]:
    command = [
        "cargo",
        "metadata",
        "--locked",
        "--format-version",
        "1",
        "--no-deps",
        "--manifest-path",
        str(wrapper_package / "Cargo.toml"),
    ]
    result = subprocess.run(command, cwd=REPO_ROOT, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        raise SystemExit(f"Failed to inspect Cargo metadata for {wrapper_package}: {message}")
    return json.loads(result.stdout)


def _local_path_dependencies(wrapper_package: Path) -> dict[str, Path]:
    packages = _cargo_metadata(wrapper_package)["packages"]
    if not isinstance(packages, list) or len(packages) != 1:
        raise SystemExit(f"Expected Cargo metadata for exactly one wrapper package at {wrapper_package}")

    dependencies = packages[0]["dependencies"]
    return {
        dependency.get("rename") or dependency["name"]: Path(dependency["path"])
        for dependency in dependencies
        if dependency.get("source") is None and dependency.get("path") is not None
    }


def _python_source_root(wrapper_package: Path) -> Path:
    # maturin's python-source setting anchors the importable package tree; default is the package dir.
    pyproject = wrapper_package / "pyproject.toml"
    if pyproject.is_file():
        try:
            import tomllib
        except ModuleNotFoundError:  # Python < 3.11
            tomllib = None
        if tomllib is not None:
            config = tomllib.loads(pyproject.read_text())
            python_source = config.get("tool", {}).get("maturin", {}).get("python-source")
            if isinstance(python_source, str):
                return (wrapper_package / python_source).resolve()
    return wrapper_package


def _expected_paths(wrapper_package: Path) -> set[ExpectedPath]:
    wrapper_archive_dir = wrapper_package.relative_to(REPO_ROOT).as_posix()
    expected = {
        ExpectedPath(
            "wrapper manifest",
            _archive_path(wrapper_archive_dir, filename),
            _archive_path(wrapper_archive_dir, filename),
        )
        for filename in ("Cargo.toml", "Cargo.lock")
    }
    expected.add(ExpectedPath("workspace manifest", "Cargo.toml", "Cargo.toml"))
    expected.add(
        ExpectedPath("wrapper manifest", _archive_path(wrapper_archive_dir, "pyproject.toml"), "pyproject.toml")
    )
    rust_toolchain = wrapper_package / "rust-toolchain.toml"
    if rust_toolchain.is_file():
        expected.add(
            ExpectedPath(
                "wrapper manifest",
                _repo_archive_root(rust_toolchain, rust_toolchain.as_posix()),
                _archive_path(wrapper_archive_dir, rust_toolchain.name),
            )
        )

    python_source_root = _python_source_root(wrapper_package)
    python_files = _paths_under(python_source_root, ("**/*.py", "**/*.pyi", "**/py.typed"))
    expected.update(_relative_archive_paths("python package", python_source_root, "", python_files))

    wrapper_rust_files = _paths_under(wrapper_package / "src", ("**/*.rs",))
    expected.update(
        _relative_archive_paths("wrapper Rust source", wrapper_package, wrapper_archive_dir, wrapper_rust_files)
    )

    for dependency_name, dependency_path in _local_path_dependencies(wrapper_package).items():
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


# Return archive entries without the generated <dist>-<version> prefix.
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


def _check_sdist(wrapper_package: Path, sdist_path: Path, verbose: bool) -> None:
    sdist = _sdist_archive(sdist_path)
    archive_names = _archive_relative_names(sdist)
    expected_paths = sorted(_expected_paths(wrapper_package), key=lambda item: (item.group, item.archive_path))
    missing_by_group: dict[str, list[str]] = {}
    for expected_path in expected_paths:
        if expected_path.archive_path not in archive_names:
            missing_by_group.setdefault(expected_path.group, []).append(expected_path.archive_path)

    missing = [f"{group}: {', '.join(paths)}" for group, paths in missing_by_group.items()]
    if missing:
        raise SystemExit(f"{sdist.name} is missing: {'; '.join(missing)}")

    if verbose:
        for expected_path in expected_paths:
            print(f"{expected_path.source_path} -> {expected_path.archive_path}")


def _resolve_package(package: str) -> Path:
    wrapper_package = (REPO_ROOT / package).resolve()
    if not (wrapper_package / "Cargo.toml").is_file():
        raise SystemExit(f"No Cargo.toml under wrapper package {wrapper_package}")
    return wrapper_package


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify a maturin wrapper package's source distribution.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sdist_parser = subparsers.add_parser("sdist", help="Verify a maturin wrapper source distribution")
    sdist_parser.add_argument(
        "--package",
        required=True,
        help="Path (relative to repo root) of the maturin wrapper package, e.g. packages/instro-ethernetip",
    )
    sdist_parser.add_argument("archive", type=Path, help="Sdist archive to verify")
    sdist_parser.add_argument("--verbose", action="store_true", help="Print every sdist path verified")

    args = parser.parse_args()
    if args.command == "sdist":
        _check_sdist(_resolve_package(args.package), args.archive, args.verbose)


if __name__ == "__main__":
    main()
