# Contributing to instro

Thanks for your interest in contributing. This guide covers the development workflow, conventions, and where different kinds of contributions belong in the workspace.

> **Using an AI coding tool?** See [AGENTS.md](./AGENTS.md): it's a denser, more scannable version of this doc plus codebase landmarks, intended for Claude Code, Cursor, Codex, Copilot Workspace, and similar tools.

## Development setup

### Prerequisites

What you need depends on which command you run. **`just check` is lightweight** (just + uv). **`just test` needs a full native toolchain**: it builds a maturin/PyO3 wheel and runs `cargo test` across the whole Rust workspace, which includes the `opcua` crate. That crate compiles `open62541-sys` (with `mbedtls`) from C source, so a C compiler, CMake, and LLVM/libclang are required.

| Layer | `just check` | `just test` |
|---|:---:|:---:|
| [`just`](https://github.com/casey/just) (task runner) | ✅ | ✅ |
| [`uv`](https://docs.astral.sh/uv/) (Python/env manager — also fetches Python) | ✅ | ✅ |
| Synced Python deps (`uv sync`) | ✅ | ✅ |
| Git Bash (Windows only — for the `#!/usr/bin/env bash` recipes) | — | ✅ |
| Rust toolchain (auto-pinned by `rust-toolchain.toml`) | — | ✅ |
| C compiler + CMake + LLVM/libclang (to build `open62541-sys`/`mbedtls`) | — | ✅ |

You do **not** need to install Python separately — `uv` downloads and manages a supported interpreter (3.10–3.13) for you. You also don't need to pick a Rust version: `rust-toolchain.toml` pins it, and `rustup` auto-installs that toolchain (with `clippy` + `rustfmt`) on first `cargo` invocation.

<details>
<summary><strong>Windows</strong></summary>

```powershell
# Core (covers `just check`)
winget install --id Casey.Just -e            # just
winget install --id astral-sh.uv -e          # uv
winget install --id Git.Git -e               # Git + Git Bash (the bash recipes need it)

# Additional for `just test`
winget install --id Rustlang.Rustup -e       # rustup -> installs the pinned toolchain on first use
winget install --id Kitware.CMake -e         # cmake (open62541-sys build)
winget install --id LLVM.LLVM -e             # libclang for bindgen
# C/C++ build tools (MSVC) — required to compile and link the native crates:
winget install --id Microsoft.VisualStudio.2022.BuildTools -e `
  --override "--quiet --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
```

After installing LLVM, set `LIBCLANG_PATH` so `bindgen` can find `libclang.dll`, then open a fresh shell:

```powershell
setx LIBCLANG_PATH "C:\Program Files\LLVM\bin"
```

</details>

<details>
<summary><strong>macOS</strong></summary>

```bash
# Core (covers `just check`)
brew install just uv
# git + the C compiler come from the Command Line Tools:
xcode-select --install

# Additional for `just test`
brew install rustup-init && rustup-init -y   # or: brew install rustup; rustup default stable
brew install cmake llvm                       # cmake + libclang (bindgen)
```

Apple Clang (from the Command Line Tools) is enough as the C compiler/linker, but `open62541-sys`'s `bindgen` wants Homebrew `llvm`'s `libclang`. If it isn't found, export:

```bash
export LIBCLANG_PATH="$(brew --prefix llvm)/lib"
```

</details>

<details>
<summary><strong>Linux (Debian/Ubuntu)</strong></summary>

```bash
# Core (covers `just check`)
curl -LsSf https://astral.sh/uv/install.sh | sh                  # uv
sudo apt-get install -y just git                                 # or: cargo install just

# Additional for `just test`
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh   # rustup
sudo apt-get install -y build-essential cmake clang libclang-dev pkg-config
```

`build-essential` (gcc + make + linker), `cmake`, and `clang`/`libclang-dev` cover the `open62541-sys` + `mbedtls` C build and the `bindgen` step. On Fedora/RHEL the equivalents are `gcc gcc-c++ make cmake clang clang-devel pkgconf-pkg-config`.

</details>

### Install and run

Clone the repo and install dependencies with [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/nominal-io/instro.git
cd instro
uv sync --extra all
```

Common dev tasks (via [just](https://github.com/casey/just)):

```bash
just check    # ruff format, mypy, ruff lint
just test     # unit tests + Rust workspace + EtherNet/IP wheel checks (no hardware required)
```

Notes:

- The **first `just test` is slow**: it compiles `open62541` and `mbedtls` from C source. Subsequent runs are cached (CI caches this with `Swatinem/rust-cache`).
- `uv run` auto-syncs the environment, so `just test` works even without a prior `just install`/`uv sync`, but running `uv sync --extra all` first makes the dependency step explicit.
- The vendor extras (`daq`, `labjack`, `mccdaq`, `i2c`/`aardvark`) are **not** required for `just test` — those test directories are deselected by default (see `[tool.pytest.ini_options]` in `pyproject.toml`) and need proprietary vendor SDKs plus hardware.

### Rust Cargo.lock (dual-lock policy)

Rust tooling spans two dependency graphs:

- Root [`Cargo.lock`](Cargo.lock) covers workspace **members** (`instro-ethernetip-rs`, `opcua`, …).
- Each standalone PyO3/maturin wrapper under `packages/<name>/` owns its own committed `Cargo.lock` beside its manifest (currently `packages/instro-ethernetip/`).

**Do not regenerate locks casually.** When dependency manifests change, refresh the relevant lock in the same PR:

- Workspace members: `cargo update` at the repo root.
- Standalone wrappers: `cargo update --manifest-path packages/<name>/Cargo.toml`.

CI verifies all committed lockfiles with `--locked`:

- `just rust-lock-check` — fast lock-only check for the root workspace and every registered standalone package.
- `just rust-standalone` — fmt-check, clippy, and locked `cargo check` for standalone wrappers.

Both run explicitly in CI and are included in `just rust` (which `just test` invokes via `just eip-test`).

**Adding a new standalone wrapper:** add the crate to `exclude` in root [`Cargo.toml`](Cargo.toml), add its path to `rust-standalone-packages` in the [`justfile`](justfile), and commit an initial `Cargo.lock` beside the manifest.

## Issues and discussion

**Every change is tracked by a GitHub issue or ticket: no exceptions, including typos and one-line fixes.** Open a [GitHub issue](https://github.com/nominal-io/instro/issues) before starting work so scope, ownership, and history are all traceable from the issue → branch → PR chain.

For larger or cross-cutting work, use the issue to align on scope before writing code. Good candidates for an explicit issue-first discussion:

- A new driver: especially one that requires a native vendor SDK we don't already wrap.
- A new instrument category (PSU, DMM, etc. aren't an exhaustive list).
- Anything touching public API or cross-cutting abstractions.
- Behaviour changes to existing drivers.

If you find an unrelated issue while working on something else, open a separate issue and keep your current PR focused. That keeps reviews quick and avoids scope creep.

## Submitting a pull request

### Branches

Every change must be tracked by a GitHub issue or ticket. See [Issues and discussion](#issues-and-discussion). Branch off `main` and name the branch after that issue/ticket ID. Examples:

```
issue-142-siglent-spd-driver
instro-248-docstring-cleanup
```

### Pull request titles

PR titles follow the [Conventional Commits](https://www.conventionalcommits.org/) specification:

```
<type>(<optional scope>): <short description>
```

| Type | When to use |
|------|-------------|
| `feat` | A new feature |
| `fix` | A bug fix |
| `chore` | Maintenance, dependency updates, tooling |
| `docs` | Documentation changes only |
| `refactor` | Code restructuring with no behaviour change |

Rules:

- Use the imperative mood ("add login", not "added login").
- Keep the title under 72 characters and don't end with a period.
- Add `!` after the type for breaking changes: `feat!: redesign auth flow`.

Examples:

```
feat(psu): add Siglent SPD3303X-E driver
fix(dmm): handle empty response from Keithley 2400
chore: bump pymodbus to 3.12
refactor(daq): consolidate channel naming
```

### Commits

Individual commits should follow the same Conventional Commits format. Each commit should represent a single logical change. Squash-merge will land in `main`, so a long history of WIP commits in the PR is fine.

### Tests and checks

Every PR must pass `just check` and `just test`. CI will run these automatically. If you've added a new driver, ship a unit test against a mocked transport (see existing tests under `tests/psu/`, `tests/dmm/`, etc. for the pattern: patch `VisaDriver` (or whatever transport your driver composes) with `autospec=True` and assert the wire-level commands).

### Documentation

Docs live in this repo, so they ship in the same PR as the code change. If your change is user-visible or alters how contributors work, update the relevant files on the same branch:

| Change type | Files to update |
|------|------|
| New vendor driver | `README.md` "Supported devices" table; add a guide page under `docs/guides/instrumentation/` if the device introduces a new user-facing workflow |
| Public API change (HAL methods, signatures, return types, new category) | `docs/reference/src/` and any affected `docs/guides/` examples |
| New feature, behavior change, or new install extra | `docs/guides/` (the Mintlify site); also `README.md` if it touches the quickstart, install instructions, or extras table |
| New category or top-level module | All of the above plus `docs/guides/docs.json` navigation |
| Contributor workflow, repo convention, or tooling change | `CONTRIBUTING.md` and [`AGENTS.md`](./AGENTS.md) |
| New or changed AI skill/subagent | Claude Code (`.claude/skills/`, `.claude/agents/`) and Codex CLI (`.agents/skills/`, `.codex/agents/`) toolchain copies, plus the table in [`AGENTS.md`](./AGENTS.md#repo-skills-and-subagents) |

Don't hand-edit `CHANGELOG.md`. It's generated by release-please from your Conventional Commits.

When in doubt, update the docs anyway. Reviewers will tell you if a doc change is unnecessary, but it's harder to catch the *absence* of one.

## Where your contribution belongs

`instro` is structured as a uv workspace with several packages. Pick the one that matches your contribution.

### Core `instro`

Drivers and library code that the maintainers have verified directly against the hardware. Anything landing here goes through full code review, and the maintainers own the device so we can keep verifying as the codebase evolves.

If you're contributing a driver for a device the maintainers don't own, **start in `instro-contrib`** (below). It can graduate into core later when the hardware is available for direct verification.

### `instro-contrib`: community-contributed drivers

The `instro-contrib` package (`packages/instro-contrib/`) hosts drivers the maintainers cannot verify directly against the device. Hardware verification is done by the contributor, not by the maintainers.

The package is published to PyPI as `instro-contrib`, released alongside `instro` through the shared release-please flow.

The bar is deliberately lower than core's because the maintainers can't independently test these drivers, but every contribution still passes review.

#### Contribution bar

A contrib driver must:

- **Have type hints** on all public methods.
- **Pass mypy and ruff** like the rest of the repo.
- **Ship a unit test** that exercises the driver against a mocked transport.
- **Document the model(s) it targets** in a module docstring.
- **Be verified by the contributor against real hardware**: this is the trust we're extending. Note the model(s) and firmware version(s) you tested against in the PR description.

It does **not** need:

- Independent hardware verification by the maintainers.
- Vendor signoff.
- API stability guarantees release-to-release (callers should pin to a specific commit if they need reproducibility).

#### Layout

The contrib package mirrors the core `instro/` layout with `.contrib` inserted right after the top-level package name. Drivers for a category that already exists in core go under `instro/contrib/<category>/drivers/`:

```
packages/instro-contrib/
  instro/
    contrib/
      psu/
        drivers/
          siglent_spd_xxx.py
      dmm/
        drivers/
          weird_dmm.py
```

Importable as:

```python
from instro.contrib.psu.drivers import SiglentSPDxxx
```

The word `contrib` travels with every import as the disclaimer.

#### When to use the contrib package

- A new driver for an existing core category (PSU, DMM, E-Load, DAQ, I2C) that the maintainers can't verify on their own.
- A driver for a device the maintainers don't own.

#### When NOT to use it

- **A driver the maintainers own the device for**: those land directly in core.
- **A whole new category** (e.g. spectrum analyzer): that's an in-development feature. Use the `instro-unstable` package until the category shape settles.
- **A driver that requires a native vendor SDK**: open an issue first. Whether it lands in `instro-contrib`, in an existing vendor package, or in a new workspace package depends on the specifics, and we'd rather discuss it before you sink time into a layout we'd ask you to change.

#### Graduating a contrib driver to core

When the maintainers acquire the device and can verify the driver directly:

1. `git mv` the file from `packages/instro-contrib/instro/contrib/<cat>/drivers/<driver>.py` to `instro/<cat>/drivers/<driver>.py`.
2. Move the entry from `packages/instro-contrib/instro/contrib/<cat>/drivers/__init__.py` to the corresponding `instro/<cat>/drivers/__init__.py`.
3. Note the graduation in `CHANGELOG.md`.

The old import path is a hard cutover: consumers pinning `instro-contrib` for that driver should switch to `instro` and update the import to drop `.contrib`.

### `instro-unstable`: in-development categories and abstractions

The `instro-unstable` package (`packages/instro-unstable/`) holds in-development features whose shape isn't yet settled: typically a whole new instrument category, a new protocol handler, or a new abstraction. Code lives here while the API is still moving, then graduates to core (or its own workspace package) once it stabilizes.

For *drivers* in existing categories (PSU, DMM, etc.), use `instro-contrib` instead. That's the right home regardless of who's contributing.

Importable as `from instro.unstable.<feature> import …`, and developers get it automatically via `uv sync`.

### Vendor-SDK packages: `instro-daq-ni`, `instro-daq-labjack`, etc.

Drivers that wrap a proprietary native SDK against hardware live in their own workspace packages, so the heavy native dependency stays optional for users who don't need it.

If you'd like to contribute a driver that needs a native vendor SDK we don't already wrap, open an issue first. We'll figure out the right home together.
