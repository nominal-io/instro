# QuantusDevice examples

Runnable against the Quantus device **simulator** — no hardware needed. The
simulator is the `instro-quantus-sim` crate in this repo (`crates/instro-quantus-sim`); it
impersonates a MicroQ mainframe: same REST API, same binary stream.

## Setup

```sh
just install quantus     # or: uv sync --extra quantus (builds the native module)
```

Start a simulator with the example's rack description (needs the Rust
toolchain, same as `just test`):

```sh
cargo run -p instro-quantus-sim -- examples/quantus/sim_simple.toml
# or for the full example:
cargo run -p instro-quantus-sim -- examples/quantus/sim_full.toml
```

Then, in another terminal:

```sh
uv run python examples/quantus/simple_stream.py
uv run python examples/quantus/full_workflow.py
```

`full_workflow.py` publishes to Nominal Core: replace its `DATASET_RID`
placeholder with your dataset RID (authentication per the
[Nominal Python client docs](https://docs.nominal.io/core/sdk/python-client/authentication)).

## Examples

| Files | What it shows |
|---|---|
| `simple_stream.py` + `rack_simple.json` + `sim_simple.toml` | Minimum viable flow: rack config from a JSON file, open -> reconcile -> start, one analog channel printed by a 6-line publisher |
| `full_workflow.py` + `rack_full.json` + `sim_full.toml` + `vehicle.dbc` | Everything: autostart, name-from-config, Nominal Core + CSV publishers, rate snapping in the reconcile report, analog + tacho-RPM + CAN streaming with native DBC decoding (the `dbc` entry on the CAN channel in the rack file), runtime writes published as Commands (auto-zero, bridge balance, CAN transmit, settings-plane write with epoch restart), teardown |

The sim TOML files also demonstrate fault-injection knobs — add a `[faults]`
section (`drop_every_nth_packet`, `disconnect_after_packets`, `apply_delay_ms`)
to watch the device ride through gaps and restarts.
