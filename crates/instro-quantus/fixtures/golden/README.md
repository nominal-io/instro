# Golden fixtures (real hardware)

Ground-truth captures from real QuantusSeries hardware, produced by
`scripts/capture_quantus.py`. Both the client parser and the sim encoder must
match these. Serial numbers and IP addresses are scrubbed.

## microq_20260722

Customer MicroQ (QServer Q2.4.11), captured 2026-07-22: built-in XMC237
(GPS + 2x CAN FD) under the controller, SC10, WSB42X2 (ch1 ICP, ch2-4 voltage,
all streaming), TAC221 (both Tacho+Scope pairs streaming), two Empty slots.
MSR 131072 Hz, Processed format.

`stream.bin` holds 1284 complete packets (contiguous sequence numbers, one
truncated packet at the tail where the capture stopped): 1279 analog blocks
on each of channels 9-12 (WSB) and 15/17 (TAC221 Scope), 18 GPS blocks on
channel 4, no tacho or CAN blocks (nothing was driving the shaft or bus).
`tests/golden_stream.rs` locks the parser to these counts.
