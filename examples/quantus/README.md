# QuantusDevice examples

Runnable against the Quantus device **simulator** — no hardware needed. The
simulator is the `quantus-sim` crate in this repo (`crates/quantus-sim`); it
impersonates a MicroQ mainframe: same REST API, same binary stream.

## Setup

```sh
just install quantus     # or: uv sync --extra quantus (builds the native module)
```

Start a simulator with the example's rack description (needs the Rust
toolchain, same as `just test`):

```sh
cargo run -p quantus-sim -- examples/quantus/sim_simple.toml
# or for the full example:
cargo run -p quantus-sim -- examples/quantus/sim_full.toml
```

Then, in another terminal:

```sh
uv run python examples/quantus/simple_stream.py
uv run --with cantools python examples/quantus/full_workflow.py
```

(`cantools` ships via the `can` extra of instro-quantus; `--with cantools`
keeps the one-off run simple.)

## Examples

| Files | What it shows |
|---|---|
| `simple_stream.py` + `sim_simple.toml` | Minimum viable flow: dict config, open -> reconcile -> start, one analog channel printed by a 6-line publisher |
| `full_workflow.py` + `sim_full.toml` + `vehicle.dbc` | Everything: name-from-config, connection override, default tags, CSV + custom publishers, rate snapping in the reconcile report, analog + tacho-RPM + DBC-decoded CAN streaming, runtime writes (auto-zero, bridge balance, CAN transmit, settings-plane write with epoch restart), teardown |

The sim TOML files also demonstrate fault-injection knobs — add a `[faults]`
section (`drop_every_nth_packet`, `disconnect_after_packets`, `apply_delay_ms`)
to watch the device ride through gaps and restarts.
