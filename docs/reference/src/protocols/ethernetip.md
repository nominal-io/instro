# EtherNet/IP (unstable)

`EtherNetIPDevice` is an unstable, config-driven EtherNet/IP client for PLC tag access.
It lives in the separate `instro-unstable` package and is imported from
`instro.unstable.ethernetip`.

Runtime hardware access also requires the native `instro-ethernetip-python` package.
The native package is loaded when `EtherNetIPDevice.open()` creates a session.

```python
from instro.unstable.ethernetip import EtherNetIPDevice

connection = {"host": "192.168.1.10", "port": 44818}
device = EtherNetIPDevice("my_plc.json", connection=connection, autostart=True)
device.read_tag("temperature")
device.write_tag("setpoint", 75.5)
device.close()
```

Or build the config in code with typed models:

```python
from instro.lib.types import DeviceInfo
from instro.unstable.ethernetip import (
    EtherNetIPConfig,
    EtherNetIPConnectionInfo,
    EtherNetIPDevice,
    TagDef,
    TimingConfig,
)

config = EtherNetIPConfig(
    device=DeviceInfo(name="my_plc"),
    timing=TimingConfig(poll_interval=1.0),
    tags=[
        TagDef(alias="temperature", tag_name="ProcessTemperature", data_type="real"),
        TagDef(alias="setpoint", tag_name="TemperatureSetpoint", data_type="real", poll=False),
    ],
)
connection = EtherNetIPConnectionInfo(host="192.168.1.10")
device = EtherNetIPDevice(config, connection=connection, autostart=True)
```

## Sample Config

```json
--8<-- "examples/ethernetip/sample_device.json"
```

## API Reference

### EtherNetIPDevice

::: instro.unstable.ethernetip.EtherNetIPDevice

### Configuration Types

::: instro.unstable.ethernetip.ethernetip_types
    options:
      members:
        - EtherNetIPConfig
        - EtherNetIPConnectionInfo
        - EtherNetIPRoutePath
        - EtherNetIPBackplaneHop
        - TagDef
        - TimingConfig
