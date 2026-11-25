# instro-opcua

Synchronous OPC UA client primitives for [instro](https://github.com/nominal-io/instro).
Python 3.11 through 3.14 are supported. The API lives under `instro.unstable.opcua`.

Use `instro.unstable.opcua` to discover endpoints and connect to a server.
Connections use anonymous authentication by default. Pass an `IdentityToken`
to authenticate with a username or certificate.

Discovery and connection use `instro/opcua` under your local data directory for the certificate store.
On Linux the base directory is `$XDG_DATA_HOME` or `~/.local/share`.
On macOS it is `~/Library/Application Support`, and on Windows it is `%LOCALAPPDATA%`.
If the operating system provides no local data directory, the client uses `./pki`.
Pass `pki_dir` to `get_endpoints()` to use a different certificate store for discovery.

```bash
pip install instro-opcua
```

```python
from instro.unstable.opcua import OpcUaClient

with OpcUaClient.connect("opc.tcp://localhost:4840") as client:
    clock = client.objects.get_child("/Server/ServerStatus/CurrentTime")
    sample = clock.value
    if sample.status.is_good:
        print(sample.value, sample.source_unix_timestamp)
    else:
        print(sample.status.name())
```

The [client guide](https://instro.nominal.io/library/protocols/opcua/) covers
authentication, navigation, reads, writes, and subscriptions. See the
[API reference](https://instro.nominal.io/ref/protocols/opcua/) for supported
types and connection settings. If your server recreates subscriptions during
reconnect, see the [known cleanup defect](https://instro.nominal.io/library/protocols/opcua/#recovery-and-shutdown).

Development setup and checks are documented in
[CONTRIBUTING.md](https://github.com/nominal-io/instro/blob/main/CONTRIBUTING.md).
