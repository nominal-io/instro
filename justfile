set positional-arguments := true

export CARGO_TERM_COLOR := "always"

# On Windows, ensure shebang recipes use Git Bash, not the WSL `bash` in System32 (see #109).
# Git Bash's dir is derived from wherever `git` resolves on PATH, not a hardcoded install path.
export PATH := if os() == "windows" { (parent_directory(parent_directory(require("git.exe"))) / "bin") + ";" + env_var("PATH") } else { env_var("PATH") }

# Default command is no subcommand given to list available commands
default:
    @just --list

# development install with dependencies (optionally specify extras, e.g. `just install daq`)
install *extras:
    #!/usr/bin/env bash
    if [ -z "{{ extras }}" ]; then
        uv sync
    elif [ "{{ extras }}" = "all" ]; then
        uv sync --all-extras
    else
        args=""
        for extra in {{ extras }}; do
            args="$args --extra $extra"
        done
        uv sync $args
    fi

# Enter into the python interpreter with all dependencies loaded
python *args:
    uv run python "$@"

# run python unit tests
test-python:
    uv run pytest

# run Rust library, integration, and doc tests for the workspace
test-rust:
    cargo test --workspace --all-features --all-targets
    cargo test --workspace --all-features --doc

# run all python and Rust tests plus EtherNet/IP packaging checks
test: test-rust test-python eip-test

# check static typing
check-types:
    uv run mypy

# check static typing across all supported python versions
check-types-all:
    uv run mypy --python-version 3.14
    uv run mypy --python-version 3.13
    uv run mypy --python-version 3.12
    uv run mypy --python-version 3.11
    uv run mypy --python-version 3.10

# check code formatting | fix with `just fix-format`
check-format:
    uv run ruff format --check

# check import ordering | fix with `just fix-imports`
check-imports:
    uv run ruff check

# run all python static analysis checks
check-python: check-format check-types check-imports

check-rust: rust-lock-check rust-standalone
    cargo +nightly fmt --all --check
    cargo clippy --all-features --all-targets --workspace -- -D warnings

# run all static analysis checks
check: check-python check-rust

# fixes out-of-order imports (note: mutates the code)
fix-imports:
    uv run ruff check --fix

# fixes code formatting (note: mutates the code)
fix-format:
    uv run ruff format

# fix python imports and formatting
fix-python: fix-format fix-imports

# fixes Rust code formatting (note: mutates the code)
fix-rust:
    cargo +nightly fmt --all
    just rust-standalone-fix

# fix imports and formatting
fix: fix-python fix-rust

# run all python tests and checks
verify-python: install test-python check-python

# run all Rust tests and checks
verify-rust: test-rust check-rust

# run all tests and checks
verify: install test check

# clean up uv environments
clean:
    uv cache clean

# build all packages as wheels
build:
    uv build --wheel --all-packages

# build docs
build-docs:
    uv run mkdocs build --config-file docs/reference/mkdocs.yml

# generate Mintlify example pages and refresh docs/guides/docs.json navigation
gen-examples:
    uv run python docs/guides/generate_examples.py

# PyO3/maturin crates excluded from the root Cargo workspace (see Cargo.toml exclude).
rust-standalone-packages := "packages/instro-ethernetip"

# Verify all committed Cargo.lock files match current manifests (no regeneration).
rust-lock-check:
    #!/usr/bin/env bash
    set -euo pipefail
    cargo check --locked --workspace --all-targets --all-features
    for pkg in {{ rust-standalone-packages }}; do
        cargo check --locked --manifest-path "$pkg/Cargo.toml"
    done

# Run fmt-check, clippy, and locked check for standalone native-extension crates.
rust-standalone manifest="":
    #!/usr/bin/env bash
    set -euo pipefail
    if [ -n "{{ manifest }}" ]; then
        manifests=("{{ manifest }}")
    else
        manifests=()
        for pkg in {{ rust-standalone-packages }}; do
            manifests+=("$pkg/Cargo.toml")
        done
    fi
    for manifest in "${manifests[@]}"; do
        cargo fmt --manifest-path "$manifest" -- --check
        cargo clippy --manifest-path "$manifest" --all-targets -- -D warnings
        cargo check --locked --manifest-path "$manifest"
    done

# Format standalone native-extension crates (local convenience; mutates files).
rust-standalone-fix:
    #!/usr/bin/env bash
    set -euo pipefail
    for pkg in {{ rust-standalone-packages }}; do
        cargo fmt --manifest-path "$pkg/Cargo.toml"
    done

# run the Rust explicit EtherNet/IP integration test against the bundled simulator
# (--all-features matches `test-rust` so both share one set of compiled artifacts)
eip-rs-test:
    cargo test -p instro-ethernetip --all-features --test explicit_session_integration

# run EtherNet/IP integration tests against the live PLC at 10.123.1.199:44818
eip-live-test:
    #!/usr/bin/env bash
    set -euo pipefail
    export INSTRO_EIP_PLC_ENDPOINT=10.123.1.199:44818
    export INSTRO_EIP_ROUTE_PATH_SLOTS=0
    export INSTRO_EIP_TARGET_L32E=1
    cargo test -p instro-ethernetip --all-features --test explicit_session_integration
    uv run --reinstall-package instro-ethernetip --with-editable . pytest -m hardware tests/ethernetip/test_ethernetip_bindings.py -q

# clean build of the EtherNet/IP Python bindings (sdist + wheel)
# uv selects the workspace package via --package, then uses that package's

# [build-system] backend; for instro-ethernetip that backend is maturin.
eip-build:
    uv build --package instro-ethernetip

# build the EtherNet/IP sdist and verify it contains source-build inputs
eip-sdist-smoke-test:
    #!/usr/bin/env bash
    set -euo pipefail
    dist_dir="$(mktemp -d)"
    trap 'rm -rf "$dist_dir"' EXIT
    uv build --sdist --package instro-ethernetip --out-dir "$dist_dir"
    sdists=("$dist_dir"/instro_ethernetip-*.tar.gz)
    if [ "${#sdists[@]}" -ne 1 ] || [ ! -f "${sdists[0]}" ]; then
        echo "Expected exactly one instro-ethernetip sdist in $dist_dir" >&2
        exit 1
    fi
    uv run python tests/ethernetip/check_ethernetip_sdist.py sdist "${sdists[0]}"

# install the built wheel into an isolated environment and verify the private native module
eip-wheel-smoke-test:
    #!/usr/bin/env bash
    set -euo pipefail
    # Use an isolated wheel dir so stale dist/ artifacts cannot be selected.
    wheel_dir="$(mktemp -d)"
    trap 'rm -rf "$wheel_dir"' EXIT
    # Build the platform-specific native extension wheel. This wheel provides
    # instro.ethernetip._ethernetip, the private PyO3 module loaded at import time.
    uv build --wheel --package instro-ethernetip --out-dir "$wheel_dir"
    wheel="$(find "$wheel_dir" -maxdepth 1 -name 'instro_ethernetip-*.whl' -print -quit)"
    if [ -z "$wheel" ]; then
        echo "No instro-ethernetip wheel found in $wheel_dir" >&2
        exit 1
    fi
    uv_run_args=(
        --isolated # Ignore the workspace virtual environment.
        --no-dev # Avoid default dev dependencies that can shadow the wheel.
        --no-cache # Avoid stale same-version wheel contents.
        --with-editable . # Use this checkout's up-to-date instro dependency.
        --with "$wheel" # Install the freshly built native extension wheel.
        --with mypy # Provide the type checker used by the smoke script.
    )
    INSTRO_EIP_WHEEL="$wheel" uv run "${uv_run_args[@]}" python tests/ethernetip/ethernetip_wheel_smoke.py

# Full EIP test suite: wheel smoke test, Rust/Python bindings, and cpppo integration
eip-test: eip-sdist-smoke-test eip-wheel-smoke-test eip-rs-test
    cargo test --all-features --all-targets -p instro-ethernetip -p instro-ethernetip-py
    uv run --reinstall-package instro-ethernetip --with-editable . pytest tests/ethernetip/test_ethernetip_bindings.py -q
