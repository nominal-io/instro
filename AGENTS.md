# AGENTS.md

Context for AI coding tools (Claude Code, Cursor, OpenAI Codex CLI, GitHub Copilot Workspace, Aider, etc.) working in this repo. Human contributors should read [CONTRIBUTING.md](./CONTRIBUTING.md) instead. This file is dense on purpose.

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

`instro` is a uv workspace. The top-level package is `instro`. Workspace members live under `packages/`.

| Path | What it is |
|---|---|
| `instro/<category>/` | Category code: HAL class (`InstroPSU`, `InstroDMM`, …), `types.py`, the base driver class (`PSUDriverBase`, etc.). Categories: `psu`, `dmm`, `eload`, `daq`, `i2c`, `modbus`. |
| `instro/<category>/drivers/` | Concrete vendor drivers, one file per vendor/model family. Registered in `drivers/__init__.py`. |
| `instro/lib/transports/` | Transport drivers (`VisaDriver`). Category bases are transport-agnostic; concrete drivers compose transports. |
| `packages/instro-contrib/` | Community-contributed drivers. Mirrors core layout under `instro/contrib/`. |
| `packages/instro-unstable/` | In-development categories and abstractions whose API isn't settled. |
| `packages/instro-{daq-ni,daq-labjack,daq-mcc,i2c-aardvark}` | Vendor packages wrapping proprietary native SDKs. |
| `tests/<category>/` | Per-category tests, predominantly mocked-transport unit tests. |

## Conventions

- **Every change has a tracking issue/ticket.** Branch off `main` and name the branch after the GitHub issue or ticket ID (e.g. `issue-142-siglent-spd-driver`, `instro-248-docstring-cleanup`). No untracked work. Open an issue first if one doesn't exist.
- **Conventional Commits** for PR titles and commits: `<type>(<scope>): <imperative description>`. Types: `feat`, `fix`, `chore`, `docs`, `refactor`. Append `!` for breaking changes. Title under 72 chars, no trailing period.
- **No multi-paragraph docstrings.** One short line max. Don't reintroduce verbose docstrings: the repo went through a deliberate cleanup pass (INSTRO-248).
- **No comments unless the *why* is non-obvious.** Don't restate what the code does.
- **Type hints required** on all public methods. `mypy` is enforced.
- **`ruff format` and `ruff check` are enforced.** Run `just check` before pushing.
- **Scope discipline.** Keep PRs focused on the work at hand. If you find something unrelated, open a separate GitHub issue rather than expanding the PR.
- **Docs ship with the code.** This repo contains its own docs (`README.md`, `CONTRIBUTING.md`, `docs/guides/`, `docs/reference/`, and this file). When a change is user-visible or alters conventions, update the relevant docs in the same PR: see [Documentation](#documentation) below.

### Naming

- **Category base classes**: `<Category>DriverBase`. Examples: `PSUDriverBase`, `DMMDriverBase`, `I2CDriverBase`. One per category.
- **Concrete vendor drivers**: `<Vendor><Model>`. Examples: `BK9115`, `RigolDP800`, `Keithley2400`. File name is the snake_case form (`bk_9115.py`).

## How to add a vendor driver

Use `instro/psu/drivers/bk_9115.py` as the reference. The shape is:

1. Create `instro/<category>/drivers/<vendor>_<model>.py`. Subclass the category base (`PSUDriverBase`, `DMMDriverBase`, …).
2. Compose `VisaDriver` (or another transport) in `__init__`: don't subclass it. Accept `str | VisaConfig` so callers can customize.
3. Implement `open`, `close`, and the category-required methods.
4. Add per-driver `_write_checked` / `_check_errors` helpers if the device supports `SYST:ERR?`. Do **not** extract these to a shared mixin (see Patterns below).
5. Register in `instro/<category>/drivers/__init__.py` (both the import and `__all__`).
6. Add tests in `tests/<category>/test_<category>_drivers.py`. The canonical pattern is in `tests/psu/test_psu_drivers.py`: patch the driver's `VisaDriver` reference with `autospec=True`, assert wire-level commands.

## How to add a community driver

Same shape as above, but in `packages/instro-contrib/instro/contrib/<category>/drivers/<vendor>_<model>.py`. Register in the corresponding contrib `drivers/__init__.py`. The smoke test at `tests/contrib/test_contrib_smoke.py` picks it up automatically: it walks every module under `instro.contrib`.

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
| New or changed AI skill/subagent | Both toolchains' copies (Claude `.claude/`, Codex `.agents/` + `.codex/`) and the [Repo skills and subagents](#repo-skills-and-subagents) table |

`CHANGELOG.md` is generated by release-please from Conventional Commits. Don't hand-edit it. Subdirectory `AGENTS.md` files (e.g. `docs/guides/AGENTS.md`) carry their own style rules for the docs they govern.

## Patterns and constraints

This repo prefers duplicated, explicit code over premature abstraction. The constraints below trace to specific cases where a shared helper, factory, or facade was attempted and walked back. Don't propose extracting a base / mixin / wrapper unless you can name two concrete drivers that share the *exact* behavior. Even then, duplication is often still the right call.

- **`<Category>DriverBase` is a contract surface, not implementation.** Required methods are `@abc.abstractmethod`. Optional methods raise `NotImplementedError` by default, and drivers override the ones their instrument actually supports. `DMMDriverBase` is the clearest example, with 8 required (`open`, `close`, `set_measurement_function`, plus 5 primary measurements) and ~15 optional (per-function range/NPLC setters, `set_digits`, `measure_four_wire_resistance`) for capabilities that aren't universal across vendors. The base carries no shared helpers, lifecycle, or state. `DAQDriverBase` is the one exception: it has a default `__init__` that initializes the channel/timing dicts the driver is required to populate (see [DAQ driver state tracking](#daq-driver-state-tracking)).
- **Each category can have drivers that use different transports.** `daq` already does this. `Keysight34980A` on VISA sits next to `NIDAQ`, `LabJackT7`, and `MCCDAQ` on vendor SDKs, all behind one `DAQDriverBase`. The base never picks a transport. The driver does.
- **Drivers own their lifecycle (`__init__`, `open`, `close`).** Because transports vary per driver, the resource a driver holds varies in shape: a `VisaDriver` wrapper for SCPI, a bare `int` handle for vendor SDKs (LabJack), a lazily-imported module object (Aardvark). `open()` sometimes does real work: Aardvark defers `import pyaardvark` to keep the optional dep out of import time. No single `self._transport` protocol fits all of these.
- **`_check_errors` is per-driver because SCPI error semantics vary.** Response prefix (`"0"` for B&K/Rigol, `"+0"` for Siglent), command form (`SYST:ERR?` vs `:SYST:ERR?`), and the vendor name in the raised message are all per-device. A configurable mixin would carry more code than the four duplicated lines.
- **`_write_checked` is per-driver because some drivers can't use it.** The helper assumes `write + _check_errors` is one atomic step. Stateful drivers can't fit that shape: `BK9140` must hold the VISA lock across `INST <n>` channel-select + write + check, so it inlines the sequence. Keeping the helper driver-local lets stateless drivers stay terse and stateful ones write atomic sequences directly.
- **`pkgutil.extend_path` in `drivers/__init__.py`** is required for any category whose drivers can come from workspace vendor packages (`daq`, `i2c` currently). Without it, vendor-package subpackages disappear at import time.
- **VISA drivers' `__init__` accepts `str | VisaConfig`.** `VisaConfig` is the canonical customization vehicle for `VisaDriver`. Don't propose dropping the union. Drivers on other transports take whatever their transport needs.
- **No vendor-string factory** (`Instrument.create(vendor="bk", ...)`). Construct concrete drivers explicitly and pass them in: `InstroPSU(name="x", driver=BK9115(...), num_channels=1)`.
- **No driver-side facade or back-channel.** Drivers don't hold a reference back to the category HAL. Any vendor-specific state a driver needs across calls (e.g. an `nidaqmx.Task` handle, a `VisaDriver`, a cached sample rate) lives on the driver itself.

## DAQ driver state tracking

`DAQDriverBase` is the single source of truth for configured channels and AI/AO/DI/DO timing config, held in **private** dicts/slots (`_ai_channels`, `_ao_channels`, `_di_channels`, `_do_channels`, `_relay_channels`, the four `_*_hw_timing_config` slots). The only sanctioned way to change this state is the `configure_*` / `define_*` path, which programs the device and *then* records the channel. State is exposed for introspection through read-only `@property` accessors that return **frozen snapshots** captured at call time (`types.MappingProxyType` over a shallow copy for the dicts; a `tuple` for the aggregate `channels`) — not live views. `InstroDAQ` does not hold its own copies: `daq.ai_channels`, `daq.relay_channels`, `daq.ai_hw_timing_config`, the aggregate `daq.channels`, and friends delegate straight to the driver's read-only accessors. The channel/timing dataclasses (`AnalogChannel`, `DigitalChannel`, `RelayChannel`, `HWTimingConfig`, …) are `frozen=True`, so a snapshot a user holds can't be mutated and won't change when later `configure_*` calls run.

Driver authoring rules:

1. **Call `super().__init__()`** at the top of every concrete driver's `__init__`. The base initializes `_ai_channels`, `_ao_channels`, `_di_channels`, `_do_channels`, `_relay_channels`, all four `_*_hw_timing_config` slots, and `points_in_buffer`. Don't reinitialize them in the subclass, and don't add public mutable copies.
2. **Populate the private dicts inside `configure_*`.** Every implementation of `configure_ai_channel`, `configure_ao_channel`, `configure_di_line_channel`, `configure_do_line_channel`, `configure_di_port_channel`, `configure_do_port_channel` ends with `self._<dict>[channel.alias] = channel` after programming the device. `configure_ai_hw_timing` ends with `self._ai_hw_timing_config = hw_timing_config`. The default `DAQDriverBase.define_relay_channel` already records on `self._relay_channels`; overrides must too.
3. **Read driver-owned state via the private `self._<dict>`.** Inside `read_analog`, `fetch_analog`, `start`, `write_analog_value`, etc., use `self._ai_channels`, `self._ai_hw_timing_config`, and so on — never the read-only `@property`, which allocates a fresh snapshot per access. End users (and `InstroDAQ`) see the same state through the read-only accessors.
4. **No `InstroDAQ` reach-back.** Driver modules must not import `InstroDAQ`. There is no back-channel.

See `instro/daq/drivers/keysight_34980a.py` for the reference shape. Tests for `InstroDAQ` use the `_make_mock_driver()` helper in `tests/daq/test_daq_drivers.py`: it returns a minimal concrete `DAQDriverBase` subclass whose `configure_*` record real frozen channels on the private dicts (so the read-only snapshot path behaves like a real driver) and whose action methods are `Mock`s for call assertions.

## Codebase landmarks

| Need | File |
|---|---|
| Driver shape | `instro/psu/drivers/bk_9115.py` |
| Category HAL | `instro/psu/psu.py` |
| Transport driver | `instro/lib/transports/visa.py` |
| Test pattern (mocked transport) | `tests/psu/test_psu_drivers.py` |
| Public API usage | `examples/<category>/`: runnable scripts showing what a user's code looks like |
| Workspace vendor package | `packages/instro-daq-ni/` |
| Community-driver layout | `packages/instro-contrib/instro/contrib/` |

## Repo skills and subagents

This repo ships reusable AI tooling, maintained in parallel for **Claude Code** and **OpenAI Codex CLI**. The two formats are kept in sync: edit both when you change one.

| Tool | Skill (procedure) | Subagent (delegated worker) |
|---|---|---|
| Claude Code | `.claude/skills/<name>/SKILL.md` | `.claude/agents/<name>.md` (markdown + YAML frontmatter; `tools:` allowlist) |
| Codex CLI | `.agents/skills/<name>/SKILL.md` | `.codex/agents/<name>.toml` (TOML; `developer_instructions`, `sandbox_mode`) |

`SKILL.md` is the same format for both (YAML frontmatter `name` + `description`, markdown body) — only the directory differs. Subagents differ by format: Claude uses markdown with a `tools:` allowlist; Codex uses TOML and gates capability via `sandbox_mode` (`read-only` for read/fetch-only workers).

**Skill = the procedure that runs inline; subagent = an isolated worker invoked for noisy/heavy subtasks.** A skill may delegate to a subagent.

Available now:

| Name | Kind | What it does |
|---|---|---|
| `add-instrument-driver` | skill | Scaffold a new vendor driver from a programming manual/API, wired in per this file's conventions (driver module, registration, mocked tests, doc updates). On completion, offers to hand off to `validate-driver-hardware`. |
| `manual-spec-extractor` | subagent | Read-only worker that extracts a structured, wire-level command spec (per-method SCPI commands, error-query semantics, channel model) from a manual/PDF/URL so the heavy document tokens stay out of the main conversation. `add-instrument-driver` Step 1 delegates to it. |
| `validate-driver-hardware` | skill | Write a standalone, self-contained (no publishers) hardware-validation script that exercises every method an authored driver implements, run it against the connected device, and iterate on the driver to fix real bugs the hardware surfaces. Lands a `@pytest.mark.hardware` test under `tests/<category>/<vendor>/`. |
| `hardware-test-runner` | subagent | Run/read-only worker that executes the validation script against the instrument and returns a structured triage (per-step pass/fail, trimmed errors, driver-bug vs script-config vs hardware-wiring hypotheses) so noisy transport I/O stays out of the main conversation. `validate-driver-hardware` Step 4 delegates to it. |

When you add or change a skill/subagent, update **both** toolchains' copies and this table. New skills/subagents are a "Contributor workflow, repo convention, or tooling change" in the [Documentation](#documentation) table.

## Per-directory agent docs

Some subdirectories have their own `AGENTS.md` with narrower instructions (e.g. `docs/guides/AGENTS.md` for documentation-site work). When working inside one of those directories, that file's guidance takes precedence over this one.
