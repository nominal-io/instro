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
- **Docs ship with the code.** This repo contains its own docs (`README.md`, `CONTRIBUTING.md`, `docs/guides/`, `docs/reference/`, and this file). When a change is user-visible or alters conventions, update the relevant docs in the same PR — see [Documentation](#documentation) below.

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

## Documentation

Docs live in this repo and ship in the same PR as the code change. When a change is user-visible or alters how contributors work, update the relevant files on the same branch:

| Change type | Files to update |
|---|---|
| New vendor driver | `README.md` "Supported devices" table; add a guide page under `docs/guides/instrumentation/` if the device introduces a new user-facing workflow |
| Public API change (HAL methods, signatures, return types, new category) | `docs/reference/src/` (reference docs) and any affected `docs/guides/` examples |
| New feature, behavior change, or new install extra | `docs/guides/` (Mintlify site); also `README.md` if it touches the quickstart, install instructions, or extras table |
| New category or top-level module | All of the above plus `docs/guides/docs.json` navigation |
| Contributor workflow, repo convention, or tooling change | `CONTRIBUTING.md` and this file (`AGENTS.md`) |

`CHANGELOG.md` is generated by release-please from Conventional Commits — don't hand-edit it. Subdirectory `AGENTS.md` files (e.g. `docs/guides/AGENTS.md`) carry their own style rules for the docs they govern.

## Patterns and constraints

This repo prefers duplicated, explicit code over premature abstraction. The constraints below trace to specific cases where a shared helper, factory, or facade was attempted and walked back. Don't propose extracting a base / mixin / wrapper unless you can name two concrete drivers that share the *exact* behavior — and even then, duplication is often still the right call.

- **`<Category>DriverBase` is a contract surface, not implementation.** Required methods are `@abc.abstractmethod`; optional methods raise `NotImplementedError` by default, and drivers override the ones their instrument actually supports. `DMMDriverBase` is the clearest example — 8 required (`open`, `close`, `set_measurement_function`, plus 5 primary measurements) and ~15 optional (per-function range/NPLC setters, `set_digits`, `measure_four_wire_resistance`) for capabilities that aren't universal across vendors. The base carries no shared helpers, lifecycle, or state.
- **Each category can have drivers that use different transports.** `daq` already does: `Keysight34980A` on VISA sits next to `NIDAQ`, `LabJackT7`, and `MCCDAQ` on vendor SDKs, all behind one `DAQDriverBase`. The base never picks a transport; the driver does.
- **Drivers own their lifecycle (`__init__`, `open`, `close`).** Because transports vary per driver, the resource a driver holds varies in shape: a `VisaDriver` wrapper for SCPI, a bare `int` handle for vendor SDKs (LabJack), a lazily-imported module object (Aardvark). `open()` sometimes does real work — Aardvark defers `import pyaardvark` to keep the optional dep out of import time. No single `self._transport` protocol fits all of these.
- **`_check_errors` is per-driver because SCPI error semantics vary.** Response prefix (`"0"` for B&K/Rigol, `"+0"` for Siglent), command form (`SYST:ERR?` vs `:SYST:ERR?`), and the vendor name in the raised message are all per-device. A configurable mixin would carry more code than the four duplicated lines.
- **`_write_checked` is per-driver because some drivers can't use it.** The helper assumes `write + _check_errors` is one atomic step. Stateful drivers can't fit that shape — `BK9140` must hold the VISA lock across `INST <n>` channel-select + write + check, so it inlines the sequence. Keeping the helper driver-local lets stateless drivers stay terse and stateful ones write atomic sequences directly.
- **`pkgutil.extend_path` in `drivers/__init__.py`** is required for any category whose drivers can come from workspace vendor packages (`daq`, `i2c` currently). Without it, vendor-package subpackages disappear at import time.
- **VISA drivers' `__init__` accepts `str | VisaConfig`.** `VisaConfig` is the canonical customization vehicle for `VisaDriver`. Don't propose dropping the union. Drivers on other transports take whatever their transport needs.
- **No vendor-string factory** (`Instrument.create(vendor="bk", ...)`). Construct concrete drivers explicitly and pass them in: `InstroPSU(name="x", driver=BK9115(...), num_channels=1)`.
- **No driver-side facade or `hal` back-channel.** Pass instrument state to driver methods as parameters; the driver does not have a reference back to the HAL.

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

## DAQ driver authoring rules

When adding or modifying a vendor DAQ driver under `instro/daq/drivers/` or `packages/instro-daq-*/instro/daq/drivers/`, the following rules apply on top of the general "How to add a vendor driver" shape:

1. **No `InstroDAQ` reach-back.** Driver modules must not import `InstroDAQ`, and `DAQDriverBase` exposes no facade reference. All context the driver needs flows through method arguments — typically via a `DAQTask` object carrying channels and timing config.
2. **Task-keyed lifecycle.** HW-timed operations (`register_task`, `configure_*_channel`, `configure_timing`, `start_task`, `stop_task`, `read_task`, `fetch_task`, `get_actual_sample_rate`, `get_points_in_buffer`, `is_running`) take a `DAQTask` as their first positional argument. SW-timed single-shot operations take the relevant channel object directly.
3. **Tasks are kind-agnostic.** A single `DAQTask` may hold any mix of analog and digital channels (input and output). Unified-scan hardware (MCC, LabJack) maps a mixed task directly to one underlying scan. Per-kind hardware (NI DAQmx) splits a mixed task internally into per-kind SDK objects synchronized to the same timing.
4. **`NotImplementedError` opt-in.** Only `open` and `close` are abstract on `DAQDriverBase`; every other method has a default that raises `NotImplementedError` with a vendor-prefixed message. Drivers override the methods their hardware supports.
5. **Single-engine vendors reject any second task.** Vendors with one hardware timing engine (Keysight 34980A, MCC, LabJack T-series) raise `NotImplementedError` from `register_task` on the second registration. NI DAQmx supports multiple concurrent tasks.
6. **Standardized return type.** `read_task` and `fetch_task` return `list[DAQSamples]` — one cluster per timebase. De-interleave and `HWTimestamper` expansion happen inside the driver. The facade builds `Measurement` objects from the clusters.
7. **Drivers are constructible in isolation.** A driver instance must be usable without an `InstroDAQ` around it (so it can be unit-tested directly). The import-graph lint in `tests/daq/test_daq_drivers.py::test_driver_modules_dont_import_instrodaq` fails CI if a driver module imports `InstroDAQ`.

## Per-directory agent docs

Some subdirectories have their own `AGENTS.md` with narrower instructions (e.g. `docs/guides/AGENTS.md` for documentation-site work). When working inside one of those directories, that file's guidance takes precedence over this one.
