# instro-opcua

OPC UA protocol primitives in `instro.unstable.opcua.types`.

Anonymous client connection and discovery are available from `instro.unstable.opcua`.

Discovery and connection use `instro/opcua` under your local data directory for the certificate store.
On Linux the base directory is `$XDG_DATA_HOME` or `~/.local/share`.
On macOS it is `~/Library/Application Support`, and on Windows it is `%LOCALAPPDATA%`.
If the operating system provides no local data directory, the client uses `./pki`.
Pass `pki_dir` to `get_endpoints()` to use a different certificate store for discovery.
