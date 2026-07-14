# Golden fixtures (real hardware)

Ground-truth captures from real QuantusSeries hardware: settings JSON dumps and
raw stream byte captures, produced by `scripts/capture_quantus.py`. Both the
client parser and the sim encoder must match these.

Empty until the customer capture session (PLAN.md Phase 0 / section 6). Until
then, the hand-built packets in
`crates/quantus-client/tests/golden_packets.rs` are the working ground truth.
