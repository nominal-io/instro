# Mecalc QuantusSeries (QServer) — API Notes

Extracted from the QuantusSoftware Manual, release Q 2.4.15
(https://github.com/Mecalc/QuantusSoftware), supplemented by the official Python
examples (https://github.com/Mecalc/ExamplesPython). Section names match the
manual's TOC. Items marked **UNKNOWN** are gaps to close against real hardware or
the C# QProtocol source.

Controllers covered: PQ20G2 (id 30080), PQ30G2 (30090), MicroQ (30100), PQ45
(30110), plus DECAQ chassis. Not SCPI — no `*IDN?`, no VISA. Identity comes from
`GET /item/list` / `GET /system/settings` (`ItemName`, `Info` serial entries) and
`GET /version`.

---

## 1. Communication model

Three planes, all over Ethernet/Wi-Fi:

| Plane | Protocol | Port | Notes |
|---|---|---|---|
| Configuration | REST, HTTP + JSON | 8080 | `http://<ip>:8080/info/ping` etc. |
| Data stream | Proprietary binary over raw TCP, or WebSocket carrying the same frames | From `GET /dataStream/setup` (`TCPPort` typically 8085, `WebSocketPort` 8090) | "The data stream cannot be REST" |
| Boot/status + web UI | HTTP | 80 | `/SystemStatus` (boot %), `/Version`, `/SystemTemperature`, `/SystemSynchronisation` available while 8080 still initialising |

- **Discovery:** mDNS `_http._tcp` on port 80, `prodIdent=Quantus`, device name
  `Quantus_<serial>` (`http://<name>.local`). Vendor ships a `QDeviceDiscovery` CLI.
- **Authentication:** none anywhere in the manual. Plain HTTP.
- **Liveness:** `GET /info/ping` → `{"Code": 0, "Message": "System is operational"}`.
- **Endpoint discovery:** `GET /endpoints` returns a machine-readable endpoint list.
- Q1.x → Q2.x renamed nearly every endpoint and changed stream headers. Target Q2.x
  only; assert `GET /version` (StatusCode 6 = API major version incompatibility).

## 2. System / channel model

- Everything is an **item** in a parent/child tree: Controller (ItemType 0) →
  Signal Conditioner (1) → Module (2) → Channel (4). Addressed by integer `itemId`
  query param — stateless per request.
- `GET /item/list` → flat array of `{ItemId, ItemName, ItemNameIdentifier,
  ItemType, ItemTypeIdentifier}`. `GET /system/settings` → same items as a nested
  tree (`Children`) with all settings inline.
- Module ids (partial): ICP4211=211, ICS425=217, WSB42X2=227, ALO42S4=151,
  CAN42S2=157, THM427=170, TAC221=222, XMC237=20005. Channel ids e.g. ICP4211=11,
  TAC221 Tacho=23, ALO42S4=24.
- The stream's Generic Channel Header `ChannelId` == the channel item's ItemId.

## 3. Configuration workflow — two-phase commit

1. `GET /item/list` — find module + channel ItemIds.
2. `PUT /item/operationMode/?itemId=X` — set Operation Mode FIRST; the mode
   determines which settings exist. Body = read-modify-write of the GET response
   with `Settings[0].Value` set to a `SupportedValues[].Id`.
3. `GET /item/settings/?itemId=X` → `{..., OperationMode, SettingsApplied: bool,
   Settings: [{Name, Type (Enumeration|Integer|Float|String|Array),
   SupportedValues: [{Id, Description}], Value}], Data: [...]}`.
   **Enums are set by integer Id, not string.** IDs vary per module/mode and are
   only partially documented — resolve by Description at runtime.
4. Mutate `Value`s; `PUT /item/settings/?itemId=X` with the whole document. This
   only **caches** on the server (`SettingsApplied: false`).
5. `PUT /system/settings/apply` — applies the whole system. Nothing takes effect
   before this. `GET /item/settings/defaults` = factory defaults;
   `/system/settings/resetToDefaults` resets.

- Apply can return StatusCode 4 ("valid, but applying will restart the
  measurement") and 14 ("applying affects other items"). A restart resets stream
  sequence numbers and (non-PTP) timestamps.
- Per-channel **streaming enable is itself a setting**: each streamable channel's
  `Data` array has `Streaming State` and `Local Storage State` enums, also
  requiring apply. (Old `/subscribeToData` endpoint is gone.)
- Changing Master Sampling Rate with PTP enabled **reboots a MicroQ** (changelog).

## 4. Analog input configuration

**Sample-rate model (hierarchical, no arbitrary Hz):**
- Controller: `Master Sampling Rate` enum — 131072 / 160000 / 163840 / 176400 /
  192000 / 200000 / 204800 Hz (example shows Ids 0–6; 131072 default in example
  dump). Also controller-level: `Analog Data Streaming Format` = Processed | Raw —
  global, changes the wire format for ALL analog channels.
- Module: `Sample Rate` enum = `MSR Divide by 1|2|4|...|256` (availability varies
  by module; THM427 offers /8../256; default /256 everywhere). One rate per module
  — "All channels have the same sample rate."
- Some modules (ICS421/ICS425, CHS42X4) have a `Four Channel High Sample Rate`
  operation mode: MSR/1 at the cost of disabling channels 2 and 5.
- Minimum analog rate at MSR 131072 = 512 Sa/s (MSR/256). Sub-512 rates (e.g.
  100 Sa/s) require client-side decimation.

**Per-channel settings by mode** (representative):
- ICP4211: `Voltage Input` (Voltage Range 10V|1V|100mV, Input Biasing
  Differential|Single Ended, Coupling DC|AC|AC+1Hz filter) | `ICP® Input` (Current
  Source 4mA, Single Ended, Voltage Range, Coupling AC|AC+1Hz) | Disabled.
- MIC42X: microphone input + `Preamp Excitation`.
- WSB42X: `Bridge Mode` Full/Half/Quarter 120Ω/350Ω, `Excitation Amplitude` 0–10 V
  or constant-current 4/8/12 mA, `Shunt Calibration Resistor`. Bridge balance via
  `PUT /wsb/bridgeBalance/apply` and `/wsb/bridgeBalance/reset` (system-wide or
  `?itemId=X`).
- THM427: 8 ch; channel-PAIR operation modes (1&2, 3&4, 5&6, 7&8 must match):
  Voltage Input | TC types E/J/K/T/U/N | PT100 (0.2 mA excitation) | Disabled.
  TC modes force Voltage Range 100 mV.
- Module-level `Grounding` (Floating | Grounded) affects all channels on a module.
- No user-selectable anti-alias filters beyond coupling — filtering is implicit in
  decimation.
- **Auto-zero:** `GET/PUT /autoZero/settings?itemId=X` + `PUT /autoZero/settings/apply`
  (system-wide without itemId). `Auto-Zero Level` (Disabled | System and Sensor |
  System Only), `Auto-Zero Average Time` (Quick | 1s | 2s | 5s).
- **TEDS:** `GET /tedsInfo?itemId=X` → raw IEEE 1451.4 byte array; decoding is the
  client's job (explicit in manual).

## 5. Data streaming

- **No acquisition start/stop.** QServer always has an active "measurement"
  (streaming epoch); connect to the TCP port and receive data for every channel
  with `Streaming State` enabled. Control: `PUT /dataStream/suspend` (discards
  data) / `PUT /dataStream/resume`. **Only 1 streaming client allowed.**
- Flow: `GET /dataStream/setup` → connect socket → parse. All little-endian
  (Byte Order Marker 0xFFFE), tightly packed, no padding.

**Packet Header (32 bytes):**
`u64 SequenceNumber` (resets at epoch start; gaps = server discarded data),
`f64 TransmitTimestamp`, `f32 BufferLevel` (server discards above **45%**),
`u32 PayloadSize`, `u32 ByteOrderMarker (0xFFFE)`, `u32 PayloadType` (0 = Data).

**Payload = repeated per-channel blocks.** Generic Channel Header (24 bytes):
`i32 ChannelId`, `i32 SampleType`, `u32 ChannelType` (0 Analog, 1 Tacho, 2 CAN,
3 GPS, 4 Triggered, 5 Triggered Status, 6 Triggered Stats), `u32 ChannelDataSize`
(bytes), `u64 Timestamp/Offset` (ns; analog = first-sample timestamp; digital =
per-sample offset; PTP epoch-based when active, else measurement-relative).

**Analog specific header** (20 B Processed / 24 B Raw): `i32 ChannelIntegrity`
(-1 N/A, 0 OK, 1 overload, 2 short, 3 open, 4 ADC error), `i32
LevelCrossingOccurred` (unsupported), `f32 Level` (0–1 FS), `f32 Min`, `f32 Max`,
plus `f32 ScalingFactor` in Raw mode. Data: SampleType 0 = f32 volts (Processed);
SampleType 1/2/3 = 16/24/32-bit fixed point requiring client scaling (24-bit needs
manual byte assembly).

**Tacho:** f64 event timestamps in **milliseconds** from epoch start; no specific
header. ICT/TAC "Scope" channels stream the analog waveform of the tacho input.

**CAN:** 24-byte reserved header + variable-length messages
(`f64 timestamp_s, u32 id, u8 header, u8 frame_format, u8 frame_type, u8 DLC,
data[1..64]`). **Raw frames only — no DBC/signal decoding anywhere in the API.**
Frames are hardware-timestamped on the same clock as analog channels.

**GPS (beta, XMC237):** 12-byte header + ASCII NMEA passthrough.

**PTP:** IEEE 1588-2008 v2 → epoch-ns timestamps across devices.

## 6. Other channel/output types

- **ALO42S4 analog output** (4 ch, ±10 V) — function generator, not a DAC:
  Operation Mode = "Disabled" | "DC Generator" | "Sine Wave Generator" |
  "Square Wave Generator" | "Triangular Wave Generator" | "White Noise
  Generator" | "Mirror Left Module" (verbatim from ALO42S4Channel.cs).
  Settings: Signal Connection, Amplitude (-9.999..10 V), Amplitude/Frequency
  Change Time (ramps), Frequency (0–10 kHz), Offset, Phase, Output Voltage
  Level ("5 V Out"|"12 V Out"). Every output change = settings write + apply
  (heavyweight). No arbitrary-waveform download. `PUT /alo/faultCondition`
  exists but is undocumented.
- **Tacho config** (ICT426/ICT42S6/TAC221): Voltage Range "2 V"|"12 V"|"30 V"|
  "60 V" (ICT42S6TachoChannel.cs), Input Biasing, Coupling, Trigger Polarity
  ("Rising Edge"|"Falling Edge"), Trigger Level / Arming Level, Trigger On nth Edge
  (1–1023).
- **CAN FD** (CAN42S2, MicroQ built-in, XMC237): modes Disabled | Listen Only |
  Participate; Arbitration Bitrate 50 kHz–1 MHz (10/33 kHz removed), Fast Data
  Bitrate 500 kHz–8 MHz, Bus Termination, Send At Fast Bitrate, Receive Own
  Messages. Transmit via `GET/PUT/DELETE /canfd/message/list?itemId=X`,
  `PUT /canfd/message/transmit`, `/canfd/message/abortTransmission`; health via
  `GET /canfd/bus/status/list`. Cannot update/clear individual list entries.
- **No general-purpose digital I/O lines exist** on this hardware.
- **Local storage / recording:** per-channel `Local Storage State`; `GET/PUT
  /localStorage/settings`; `PUT /recording/startprerun|start|stop`; `GET
  /recording/state|stats`; `GET /localstorage/measurement/list`. **No REST
  file-download endpoint** — retrieval is via the QDataManager GUI only.

## 7. Error handling

- HTTP: 200, 204, 400 (bad request / invalid config), 404, 500, 501.
- JSON status body: `{"TypeCode": 0|1|2 (Status|Information|Error), "StatusCode":
  0–24, "Message": "..."}`. Key codes: 2 invalid setting/enum (400); 3 settings
  updated OK (200); **4 valid-but-restarts-measurement (200)**; 5 invalid itemId
  (400); 6 version incompatibility (400); **14 affects other items (200)**; 15/16
  auto-zero unsupported/failed; 23/24 detailed invalid-settings strings (400).
- Codes 4 and 14 are successes with side effects, not errors.

## 8. Vendor software / references

- C# (official, GitHub, MIT): `Mecalc/QProtocolCSharp` (typed protocol),
  `Mecalc/QClientCSharp` (HTTP layer), `Mecalc/ExamplesCSharp`. Not on NuGet.
- Python: no SDK — `Mecalc/ExamplesPython` (MIT): `ReadItemList.py`,
  `ConfigureICS42.py`, `StreamData.py` (~400 lines, requests + socket + struct).
  Complete reference implementation of config + stream parse; use as CI referee
  against the simulator.
- MATLAB examples exist. **No vendor simulator exists** (checked manual, GitHub
  org, website — July 2026).

## 9. Canonical protocol facts from QProtocolCSharp (vendor-generated C#)

Extracted July 2026 from `Mecalc/QProtocolCSharp` + `QClientCSharp` (MIT).
Wire JSON field names are exactly the C# PascalCase property names (default
System.Text.Json, no naming policy); enums serialize as integers; every
endpoint path carries a trailing slash.

- **`/item/list` entry**: `{ItemId:int, ItemName:string, ItemNameIdentifier:int,
  ItemType:string, ItemTypeIdentifier:int}`. `ItemType` strings: "Controller",
  "SignalConditioner", "Module", "External Module", "Channel"
  (ItemTypeIdentifier 0/1/2/3/4, Uninitialized=255).
- **Settings document**: envelope + `Info` `[{Name,Value}]` + `OperationMode`
  `{Id, Description, Numeric?, SIUnit?}` + `SettingsApplied:bool` +
  `Settings`/`Data` arrays. Setting entry: `{Name, Type, SupportedValues?
  [{Id, Description, Numeric?, SIUnit?}], ValidationLimits? {Upper, Lower},
  Value}`. Type strings include "Enumeration", "Float", "Double", "Byte",
  "Unsigned Integer", "String". `Data` entries: "Streaming State" /
  "Local Storage State", Disabled=0/Enabled=1.
- **`/item/operationMode` document**: envelope + `Info` + `SettingsApplied` +
  `Settings` with exactly one "Operation Mode" enumeration. No top-level
  `OperationMode`, no `Data`.
- **`/system/settings`**: root = full controller settings document + `Children`
  (recursive; typed client reads only ItemInfo fields + Children per child).
- **`/version`**: `{Version: string}`. **`/info/ping`**: `{Code:int, Message}`.
  **`/dataStream/setup`**: `{IPAddresses:[string], TCPPort:int, WebSocketPort:int}`.
- **Status body**: `{TypeCode, StatusCode, Message}`; TypeCode Status=0, Info=1,
  Error=2. StatusCodes: Failure=0, Success=1, InvalidConfiguration=2, Updated=3,
  RequiresRestart=4, InvalidId=5, VersionMismatch=6, ActionNotFound=7, ...
  ActionHasSideEffects=14, AutoZeroNotSupported=15, AutoZeroFailed=16, ...
  InvalidSettings=23, Error=24. Vendor client accepts 1/3/4 silently and throws
  on everything else including 14.
- **`/SystemStatus` (port 80)**: `{BootPercentage:int, SystemState:int,
  SystemStateDescription, ApplicationStatus:int, ApplicationStatusDescription}`.
- **Controller identifiers** (ItemNameIdentifier): PQ20G2=30080, PQ30G2=30090,
  MicroQ=30100, PQ45=30110. **SC types**: SC42 G2=10070, SC42S G2=10080,
  SC45=10087, SC25=10088, SC10=10101.
- **Module identifiers** (customer-relevant): ICP4211=211, ICS421=213,
  ICS425=217, ICS42L5=182, MIC42X7=180, WSB42X2=227, WSB42X5=229, WSB42X6=230,
  THM427=170, CAN42S2=157, ICT42S6=65754, ICT426=218, TAC221=222, ALO42S4=151,
  XMC237=20005.
- **Channel identifiers**: ICP4211=11, ICS421=13, ICS425=15, MIC42X7=47,
  WSB42X2=30, THM427=45, CAN42S2=0, ICT42S6 Tacho=16 / Scope=18 / Icp=20,
  TAC221 Tacho=23, ALO42S4=24.
- **Master Sampling Rate** Ids: 0=131072 … 6=204800 (default 6). "Analog Data
  Streaming Format": Processed=0, Raw=1.
- **Module "Sample Rate" divisor Ids differ per family**: ICP4211/ICS425
  0=÷2…7=÷256; WSB42X2/MIC42X7 0=÷1…8=÷256; THM427 0=÷8…5=÷256. ICS425
  high-rate mode: separate enum, 0=÷1.
- **Per-channel enums** (selection): ICS425/ICP4211 channel modes Disabled=0,
  Voltage Input=1, ICP® Input=2; Voltage Range 0=100 mV, 1=1 V, 2=10 V.
  MIC42X7 adds Microphone Input=3; ranges 120 mV/1.2 V/12 V; Current Source
  4/8/12 mA=0/1/2. THM427 channel modes Disabled=0, Voltage=1, TC E/J/K/T/U/N=
  2..7, PT100=8; "Temperature SI Unit" Celsius=0/Kelvin=1. WSB42X2 modes
  (verbatim, WSB42X2Channel.cs) "Disabled"=0, "Voltage Input"=1, "ICP® Input"=2,
  "WSB Input: Voltage Excitation"=3, "WSB Input: 4 Wire Current Excitation"=4,
  "WSB Input: 2 Wire Current Excitation"=5; Bridge Mode Full=0/Half=1/
  Quarter 120Ω=2/Quarter 350Ω=3. CAN42S2 modes Disabled=0, Listen Only=1,
  Participate=2. ALO42S4 modes "Disabled"=0, "DC Generator"=1,
  "Sine Wave Generator"=2, "Square Wave Generator"=3,
  "Triangular Wave Generator"=4, "White Noise Generator"=5,
  "Mirror Left Module"=6.
- **DataIntegrity** adds Reserved=5 and AdcOverload=6 beyond the manual's list.

## 10. UNKNOWN — close against hardware or QProtocolCSharp

- ~~Settings persistence across power cycles~~ RESOLVED: they persist — QServer
  boots with "the stored settings from a previous session" ("The concept of a
  Measurement").
- Apply latency / timeout guidance for `system/settings/apply` (changelog hints it
  can be slow).
- Whether ALO amplitude changes restart the streaming epoch in practice.
- Exact enum IDs per module/mode/firmware (manual documents descriptions, few IDs).
- Exact JSON bodies for `PUT /system/time`, `/localStorage/settings`, CAN
  TransmitMessage, `/recording/state|stats` responses.
- `/alo/faultCondition` semantics.
- Triggered / Triggered Status / Triggered Stats stream channel types (4/5/6) —
  appear in the enum with "NA" data rows, otherwise undocumented.
- WebSocket framing (frame↔packet mapping unspecified; vendor Python example uses
  raw TCP only).
