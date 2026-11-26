# instro-opcua

OPC UA protocol primitives in `instro.unstable.opcua.types`.

Use `instro.unstable.opcua` to discover endpoints and connect to a server.
Connections use anonymous authentication by default. Pass an `IdentityToken`
to authenticate with a username or certificate.

Discovery and connection use `instro/opcua` under your local data directory for the certificate store.
On Linux the base directory is `$XDG_DATA_HOME` or `~/.local/share`.
On macOS it is `~/Library/Application Support`, and on Windows it is `%LOCALAPPDATA%`.
If the operating system provides no local data directory, the client uses `./pki`.
Pass `pki_dir` to `get_endpoints()` to use a different certificate store for discovery.

Use Nodes for navigation, attribute access, and ordered batch reads.

Subscriptions retain notifications and support optional fallback polling.
