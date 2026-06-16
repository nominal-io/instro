---
name: add-instrument-driver
description: Scaffold a new instrument driver for a vendor/model from its programming reference (SCPI/programming manual/API SDK as a PDF, doc, HTML file, or website URL) and wire it into the repo per AGENTS.md conventions. Use when asked to "add a driver for <vendor> <model>", "write a driver from this manual/datasheet", or similar. Produces the driver module, registration, mocked-transport tests, and doc updates on a tracking branch.
---

# Add an instrument driver from a manual/API

This skill turns a vendor programming reference into a correct, tested,
registered driver PR. It follows the repo's existing conventions exactly — read
`AGENTS.md` ("How to add a vendor driver", "Patterns and constraints",
"DAQ driver state tracking") before deviating. **When in doubt, duplicate
explicit code rather than abstract.** This repo deliberately prefers duplication
over shared mixins/factories.

## Prerequisites — gather before writing code

1. **The programming reference.** A path to a PDF/doc/HTML file, or a URL. If the
   user hasn't provided one, ask for it — do not guess SCPI commands or API code 
   from model knowledge.
2. **Vendor and model** (e.g. "Siglent SPD3303"). Confirm the exact model family
   the driver should cover; SCPI surfaces and APIs are often shared across a series.
3. **Category.** One of `psu`, `dmm`, `eload`, `daq`, `i2c`, `modbus`, `scope`, `ethernetip`. 
   Infer from the instrument type; confirm with the user if ambiguous.
4. **Tracking issue + branch.** Per `AGENTS.md`, no untracked work. Confirm an
   issue exists (or create one) and branch off `main` named after it
   (e.g. `issue-142-siglent-spd-driver`).
5. **Core vs. contrib.** Default to **core** (`instro/<category>/drivers/`) if
   the user is a maintainer, otherwise use **contrib**
   (`packages/instro-contrib/instro/contrib/<category>/drivers/`). Some instruments 
   only exist in **unstable** (`packages/instro-unstable/`). See `CONTRIBUTING.md` for the bar. 
   Ask if unclear.

## Step 1 — Extract a structured command spec from the manual

Manuals are large and noisy. **Delegate parsing to the `manual-spec-extractor`
subagent** (`.claude/agents/manual-spec-extractor.md`) so the heavy document
tokens stay out of this conversation. Give it:

- the manual location (path or URL),
- the target category and its required methods (read them from the category base
  class — see Step 3),
- the vendor/model.

It returns a structured spec: per-method SCPI command(s), the error-query form
and OK-prefix, the channel model, value formatting (decimal places, units), and
which optional category methods the instrument supports. Review the spec against
the manual yourself for anything safety- or wire-critical before coding.

If the manual is small or already pasted into the conversation, you may extract
inline instead of spawning the agent — use judgment.

## Step 2 — Read the reference driver for the category

The canonical shape is `instro/psu/drivers/bk_9115.py`. For other categories,
read an existing driver in `instro/<category>/drivers/` first — the method set
and transport differ. Match the surrounding code's idiom, naming, and comment
density.

**Let the category you're adding to decide layout and conventions — the paths
below are the core-category default, not universal.** Categories in
`instro-unstable` (e.g. `scope`) diverge, and a real driver in the category is
the source of truth. Things that vary, observed in `scope`:

- **Directory layout.** `scope` nests drivers under a vendor subdir
  (`drivers/<vendor>/<vendor>_<model>.py` + a `drivers/<vendor>/__init__.py`),
  not the flat `drivers/<vendor>_<model>.py`.
- **Docstring style.** A category's base class may carry multi-paragraph
  docstrings (`ScopeDriverBase` does) even though the one-line rule holds
  elsewhere. Match the *concrete* sibling driver's idiom (Keysight/Tektronix
  scope drivers use one-liners), not the base.
- **Unsupported-feature exception.** Core PSU/DMM raise
  `FeatureNotSupportedError`; `scope` drivers raise plain `NotImplementedError`.
  Use whatever the category's reference driver uses.

## Step 3 — Read the category base class

`instro/<category>/<category>.py` defines `<Category>DriverBase`. Note:

- **Required methods** are `@abc.abstractmethod` — you MUST implement all of them.
- **Optional methods** raise `NotImplementedError`/`FeatureNotSupportedError` by
  default. Override only the ones the instrument actually supports (per the
  Step 1 spec). For unsupported optional methods, follow the reference driver's
  pattern (e.g. BK9115 raises `FeatureNotSupportedError`).

## Step 4 — Write the driver module

Create the driver at the path the category actually uses (see Step 2): core
categories use `instro/<category>/drivers/<vendor>_<model>.py`; vendor-subdir
categories like `scope` use
`packages/instro-unstable/instro/unstable/<category>/drivers/<vendor>/<vendor>_<model>.py`.
File name is the snake_case form (e.g. `siglent_spd3303.py`,
`siglent_sds1000x_e.py`). Class name is `<Vendor><Model>` (e.g. `SiglentSPD3303`,
`SiglentSDS1000XE`).

Rules (from `AGENTS.md` "Patterns and constraints"):

- **Subclass the category base.** `class SiglentSPD3303(PSUDriverBase):`
- **Compose, don't subclass, the transport.** For SCPI/VISA instruments hold a
  `VisaDriver` in `__init__` and accept `str | VisaConfig`:
  `def __init__(self, visa_resource: str | VisaConfig) -> None:` then
  `self._visa = VisaDriver(visa_resource)`. Keep the union — `VisaConfig` is the
  canonical customization vehicle. Drivers on other transports (LabJack handle,
  Aardvark module) take whatever their transport needs.
- **Implement `open`/`close`** to delegate to the transport.
- **Per-driver error helpers, only if the device supports `SYST:ERR?`.** Add
  driver-local `_write_checked` / `_check_errors` / `_query_checked_float` like
  BK9115. **Do NOT extract these to a shared mixin.** The OK-prefix
  (`"0"` for B&K/Rigol, `"+0"` for Siglent), command form (`SYST:ERR?` vs
  `:SYST:ERR?`), and the vendor name in the raised message are all per-device —
  take them from the Step 1 spec.
- **Stateful sequences inline the lock.** If a command needs channel-select
  before write (e.g. `INST <n>` then write), hold `self._visa.lock()` across the
  whole sequence rather than using `_write_checked` — see `bk_9140.py`.
- **Type hints on all public methods.** `mypy` is enforced.
- **One-line docstrings max.** No multi-paragraph docstrings, no comments that
  restate the code.

### DAQ drivers — extra rules

If the category is `daq`, follow "DAQ driver state tracking" in `AGENTS.md`
exactly (reference: `instro/daq/drivers/keysight_34980a.py`):

- Call `super().__init__()` at the top of `__init__` (the base initializes the
  private channel/timing dicts and `points_in_buffer`).
- Record channels on the **private** dicts inside `configure_*`
  (`self._ai_channels[channel.alias] = channel`, etc.) — after programming the
  device.
- Read driver-owned state via the private `self._<dict>`, never the read-only
  `@property` (which allocates a fresh snapshot per access).
- No `InstroDAQ` reach-back / imports.

## Step 5 — Register the driver

Registration follows the category's existing pattern — check how a sibling
driver is wired before assuming:

- **Core categories:** edit `instro/<category>/drivers/__init__.py`, adding both
  the import and the `__all__` entry.
- **Vendor-subdir categories (e.g. `scope`):** register in the vendor
  subpackage's `drivers/<vendor>/__init__.py` (import + `__all__`). The top-level
  `drivers/__init__.py` may be intentionally empty and drivers are imported
  directly from the vendor subpackage (`from ...scope.drivers.siglent import
  SiglentSDS1000XE`) — don't add a top-level re-export if siblings don't have one.
- **Contrib drivers:** register in the corresponding contrib
  `drivers/__init__.py` (the smoke test picks it up automatically).

## Step 6 — Write tests

Put tests where the category's existing driver tests live — don't assume the
path. Core categories use `tests/<category>/test_<category>_drivers.py`; some
categories keep driver tests in a single top-level file (e.g. `scope` →
`tests/test_instro_scope.py`). Grep for an existing driver class name in `tests/`
to find the right file. The canonical pattern is in
`tests/psu/test_psu_drivers.py`:

- Patch the driver's `VisaDriver` reference with `autospec=True`:
  `patch("instro.<category>.drivers.<vendor>_<model>.VisaDriver", autospec=True)`.
- Provide fixtures for the patched class and instance; default
  `visa.query.return_value` to a no-error response (e.g. `'0,"No error"'` or
  `'+0,"No error"'` matching the device's OK-prefix).
- Assert **wire-level commands**: `visa.write.assert_called_once_with("VOLT 5.000")`,
  and that the error query fires (`visa.query.assert_called_once_with("SYST:ERR?")`).
- Use `side_effect` lists for query+error-check sequences.
- Cover: init builds the transport from both `str` and `VisaConfig`; open/close
  delegate; each implemented method's wire command; response parsing;
  `_check_errors` raises on a non-OK prefix.

## Step 7 — Update docs (same PR)

Per `AGENTS.md` "Documentation":

- **`README.md`** — add the model to the **"Supported devices"** table for core
  categories. Categories in `instro-unstable` (e.g. `scope`) are listed under
  **"Experimental modules"** instead — update that prose, not the table.
- **`docs/guides/instrumentation/<category>.mdx`** — add a guide entry only if
  the device introduces a new user-facing workflow.
- If a new public API/category was introduced (rare for a single driver), update
  `docs/reference/src/` and `docs/guides/docs.json` navigation too.
- Do **not** hand-edit `CHANGELOG.md` (release-please generates it).

## Step 8 — Verify

Run the repo gates and fix anything they flag:

```bash
just check    # ruff format, mypy, ruff lint
just test     # mocked unit tests, no hardware
```

If both pass, CI will pass. Then summarize for the user: the new driver, what
optional capabilities it supports/omits (and why).

## Step 9 — Offer hardware validation

Mocked unit tests prove the wire commands are *shaped* right; they cannot prove
the driver works against the real instrument. Once the driver is written and the
mocked gates pass, **ask the user whether they have the physical device on hand**:

> The driver passes its mocked unit tests. Do you have the actual `<vendor>
> <model>` connected right now? If so, I can write a standalone
> hardware-validation script that exercises every method against the real device
> and self-correct the driver from what it finds.

- **If yes:** gather the device identifier/connection string, exact model, vendor,
  channel count, and what's wired to each channel (stimulus/loopback), then invoke
  the **`validate-driver-hardware` skill** with those details. It writes a
  hardware validation script, runs it against the device, and iterates on
  the driver until every supported method passes.
- **If no:** stop here. Remind the user the wire-level behavior should still be
  confirmed against real hardware before merge, and that they can run the
  `validate-driver-hardware` skill later when the device is available.

## Anti-patterns to refuse

- No vendor-string factory (`Instrument.create(vendor=...)`). Construct drivers
  explicitly: `InstroPSU(name="x", driver=BK9115(...), num_channels=1)`.
- No shared error-handling/transport mixin or base helper.
- No driver-side facade or reference back to the category HAL.
- No `import`-time dependency on optional vendor SDKs (defer in `open()` if the
  transport needs it, like Aardvark).
- No guessing commands the manual doesn't show. If a required method's command is
  unknown, flag it for the user rather than inventing one.
- No edits to shared/core library code. Scope is the driver module, its
  registration, its tests, and docs — never `instro/lib/` (transports like
  `VisaDriver`, `instro/lib/types.py`), the category base class, or the HAL. If
  the driver seems to need a transport capability the public API doesn't expose,
  use the VISA escape hatch (`self._visa._inst` under `self._visa.lock()`) from
  within the driver, or flag the gap to the user — don't modify lib.
