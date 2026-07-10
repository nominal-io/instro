# instro-quantus

Mecalc QuantusSeries DAQ mainframe support for instro: a config-driven
`QuantusDevice` in the spirit of `EtherNetIPDevice`, backed by the native
`_quantus` module (PyO3 over `instro-quantus-rs`: REST configuration engine +
binary stream engine that drains the device socket outside the GIL).

Structure mirrors `instro-ethernetip`:

- `packages/instro-quantus-rs` — reusable pure-Rust client crate (root Cargo
  workspace member)
- `packages/instro-quantus` — this maturin package: `QuantusDevice` (Python) +
  the `_quantus` PyO3 bindings (standalone crate, own Cargo.lock)
- `crates/quantus-sim` — device simulator used by tests and
  `examples/quantus/` (root workspace member; `cargo run -p quantus-sim`)

```python
from instro.quantus import QuantusDevice

daq = QuantusDevice(
    config="rack.json",                   # or a dict; JSON canonical
    dbc={"vehicle_bus": "vehicle.dbc"},   # optional: decode CAN by alias
    publishers=[...],
)
daq.open()
report = daq.reconcile()   # writes settings, applies once, snaps rates
daq.start()                # stream -> Measurements on {name}.{alias}
daq.close()
```

Analog channels publish sampled batches; tacho channels publish RPM computed
from edge intervals; CAN channels publish DBC-decoded signals as
`{name}.{alias}.{signal}` (unknown ids counted on `{name}.{alias}.unknown_frames`).
Runnable examples against the simulator: `examples/quantus/`.

Design/protocol references live with the Rust crate:
`packages/instro-quantus-rs/{PLAN.md,docs/}`.
