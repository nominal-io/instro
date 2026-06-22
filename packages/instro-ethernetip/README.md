# instro-ethernetip

Python binding layer for the Rust EtherNet/IP client.

This is surfaced to Python as the private native module `instro.unstable._ethernetip`, while remaining intentionally excluded from the stable `instro[all]` extra. Published users install it through the optional `instro-unstable[ethernetip]` extra.

It exists separately from `instro-ethernetip-rs` on purpose:

- `instro-ethernetip-rs` is the core Rust library and should stay usable from pure Rust code without pulling in PyO3 or Python packaging concerns.
- `instro-ethernetip` is the optional Python-facing wrapper built with PyO3 and `maturin`.

That split keeps the Rust crate focused on its transport and value API while allowing Python packaging, type stubs, and extension-module details to evolve independently.

## Layout

- `src/lib.rs`: PyO3 module entrypoint
- `src/sync_session.rs`: synchronous `EtherNetIpSession` API
- `src/values.rs`: `PlcValue`, `PlcKind`, `StructuredValue`, and Python/Rust value conversions
- `src/errors.rs`: shared Python exception mapping
- `instro/unstable/_ethernetip.pyi`: typing stub for the private native module
- `instro/py.typed`: marker that makes the package's type information visible to type checkers
- `pyproject.toml`: `maturin` packaging configuration

## Relationship to the Rust crate

The Python wrapper depends on `instro-ethernetip-rs` and translates between:

- Python classes such as `EtherNetIpSession`, `PlcValue`, `PlcKind`, and `StructuredValue`
- the Rust crate's `blocking::ExplicitSession`, `Value`, and `StructuredValue` API

The Rust crate remains the source of truth for EtherNet/IP behavior and owns the private shared
Tokio runtime used by the synchronous wrapper. The PyO3 layer remains responsible for releasing
the GIL around blocking calls and translating values and exceptions into Python types.

`EtherNetIpSession.read_tag()` returns `PlcValue` rather than a bare Python scalar so callers can
preserve the PLC scalar kind, such as `DINT` versus `UDINT` or `REAL` versus `LREAL`. The
`PlcValue.value` property exposes the Python payload, and `PlcValue.kind` exposes the corresponding
`PlcKind`.

`EtherNetIpSession.read_tags()` reads several tags in one batched request and returns one
`(name, result)` tuple per requested tag. Successful results are `PlcValue` instances. Per-tag
failures are typed `EtherNetIpBatchError` instances, so one missing or mismatched tag does not
discard successful reads from the same batch.

`EtherNetIpSession.write_tag()` accepts:

- `PlcValue` for explicit scalar and structured writes
- `StructuredValue`, which is promoted to a structured PLC value
- `bool`, which maps unambiguously to PLC `BOOL`

It intentionally rejects bare Python `int`, `float`, `str`, `bytes`, and `bytearray` values. Use the
appropriate `PlcValue.*(...)` constructor for numeric writes and `StructuredValue(data=...)` for
raw structured bytes.

## Type stubs

The `.pyi` file describes the Python API for type checkers, editors, and tests. It is not executed
at runtime.

- `instro/unstable/_ethernetip.pyi` belongs to this package and describes the private
  compiled extension module built from Rust. The runtime `_ethernetip` module is a PyO3 native
  module, so this stub is where Python tooling learns about `EtherNetIpSession`, `PlcKind`,
  `PlcValue`, `StructuredValue`, `EtherNetIpError`, method signatures, properties, and value
  aliases.
- `instro/py.typed` marks the package as typed so consumers and tests can see the stub
  when the package is installed.

When the PyO3 API changes, keep the Rust exports and `_ethernetip.pyi` in sync.

## Build, CI, and packaging

`instro-ethernetip` is a PyO3 extension crate packaged by `maturin`.

- `pyo3` exposes Rust types and functions as Python classes in the private `instro.unstable._ethernetip` module
- `maturin` drives the Python packaging step and turns the Rust crate into a Python wheel
- the crate is built as a C-compatible dynamic library (`cdylib`), which the wheel includes.
  - It's this dynamic library that's loaded by Python at import time to interface with Rust code.
  - On disk, the dynamic library is a platform-specific Python extension module, such as
    `.abi3.so` on Linux and macOS or `.pyd` on Windows.
- the wheel is ABI-stable across CPython 3.10+ because the crate uses PyO3's `abi3-py310` mode, which is a good fit because Connect already depends on Python 3.10+
  - concretely, this means we don't need to build each wheel for different python versions (3.10, 3.11, 3.12, etc).

At build time, `maturin` compiles the Rust crate, places the resulting shared library in the wheel
as the private `instro.unstable._ethernetip` extension module, and includes `_ethernetip.pyi`
and `py.typed` for typing.

For local verification on your current machine:

- `just eip-build` builds both an sdist and a wheel for the current host platform into `dist/`
- `just eip-wheel-smoke-test` builds the wheel, installs it into an isolated environment, and verifies that `instro.unstable._ethernetip` imports successfully
- `just eip-test` runs the EtherNet/IP-specific flow: wheel import smoke test, Rust formatting,
  linting, library tests, doc tests, and Python binding tests in editable mode
- `just test` runs `just eip-test` and then the broader Python test suite, including the local
  type-stub boundary tests

## Distribution and platform coverage

EtherNet/IP support is exposed as the optional `ethernetip` extra on `instro-unstable`.
Bare `instro-unstable` installs remain pure Python, so other unstable modules can import
without resolving a native wheel.

- `instro-unstable[ethernetip]` depends on `instro-ethernetip`
- the root dev dependency group includes `instro-ethernetip`, so `uv sync` builds and installs the local PyO3 package for development
- `just eip-wheel-smoke-test` can still build a local wheel and verify the private native module against that wheel

The release workflow builds platform-specific wheels for:

- Linux `x86_64` and `aarch64`
- macOS `x86_64` and `aarch64`
- Windows `x86_64`

The release workflow also publishes a source distribution. The sdist includes the Rust
source for both `instro-ethernetip` and `instro-ethernetip-rs`, plus the Cargo
manifests and lockfile, so source builds do not depend on unpublished repository files.

Development setup:

```bash
uv sync
```

After `uv sync`, the local native package is available from its private module path.

## Import path and runtime loading

Local development currently imports the private native module directly:

```python
from instro.unstable._ethernetip import EtherNetIpSession, PlcValue, StructuredValue
```

Under the hood:

- `instro.unstable._ethernetip` is a private local module owned by this unpublished package
- importing `instro.unstable._ethernetip` loads the compiled PyO3 extension module from the installed package
- the module initializer registers the Rust-backed Python classes, and method calls then execute in Rust against `instro-ethernetip-rs`
