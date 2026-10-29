# instro-opcua

Rust OPC UA client utilities for browsing nodes, reading values, and polling or subscribing to samples.

This crate is the pure-Rust OPC UA core for instro. It wraps `open62541` with
instro-owned connection configuration, node metadata, and read batch types while
exposing native `open62541::ua` types for OPC UA values.

## Installation

```toml
[dependencies]
anyhow = "1"
instro-opcua = "0.1"
tokio = { version = "1", features = ["macros", "rt-multi-thread"] }
```

`instro-opcua` depends on `open62541`/`open62541-sys`, which build native C dependencies. Consumers need a C compiler, CMake, and LLVM/libclang available during builds.

## Usage

```rust
use instro_opcua::prelude::*;
use tokio::runtime::Runtime;

fn main() -> anyhow::Result<()> {
    let client = ClientBuilder::new()
        .with_identity_token(
            &ua::UserIdentityToken::Anonymous(ua::AnonymousIdentityToken::init())
        )
        .with_security_mode(ua::MessageSecurityMode::NONE)
        .with_security_policy(SecurityPolicyUri::none())
        .with_pki(PkiConfig::None)
        .connect("opc.tcp://127.0.0.1:4840")?;

    let node_id: ua::NodeId = "ns=0;i=85".parse()?;
    let nodes = client.browse_all(node_id, Some(2), None);

    let runtime = Runtime::new()?;
    let nodes = runtime.block_on(nodes)?;
    let node_ids: Vec<_> = nodes.into_iter().map(|node| node.node_id).collect();
    let batch = NodeReadBatch::new_cartesian(&node_ids, &[ua::AttributeId::VALUE]);
    let samples = runtime.block_on(client.read_nodes(&batch))?;

    println!("read {} samples", samples.count());
    runtime.block_on(client.disconnect())
}
```

## Development

The integration harness depends on monorepo-only test helper crates and is excluded from the crates.io archive. Run the full harness from the repository root with:

```bash
cargo test -p instro-opcua --test client_harness
```
