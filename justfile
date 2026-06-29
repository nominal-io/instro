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

# run unit tests plus EtherNet/IP packaging checks
test: eip-test
    uv run pytest

# check static typing
check-types:
    uv run mypy

# check static typing across all supported python versions
check-types-all:
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

# run all static analysis checks
check: check-format check-types check-imports

# fixes out-of-order imports (note: mutates the code)
fix-imports:
    uv run ruff check --fix

# fixes code formatting (note: mutates the code)
fix-format:
    uv run ruff format

# fix imports and formatting
fix: fix-format fix-imports

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

# run Rust formatting, linting, and library/doc tests for the workspace
rust:
    cargo fmt --all
    cargo clippy --workspace --all-targets --all-features -- -D warnings
    cargo test --workspace --all-features --lib --tests
    cargo test --workspace --all-features --doc

# run the Rust explicit EtherNet/IP integration test against the bundled simulator
eip-rs-test:
    cargo test -p instro-ethernetip-rs --test explicit_session_integration

# run EtherNet/IP integration tests against the live PLC at 10.123.1.199:44818
eip-live-test:
    #!/usr/bin/env bash
    set -euo pipefail
    export INSTRO_EIP_PLC_ENDPOINT=10.123.1.199:44818
    export INSTRO_EIP_ROUTE_PATH_SLOTS=0
    export INSTRO_EIP_TARGET_L32E=1
    cargo test -p instro-ethernetip-rs --test explicit_session_integration
    uv run --no-cache --reinstall-package instro-ethernetip --with-editable . pytest -m hardware tests/test_ethernetip_bindings.py -q

# clean build of the unstable EtherNet/IP Python bindings (sdist + wheel)
# uv selects the workspace package via --package, then uses that package's

# [build-system] backend; for instro-ethernetip that backend is maturin.
eip-build:
    uv build --package instro-ethernetip

# install the built wheel into an isolated environment and verify the private native module
eip-wheel-smoke-test:
    #!/usr/bin/env bash
    set -euo pipefail
    # Use an isolated wheel dir so stale dist/ artifacts cannot be selected.
    wheel_dir="$(mktemp -d)"
    trap 'rm -rf "$wheel_dir"' EXIT
    # Build the platform-specific native extension wheel. This wheel provides
    # instro.unstable._ethernetip, the private PyO3 module loaded at import time.
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
    INSTRO_EIP_WHEEL="$wheel" uv run "${uv_run_args[@]}" python tests/ethernetip_wheel_smoke.py

# Full EIP test suite: wheel smoke test, Rust/Python bindings, and cpppo integration
eip-test: eip-wheel-smoke-test rust eip-rs-test
    uv run --no-cache --reinstall-package instro-ethernetip --with-editable . pytest tests/test_ethernetip_bindings.py -q
