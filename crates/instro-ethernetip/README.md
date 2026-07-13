# instro-ethernetip

Rust EtherNet/IP explicit messaging for simple PLC tag reads and writes.

This crate is the pure-Rust core used by the Python `instro-ethernetip` package. It can also be used directly from Rust without PyO3 or Python packaging dependencies.

## Installation

```toml
[dependencies]
instro-ethernetip = "0.1"
tokio = { version = "1", features = ["macros", "rt-multi-thread"] }
```

Enable the `blocking` feature for the synchronous wrapper:

```toml
[dependencies]
instro-ethernetip = { version = "0.1", features = ["blocking"] }
```

## Async Usage

```rust
use instro_ethernetip::{ExplicitSession, Result, Value};

#[tokio::main]
async fn main() -> Result<()> {
    let mut session = ExplicitSession::connect("192.168.1.10:44818").await?;

    let running = session.read_tag("MotorRunning").await?;
    println!("MotorRunning = {running:?}");

    session.write_tag("CommandSpeed", Value::Dint(1500)).await?;
    session.close().await
}
```

## Blocking Usage

```rust
use instro_ethernetip::blocking::ExplicitSession;
use instro_ethernetip::{Result, Value};

fn main() -> Result<()> {
    let mut session = ExplicitSession::connect("192.168.1.10:44818")?;
    session.write_tag("CommandSpeed", Value::Dint(1500))?;
    session.close()
}
```

The blocking API uses a shared private Tokio runtime. Do not call it from inside async Tokio code; use `ExplicitSession` directly there.

## Development

The repository integration test uses a Python simulator from the monorepo checkout, so it is excluded from the crates.io archive. Run it from the repository root with:

```bash
cargo test -p instro-ethernetip --test explicit_session_integration
```
