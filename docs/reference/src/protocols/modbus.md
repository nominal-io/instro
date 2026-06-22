# Modbus

ModbusDevice is a config-driven Modbus client. Describe your device in a JSON file (registers,
addresses, data types) and interact using human-readable aliases instead of raw addresses.

```python
from instro.modbus import ModbusDevice

connection = {"transport": "tcp", "host": "192.168.1.10", "port": 502}
device = ModbusDevice("my_device.json", connection=connection, autostart=True)
device.read("temperature")
device.write("setpoint", 75.5)
device.write("mode", "auto")  # string values via write_value_map
device.close()
```

Or build the config in code with full IDE autocomplete:

```python
from instro.modbus import ModbusDevice, ModbusConfig, TCPConnection
from instro.modbus.types import (
    DeviceInfo, RegisterDef, TimingConfig,
)

config = ModbusConfig(
    device=DeviceInfo(name="my_device"),
    timing=TimingConfig(poll_interval=1.0, write_delay_ms=300),
    registers=[
        RegisterDef(name="temperature", starting_address=0, data_type="float32",
                    write_min=-40.0, write_max=500.0),
        RegisterDef(name="mode", starting_address=100, data_type="uint16",
                    write_value_map={"off": 0, "auto": 1, "manual": 2}),
    ],
)
connection = TCPConnection(host="192.168.1.10")
device = ModbusDevice(config, connection=connection, autostart=True)
```

## Sample Config

A complete config for a heat exchanger with temperature sensors, flow rate, a setpoint,
a pump coil, and a status register with bitmap extraction:

```json
--8<-- "examples/modbus/sample_device.json"
```

## JSON Config Reference

### Connection

Connection can be provided in the config or passed to the `ModbusDevice` constructor.
The constructor parameter takes precedence, allowing the config to be a standalone device
description shared across environments.

=== "Constructor (recommended)"

    ```python
    device = ModbusDevice(
        "my_device.json",
        connection={"transport": "tcp", "host": "192.168.1.10", "port": 502},
    )
    ```

=== "TCP (in config)"

    ```json
    {
        "connection": {
            "transport": "tcp",
            "host": "192.168.1.10",
            "port": 502,
            "unit_id": 1,
            "timeout": 3.0
        }
    }
    ```

=== "RTU (in config)"

    ```json
    {
        "connection": {
            "transport": "rtu",
            "port": "/dev/ttyUSB0",
            "baudrate": 9600,
            "parity": "N",
            "stopbits": 1,
            "bytesize": 8,
            "unit_id": 1,
            "timeout": 3.0
        }
    }
    ```

    Serial port paths vary by platform:

    - **Linux**: `/dev/ttyUSB0`, `/dev/ttyACM0`
    - **macOS**: `/dev/cu.usbserial-1234`, `/dev/cu.usbmodem1234`
    - **Windows**: `COM3`, `COM4`

### Timing

Controls background polling interval and write delay:

```json
{
    "timing": {
        "poll_interval": 1.0,
        "write_delay_ms": 300
    }
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `poll_interval` | float | *required* | Seconds between polling cycles (0.01 to 10.0) |
| `write_delay_ms` | int | `0` | Milliseconds to sleep after each write |

Activate polling with `autostart=True` in the constructor, or call `open()` then `start()` manually.
The write delay is applied automatically after every `write()` call, with no manual `time.sleep()` needed.

### Registers

Each register entry defines a named channel:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | string | *required* | Alias used in `read()` and `write()` |
| `starting_address` | int | *required* | Modbus register address (0 to 65535) |
| `register_type` | string | `"holding"` | `"holding"`, `"input"`, `"coil"`, or `"discrete"` |
| `data_type` | string | `"uint16"` | `"uint16"`, `"int16"`, `"uint32"`, `"int32"`, `"uint64"`, `"int64"`, `"float32"`, `"float64"`, `"bool"` |
| `byte_swap` | bool | `false` | Swap bytes within 16-bit words |
| `word_swap` | bool | `false` | Swap 16-bit words (32-bit and 64-bit types) |
| `long_swap` | bool | `false` | Swap 32-bit halves (64-bit types only) |
| `scale` | object | `null` | Linear scaling config, e.g. `{"type": "linear", "gain": <float>, "offset": <float>}`. See [Scaling](#scaling) |
| `bitmap` | list | `null` | Bit extraction: `[{"name": "alarm", "bit_index": 0}]` |
| `poll` | bool | `true` | Include in background polling |
| `write_min` | number | `null` | Minimum allowed write value, in the same units the caller passes to `write()` (scaled if `scale` is set, raw otherwise). Holding registers only. |
| `write_max` | number | `null` | Maximum allowed write value, in the same units the caller passes to `write()` (scaled if `scale` is set, raw otherwise). Holding registers only. |
| `write_value_map` | object | `null` | Map string labels to register values (holding registers only) |
| `read_group` | string | `null` | Group ID for batched reads (all registers in a group are read in one transaction) |

### Scaling

Linear scaling converts between raw register values and physical units:

```
physical = offset + (gain * raw)
```

```json
{
    "name": "pressure_psi",
    "starting_address": 10,
    "data_type": "uint16",
    "scale": {"type": "linear", "gain": 0.01, "offset": 0}
}
```

### Write Value Map

Map human-readable strings to raw register values. Eliminates magic numbers in application code:

```json
{
    "name": "control_mode",
    "starting_address": 100,
    "data_type": "uint16",
    "write_value_map": {
        "off": 0,
        "auto": 1,
        "manual": 2
    }
}
```

```python
device.write("control_mode", "auto")   # writes 1
device.write("control_mode", 1)        # also works
```

Values in the map must be unique and must fall within `write_min`/`write_max` if those are set.

### Write Limits

Reject writes outside a safe range before they reach the device:

```json
{
    "name": "setpoint",
    "starting_address": 0,
    "data_type": "float32",
    "write_min": 32.0,
    "write_max": 300.0
}
```

```python
device.write("setpoint", 150.0)   # ok
device.write("setpoint", 999.0)   # raises ValueError
```

Limits are checked in physical units (before scaling).

### Read Groups

Registers with the same `read_group` are read in a single Modbus transaction, reducing
the number of round trips per polling cycle:

```json
{
    "name": "heat_power",
    "starting_address": 100,
    "data_type": "float32",
    "read_group": "power"
},
{
    "name": "cool_power",
    "starting_address": 102,
    "data_type": "float32",
    "read_group": "power"
}
```

Constraints:

- All registers in a group must share the same `register_type`
- All registers in a group must have `poll: true`
- Holding/input groups cannot span more than 125 registers
- Coil/discrete groups cannot span more than 2000 addresses

### Bitmap

Extract individual bits from a `uint16` holding or input register as named channels:

```json
{
    "name": "status_register",
    "starting_address": 100,
    "data_type": "uint16",
    "bitmap": [
        {"name": "alarm_high", "bit_index": 0},
        {"name": "alarm_low", "bit_index": 1},
        {"name": "motor_running", "bit_index": 5}
    ]
}
```

Reading `status_register` returns the raw value plus each bit as a separate channel (0 or 1).

## API Reference

### ModbusDevice

::: instro.modbus.ModbusDevice

### Configuration Types

::: instro.modbus.types
    options:
      members:
        - ModbusConfig
        - TimingConfig
        - TCPConnection
        - RTUConnection
        - RegisterDef
        - BitDef

### Shared Types

::: instro.lib.types
    options:
      members:
        - DeviceInfo
        - LinearScale
