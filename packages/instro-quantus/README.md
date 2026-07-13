# instro-quantus

Mecalc QuantusSeries DAQ mainframe support for instro: a config-driven
`QuantusDevice` in the spirit of `EtherNetIPDevice`, backed by the native
`_quantus` module (PyO3 over `instro-quantus-rs`: REST configuration engine +
binary stream engine that drains the device socket outside the GIL).

The API isn't settled yet, so it ships under the `instro.unstable` namespace
(alongside `instro-unstable`); it moves to `instro.quantus` when it stabilizes.

Structure mirrors `instro-ethernetip`:

- `packages/instro-quantus-rs` — reusable pure-Rust client crate (root Cargo
  workspace member)
- `packages/instro-quantus` — this maturin package: `QuantusDevice` (Python) +
  the `_quantus` PyO3 bindings (standalone crate, own Cargo.lock)
- `crates/quantus-sim` — device simulator used by tests and
  `examples/quantus/` (root workspace member; `cargo run -p quantus-sim`)

```python
from instro.unstable.quantus import QuantusDevice

daq = QuantusDevice(
    config="rack.json",  # or a dict; JSON canonical
    publishers=[...],
)
daq.open()
report = daq.reconcile()   # writes settings, applies once, snaps rates
daq.start()                # stream -> Measurements on {name}.{alias}
daq.close()
```

Analog channels publish sampled batches; tacho channels publish RPM computed
from edge intervals (multi-tooth wheel? prefer setting `"Trigger On nth Edge"`
to the tooth count so the device triggers once per revolution; otherwise
declare `pulses_per_rev` on the channel — if both are used, pulses_per_rev =
teeth / nth); CAN channels with a `dbc` entry in the rack config are decoded
natively (in Rust) and publish per-signal channels as
`{name}.{alias}.{signal}` (undecodable ids counted per batch on
`{name}.{alias}.unknown_frames`; a streaming CAN channel with no `dbc` only
gets that counter — raw capture needs the `QuantusClient`/`StreamReader` layer).
Runnable examples against the simulator: `examples/quantus/`.

Design/protocol references live with the Rust crate:
`packages/instro-quantus-rs/{PLAN.md,docs/}`.
