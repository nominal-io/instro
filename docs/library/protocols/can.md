---
orphan: true
myst:
  html_meta:
    description: "Using CANInterface to read and write CAN bus signals described by a DBC file"
---

# CAN

{.lead}
Using CANInterface to read and write CAN bus signals described by a DBC file

```{warning}
CANInterface is new and is currently available only in the Unstable package (`instro.unstable.canbus`).
```

## CANInterface

CANInterface is a config-driven CAN client. One JSON file names the adapter and a DBC file. The
DBC describes every message and signal (bit layout, scaling, units), so you read and write signals
by name, as `"Message.signal"`, instead of packing bytes. Signals are defined only in the DBC; the
JSON config never redefines them.

### Quickstart

```python
from instro.unstable.canbus import CANInterface

# autostart opens the bus and starts the background daemon; it requires a `timing` section.
bus = CANInterface("bench_can.json", autostart=True)

bus.read("BMS_Status.pack_voltage")             # latest received value, in volts
bus.write("Motor_Command.torque_request", 5.0)  # sends the whole Motor_Command frame
bus.close()
```

`bench_can.json` sits next to the DBC it names:

```{literalinclude} ../../../packages/instro-unstable/instro/unstable/canbus/examples/bench_can.json
:language: json
```

The full field reference is on the [CAN SDK page](/sdk/protocols/can.md).

### Key Concepts

#### Reading

CAN devices broadcast, so `read("Message.signal")` requests nothing from the bus. It returns the
value from the newest frame received since `open()`, and raises if none has arrived yet.
Samples are timestamped with the host's receive time: adapter timestamps (gs_usb, for one) are
device counters, not wall-clock time.

Frames that carry no signal data are skipped: remote requests, error frames, and frames whose
length disagrees with the DBC. A skipped frame never replaces the last good value.

#### Polling

The background daemon runs `poll()`, which publishes one Measurement per message that received a
new frame since the last poll, with a channel per signal: `vehicle_bus.BMS_Status.pack_voltage`.
A message that goes quiet stops publishing. Only the newest frame per message is kept between
polls, so set `timing.poll_interval` shorter than a message's cycle time to publish every frame.

#### Writing

`write("Message.signal", value)` takes the value in the DBC's units and sends the whole frame.
The message's other signals keep the last value written to them (their DBC initial value until
then), so writing one signal never resets its neighbours. Values outside the signal's DBC range
raise `cantools.database.EncodeError` and nothing is sent. Each write publishes a Command on
`<channel>.cmd`, e.g. `vehicle_bus.Motor_Command.torque_request.cmd`.

Only messages listed in `tx_messages` can be written. `send_frame(arbitration_id, data,
is_extended_id=...)` sends a raw frame, bypassing the DBC.

#### Sharing an Adapter

To put several interfaces on one adapter, pass the same
{py:class}`~instro.unstable.transports.can.CanTransport` to each. The bus stays open until the
last one closes, and each interface receives only its own `rx_messages`:

```python
from instro.unstable.transports import CanConfig, CanTransport

transport = CanTransport(CanConfig(channel=0, interface="gs_usb"))
bms = CANInterface("bms.json", connection=transport)
inverter = CANInterface("inverter.json", connection=transport)
```

### Limitations

Not yet supported: CAN FD, and writing multiplexed messages (reading them works).
