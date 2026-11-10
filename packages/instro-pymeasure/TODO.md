# instro-pymeasure TODO

Deferred work for the pymeasure integration. Move items into GitHub issues once they're picked up.

## Planned

- [ ] **Raw SCPI traffic tap (opt-in).** Wrap `instrument.adapter.write`/`read` and publish string channels
  `<name>.scpi.tx` / `<name>.scpi.rx`. This covers traffic the property hook can't see: methods that call
  `self.write()` directly (`beep()`, `reset()`, most `apply_*`/`measure_*` helpers), `ask()`/`values()`
  from user code, and binary transfers (which would be published as a byte count, not their content).
  Proposed API: `PymeasureInstrument(..., raw_traffic=True)`. Decide whether raw channels go through the
  channel map.
- [ ] **Method calls as commands (opt-in).** Publish the call arguments of selected methods (e.g.
  `ramp_to_current(target)` → `<name>.ramp_to_current.target.cmd`), configured from the channel map.
  Today only the property access inside a method is published.
- [ ] **Traces and buffers.** Lists longer than 16 values and numpy arrays (`buffer_data`, scope waveforms)
  are skipped with a warning. Publish them as multi-sample `Measurement`s once there is a way to declare
  their timebase (per-entry `dt`/`x_increment` source in the channel map?).

## Open questions

- [ ] **Rejected sets.** With `check_set_errors=True`, pymeasure logs instrument errors but doesn't raise,
  so a set the instrument rejected is still published as a `.cmd`. Should we hook `check_set_errors()` and skip
  (or tag) those commands?
- [ ] **Publisher failures.** A publisher that raises propagates into the user's pymeasure call, after the
  instrument already acted (same as core instro). For `attach()` on existing code, logging the failure
  might be friendlier. Decide on one rule.
- [ ] **Units.** pymeasure has no machine-readable units on most properties. Should the channel map grow an
  optional `units` field that is sent to Nominal as channel metadata?
- [ ] **Value transforms in the map.** Allow per-entry `scale`/`offset` (reuse `instro.lib.types.LinearScale`)?
- [ ] **Map discovery without hardware for runtime channels.** `--resource` connects to the instrument. A fake
  adapter would work for drivers whose `__init__` doesn't query the instrument.

## Docs and release

- [ ] Render `examples/pymeasure/pymeasure_publishing.md` on the docs site. The docs build uses `myst-parser`,
  not `myst-nb`, and `_ext/examples.py` only picks up `examples/**/*.py`. Either add `myst-nb` (with
  execution off) or teach the extension to include MyST notebooks.
- [ ] Add `examples/pymeasure` to `CATEGORIES` in `docs/_ext/examples.py` once it has a rendered example.
- [ ] Validate against real hardware (Keysight E36312A, Keithley 2400) before the first release.
