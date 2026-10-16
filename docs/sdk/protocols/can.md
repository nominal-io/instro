# CAN

CANInterface is a config-driven CAN client. One JSON file names the adapter and a DBC file;
the DBC describes every message and signal (bit layout, scaling, units), so you read and
write signals by name instead of packing bytes.

```{warning}
CANInterface is new and is currently available only in the Unstable package.
```

```python
from instro.unstable.canbus import CANInterface

bus = CANInterface("bench_can.json", autostart=True)
bus.read("BMS_Status.pack_voltage")            # latest received value, in volts
bus.write("Motor_Command.torque_request", 5.0) # sends the whole Motor_Command frame
bus.close()
```

## Sample Config

```{literalinclude} ../../../packages/instro-unstable/instro/unstable/canbus/examples/bench_can.json
:language: json
```

## JSON Config Reference

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `version` | int | `1` | Config format version |
| `protocol` | string | `"can"` | Must be `"can"` |
| `device` | object | *required* | {py:class}`~instro.lib.types.DeviceInfo`; `name` is the channel prefix |
| `connection` | object | `null` | The CAN adapter; see [Connection](#connection) |
| `timing` | object | `null` | `{"poll_interval": <seconds>}`; required for `autostart=True` |
| `dbc` | string | *required* | Path to the DBC file, relative to the JSON file |
| `rx_messages` | list | every DBC message | DBC message names to receive |
| `tx_messages` | list | `[]` | DBC message names this interface may send |

Message names are checked against the DBC when the config loads, so a typo fails before the bus is opened.

### Connection

Connection can be provided in the config or passed to the constructor, which takes precedence:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `interface` | string | *required* | python-can interface: `"gs_usb"`, `"slcan"`, `"socketcan"`, ... |
| `channel` | string or int | *required* | Adapter channel, e.g. `0` for gs_usb or `"COM4"` for slcan |
| `bitrate` | int | `500000` | Bus bitrate in bit/s |
| `bus_kwargs` | object | `{}` | Extra keyword arguments for `can.Bus` |

Sharing one adapter between several interfaces, and how reading, polling, and writing behave,
are covered in the [CAN guide](/library/protocols/can.md).

## API Reference

```{eval-rst}
.. autosummary::
   :toctree: generated
   :nosignatures:

   ~instro.unstable.canbus.CANInterface
   ~instro.unstable.canbus.CANConfig
   ~instro.unstable.canbus.CANConnectionInfo
```
