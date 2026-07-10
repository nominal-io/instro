# QuantusDevice examples

Runnable against the Quantus device **simulator** — no hardware needed. The
simulator lives in the quantus repo (next to this one) and impersonates a
MicroQ mainframe: same REST API, same binary stream.

## Setup

1. Build the pieces (in the quantus repo):

   ```sh
   cd ../quantus
   cargo build -p quantus-sim
   uvx maturin build --release -m crates/quantus-py/Cargo.toml -o target/wheels
   ```

2. Start a simulator with the example's rack description:

   ```sh
   # from the quantus repo, for the simple example:
   ./target/debug/quantus-sim ../instro/examples/quantus/sim_simple.toml
   # or for the full example:
   ./target/debug/quantus-sim ../instro/examples/quantus/sim_full.toml
   ```

3. Run the example (from the instro repo, until the quantus wheel is published
   and `instro-quantus` joins the workspace):

   ```sh
   PYTHONPATH=packages/instro-quantus uv run --no-sync \
     --with ../quantus/target/wheels/quantus-0.1.0-cp39-abi3-win_amd64.whl \
     --with numpy --with cantools \
     python examples/quantus/simple_stream.py
   ```

## Examples

| Files | What it shows |
|---|---|
| `simple_stream.py` + `sim_simple.toml` | Minimum viable flow: dict config, open -> reconcile -> start, one analog channel printed by a 6-line publisher |
| `full_workflow.py` + `sim_full.toml` + `vehicle.dbc` | Everything: name-from-config, connection override, default tags, CSV + custom publishers, rate snapping in the reconcile report, analog + tacho-RPM + DBC-decoded CAN streaming, runtime writes (auto-zero, bridge balance, CAN transmit, settings-plane write with epoch restart), teardown |

The sim TOML files also demonstrate fault-injection knobs — add a `[faults]`
section (`drop_every_nth_packet`, `disconnect_after_packets`, `apply_delay_ms`)
to watch the device ride through gaps and restarts.
