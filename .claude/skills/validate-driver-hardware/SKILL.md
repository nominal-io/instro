---
name: validate-driver-hardware
description: >-
  Write and run a standalone hardware-validation script for an instro driver
  against the real device, then iterate on the driver until every supported
  method passes. Use after authoring a driver (the add-instrument-driver skill
  hands off here) or when asked to "validate <driver> on hardware", "smoke-test
  this driver against the real instrument", or similar. Produces a
  self-contained, runnable test script under tests/<category>/<vendor>/ and a
  triaged pass/fail report; self-corrects driver bugs found along the way.
---

# Validate a driver against real hardware

This skill takes an already-authored driver and proves it works against the
physical instrument. It writes a **standalone, runnable** validation script that
exercises *every* method the driver implements, runs it against the connected
device, triages the results, and **iterates on the driver** to fix real bugs the
hardware surfaces. It is the hardware counterpart to `add-instrument-driver`'s
mocked unit tests.

Borrow the *style* of the runnable example scripts in `examples/` — especially
`examples/modbus/labjack_t4_loopback_test.py` (per-check `OK`/`FAIL`,
accumulated failures, final summary, `try/finally` close).

## Step 1 — Confirm hardware availability and gather connection details

This skill is only useful with the physical device attached. **Ask the user
first**, and do not write or run anything until you have:

1. **Do you have the instrument connected right now?** If no, stop — offer to
   write the script anyway (with placeholder config) so they can run it later,
   but make clear nothing will be validated until hardware is present.
2. **Device identifier / connection string.** The exact value the transport
   needs: a VISA resource (`USB0::0x...::INSTR`, `TCPIP0::192.168.1.5::INSTR`,
   `ASRL3::INSTR`), a `host:port` for Modbus/TCP, a serial port, etc. Match the
   transport the driver actually uses.
3. **Vendor, model, and exact unit** (e.g. "Siglent SDS1104X-E"). Confirm it is
   in the family the driver targets.
4. **Channel count / channel map.** How many channels, and what is physically
   wired to each one.
5. **Stimulus and wiring.** What signal/load/DUT is present, on which channel,
   and its known properties (e.g. "LabJack T4 DAC0 → CH1, 100 Hz sine, ~0–2.5 V
   unipolar"). This is what makes value checks meaningful — capture it precisely,
   including limitations (e.g. a unipolar source never crosses 0 V, so a 0 V
   trigger won't fire). Ask for loopback wiring where the category needs it
   (DAQ analog/digital loopback, I2C target address, etc.).
6. **Safety constraints.** For sourcing instruments (PSU, eload, DAQ AO,
   relays), confirm safe limits before any output is enabled and what state to
   leave the device in.

Use `AskUserQuestion` when several of these are unknown — don't guess a VISA
resource or a wiring map.

## Step 2 — Enumerate the full driver surface to cover

The script must exercise **everything the driver implements** — that is the
point of hardware validation. Build the list from code, not memory:

- Read the category base `instro/<category>/<category>.py` (or the unstable
  package equivalent, e.g. `packages/instro-unstable/instro/unstable/scope/driver.py`)
  for the required + optional method set.
- Read the concrete driver module and list every method it actually overrides.
  Skip only the ones that raise `FeatureNotSupportedError`/`NotImplementedError`.
- Read the matching HAL (`InstroPSU`, `InstroDMM`, `InstroScope`, `InstroDAQ`,
  …) to learn the **public** call surface and the return types. Methods return
  `Measurement` / `Command` (see `instro/lib/types.py`); unwrap with `.latest`,
  `.values`, or `.channel_data[...]` exactly as the example scripts and the
  HAL's own signatures dictate. Don't invent accessors.

Group the surface into validation steps: connection/sync, per-channel setting
roundtrips (set → readback within tolerance), coupling/mode enums, queries
(sample rate, status), acquisition/read/fetch, built-in measurements, run/stop,
and file ops (screenshots, settings save/load). Cover each implemented method at
least once.

## Step 3 — Write the validation script

Place it where the category's hardware tests live; default to
`tests/<category>/<vendor>/test_<vendor>_<model>_hardware.py` (mirrors
`tests/scope/siglent/`). Requirements:

- **Mark it `@pytest.mark.hardware`** so `just test` deselects it (the repo sets
  `addopts = "-m 'not hardware'"`), AND make it runnable standalone via a
  `main()` and `if __name__ == "__main__": sys.exit(main())`. The user runs it
  with `uv run python <path>`.
- **Module-level config block** at the top — connection string, channel map,
  stimulus description, tolerances, and optional `EXPECTED_*` constants — each
  clearly marked to edit before running. Fill these from Step 1.
- **No publishers.** Construct the HAL with `publishers=None`; the script pushes
  data to no external backend.
- **Continue through all steps**, accumulating failures rather than aborting on
  the first — you want the full picture in one run (the loopback example does
  this). Print `OK`/`FAIL` per step and a final summary with a non-zero exit code
  if anything failed.
- **`try/finally`**: always `close()` the instrument and restore a safe state
  (outputs off, attenuation/level reset) in `finally`.
- **Two check tiers**, following the existing scope hardware test's philosophy:
  *structural sanity* (finite, positive, `max > min`, expected length) is always
  asserted; *strict value* checks (measured frequency ≈ expected) run only when
  the corresponding `EXPECTED_*` constant is set, since they depend on the
  stimulus.

Skeleton to adapt (category-agnostic; fill in the real driver, HAL, and steps):

```python
"""Hardware validation for <Vendor> <Model> via <InstroHAL>. Self-contained; no publishers.

Wiring / stimulus:
    <describe what is connected to each channel and its known properties>

Run:
    uv run python tests/<category>/<vendor>/test_<vendor>_<model>_hardware.py
"""

import sys
import pytest

from instro.<category> import <InstroHAL>
from instro.<category>.drivers import <DriverClass>  # or unstable path

RESOURCE = "<visa-resource-or-host>"   # <-- edit before running
SIGNAL_CHANNEL = 1
# EXPECTED_FREQUENCY_HZ = None  # set to enable the strict value check


def _make_hal() -> <InstroHAL>:
    hal = <InstroHAL>(name="hw_validate", driver=<DriverClass>(RESOURCE),
                      num_channels=<n>, publishers=None)
    hal.open()
    return hal


def _run(name, fn, failures):
    try:
        fn()
        print(f"  [OK]   {name}")
    except Exception as exc:  # noqa: BLE001 - report, don't abort
        print(f"  [FAIL] {name}: {exc}")
        failures.append((name, exc))


def run_all() -> list:
    hal = _make_hal()
    failures: list = []
    try:
        # one _run(...) per implemented driver method / capability
        ...
    finally:
        # restore safe state, then close
        hal.close()
    return failures


@pytest.mark.hardware
def test_<model>_hardware():
    failures = run_all()
    assert not failures, f"{len(failures)} hardware check(s) failed: {failures}"


def main() -> int:
    failures = run_all()
    print(f"\n{'PASSED' if not failures else f'FAILED ({len(failures)})'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
```

## Step 4 — Run it against the hardware

Delegate execution to the **`hardware-test-runner` subagent**
(`.claude/agents/hardware-test-runner.md`). The run is noisy — verbose VISA/
transport I/O and long tracebacks — and the subagent keeps that out of this
conversation, returning a structured triage: overall pass/fail, per-step status,
trimmed error excerpts, and a hypothesis classifying each failure as
**driver-bug**, **script/config**, or **hardware/wiring**.

Give it the script path and the exact run command
(`uv run python <path>`), plus the Step 1 stimulus notes so it can tell a real
driver bug from "no signal on CH1".

If hardware tests are quick and quiet for this device, you may run the script
inline instead — use judgment.

## Step 5 — Triage and self-correct

For each failure, classify before touching code (this is the same discipline as
diagnosing a real bug — wrong command/format/parse vs. wrong test config vs. no
signal):

- **Driver bug** — wrong wire command, bad value formatting (e.g. sending
  `10.0` where the device wants `10`), mis-parsed response, buffer desync on a
  binary read. **Fix the driver, and only the driver.** Confine every edit to the
  concrete driver module and its accompanying tests (`tests/<category>/...`).
  **Never edit shared/core library code** — `instro/lib/` (transports like
  `VisaDriver`, `instro/lib/types.py`), the category base class
  (`<Category>DriverBase`), or the HAL. Those are out of scope for a single-driver
  validation. If a fix looks like it needs a transport capability the public API
  doesn't expose (e.g. a length-aware binary read, or disabling the read
  terminator for a raw block), **use the VISA escape hatch** — reach the
  underlying pyvisa resource from inside the driver via `self._visa._inst` while
  holding `self._visa.lock()`, restoring any attribute you change (e.g.
  `read_termination`) in a `finally`. If the cause genuinely cannot be fixed
  without changing core lib, **stop and flag it for the user**; do not edit lib
  yourself. After fixing the driver, update the mocked unit tests if the wire
  command changed, and re-run `just check` and `just test` to confirm no
  regression.
- **Script/config** — wrong channel, timebase showing too few cycles, a trigger
  level the signal never crosses, tolerance too tight. **Fix the script**, not
  the driver.
- **Hardware/wiring** — no stimulus, wrong probe attenuation, unipolar source vs
  bipolar expectation. **Report to the user**; don't paper over it in code.

Re-run via the subagent after each change. Loop until the script passes or the
only remaining failures are genuine hardware/wiring issues the user must resolve.
Make the smallest change that fixes the cause; don't refactor the driver beyond
the bug.

## Step 6 — Wrap up

- Ensure `just check` and `just test` still pass (mocked tests green, types and
  lint clean) — the hardware test is deselected there by design.
- Summarize for the user: which methods were validated, what was fixed in the
  driver (with the wire-level before/after), any steps that depend on stimulus
  the user should tune via `EXPECTED_*`, and any unresolved hardware/wiring
  issues.
- The script ships in the same branch/PR as the driver. It is a `hardware`-marked
  test, so it won't run in CI — note that in the PR description and that the
  driver was confirmed against a real unit.

## Anti-patterns to refuse

- **No data-publishing backend in the validation script** — no publisher,
  client, or dataset identifiers. If the user wants streaming/telemetry, that is
  a separate example, not this script.
- **Don't fix the driver to match a broken test.** If the test config or wiring
  is wrong, fix that instead — the driver's wire behavior must stay correct.
- **Don't edit shared/core library code.** A driver fix must land only in the
  driver module and its tests — never in `instro/lib/` (incl. `VisaDriver` and
  other transports), `<Category>DriverBase`, or the HAL. Need a capability the
  transport API lacks? Use the VISA escape hatch (`self._visa._inst` under
  `self._visa.lock()`) from within the driver, or flag the gap to the user — do
  not modify lib.
- **Don't silently skip methods.** If a method can't be validated with the
  available stimulus, print it as `SKIPPED` with the reason, not omit it.
- **Don't leave the instrument in an unsafe state.** Restore outputs/levels in
  `finally`.
- **Don't invent expected values.** Strict value checks come from the user's
  stated stimulus; otherwise assert structure only.
