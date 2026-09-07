# Contributing to instro

Thanks for your interest in contributing. This guide covers the development workflow, conventions, and where different kinds of contributions belong in the workspace.

> **Using an AI coding tool?** See [AGENTS.md](./AGENTS.md): it's a denser, more scannable version of this doc plus codebase landmarks, intended for Claude Code, Cursor, Codex, Copilot Workspace, and similar tools.

## Development setup

### Prerequisites

The default development environment needs **Rust and a C/C++ toolchain**, even for `just check-python` and `just test-python`. The default `dev` group in [pyproject.toml](./pyproject.toml) includes the local `instro-ethernetip` package, whose [build backend](./packages/instro-ethernetip/pyproject.toml) is maturin. Both `uv sync` and the recipes' `uv run` commands can build this native extension on a fresh checkout.

**`just check` and `just test` also need CMake and LLVM/libclang**: their [Rust recipes](./justfile) cover the whole [Cargo workspace](./Cargo.toml), including OPC UA and its C dependencies (`open62541-sys` and `mbedtls`). Python-only recipes skip those workspace-wide Rust checks, but still use the default development environment.

| Layer | `just check-python` / `just test-python` | `just check` / `just test` |
|---|:---:|:---:|
| [`just`](https://github.com/casey/just) (task runner) | ✅ | ✅ |
| [`uv`](https://docs.astral.sh/uv/) (Python/env manager — also fetches Python), `>=0.12` | ✅ | ✅ |
| Synced Python deps (`uv sync`) | ✅ | ✅ |
| Git Bash (Windows only — for the `#!/usr/bin/env bash` recipes) | — | ✅ |
| Rust toolchain (pinned by `rust-toolchain.toml`) + C/C++ compiler/linker | ✅ | ✅ |
| CMake + LLVM/libclang (to build `open62541-sys`/`mbedtls`) | — | ✅ |
| Separate nightly toolchain with `rustfmt` | — | `just check` only |

You do **not** need to install Python separately — `uv` downloads and manages a supported interpreter (3.10–3.14) for you. [rust-toolchain.toml](./rust-toolchain.toml) pins the Rust build toolchain, which `rustup` installs on first use. Formatting uses a separate `cargo +nightly fmt` invocation in `just check-rust` and `just fix-rust`; after installing rustup, install nightly rustfmt as [CI does](./.github/workflows/build-check-test.yml):

```bash
rustup toolchain install nightly --profile minimal --component rustfmt
```

The uv version has a floor, set by `required-version` in `[tool.uv]`: uv refuses to run below it, and CI resolves the same constraint, so run `uv self update` if you hit that error.

<details>
<summary><strong>Windows</strong></summary>

```powershell
# Default development environment (including Python checks)
winget install --id Casey.Just -e            # just
winget install --id astral-sh.uv -e          # uv
winget install --id Git.Git -e               # Git + Git Bash (the bash recipes need it)

winget install --id Rustlang.Rustup -e       # rustup -> installs the pinned toolchain on first use
# C/C++ build tools (MSVC) — required to compile and link the native crates:
winget install --id Microsoft.VisualStudio.2022.BuildTools -e `
  --override "--quiet --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"

# Additional for `just check` and `just test`
winget install --id Kitware.CMake -e         # cmake (open62541-sys build)
winget install --id LLVM.LLVM -e             # libclang for bindgen
```

After installing LLVM, set `LIBCLANG_PATH` so `bindgen` can find `libclang.dll`, then open a fresh shell:

```powershell
setx LIBCLANG_PATH "C:\Program Files\LLVM\bin"
```

</details>

<details>
<summary><strong>macOS</strong></summary>

```bash
# Default development environment (including Python checks)
brew install just uv
# git + the C compiler come from the Command Line Tools:
xcode-select --install

brew install rustup-init && rustup-init -y   # or: brew install rustup; rustup default stable

# Additional for `just check` and `just test`
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
# Default development environment (including Python checks)
curl -LsSf https://astral.sh/uv/install.sh | sh                  # uv
sudo apt-get install -y just git                                 # or: cargo install just

curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh   # rustup
sudo apt-get install -y build-essential

# Additional for `just check` and `just test`
sudo apt-get install -y cmake clang libclang-dev pkg-config
```

`build-essential` (gcc + make + linker), `cmake`, and `clang`/`libclang-dev` cover the `open62541-sys` + `mbedtls` C build and the `bindgen` step. On Fedora/RHEL the equivalents are `gcc gcc-c++ make cmake clang clang-devel pkgconf-pkg-config`.

</details>

### Install and run

Clone the repo and install dependencies with [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/nominal-io/instro.git
cd instro
uv sync
```

Common dev tasks (via [just](https://github.com/casey/just)):

```bash
just check           # all static analysis: python (ruff format, mypy, ruff lint) + Rust (rustfmt, clippy)
just test            # all tests: python + Rust workspace + EtherNet/IP wheel checks (no hardware required)
just check-python    # python static analysis in the default development environment
just test-python     # python unit tests only
just check-rust      # nightly rustfmt + clippy with the committed Cargo.lock
just test-rust       # Rust library/integration/doc tests only
```

Notes:

- The **first `just test` is slow**: it compiles `open62541` and `mbedtls` from C source. Subsequent runs are cached (CI caches this with `Swatinem/rust-cache`).
- `uv sync` installs the default `dev` and `test` groups, including the EtherNet/IP, unstable, and contrib workspace packages. `just install` runs the same command. `uv run` also syncs this environment automatically.
- Add vendor extras only when needed, for example `uv sync --extra nidaq`. `uv sync --extra all` selects the extras listed in [pyproject.toml](./pyproject.toml); it does not install proprietary system SDKs or every workspace member.
- The vendor extras (`daq`, `labjack`, `mccdaq`, `i2c`/`aardvark`) are **not** required for `just test` — those test directories are deselected by default (see `[tool.pytest.ini_options]` in `pyproject.toml`) and need proprietary vendor SDKs plus hardware.

### Rust Cargo.lock

The root [`Cargo.lock`](Cargo.lock) covers every Rust workspace member, including mixed PyO3/maturin packages under `packages/`.

**Do not regenerate the lock casually.** When a dependency manifest changes, run `cargo update` at the repository root and commit the updated lock in the same PR.

CI verifies the committed lockfile with `--locked`, as part of `just check-rust` (which `just check` runs and CI invokes directly in its Rust checks step).

### Release PRs

release-please opens a single `chore(main): release` PR that carries every pending package bump (root `instro`, the `packages/` members, and the `crates/` members). Each package is still versioned and tagged independently; the PR is just the one place to merge them. Do not set `separate-pull-requests` in [`.github/release-please-config.json`](.github/release-please-config.json): per-package release PRs were tried and walked back because they multiplied the bot PRs to babysit (#466).

To force a specific version (for example when a `!` commit should not ship as a major), land a commit whose footer is `Release-As: X.Y.Z`, one commit per component, each touching a file under that component's path. Root `instro` sees every commit outside its `exclude-paths` and takes the newest override, so merge the root one last. Squash-merge with the footer as the commit body (`gh pr merge --squash --body "Release-As: X.Y.Z"`); a footer buried in a bulleted commit list is ignored. See #476 and the Release PRs section of [AGENTS.md](AGENTS.md).

Only commits that touch files shipped in the `instro` wheel (or its PyPI metadata: `pyproject.toml`, `README.md`) bump the root `instro` version. The root component's `exclude-paths` in [`.github/release-please-config.json`](.github/release-please-config.json) lists the top-level directories that don't ship (`packages`, `crates`, `tests`, `docs`, `examples`, `res`, and the CI/agent-tooling directories), so work confined to `packages/instro-unstable` or `.github/workflows` no longer cuts an `instro` release (#477). If you add a top-level directory that isn't part of the package, add it there.

### Rust crate releases

Pure-Rust crates under `crates/` that are published to crates.io are managed by release-please with `release-type: rust`. They are versioned independently from the Python packages and from each other. The public crate names are their Cargo package names, but release-please component names may differ to avoid GitHub tag collisions with Python packages; for example, the Rust EtherNet/IP crate uses `instro-ethernetip-rs` tags while the Python wrapper keeps `instro-ethernetip` tags.

When a Rust core crate backs a Python package, release-please's `cargo-workspace` plugin can patch-bump the wrapper automatically when the core crate releases. The wrapper dependency key must exactly match the core crate's `[package].name`; do not alias a path dependency with `package = ...`, because release-please does not resolve dependency paths when building its workspace graph.

Do not pre-seed a new crate path in [`.github/release-please-manifest.json`](.github/release-please-manifest.json) when the next release should be that initial version. Set `initial-version` in [`.github/release-please-config.json`](.github/release-please-config.json) and let the first generated release PR add the manifest entry.

The release workflow publishes crates with crates.io Trusted Publishing (`rust-lang/crates-io-auth-action`) instead of a stored `CARGO_REGISTRY_TOKEN`. Each crate must already exist on crates.io and must have a trusted publisher configured for `nominal-io/instro` and `.github/workflows/release-please-publish.yml`.

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

Every PR must pass `just check` and `just test`. [Build/test CI](./.github/workflows/build-check-test.yml) runs the checks across Windows, macOS, Linux, and the supported Python versions, and separately checks `uv lock --check`. Passing locally covers your current environment; it does not guarantee that the full CI matrix passes. [Docs CI](./.github/workflows/docs-check.yml) checks generated example pages and navigation for drift, and [PR title CI](./.github/workflows/lint-pr-title.yml) checks Conventional Commits formatting. To reproduce the docs regeneration locally, run `just gen-examples`, inspect `git diff -- docs/guides/instrumentation/examples docs/guides/docs.json`, and commit any required generated changes.

A separate [scheduled workflow](./.github/workflows/latest-deps-test.yml) re-resolves dependencies to the latest versions `pyproject.toml` allows and re-runs the Python tests. If that workflow fails, fix the incompatibility or tighten the constraint rather than re-pinning the lockfile.

For a new driver, ship a unit test against a mocked transport. [The BK9115 software tests](./tests/psu/bk/test_bk_9115_software.py) patch the driver's `VisaDriver` reference with `autospec=True` and assert wire-level commands. Follow the existing layout for the category; PSU driver tests live under `tests/psu/<vendor>/`, while [test_psu_drivers.py](./tests/psu/test_psu_drivers.py) covers the base contract and HAL composition. For a focused local run:

```bash
uv run pytest tests/psu/bk/test_bk_9115_software.py
```

GitHub Actions in `.github/workflows/` are pinned to full commit SHAs with the release version as a trailing comment (`uses: actions/checkout@11d5960a... # v4.4.0`), so a repointed upstream tag can't silently change what runs in CI. Dependabot (`.github/dependabot.yml`) opens a weekly grouped PR bumping the pins. When adding an action, pin it the same way, resolving the SHA from the upstream repo's release — never from an unverified suggestion.

Write unit tests to cover the invariant or edge case under test with the least necessary complexity. Prefer targeted, high-signal cases over broad, redundant matrices, heavily abstracted helpers. Don't rewrite tests where the main effect is making tests look shareable. Don't add tests simply to increase line coverage or the number of executed cases. You're going to be working features/bug-fixes that have a clear reason test, so make sure that your tests are meaningfully addressing the real needs for test coverage. Shared test helpers are fine when they remove duplication without hiding the behavior that each test proves. A large, complicated test suite that repeats the same assertion in different shapes is almost as unhelpful as no coverage.

Bug fixes should add regression coverage. Opening a PR with a title like `fix(...): ...` needs to include at least one new test that would have failed before the fix. If no such test is written, provide a convincing explantation in the PR description for why new coverage is not necessary.

### Documentation

Docs live in this repo, so they ship in the same PR as the code change. If your change is user-visible or alters how contributors work, update the relevant files on the same branch:

| Change type | Files to update |
|------|------|
| New vendor driver | `README.md` "Supported devices" table; add a guide page under `docs/guides/instrumentation/` if the device introduces a new user-facing workflow |
| New contrib driver | "Available drivers" section of `docs/guides/instrumentation/contrib.mdx` |
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
- **Add an entry to the "Available drivers" section** of [`docs/guides/instrumentation/contrib.mdx`](./docs/guides/instrumentation/contrib.mdx) in the same PR. That section is documented as the complete set of contrib drivers for the current release; a merged driver missing from it makes the doc wrong.

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
3. Leave a stub module at the old path that raises an `ImportError` naming the destination and the graduating release (e.g. `SomeVendorPSU graduated to core in v1.2. Import it from instro.psu.drivers.`). Exclude the stub from the contrib smoke test if needed.
4. Open a follow-up issue to delete the stub in the next release.
5. Describe the import-path change in the PR and affected user documentation, and use a breaking-change Conventional Commit title so release-please generates the changelog entry. Do not hand-edit `CHANGELOG.md`.

The old import path is a hard cutover: consumers pinning `instro-contrib` for that driver should switch to `instro` and update the import to drop `.contrib`. The stub is not a compatibility shim. Old code stays broken; the error just points to the new import path for one release.

### `instro-unstable`: in-development categories and abstractions

The `instro-unstable` package (`packages/instro-unstable/`) holds in-development features whose shape isn't yet settled: typically a whole new instrument category, a new protocol handler, or a new abstraction. Code lives here while the API is still moving, then graduates to core (or its own workspace package) once it stabilizes.

For *drivers* in existing categories (PSU, DMM, etc.), use `instro-contrib` instead. That's the right home regardless of who's contributing.

Importable as `from instro.unstable.<feature> import …`, and developers get it automatically via `uv sync`.

### Vendor-SDK packages: `instro-daq-ni`, `instro-daq-labjack`, etc.

Drivers that wrap a proprietary native SDK against hardware live in their own workspace packages, so the heavy native dependency stays optional for users who don't need it.

If you'd like to contribute a driver that needs a native vendor SDK we don't already wrap, open an issue first. We'll figure out the right home together.
