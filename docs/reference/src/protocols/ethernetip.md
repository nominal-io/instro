# EtherNet/IP

EtherNet/IP support provides config-driven access to explicitly declared Allen-Bradley PLC tags.
It currently lives under `instro.unstable.ethernetip` and depends on the local native
`instro-ethernetip-python` package.

```python
from instro.unstable.ethernetip import EtherNetIP

connection = {
    "host": "192.168.1.10",
    "port": 44818,
    "route_path": {"hops": [{"type": "backplane", "slot": 0}]},
}

plc = EtherNetIP("compactlogix.json", connection=connection, autostart=True)
plc.read("line_speed")
plc.write("line_speed", 1200.0)
plc.close()
```

## Current Scope

| Area | Supported today |
|------|-----------------|
| Tested PLC | Allen-Bradley CompactLogix 5332E 1769-L32E |
| Transport | EtherNet/IP explicit messaging over TCP |
| Route paths | Direct connection or local backplane slot hops only |
| Polling | Batched reads for `poll: true` scalar tags |
| Streaming values | Boolean and numeric scalar tags |
| Manual strings | Supported through the native session API |
| Tag discovery | Not supported |
| UDTs | Not supported in the config-driven API |
| Arrays | Not supported in the config-driven API |

## JSON Config Reference

### Connection

Connection can be provided in the config or passed to the `EtherNetIP` constructor. The constructor
parameter takes precedence, allowing the same tag map to be reused across environments.

=== "Constructor"

    ```python
    plc = EtherNetIP(
        "compactlogix.json",
        connection={
            "host": "192.168.1.10",
            "port": 44818,
            "route_path": {"hops": [{"type": "backplane", "slot": 0}]},
        },
    )
    ```

=== "In Config"

    ```json
    {
        "connection": {
            "host": "192.168.1.10",
            "port": 44818,
            "route_path": {
                "hops": [
                    {"type": "backplane", "slot": 0}
                ]
            }
        }
    }
    ```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `host` | string | *required* | PLC IP address or hostname |
| `port` | int | `44818` | EtherNet/IP TCP port |
| `route_path` | object | `null` | Optional local backplane route path |

Route path hops are restricted to local backplane hops:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `hops` | list | `[]` | Ordered local backplane hops |
| `hops[].type` | string | *required* | Must be `"backplane"` |
| `hops[].slot` | int | *required* | Backplane slot number, 0-255 |

Network hops to another PLC, remote chassis, or IP address are not supported. Although the schema
accepts multiple local backplane hops, current testing has only covered a single backplane hop.

### Timing

Controls background polling:

```json
{
    "timing": {
        "poll_interval": 1.0
    }
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `poll_interval` | float | *required* | Seconds between polling cycles (0.01-10.0) |

When polling is running, all `poll: true` tags are read in one batched request per polling cycle.
Per-tag failures are logged without discarding successful values from the same batch.

### Tags

Each tag entry defines one named PLC tag:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `alias` | string | *required* | Alias used in `read()` and `write()` |
| `tag_name` | string | *required* | PLC tag name |
| `description` | string | `null` | Optional description |
| `data_type` | string | *required* | Expected PLC scalar type |
| `scale` | object | `null` | Linear scaling config for numeric tags |
| `poll` | bool | `true` | Include in background polling |
| `write_min` | number | `null` | Minimum allowed write value for numeric tags |
| `write_max` | number | `null` | Maximum allowed write value for numeric tags |

Supported `data_type` values:

| Data Type | Streamable | Notes |
|-----------|------------|-------|
| `bool` | Yes | Published as numeric `0` or `1` |
| `sint` | Yes | 8-bit signed integer |
| `int` | Yes | 16-bit signed integer |
| `dint` | Yes | 32-bit signed integer |
| `lint` | Yes | 64-bit signed integer |
| `usint` | Yes | 8-bit unsigned integer |
| `uint` | Yes | 16-bit unsigned integer |
| `udint` | Yes | 32-bit unsigned integer |
| `ulint` | Yes | 64-bit unsigned integer |
| `real` | Yes | 32-bit floating point |
| `lreal` | Yes | 64-bit floating point |
| `string` | No | Must set `poll: false`; use manual reads/writes |

String tags cannot be streamed to Core or Connect, but can be read or written manually through
`instro.unstable._ethernetip.EtherNetIpSession`.

### Scaling

Linear scaling converts between raw numeric PLC values and physical units:

```
physical = offset + (gain * raw)
```

```json
{
    "alias": "pressure",
    "tag_name": "PressureRaw",
    "data_type": "dint",
    "scale": {"type": "linear", "gain": 0.01, "offset": 0}
}
```

### Write Limits

Reject writes outside a safe range before they reach the PLC:

```json
{
    "alias": "line_speed",
    "tag_name": "LineSpeed",
    "data_type": "real",
    "write_min": 0.0,
    "write_max": 2500.0
}
```

```python
plc.write("line_speed", 1200.0)  # OK
plc.write("line_speed", 9999.0)  # raises ValueError
```

Limits are checked in physical units before reverse scaling is applied.

## Validation Rules

- `protocol` must be `"ethernetip"`.
- Tag aliases must be unique.
- Every tag must declare `data_type`.
- `scale`, `write_min`, and `write_max` are only valid for numeric tags.
- `write_min` must be less than or equal to `write_max`.
- Integer write limits must fit in the configured PLC integer type after scaling.
- String tags must set `poll: false`.
- Route path hops must use `type: "backplane"` with `slot` from 0 to 255.
- Tag discovery, UDTs, and arrays are not supported.

## API Reference

### EtherNetIP

::: instro.unstable.ethernetip.ethernetip.EtherNetIP

### Configuration Types

::: instro.unstable.ethernetip.ethernetip_types
    options:
      members:
        - EtherNetIPConfig
        - TimingConfig
        - EtherNetIPConnection
        - EtherNetIPRoutePath
        - EtherNetIPBackplaneHop
        - TagDef
