---
orphan: true
myst:
  html_meta:
    description: "Using I2CInterface for vendor-independent I2C communication"
---

# Inter-Integrated Circuit (I2C)

{.lead}
Using I2CInterface for vendor-independent I2C communication

## I2CInterface

I2CInterface is a hardware abstraction layer (HAL) that provides a unified interface for I2C communication across multiple adapter vendors. The key benefit is **vendor-independent code**: describe your bus once in a JSON config (or an `I2CConfig` built in Python), and the same code works with different I2C adapters.

### Supported Vendors

- **Total Phase** - Aardvark I2C/SPI Host Adapter

If your adapter vendor is not listed, [custom driver development](#driver-development) is available to add support for your hardware.

### Key Concepts

#### System Definition

I2CInterface's architecture utilizes a system definition to provide a **low-code, human-readable way** to interact with I2C devices on the bus. This design reduces the need for magic numbers, manual bitwise operations, and scattered hardware knowledge throughout your test code.

The system definition is the `devices` list of an `I2CConfig`, loaded from a JSON file or built in Python. It serves as a **single source of truth** about your I2C bus configuration, centralizing:
- Device addresses and names
- Register maps and bit field definitions
- Data formats and scaling functions
- Command definitions for command-based devices

Instead of writing code like:
```python
# Hard-coded addresses, magic numbers, manual bit manipulation
i2c.write(0x20, 0x01, 0xFF)
value = i2c.read(0x20, 0x01)
mode = (value & 0x06) >> 1  # Manual bit masking
```

You write code like:
```python
# Clear, readable, maintainable
i2c.write("gpio_expander", "OUTPUT", 0xFF)
value = i2c.read("gpio_expander", "OUTPUT")
mode = i2c.read("gpio_expander", "CONFIG", field="mode")
```

See the [System Definition](/library/protocols/i2c/system-definition.md) page for detailed information on describing your devices, registers, and commands.

:::{tip}
Keep the bus description in its own JSON file, separate from your test code. This cleanly separates hardware configuration (typically done by a hardware/firmware engineer) from the test and automation logic (usually the test engineer's domain), leading to more maintainable and collaborative code.
:::

#### Lifecycle Pattern

The typical I2CInterface workflow follows this pattern:

1. **Describe your bus** - Write a JSON config or build an `I2CConfig` in Python (see [System Definition](/library/protocols/i2c/system-definition.md))
2. **Construct `I2CInterface(config=...)`** - The driver comes from the config's `connection` block, or pass `driver=Aardvark(...)` directly
3. **`open()`** - Establish connection to the I2C adapter hardware
4. **`start()`** - Begins a periodic daemon in the background that reads every register and batch command marked `poll: true`. (Optional)
5. **Configure and communicate** - Access registers, fields, or send commands to devices
6. **`stop()`** - End background daemon (if started)
7. **`close()`** - Disconnect from hardware

Pass `autostart=True` to combine steps 3 and 4. This requires a `timing` section in the config.

**Custom Background Daemon**
* To define your own background daemon, call `define_background_daemon()`.
* To add a method to the background daemon stack, call `add_background_daemon_function()`.

See [Two ways to get data](/using-instro.md#two-ways-to-get-data) for more information regarding background fetching of measurements.

:::{note}
**Important Note about Publishers**

Data is published as a direct result of an instrument method being called.

For example, when you call `read()`, this not only returns a register value but also causes all attached Publishers to publish the response automatically.

Therefore the background daemon, when calling these instrument methods, is publishing data in the background as well!
:::

#### Device Types

I2CInterface's `System Definition` supports two types of I2C devices:

- **Register-based devices** - Devices with register maps (e.g., GPIO expanders, sensors with registers)
- **Command-based devices** - Devices that respond to command bytes (e.g., ADCs that accept {abbr}`channel (A named signal for a series of measurements or computed values, example: voltage, pressure, system state.)` selection commands)

Both device types are configured in the config with human-readable names, allowing you to access devices without remembering raw I2C addresses.

:::{note}
You can read and write directly to the I2C bus using `write_raw()` and `read_raw()`, which bypasses the benefits provided by the system definition architecture.
This is useful for using I2C devices that I2CInterface doesn't yet provide lower-code interactions with, allowing you to move forward regardless.
:::

### Creating an I2CInterface Instance

`I2CInterface` takes a `config`: a path to a JSON file, a dict, or an {py:class}`~instro.i2c.config.I2CConfig`. The config describes the bus and, optionally, the adapter it hangs off:

::::{tab-set}
:::{tab-item} From JSON

```python
from instro.i2c import I2CInterface

# The driver comes from the config's `connection` block.
i2c = I2CInterface(config="sensor_bus.json")

# Or pass the driver explicitly. It overrides `connection`, so one config can
# serve benches with different adapters.
from instro.i2c.drivers.totalphase import Aardvark

i2c = I2CInterface(config="sensor_bus.json", driver=Aardvark(serial_number="123456"))
```
:::
:::{tab-item} Config (sensor_bus.json)

```json
{
  "version": 1,
  "protocol": "i2c",
  "device": {"name": "sensor_bus", "manufacturer": "Total Phase", "model": "Aardvark"},
  "timing": {"poll_interval": 1.0},
  "connection": {"interface": "aardvark", "serial_number": "123456"},
  "devices": [
    {
      "type": "register",
      "name": "gpio_expander",
      "address": "0x20",
      "registers": [
        {"alias": "OUTPUT", "register": "0x01", "poll": true},
        {
          "alias": "CONFIG",
          "register": "0x0A",
          "fields": [
            {"name": "interrupt_enable", "lsb": 0, "width_bits": 1},
            {"name": "interrupt_polarity", "lsb": 1, "width_bits": 1}
          ]
        }
      ]
    },
    {
      "type": "command",
      "name": "adc",
      "address": "0x48",
      "data_format": {
        "transfer_bits": 16,
        "signed": true,
        "scaling": {"type": "linear", "gain": 0.001, "offset": 0.0},
        "units": "V"
      },
      "commands": {"channel": {"CH0": "0x00", "CH1": "0x10"}},
      "batch_commands": [
        {"name": "read_ch0", "commands": {"channel": "CH0"}, "poll": true},
        {"name": "read_ch1", "commands": {"channel": "CH1"}}
      ]
    }
  ]
}
```
:::
:::{tab-item} In Python

```python
from instro.i2c import (
    CommandDeviceConfig,
    BatchCommandConfig,
    DataFormatConfig,
    I2CConfig,
    I2CInterface,
    RegisterConfig,
    RegisterDeviceConfig,
)
from instro.i2c.drivers.totalphase import Aardvark
from instro.i2c.types import FieldDef
from instro.lib.config import TimingConfig
from instro.lib.types import DeviceInfo, LinearScale

config = I2CConfig(
    device=DeviceInfo(name="sensor_bus"),
    timing=TimingConfig(poll_interval=1.0),
    devices=[
        RegisterDeviceConfig(
            name="gpio_expander",
            address=0x20,
            registers=[
                RegisterConfig(alias="OUTPUT", register=0x01, poll=True),
                RegisterConfig(
                    alias="CONFIG",
                    register=0x0A,
                    fields=[FieldDef("interrupt_enable", lsb=0), FieldDef("interrupt_polarity", lsb=1)],
                ),
            ],
        ),
        CommandDeviceConfig(
            name="adc",
            address=0x48,
            data_format=DataFormatConfig(transfer_bits=16, signed=True, scaling=LinearScale(gain=0.001), units="V"),
            commands={"channel": {"CH0": 0x00, "CH1": 0x10}},
            batch_commands=[
                BatchCommandConfig(name="read_ch0", commands={"channel": "CH0"}, poll=True),
                BatchCommandConfig(name="read_ch1", commands={"channel": "CH1"}),
            ],
        ),
    ],
)

i2c = I2CInterface(config=config, driver=Aardvark(serial_number="123456"))
```
:::
::::

#### Parameters

- **`config`**: A path to a JSON file, a dict, or an `I2CConfig`. `device.name` becomes the channel-name prefix unless `name` is given. A passed-in config object is copied up front, so later edits to it don't reach the instrument.
- **`driver`**: An `I2CDriverBase` implementation (e.g. `Aardvark(serial_number=...)`). Overrides the config's `connection` block, and is required when the config has none.
- **`name`**: Optional channel-name prefix; defaults to `config.device.name`.
- **`publishers`**: Optional list of publishers to attach
- **`autostart`**: When `True`, opens the adapter and starts background polling immediately. Requires a `timing` section in the config.
- **`**kwargs`**: Additional keyword arguments become default tags when using a publisher that supports tags (like `NominalCorePublisher`).

:::{note}
**Deprecated construction**

`I2CInterface(name, driver, system_definition)`, with a `SystemDefinition` built from the `instro.i2c.types` dataclasses, still works but emits a `DeprecationWarning` and will be removed in a future release. Migrate the definition to an `I2CConfig`; the field names are the same.
:::

#### Background polling

Any register or batch command with `"poll": true` is read by the background daemon once `start()` is called, at `timing.poll_interval` seconds (or the default interval when the config has no `timing` section). `poll` defaults to `false`: I2C reads can have side effects (clear-on-read status registers, FIFOs), so nothing is polled without opting in.

#### Custom scaling at runtime

JSON expresses linear scaling only. For anything else, load the config and then attach a {py:class}`~instro.i2c.types.ScalingFunction` with `set_scaling()`. It replaces the scaling from the config and takes effect on the next read, including background polls:

```python
from instro.i2c.types import CustomScaling

i2c = I2CInterface(config="sensor_bus.json")

# Command device: the device's single data format is rescaled.
i2c.set_scaling("adc", CustomScaling(to_physical_fn=lambda raw: raw / 4095 * 5.0 * 7.2))

# Register device: name the register.
i2c.set_scaling("gpio_expander", CustomScaling(to_physical_fn=thermistor_curve), register_alias="OUTPUT")
```

### Examples

All measurement methods return {py:class}`~instro.lib.types.Measurement` objects. This is common amongst all `Instrument` objects.

All examples below load the `sensor_bus.json` shown above. This follows the recommended practice of separating hardware configuration from test logic.

#### Basic Register Read/Write

```python
from instro.i2c import I2CInterface

i2c = I2CInterface(config="sensor_bus.json")

i2c.open()

# Write to register
i2c.write("gpio_expander", "OUTPUT", 0xFF)

# Read from register
measurement = i2c.read("gpio_expander", "OUTPUT")
print(f"Output state: {measurement.latest}")

i2c.close()
```

#### Field-Level Register Access

For registers with bit fields, you can read and write individual fields:

```python
from instro.i2c import I2CInterface

i2c = I2CInterface(config="sensor_bus.json")

i2c.open()

# Read specific field (automatically masks and shifts)
enable = i2c.read("gpio_expander", "CONFIG", field="interrupt_enable")

# Write to specific field (read-modify-write operation)
i2c.write("gpio_expander", "CONFIG", 1, field="interrupt_enable")

i2c.close()
```

#### Command-Based Device Query

For command-based devices (like ADCs):

```python
from instro.i2c import I2CInterface

i2c = I2CInterface(config="sensor_bus.json")

i2c.open()

# Query device (sends command and reads back scaled result)
voltage = i2c.query("adc", "read_ch0")
print(f"Channel 0 voltage: {voltage.latest}V")

i2c.close()
```

#### Raw I2C Operations

For advanced use cases, you can bypass the system definition and use raw I2C operations:

```python
from instro.i2c import I2CInterface

# The config is still needed to create the instrument.
i2c = I2CInterface(config="sensor_bus.json")

i2c.open()

# Raw write
i2c.write_raw(address=0x20, data=b"\x01\xFF")

# Raw read
value = i2c.read_raw(address=0x20, length=2, endianness="little")

# Write-then-read (with stop condition between operations)
response = i2c.write_then_read_raw(
    address=0x48,
    payload=b"\x00",
    length=2,
    endianness="big"
)

# Write-read (no stop condition, typical for register reads)
register_value = i2c.write_read_raw(
    address=0x20,
    payload=b"\x01",  # Register address
    length=1,
    endianness="big"
)

i2c.close()
```

:::{note}
**Raw vs System Definition Methods**

- **System definition methods** (`read()`, `write()`, `query()`): Use device names and register aliases, handle data format conversion automatically
- **Raw methods** (`read_raw()`, `write_raw()`, etc.): Direct I2C address and byte-level operations, no format conversion

Use system definition methods for most applications. Use raw methods only when you need direct control over I2C transactions.
:::

#### Register Reset

Reset a register to its default value as defined in the system definition:

```python
i2c.reset_reg("gpio_expander", "CONFIG")
```

This is equivalent to writing the register's `default_value` from the config.

### Published channels

Every read/write produces a channel keyed under `{name}.{descriptor}`, where `{name}` is the instrument name (`config.device.name` unless overridden) and `{descriptor}` is built from the device names you defined in the config.

| Method | Descriptor | Type |
|--------|------------|------|
| `read(peripheral, register_alias)` | `{peripheral}.{register_alias}` | telemetry |
| `read(peripheral, register_alias, field=...)` | `{peripheral}.{register_alias}.{field}` | telemetry |
| `write(peripheral, register_alias, ...)` | `{peripheral}.{register_alias}.cmd` | command |
| `write(peripheral, register_alias, field=..., ...)` | `{peripheral}.{register_alias}.{field}.cmd` | command |
| `query(peripheral, batch_command)` | `{peripheral}.{batch_command}` | telemetry |

`{peripheral}`, `{register_alias}`, `{field}`, and `{batch_command}` are the names you assigned in the config. If you depend on the pre-v1.0 underscore-separator form (e.g. `{name}_{peripheral}_{register}` with a trailing `_cmd`), pass [`legacy_naming=True`](/library/library.md#backwards-compatible-channel-naming-legacy_naming) to the constructor.

### Method Reference

| Method | Purpose |
|--------|---------|
| `I2CInterface(config=..., driver=None, name=None, publishers=None, autostart=False, legacy_naming=False, **kwargs)` | Construct an I2CInterface instance from a config, with the driver from the config's `connection` or passed in |
| `open()` | Establish connection to the I2C adapter |
| `close()` | Disconnect from adapter and close all publishers |
| `read(peripheral, register_alias, field="", **kwargs)` | Read a register or field from a register-based device |
| `write(peripheral, register_alias, value, field="", **kwargs)` | Write to a register or field on a register-based device |
| `reset_reg(peripheral, register_alias, **kwargs)` | Reset a register to its default value |
| `query(peripheral, batch_command, **kwargs)` | Send command to a command-based device and read response |
| `set_scaling(peripheral, scaling, register_alias=None)` | Replace a register's or command device's scaling at runtime |
| `read_raw(address, length, endianness)` | Perform raw I2C read operation |
| `write_raw(address, data)` | Perform raw I2C write operation |
| `write_read_raw(address, payload, length, endianness)` | Write then read without stop condition |
| `write_then_read_raw(address, payload, length, endianness)` | Write then read with stop condition between operations |
| `start()` | Begin background telemetry daemon |
| `stop()` | End background telemetry daemon |
| `add_publisher(publisher)` | Attach a publisher for data routing |

---

## Driver Development

This section is for developers implementing I2CInterface support for I2C adapter vendors that are not supported out of the box.

### Overview

Driver developers implement the `I2CDriverBase` abstract interface to add support for new I2C adapter vendors. The driver is responsible for **translating I2CInterface's vendor-independent API calls into vendor-specific hardware operations**, ensuring users get consistent behavior regardless of the underlying hardware.

### Driver Responsibilities

An I2C driver must:

1. **Hardware connection lifecycle**: Implement `open()` and `close()` for establishing and terminating hardware connections
2. **Basic I2C operations**: Implement `read()`, `write()`, and `write_read()` for fundamental I2C transactions
3. **Hardware configuration**: Implement `set_bitrate()`, `set_pullups()`, and `set_power_enable()` for adapter configuration
4. **Resource management**: Properly manage hardware resources and thread safety

### I2CDriverBase Interface

All I2C drivers must subclass `I2CDriverBase` and implement these abstract methods:

#### Required Methods

```python
def read(self, address: int, length: int) -> bytes:
    """Read bytes from an I2C device.

    Args:
        address: 7-bit I2C device address
        length: Number of bytes to read

    Returns:
        Bytes read from the device
    """

def write(self, address: int, data: bytes) -> None:
    """Write bytes to an I2C device.

    Args:
        address: 7-bit I2C device address
        data: Bytes to write to the device
    """

def write_read(self, address: int, data: bytes, read_len: int) -> bytes:
    """Write bytes then immediately read without stop condition.

    Performs a write operation followed by a read operation without
    issuing an I2C stop condition between them. Used for register reads.

    Args:
        address: 7-bit I2C device address
        data: Bytes to write (typically register address)
        read_len: Number of bytes to read back

    Returns:
        Bytes read from the device
    """

def set_bitrate(self, bitrate: int) -> None:
    """Set the I2C master clock rate in kHz.

    Args:
        bitrate: Desired bus clock rate in kHz (e.g., 100 for 100 kHz)
    """

def set_pullups(self, enable: bool) -> None:
    """Enable or disable I2C pull-up resistors.

    Args:
        enable: True to enable pull-ups, False to disable
    """

def set_power_enable(self, enable: bool) -> None:
    """Enable or disable power supply on I2C bus.

    Some adapters can provide power to I2C devices.

    Args:
        enable: True to enable power, False to disable
    """

def close(self) -> None:
    """Close the driver and release hardware resources."""
```

#### Driver Composition

Concrete I2C drivers own their transport SDK. `I2CInterface` calls `driver.open()` /
`driver.close()` and forwards I2C transactions to the driver. The driver does not
need a back-reference to the instrument.

### Implementation Example: Total Phase Driver

Here's a reference implementation for part of the Total Phase Aardvark driver:

```python
from instro.i2c import I2CDriverBase

class Aardvark(I2CDriverBase):
    def __init__(self, serial_number: str | None = None) -> None:
        self._serial_number = serial_number
        self._device = None

    def open(self) -> None:
        import pyaardvark  # type: ignore

        self._device = pyaardvark.open(serial_number=self._serial_number)

    def close(self) -> None:
        if self._device is not None:
            self._device.close()
            self._device = None

    def read(self, address: int, length: int) -> bytes:
        return self._device.i2c_master_read(address, length)

    def write(self, address: int, data: bytes) -> None:
        self._device.i2c_master_write(address, data)

    def write_read(self, address: int, data: bytes, read_len: int) -> bytes:
        return self._device.i2c_master_write_read(address, data, read_len)
    ...
    # more implementations
    ...
```

### Using Custom Drivers

For custom drivers, pass your driver instance alongside the config. It takes the place of the config's `connection` block:

```python
from instro.i2c import I2CInterface, I2CDriverBase

class MyCustomI2CDriver(I2CDriverBase):
    """Custom driver for my lab's proprietary I2C adapter."""

    def open(self) -> None:
        # Open the underlying transport
        pass

    def close(self) -> None:
        # Release the underlying transport
        pass

    def read(self, address: int, length: int) -> bytes:
        # Custom implementation
        pass

    # ... implement other required methods ...

i2c = I2CInterface(
    config="sensor_bus.json",
    driver=MyCustomI2CDriver(device_id="<DEVICE_ID>"),
)

i2c.open()
i2c.write("my_device", "REGISTER", 0xFF)
i2c.close()
```

### Summary

Driver development requires careful mapping of vendor-specific operations to the unified `I2CDriverBase` interface. Focus on:

- Implementing all `I2CDriverBase` abstract methods
- Handling 7-bit I2C addresses correctly (most vendors expect this)
- Properly managing hardware resources (open/close)
- Supporting both single and combined write-read operations
- Testing with actual hardware to ensure commands work as expected
