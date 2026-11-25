# OPC UA device

Use `OpcUaDevice` to manage a configured OPC UA connection and check that the server
can resolve the browse paths your application expects. Save the configuration as
JSON, pass a dict, or construct an `OpcUaDeviceConfig` in Python.

```json
{
  "connection": {
    "endpoint_url": "opc.tcp://localhost:4840",
    "request_timeout_s": 5
  },
  "nodes": {
    "setpoint": "/Objects/2:Setpoint"
  }
}
```

Save the configuration as `line_1.json`, then open the device to read the setpoint.

```python
from instro.unstable.opcua import OpcUaDevice

with OpcUaDevice("line_1", "line_1.json") as device:
    setpoint = device.client.get_node("/Objects/2:Setpoint")
    sample = setpoint.value
    if sample.status.is_good:
        print(sample.value)
```

Construction validates the configuration without connecting. Opening the device
connects and resolves all configured browse paths from RootFolder in one request.
The names in `nodes` identify config entries. Use the browse path on `device.client`
to get a node handle. The `client` property is available while the device is open.
Use it for browsing, reading, and subscriptions, and use native node handles for
writes. If path resolution fails, opening the device disconnects the client and
reports the error.

The `nodes` field accepts string paths. A path such as `/Objects/12:Setpoint`
can pass validation with `12:Setpoint` interpreted as a name in namespace 0.
For namespace indexes of 10 or higher, omit that entry from `nodes` and
pass a [structured path](/library/protocols/opcua.md#construct-paths-with-multi-digit-namespace-indexes)
to `device.client.get_node()` after opening the device.

Reading a node returns a sample. To send that data to a publisher, pass
publishers to the `OpcUaDevice` constructor, create a `Measurement` from the
sample, and call `device.publish(measurement)`. See [publishers](/library/publishers.md).

## Connection settings

The default connection uses anonymous authentication. To authenticate with a
username, set `authentication` to an object with `mode` set to `username` and
supply `username` and `password_env`. The device reads that environment variable
when it opens. Username authentication requires `security_mode` set to
`SignAndEncrypt` and a matching `security_policy`, such as `Basic256Sha256`.

Use `pki_dir` for the application's PKI directory. When specifying an application
certificate, provide both `certificate_path` and `private_key_path`. Relative
certificate and key paths resolve under `pki_dir`. Server certificate verification
uses the native client's defaults. Set `request_timeout_s` to a positive number of
seconds to override the 60-second request timeout.

## API reference

```{eval-rst}
.. autosummary::
   :toctree: .
   :nosignatures:

   ~instro.unstable.opcua.OpcUaDevice
   ~instro.unstable.opcua.OpcUaDeviceConfig
   ~instro.unstable.opcua.OpcUaConnectionConfig
   ~instro.unstable.opcua.OpcUaAnonymousAuth
   ~instro.unstable.opcua.OpcUaUsernameAuth
```
