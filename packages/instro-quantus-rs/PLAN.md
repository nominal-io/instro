# Quantus — Mecalc QuantusSeries Rust Client, Simulator, and Python Bindings

> Development moved into the instro repo (branch quantus-device-draft):
> packages/instro-quantus-rs (client crate), crates/quantus-sim (simulator),
> packages/instro-quantus (maturin package). Paths below reflect the original
> quantus-repo layout.

Standalone repo for integrating Mecalc QuantusSeries DAQ mainframes (MicroQ, DecaQ,
PQ-series) with Nominal tooling. Lives next to `instro` but is independent of it:
the deliverables here are a reusable Rust protocol crate, a config-driven device
simulator, and a Python wheel. The instro-facing `QuantusDevice` integration is a
thin downstream consumer that lands in the instro repo in a later phase.

Status: Phases 0–4 essentially complete (ALO template pending). Phase 1: sim
REST plane (vendor ReadItemList/ConfigureICS42 pass against it). Phase 2:
client config schema + declarative reconcile engine (async core + blocking
facade) — rate snapping (customer's 100 Sa/s → 512 Sa/s case), THM427
pair-mode constraints, StatusCode 4/14 epoch reporting. Phase 3: streaming
both sides — sim paced generator (independent encoder, 45% buffer-discard
model, suspend/resume, fault injection) and client stream engine (reader
thread, epoch/gap tracking, health telemetry); vendor StreamData.py parsed
500 sim packets clean; ChannelDataSize excludes the type-specific header.
Phase 4: tacho event channels (ICT42S6, rpm-driven), CAN (CAN42S2 template,
frame playback → timestamped CanFrame events, message-list/transmit endpoints
with Participate gating; blocks emitted last-in-packet to dodge the vendor
parser's CAN index bug), Raw 24-bit end-to-end, D12 writes (action-plane:
auto-zero/bridge-balance; settings-plane: `write_settings` with epoch-impact
reporting), and the sustained-rate benchmark: 24 ch × 131 kSa/s for 3 s,
99.4% of nominal, zero gaps. Phase 4 complete incl. ALO42S4 template + settings-plane
output writes. Phase 5a complete: `quantus-py` wheel (PyO3/abi3, numpy arrays)
builds via maturin and passes an end-to-end smoke test against the sim
(reconcile + all stream event types + write_settings from Python). Phase 5b
drafted: `QuantusDevice(Instrument)` lives on the instro repo's local
`quantus-device-draft` branch (packages/instro-quantus + tests, 6 passing) —
not in the uv workspace, no PR; blocked on publishing the quantus wheel.
Config canonical format: JSON (D13). Proceeding on documented educated guesses
— see [docs/assumptions.md](./docs/assumptions.md); protocol reference incl.
canonical QProtocolCSharp enum IDs in [docs/api-notes.md](./docs/api-notes.md)
§9. Next: wheel distribution decision, then Phase 6 (hardware validation).

---

## 1. Why this exists

A customer runs MicroQ and DecaQ chassis with most of the Quantus input families —
ICP/voltage (ICP42x), microphones (MIC42X), Wheatstone bridges (WSB42X),
thermocouples (THM42), tachometers (ICT42S), and CAN FD (CAN42S) — at mixed rates
(mics at 65,536 Sa/s, slow channels nominally 100 Sa/s) and wants the data flowing
into Nominal without intermediate file conversions.

Quantus hardware exposes **QServer** (Q2.x): a REST/JSON configuration API on port
8080 plus a proprietary little-endian binary data stream on a TCP port. There is no
SCPI/VISA, no vendor Python SDK (only MIT-licensed examples), and no vendor
simulator.

### Why not an instro `InstroDAQ` driver

Evaluated and rejected. Quantus's model conflicts with the `DAQDriverBase` contract
in three ways: settings are a two-phase commit (cache then system-wide apply) while
`configure_*` is program-immediately; sample rates are master-rate × per-module
power-of-two divisors, not the single arbitrary-Hz rate the HAL expresses; and the
channel model (operation modes, excitation, bridge config, TC types, paired-channel
constraints) is far richer than instro's `AnalogChannel`. Tacho event streams and
CAN frames don't map to the DAQ HAL at all.

### The chosen shape

A **config-driven device** (the pattern instro uses for Modbus and EtherNet/IP):
declare the full rack setup in one config, reconcile+apply at open, then stream
everything enabled. Quantus's declarative settings tree maps onto this naturally,
and mixed rates / tacho / CAN just become published channels.

### Why Rust, and why the full client in Rust

- A Rust reader thread drains the stream socket outside the Python GIL, making the
  server's discard-above-45%-buffer failure mode structurally impossible for the
  Python consumer, and makes Raw fixed-point parsing (incl. 24-bit) cheap.
- The crate is intended for **reuse beyond instro** (other Nominal consumers), so
  the REST/config plane — including the trickiest logic: two-phase commit, enum-ID
  discovery, epoch-restart semantics — is implemented once in the crate, not
  re-implemented per consumer.
- Precedent and toolchain already exist in instro (`instro-ethernetip-rs` +
  maturin/PyO3 wheel).

---

## 2. Architecture

```
┌────────────────────────────────────────────────────────────┐
│ quantus repo (this repo, Cargo workspace)                  │
│                                                            │
│  crates/quantus-client     reusable protocol crate         │
│    - discovery (mDNS), REST client, settings engine        │
│    - stream engine (reader thread, ring buffer, epochs)    │
│    - async core (tokio/reqwest) + blocking facade          │
│                                                            │
│  crates/quantus-sim        config-driven device simulator  │
│    - serves REST plane + binary stream like a real device  │
│    - INDEPENDENT wire encoder (does not reuse client types)│
│    - fault injection                                       │
│                                                            │
│  crates/quantus-py         PyO3/maturin wheel ("quantus")  │
│    - thin binding over quantus-client's blocking facade    │
│    - numpy-ready arrays via rust-numpy                     │
└────────────────────────────────────────────────────────────┘
                        │ wheel dependency
                        ▼
┌────────────────────────────────────────────────────────────┐
│ instro repo (later phase, separate PR there)               │
│  packages/instro-quantus: QuantusDevice(Instrument)        │
│    - Measurement packaging, publishers, cantools DBC decode│
│    - imperative runtime methods (zero, balance, CAN tx,    │
│      suspend/resume, reconfigure)                          │
└────────────────────────────────────────────────────────────┘
```

### Settled design decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | Config-driven device, not imperative channel-by-channel API | Matches QServer's declarative settings tree and the industry norm (PAK/Dewesoft-style setup files) |
| D2 | Full protocol client in Rust; Python layer is instro-glue only | Crate reuse; two-phase-commit logic written once |
| D3 | Config schema owned by the crate (serde); consumers pass JSON/YAML through | Prevents Pydantic/serde drift; JSON Schema generated from serde types for editor/docs ergonomics |
| D4 | Generic settings core (`serde_json::Value` read-modify-write, enum resolution by description string), typed helpers per module family layered on top | Settings schemas are dynamic (depend on operation mode; enum IDs discovered at runtime); firmware changes settings between releases |
| D5 | Async core with a blocking facade | Future Rust consumers likely tokio services; PyO3 wraps the blocking facade |
| D6 | DBC/CAN decode stays ABOVE the crate boundary | Crate delivers timestamped raw frames; instro uses `cantools`, Rust consumers can use `can-dbc`. Revisit as an optional cargo feature only if two consumers want identical decode |
| D7 | Sim's wire encoder is written independently from the client's parser | Shared serialization code would make spec-misreadings pass tests symmetrically |
| D8 | `open()` = full declarative reconciliation (read tree, write every declared setting, apply), idempotent, regardless of device state | Settings persistence across power cycles is undocumented; REST is unauthenticated so external clients can mutate state |
| D9 | Mid-session settings changes are an explicit `reconfigure(new_config)` that knowingly restarts the streaming epoch; runtime actions (auto-zero, bridge balance, CAN tx, suspend/resume, recording) are imperative methods | The hardware punishes incremental changes (measurement restart, sometimes device reboot); keep the mental model "immutable config + runtime actions" |
| D10 | Vocabulary: "streaming epoch," not Mecalc's "measurement" | Avoids collision with instro's `Measurement` class |
| D11 | Target QServer Q2.x only; assert via `GET /version` at open | Q1→Q2 renamed nearly every endpoint and changed stream headers (StatusCode 6 = version incompatibility) |
| D13 | Rack config canonical format is **JSON** (`RackConfig::from_json_str` / `from_path`), matching the instro `EtherNetIPDevice` precedent and the dict the Python layer passes through. TOML remains supported (`from_toml_str`, `.toml` via `from_path`) for hand-edited rack files and sim-config parity | The schema is serde-defined and format-agnostic; declaring JSON canonical keeps `QuantusDevice(config=...)` consistent with its instro siblings without giving up TOML ergonomics for humans |
| D12 | Writes are two distinct classes with different APIs. **Action-plane writes** (CAN transmit, bridge balance, auto-zero, suspend/resume, recording control) hit dedicated endpoints, need no system apply, and are safe at runtime — plain imperative methods. **Settings-plane writes** (ALO output mode/amplitude/frequency, and any other output realized as a setting) go through the settings engine as a targeted PUT + apply; the crate exposes them as an explicit `write_settings`-style call that reports epoch impact (StatusCode 4) to the caller | On this hardware "writing an output" is often a settings mutation with system-wide apply semantics — hiding that behind a `write_analog(value)`-style API would silently risk epoch restarts. Quantus's built-in ramping (`Signal Amplitude/Frequency Change Time`) is the vendor-sanctioned way to get smooth output changes from infrequent writes; expose it in the config/API rather than emulating ramps with rapid writes |

---

## 3. Repo layout (target)

```
quantus/
├── PLAN.md                      # this file
├── Cargo.toml                   # workspace
├── crates/
│   ├── quantus-client/
│   ├── quantus-sim/             # lib + bin (quantus-sim binary)
│   └── quantus-py/              # maturin package, module name `quantus`
├── docs/
│   └── api-notes.md             # extracted QServer API reference
├── fixtures/
│   ├── golden/                  # byte dumps + settings JSON captured from real hardware
│   └── sim/                     # sim rack configs used by tests
└── tests/                       # cross-crate integration tests (client ↔ sim)
```

---

## 4. Phases

Sim and client grow together: each client capability lands with the sim behavior
that tests it. The sim's REST plane comes first because it forces the item-tree and
settings modeling before any client code exists.

### Phase 0 — Scaffolding and spec fixtures (~2–3 days)
- Cargo workspace, CI (fmt/clippy/test), crate skeletons.
- Encode the wire-format tables from docs/api-notes.md as Rust test fixtures
  (hand-built golden packets per channel type).
- Send the customer the capture request (see §6) so real fixtures arrive before
  Phase 3 ends.

### Phase 1 — Simulator REST plane (~1 week)
- Rack-description config (TOML): chassis, slots → module types, per-channel modes
  and signal definitions (sine/ramp/noise, RPM profile, CAN playback).
- Item-tree generation with correct ItemType ids; `/item/list`, `/system/settings`,
  `/item/settings` GET/PUT with cache-don't-apply + `SettingsApplied` flags,
  `/item/operationMode` with mode-dependent settings schemas,
  `/system/settings/apply` → epoch restart + StatusCode 4/14, `/dataStream/setup`,
  `/info/ping`, `/version`. Port-80 boot-status endpoints with configurable boot delay.
- Exit criteria: Mecalc's own `ExamplesPython/ReadItemList.py` and
  `ConfigureICS42.py` run unmodified against the sim.

### Phase 2 — Client config + REST engine (~1 week)
- Config schema (serde) covering the customer's module families: ICP42x, MIC42X,
  WSB42X, THM427, ICT42S, CAN42S — plus ALO42S4 output declarations (mode,
  initial amplitude/frequency/offset, ramp times); MSR + per-module divisor with
  rate snapping and achievable-rate reporting.
- Settings engine: read-modify-write, enum resolution by description, operation-mode
  ordering (mode first, then mode-dependent settings), pairing constraints (THM427
  channel pairs), declarative reconcile + apply, StatusCode 4/14 handling.
- Blocking facade + error taxonomy (transport / config-rejected / version-mismatch).
- Tested entirely against the Phase 1 sim.

### Phase 3 — Streaming, analog Processed mode (~1–1.5 weeks)
- Sim: paced binary stream generator (independent encoder) — packet framing,
  per-module rates from MSR×divisor, analog f32 blocks with integrity fields,
  buffer-level model with real discard-above-45% behavior, single-client
  enforcement, suspend/resume.
- Client: stream engine — reader thread, ring buffer, epoch/sequence tracking
  (gap + restart detection), per-channel batch assembly with (t0, dt), stream-health
  telemetry (buffer level, gaps).
- Fault injection round 1: apply latency, mid-stream disconnect, slow-client
  discards, spontaneous epoch restart.
- Exit criteria: `StreamData.py` (vendor example) parses the sim's stream; client
  survives every fault-injection scenario; sustained-rate benchmark
  (DecaQ-worst-case ≈ 30 MB/s) with zero gaps.

### Phase 4 — Tacho, CAN, Raw mode, writes (~1–1.5 weeks)
- Tacho event channels (f64 edge timestamps), CAN frame delivery (raw, timestamped),
  Raw fixed-point parsing (16/24/32-bit + ScalingFactor).
- Write support per D12:
  - Action-plane: CAN transmit (message list CRUD + transmit/abort), auto-zero,
    bridge balance, suspend/resume, recording control — client methods + sim
    endpoint behavior.
  - Settings-plane: ALO output writes (targeted settings PUT + apply with epoch
    impact reported); ramp-time support. Sim models ALO items, honors amplitude/
    frequency changes in its generated output state, and simulates the
    epoch-restart-on-apply behavior (configurable, since the real behavior is
    UNKNOWN until Phase 6).
- Validate against golden fixtures from customer hardware (should exist by now).

### Phase 5 — Python wheel + instro integration (~1 week, instro PR separate)
- `quantus-py`: PyO3 binding over the blocking facade; rust-numpy arrays
  (per-channel `(t0, dt, f32[])`, tacho `f64[]`, CAN frame structs); config passed
  as JSON/YAML path or dict.
- In the **instro repo**: `packages/instro-quantus` with `QuantusDevice(Instrument)` —
  Measurement packaging (one per timebase cluster), publishers, cantools DBC decode
  (unknown-ID counter channel + raw-frame escape hatch), imperative methods
  (both write classes per D12, published as instro `Command`s), `reconfigure()`.
  Follows instro conventions (tracking issue, docs table, README device table).

### Phase 6 — Hardware validation (~3–5 days with device access)
- Run the instro `validate-driver-hardware` flow against a real MicroQ/DecaQ.
- Close the documented unknowns: apply latency, whether ALO/setting changes restart
  the epoch in practice, exact enum IDs per module/firmware, settings persistence
  across power cycle, WebSocket vs raw-TCP framing.
- Promote captured dumps into `fixtures/golden/` and re-verify the sim against them.

**Total: roughly 5–7 weeks** of focused work, phases 1–2 partially parallelizable
with 3–4 if two people are on it.

---

## 5. Testing strategy

1. **Golden fixtures** — real-hardware byte dumps and settings JSON are the ground
   truth both client and sim must match. Until they exist, hand-built packets from
   the manual's byte-layout tables.
2. **Vendor referee** — Mecalc's MIT-licensed `ExamplesPython` scripts run against
   the sim in CI as an independent third-party client (they were written by the
   vendor, so they encode the vendor's reading of the spec).
3. **Independent encode/decode** (D7) — sim encoder and client parser are separate
   implementations; a shared misreading can't pass both.
4. **Fault injection in CI** — every recovery path (gaps, restarts, disconnects,
   discards, boot delay) exercised deterministically via sim knobs.
5. **Sustained-rate benchmark** — worst-case DecaQ synthetic load; regression-gate
   on zero sequence gaps.

---

## 6. Open questions

### For the customer
- **100 Sa/s slow channels**: hardware minimum is MSR/256 (= 512 Sa/s at MSR
  131,072). Do they expect decimation to 100 Sa/s in our layer, or is native rate
  acceptable at ingestion?
- **DBC files**: available for their CAN buses? Any non-DBC traffic (J1939 PGNs,
  UDS/XCP request-response) that passive decode won't cover?
- **Capture session**: one-time `GET /system/settings` dump per chassis + a short
  raw stream capture (script provided by us) → becomes `fixtures/golden/`.
- **Hardware access** for Phase 6 (loaner, remote, or on-site).
- Firmware/QServer versions in their fleet (Q2.x assumed — verify).
- **Write/output usage**: do they run ALO42S4 (or other output) modules, and at
  what write cadence (set-and-hold stimulus vs. frequent updates)? Do they
  transmit on CAN or only listen? Answers determine how much of the D12
  settings-plane write path Phase 4 must harden.

### Technical (resolve during build)
- mDNS discovery in-crate vs. document-an-IP (Phase 2 decision; `mdns-sd` crate is
  low-cost).
- WebSocket stream framing (manual doesn't specify frame↔packet mapping; raw TCP is
  primary, WS optional later).
- Whether `quantus-sim` also ships as a published binary for demo/customer use.
- Local-storage/recording endpoints: expose in crate v1 or defer (no REST download
  endpoint exists; retrieval is GUI-only).

---

## 7. Risks

| Risk | Mitigation |
|---|---|
| Enum IDs only partially documented; wire-accurate values vary by module/firmware | Generic settings engine resolves enums by description at runtime (D4); golden captures pin exact IDs; sim invents internally-consistent IDs until then |
| Manual gaps: apply latency, restart side effects, persistence | Phase 6 hardware validation closes them; client treats every apply as a potential epoch restart |
| Unauthenticated REST — external clients (QAcquire UI) can mutate settings mid-session | Epoch-restart detection in stream engine; loud logging; optional settings re-verify on restart |
| Sim fidelity drift vs. real firmware | Vendor-example referee tests + golden fixtures re-run in CI |
| Single streaming client limit — sim/tests must never leak a second connection | Sim enforces the limit (as the device does), so violations fail in CI |
| Two-phase apply can reboot the device (MSR change with PTP on MicroQ) | Client surfaces apply as potentially-slow with device-reconnect handling; documented in crate API |

---

## 8. References

- QuantusSoftware manual (Q 2.4.15): https://github.com/Mecalc/QuantusSoftware —
  extracted reference in [docs/api-notes.md](./docs/api-notes.md)
- Vendor examples (MIT): https://github.com/Mecalc/ExamplesPython,
  https://github.com/Mecalc/QProtocolCSharp, https://github.com/Mecalc/QClientCSharp
- instro precedents: `packages/instro-ethernetip{,-rs}` (Rust core + PyO3 wheel),
  `instro/modbus` (config-driven device, sim server), `EtherNetIPDevice`
  (config-driven Instrument shape)
