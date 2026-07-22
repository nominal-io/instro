# Assumption ledger

Educated guesses baked into the simulator (and later the client), each with its
source and how to verify. When a real-hardware capture contradicts a row, fix
the code/template and move the row to **Resolved**.

Confidence: **high** = vendor-generated C# code (QProtocolCSharp) or manual JSON
example; **medium** = manual prose interpreted; **low** = invented, internally
consistent, wire-accuracy unknown.

## Open

| # | Assumption | Confidence | Source / verification |
|---|---|---|---|
| A1 | ItemIds are assigned sequentially depth-first starting at 1 (controller=1, SC=2, ...) | low | Real devices may number differently; harmless because clients must discover ids via `/item/list`. Verify against capture. |
| A2 | `GET /version` returns `{"Version": "Q2.4.15"}` — a string containing the QuantusSoftware release | medium | Field name confirmed (QProtocolCSharp `Version.cs`); the string *format* is unknown. Verify against capture. |
| A3 | PUT `/item/settings` success body = `{TypeCode:1, StatusCode:3 (Updated), Message}`; apply-with-pending = `StatusCode 4`; apply-with-nothing-pending = `StatusCode 1`; invalid id = HTTP 400 `StatusCode 5`; bad value = HTTP 400 `StatusCode 2` | medium | StatusCode enum values confirmed from QProtocolCSharp; which code each endpoint returns in each case is inferred from the client's accept-list (1/3/4 pass silently). Exact Message strings invented. |
| A4 | Every apply with pending changes restarts the measurement (sim always answers StatusCode 4) | low (deliberate simplification) | Real firmware restarts only for rate/enable changes. Phase 4 refines per-setting; Phase 6 verifies. |
| A5 | Switching an item's operation mode resets its settings document to the new mode's defaults | medium | Implied by mode-dependent settings schemas (ConfigureICS42.py re-GETs settings after mode change). Unknown whether the device preserves values when switching back. |
| A6 | `GET /system/settings` inlines full settings (OperationMode/Settings/Data) for every node in `Children`, recursively | medium | Manual's Q1 example shows a full tree; QProtocolCSharp types only ItemInfo+Children for child nodes. Extra fields are ignored by typed clients either way. |
| A7 | SC42 signal conditioner: ItemName "SC42", identifier 10070 ("SC42 G2" in ScType), empty settings, single Enabled mode | medium | Identifier from QProtocolCSharp ScType; its settings surface is unexplored. |
| A8 | MIC42X7 has 6 channels; WSB42X2 has 2 channels | low | Channel counts not in extracted sources. THM427=8 (manual) and ICS425=6 (vendor example) are confirmed. |
| A9 | MIC42X7 "Preamp Excitation" is a Disabled/Enabled enum; mic/ICP mode setting lists are approximated from the C# enum names | low | Verify against capture or deeper QProtocolCSharp extraction (clone in scratchpad). |
| A10 | WSB42X2 per-mode setting lists (which settings appear in which excitation mode) approximated; "Excitation Amplitude" is Float 0–10 V in voltage-excitation mode and a 4/8/12 mA enum in current modes | low | Enum ids confirmed; per-mode composition guessed from manual prose. |
| A11 | Deprecated `TCP`/`Websocket` blocks still present in `/dataStream/setup` on Q2.4.x | medium | Manual shows them with a deprecation note; current QProtocolCSharp no longer types them. Harmless either way. |
| A12 | Sim rack config uses controller models PQ20G2/PQ30G2/MicroQ/PQ45; a DecaQ rack is represented by its actual controller | medium | DECAQ is a chassis, not a ControllerType in QProtocolCSharp. Confirm with customer what `/item/list` reports on their DecaQ. |
| A13 | Numeric `Value` types accept any JSON number; ValidationLimits enforcement not yet implemented in the sim | — | Phase 2/4 work item, not a protocol assumption. |
| A14 | During `/dataStream/suspend`, the sequence number keeps advancing for discarded packets (resume shows a gap); analog `Level` header field approximated as \|max\|/FS | low | The manual says suspend "discards data" but not whether sequence advances. Verify against hardware; affects gap accounting during suspend. |
| A15 | Packet cadence: the device batches all channels' new samples into one packet on a fixed tick; sim uses 20 ms | low | Real packetization policy unknown; clients must not assume any cadence (ours doesn't — it walks PayloadSize). |
| A16 | ICT42S6 modeled as 6 tacho channels (identifier 16); its Scope/Icp channel items (18/20) not modeled. CAN42S2 = 2 channels | medium | CAN42S2 bus count matches "two CAN busses" in the manual; ICT42S6 channel structure (does one input expose tacho+scope+icp items?) unknown — verify via capture. |
| A17 | CAN wire details: DLC field = actual payload byte count (per vendor parser); frame timestamp = f64 seconds from epoch start; CAN blocks emitted last-in-packet (vendor StreamData.py over-advances its index after a CAN block) | medium | DLC + parse behavior from ExamplesPython; block ordering is our own defensive choice. |
| A18 | Raw mode: sim emits 24-bit fixed point (SampleType 2) with scaling = 10 V / 2^31; real firmware's per-module SampleType and full-scale choice unknown | low | Client decodes all three fixed-point types regardless; verify actual types via Raw-mode capture. |
| A19 | CAN bitrate enum values (9 arbitration entries 50 kHz–1 MHz, 6 fast entries 500 kHz–8 MHz) interpolated between documented endpoints | low | Id ranges from QProtocolCSharp; exact frequency list per Id unverified. |
| A20 | `/canfd/bus/status/list` and auto-zero / bridge-balance apply responses modeled as simple success bodies | low | Response shapes undocumented; sim returns plausible minimal bodies. |
| A21 | `/item/list` orders channels immediately after their parent module (adjacency). The client hard-errors when a channel's ItemName does not carry the preceding module's name, so a violation is loud, not silent | medium | Adjacency confirmed on a real MicroQ (Q2.4.11 capture, 2026-07-22), including built-in modules under the controller. Channel names are the bare module name ("WSB42X2") or module name + role suffix ("TAC221 Tacho", "XMC237 CAN FD"); the guard accepts both. Vendor C# builds the tree from `/system/settings` Children instead; consider switching if other firmware trips the guard. |
| A22 | A quiet stream socket is a normal state: no keepalive exists, so a silently dead device (power pulled, no TCP reset) is indistinguishable from idle and the reader waits forever | medium | PayloadTypes has only Data=0 (vendor PacketHeader.cs). Verify on hardware whether packets flow continuously; if not, consider TCP keepalive. |
| A23 | Tacho RPM = 60000 / (edge-interval-ms x pulses_per_rev); stream timestamp semantics when "Trigger On nth Edge" > 1 (per-trigger vs per-transition) unverified | low | Capture a tacho stream with nth-edge > 1 on hardware day. Prefer nth-edge = tooth count for multi-tooth wheels; `pulses_per_rev` covers the rest. |
| A24 | Cached-then-failed settings writes: after a PUT succeeds but apply fails, QServer applies the cached values on the NEXT apply, whoever triggers it | medium | Inferred from the two-phase-commit model; the client warns loudly on this path. Verify rollback options on hardware. |
| A25 | THM427 same-pass reconcile assumes a cached module-level pair-mode change immediately regenerates the children's settings documents (pre-apply), so mode-dependent channel settings resolve in the same pass | low | If hardware only regenerates at apply, split THM427 reconcile into two apply phases. |
| A26 | `auto_zero` applies whatever `/autoZero/settings` level is configured on the device; the client does not (yet) read or set the level, which may be Disabled | low | Read the level on hardware; expose `/autoZero/settings` read/write if the default is not usable. |
| A27 | Multi-role tacho modules (ICT42S6 Tacho=16 / Scope=18 / ICP=20 items per input) are addressed positionally; the real interleaving of those channel items in `/item/list` is unknown and the A21 guard will fail loudly if they break adjacency | low | QProtocolCSharp ships all three channel classes. Capture `/item/list` on a real ICT42S6 before configuring one; a per-channel `role` config key is the likely fix. |
| A28 | "Four Channel High Sample Rate" module modes rename the rate setting and disable channels 2/5; the client matches the rate setting by substring and rejects configs declaring channels 2/5 in such modes, but the true wire schema is unverified | low | Capture an ICS425 in high-rate mode. |
| A29 | Triggered channel specific headers: type 4 = 32 bytes (3x u64 + f32 + reserved), type 5 = 24 bytes (i32 + 5x f32); the client skips them by these sizes. Type 6 (TriggeredStats) appears in the manual but not QProtocolCSharp | medium | Sizes from vendor TriggeredDataChannelHeader/TriggeredScopeChannelHeader. Verify with a triggered-channel capture. |

## Resolved

| # | Was assumed | Resolution |
|---|---|---|
| R1 | Settings persistence across power cycles unknown | Manual ("The concept of a Measurement"): QServer boots with "the stored settings from a previous session" — settings **do persist**. Client must still reconcile at open (PLAN.md D8). |
| R2 | Enum ids guessed from manual listing order | Replaced with vendor-generated ids from QProtocolCSharp (ItemType/ModuleType/ChannelType tables, per-module Sample Rate id ranges, per-channel setting enums). See docs/api-notes.md §9. |
| R3 | `ItemType` assumed integer in `/item/list` | It is a human-readable string ("Controller", "SignalConditioner", "Module", "External Module", "Channel"); the integer is `ItemTypeIdentifier`. |
