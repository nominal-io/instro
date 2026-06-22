# EtherNet/IP (unstable)

EtherNet/IP config files declare an Allen-Bradley PLC endpoint, an optional local backplane route,
and the scalar tags Nominal reads or writes. The client lives under `instro.unstable.ethernetip`
and depends on the optional native `instro-ethernetip` package from
`instro-unstable[ethernetip]`.

```python
from instro.unstable.ethernetip import EtherNetIPDevice

connection = {
    "host": "192.168.1.10",
    "port": 44818,
    "route_path": {"hops": [{"type": "backplane", "slot": 0}]},
}

plc = EtherNetIPDevice("compactlogix.json", connection=connection, autostart=True)
plc.read_tag("line_speed")
plc.write_tag("line_speed", 1200.0)
plc.close()
```

## Current scope

| Area | Supported today |
|------|-----------------|
| Tested PLC | Allen-Bradley CompactLogix 5332E 1769-L32E |
| Transport | EtherNet/IP explicit messaging over TCP |
| Route paths | Direct connection or local backplane slot hops only |
| Polling | Automatic batched reads for `poll: true` scalar tags |
| Streaming values | Boolean and numeric scalar tags |
| Manual native operations | Single-tag reads, batched reads, and writes |
| Unsigned integer validation | `usint`, `uint`, `udint`, and `ulint` are implemented, but not validated |
| Tag discovery | Not supported |
| UDTs | Not supported in the config-driven API |
| Arrays | Not supported in the config-driven API |

## JSON config reference

### Connection

Provide the connection in the config or pass it to the `EtherNetIPDevice` constructor. The constructor
parameter takes precedence, so one tag map can target multiple environments.

=== "Constructor"

    ```python
    plc = EtherNetIPDevice(
        "compactlogix.json",
        connection={
            "host": "192.168.1.10",
            "port": 44818,
            "route_path": {"hops": [{"type": "backplane", "slot": 0}]},
        },
    )
    ```

=== "In config"

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

Route paths accept local backplane hops only:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `hops` | list | `[]` | Ordered local backplane hops |
| `hops[].type` | string | *required* | Must be `"backplane"` |
| `hops[].slot` | int | *required* | Backplane slot number, 0-255 |

Network hops to another PLC, remote chassis, or IP address are not supported. The schema accepts
multiple local backplane hops, but current testing has covered one backplane hop.

### Timing

The `timing` section controls background polling:

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

When polling is running, `EtherNetIPDevice` reads every `poll: true` tag in one batched native
request at the configured interval. A per-tag failure skips that tag for the current measurement;
successful values from the same batch are still published.

### Tags

Each tag entry defines one named PLC tag:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `alias` | string | *required* | Alias used in `read_tag()` and `write_tag()` |
| `tag_name` | string | *required* | PLC tag name |
| `description` | string | `null` | Optional description |
| `data_type` | string | *required* | Expected PLC scalar type |
| `poll` | bool | `true` | Include in background polling |
| `write_min` | number | `null` | Minimum allowed write value for numeric tags |
| `write_max` | number | `null` | Maximum allowed write value for numeric tags |

Supported `data_type` values:

| Data type | Streamable | Notes |
|-----------|------------|-------|
| `bool` | Yes | Published as numeric `0` or `1` |
| `sint` | Yes | 8-bit signed integer |
| `int` | Yes | 16-bit signed integer |
| `dint` | Yes | 32-bit signed integer |
| `lint` | Yes | 64-bit signed integer |
| `usint` | Yes | 8-bit unsigned integer. Implemented, but not validated |
| `uint` | Yes | 16-bit unsigned integer. Implemented, but not validated |
| `udint` | Yes | 32-bit unsigned integer. Implemented, but not validated |
| `ulint` | Yes | 64-bit unsigned integer. Implemented, but not validated |
| `real` | Yes | 32-bit floating point |
| `lreal` | Yes | 64-bit floating point |

PLC string tags are not part of the Python EtherNet/IP API.

### Native batched reads

`instro.unstable._ethernetip.EtherNetIpSession.read_tags()` reads several PLC tags in one native
request and preserves input order:

```python
from instro.unstable._ethernetip import EtherNetIpBatchError, EtherNetIpSession

with EtherNetIpSession("192.168.1.10:44818", route_path_slots=[0]) as session:
    for name, result in session.read_tags(["MotorRunning", "LineSpeed"]):
        if isinstance(result, EtherNetIpBatchError):
            print(name, result)
            continue
        print(name, result.kind, result.value)
```

The call raises `EtherNetIpError` when the whole batch cannot be dispatched or parsed. Individual
tag failures are returned as typed `EtherNetIpBatchError` instances, including `TagNotFoundError`,
`DataTypeMismatchError`, `NetworkBatchError`, `CipError`, `TagPathError`, `SerializationError`,
`BatchTimeoutError`, and `OtherBatchError`.

### Write limits

Reject writes outside the configured range before they reach the PLC:

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
plc.write_tag("line_speed", 1200.0)  # OK
plc.write_tag("line_speed", 9999.0)  # raises ValueError: above write_max (2500.0)
```

`EtherNetIPDevice` checks limits before sending the write to the PLC.

## Validation rules

- `protocol` must be `"ethernetip"`.
- Tag aliases must be unique.
- Every tag must declare `data_type`.
- `write_min` and `write_max` are only valid for numeric tags.
- `write_min` must be less than or equal to `write_max`.
- Integer write limits must fit in the configured PLC integer type.
- Route path hops must use `type: "backplane"` with `slot` from 0 to 255.
- Tag discovery, UDTs, and arrays are not supported.

## API reference

### EtherNetIPDevice

::: instro.unstable.ethernetip.ethernetip.EtherNetIPDevice

### Configuration types

::: instro.unstable.ethernetip.ethernetip_types
    options:
      members:
        - EtherNetIPConfig
        - TimingConfig
        - EtherNetIPConnectionInfo
        - EtherNetIPRoutePath
        - EtherNetIPBackplaneHop
        - TagDef
