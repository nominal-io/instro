# instro-opcua

Rust OPC UA client utilities for browsing nodes, reading values, and polling or subscribing to samples.

This crate is the pure-Rust OPC UA core for instro. It wraps `open62541` with instro-owned types for connection configuration, node metadata, read batches, and sample values.

## Installation

```toml
[dependencies]
anyhow = "1"
instro-opcua = "0.1"
open62541 = { version = "0.10", features = ["mbedtls", "x509"] }
tokio = { version = "1", features = ["macros", "rt-multi-thread"] }
```

`instro-opcua` depends on `open62541`/`open62541-sys`, which build native C dependencies. Consumers need a C compiler, CMake, and LLVM/libclang available during builds.

## Usage

```rust
use instro_opcua::browse::BrowseAll as _;
use instro_opcua::client::{OpcUaClientBuilder, OpcUaNodeReadBatch};
use instro_opcua::types::{
    OpcUaNodeId, OpcUaPki, OpcUaSecurityMode, OpcUaSecurityPolicy, OpcUaUserToken,
};
use open62541::ua;

fn main() -> anyhow::Result<()> {
    let client = OpcUaClientBuilder::new()
        .user_identity_token(OpcUaUserToken::anonymous("anonymous".to_owned())?)
        .security_mode(OpcUaSecurityMode::None)
        .security_policy(OpcUaSecurityPolicy::None)
        .pki(OpcUaPki::None)
        .connect("opc.tcp://127.0.0.1:4840")?;

    let node_id: OpcUaNodeId = "ns=0;i=85".parse()?;
    let nodes = client.browse_all(node_id, Some(2));

    let runtime = tokio::runtime::Runtime::new()?;
    let nodes = runtime.block_on(nodes)?;
    let batch = OpcUaNodeReadBatch::new(nodes, ua::AttributeId::VALUE);
    let samples = runtime.block_on(client.read_nodes(&batch))?;

    println!("read {} samples", samples.len());
    runtime.block_on(client.disconnect())
}
```

## Development

The integration harness depends on monorepo-only test helper crates and is excluded from the crates.io archive. Run the full harness from the repository root with:

```bash
cargo test -p instro-opcua --test client_harness
```
