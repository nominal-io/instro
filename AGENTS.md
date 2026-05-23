# AGENTS.md

Context for AI coding tools (Claude Code, Cursor, OpenAI Codex CLI, GitHub Copilot Workspace, Aider, etc.) working in this repo. Human contributors should read [CONTRIBUTING.md](./CONTRIBUTING.md) instead — this file is dense on purpose.

## Quick reference

```bash
uv sync --extra all              # install everything
uv sync --extra <name>           # install one optional package (daq, labjack, nidaq, mccdaq, i2c, aardvark)
just check                       # ruff format, mypy, ruff lint
just test                        # unit tests; no hardware required
uv build --package <name>        # build a wheel for a workspace package
```

If `just check` and `just test` both pass, CI will pass.

## Codebase layout

`instro` is a uv workspace. The top-level package is `instro`; workspace members live under `packages/`.

| Path | What it is |
|---|---|
| `instro/<category>/` | Category code: HAL class (`InstroPSU`, `InstroDMM`, …), `types.py`, the base driver class (`PSUDriverBase`, etc.). Categories: `psu`, `dmm`, `eload`, `daq`, `i2c`, `modbus`. |
| `instro/<category>/drivers/` | Concrete vendor drivers, one file per vendor/model family. Registered in `drivers/__init__.py`. |
| `instro/utils/transports/` | Transport drivers (`VisaDriver`). Category bases are transport-agnostic; concrete drivers compose transports. |
| `packages/instro-contrib/` | Community-contributed drivers. Mirrors core layout under `instro/contrib/`. |
| `packages/instro-unstable/` | In-development categories and abstractions whose API isn't settled. |
| `packages/instro-{daq-ni,daq-labjack,daq-mcc,i2c-aardvark}` | Vendor packages wrapping proprietary native SDKs. |
| `tests/<category>/` | Per-category tests, predominantly mocked-transport unit tests. |

## Conventions

- **Every change has a tracking issue/ticket.** Branch off `main`; name the branch after the GitHub issue or ticket ID (e.g. `issue-142-siglent-spd-driver`, `con-2498-docstring-cleanup`). No untracked work — open an issue first if one doesn't exist.
- **Conventional Commits** for PR titles and commits: `<type>(<scope>): <imperative description>`. Types: `feat`, `fix`, `chore`, `docs`, `refactor`. Append `!` for breaking changes. Title under 72 chars, no trailing period.
- **No multi-paragraph docstrings.** One short line max. Don't reintroduce verbose docstrings — the repo went through a deliberate cleanup pass (CON-2498).
- **No comments unless the *why* is non-obvious.** Don't restate what the code does.
- **Type hints required** on all public methods. `mypy` is enforced.
- **`ruff format` and `ruff check` are enforced.** Run `just check` before pushing.
- **Scope discipline.** Keep PRs focused on the work at hand. If you find something unrelated, open a separate GitHub issue rather than expanding the PR.

### Naming

- **Category base classes** — `<Category>DriverBase`. Examples: `PSUDriverBase`, `DMMDriverBase`, `I2CDriverBase`. One per category.
- **Concrete vendor drivers** — `<Vendor><Model>`. Examples: `BK9115`, `RigolDP800`, `Keithley2400`. File name is the snake_case form (`bk_9115.py`).

## How to add a vendor driver

Use `instro/psu/drivers/bk_9115.py` as the reference. The shape is:

1. Create `instro/<category>/drivers/<vendor>_<model>.py`. Subclass the category base (`PSUDriverBase`, `DMMDriverBase`, …).
2. Compose `VisaDriver` (or another transport) in `__init__` — don't subclass it. Accept `str | VisaConfig` so callers can customize.
3. Implement `open`, `close`, and the category-required methods.
4. Add per-driver `_write_checked` / `_check_errors` helpers if the device supports `SYST:ERR?`. Do **not** extract these to a shared mixin (see Patterns below).
5. Register in `instro/<category>/drivers/__init__.py` (both the import and `__all__`).
6. Add tests in `tests/<category>/test_<category>_drivers.py`. The canonical pattern is in `tests/psu/test_psu_drivers.py`: patch the driver's `VisaDriver` reference with `autospec=True`, assert wire-level commands.

## How to add a community driver

Same shape as above, but in `packages/instro-contrib/instro/contrib/<category>/drivers/<vendor>_<model>.py`. Register in the corresponding contrib `drivers/__init__.py`. The smoke test at `tests/contrib/test_contrib_smoke.py` picks it up automatically — it walks every module under `instro.contrib`.

The contrib bar is in [CONTRIBUTING.md](./CONTRIBUTING.md#instro-contrib--community-contributed-drivers).

## Patterns and constraints

This repo prefers duplicated, explicit code over premature abstraction. The constraints below trace to specific cases where a shared helper, factory, or facade was attempted and walked back. Don't propose extracting a base / mixin / wrapper unless you can name two concrete drivers that share the *exact* behavior — and even then, duplication is often still the right call.

- **`pkgutil.extend_path` in `drivers/__init__.py`** is required for any category whose drivers can come from workspace vendor packages (`daq`, `i2c` currently). Without it, vendor-package subpackages disappear at import time.
- **`VisaConfig` is the canonical customization vehicle** for `VisaDriver`. Driver `__init__` accepts `str | VisaConfig`. Don't propose dropping the union.
- **Drivers write their own `__init__`, `open`, `close` directly.** This is a feature, not duplication. Don't hide it behind base-class helpers.
- **No vendor-string factory** (`Instrument.create(vendor="bk", ...)`). Construct concrete drivers explicitly and pass them in: `InstroPSU(name="x", driver=BK9115(...), num_channels=1)`.
- **No driver-side facade or `hal` back-channel.** Pass instrument state to driver methods as parameters; the driver does not have a reference back to the HAL.
- **`SYST:ERR?` helpers (`_write_checked`, `_check_errors`) are duplicated per driver intentionally.** SCPI error-check semantics aren't universal — a shared mixin would over-fit one vendor's behavior.

## Codebase landmarks

| Need | File |
|---|---|
| Driver shape | `instro/psu/drivers/bk_9115.py` |
| Category HAL | `instro/psu/psu.py` |
| Transport driver | `instro/utils/transports/visa.py` |
| Test pattern (mocked transport) | `tests/psu/test_psu_drivers.py` |
| Public API usage | `examples/<category>/` — runnable scripts showing what a user's code looks like |
| Workspace vendor package | `packages/instro-daq-ni/` |
| Community-driver layout | `packages/instro-contrib/instro/contrib/` |

## Per-directory agent docs

Some subdirectories have their own `AGENTS.md` with narrower instructions (e.g. `docs/guides/AGENTS.md` for documentation-site work). When working inside one of those directories, that file's guidance takes precedence over this one.
