# instro-opcua

Rust OPC UA client utilities for browsing nodes, reading values, and polling or subscribing to samples.

This crate is the pure-Rust OPC UA core for instro. It wraps `open62541` with instro-owned types for connection configuration, node metadata, read batches, and sample values.

## Installation

```toml
[dependencies]
anyhow = "1"
instro-opcua = "1"
open62541 = { version = "0.12", features = ["mbedtls", "x509"] }
tokio = { version = "1", features = ["macros", "rt-multi-thread"] }
```

`instro-opcua` depends on `open62541`/`open62541-sys`, which build native C dependencies. Consumers need a C compiler, CMake, and LLVM/libclang available during builds.

## Usage

```rust
use instro_opcua::browse::OpcUaBrowseOptions;
use instro_opcua::client::{OpcUaClientBuilder, OpcUaNodeReadBatch};
use instro_opcua::types::{
    OpcUaPki, OpcUaSecurityMode, OpcUaSecurityPolicy, OpcUaUserToken,
};
use open62541::ua;

fn main() -> anyhow::Result<()> {
    let client = OpcUaClientBuilder::new()
        .user_identity_token(OpcUaUserToken::anonymous("anonymous".to_owned())?)
        .security_mode(OpcUaSecurityMode::None)
        .security_policy(OpcUaSecurityPolicy::None)
        .pki(OpcUaPki::None)
        .connect("opc.tcp://127.0.0.1:4840")?;

    let runtime = tokio::runtime::Runtime::new()?;
    let graph = runtime.block_on(client.browse_root(OpcUaBrowseOptions::new()))?;
    let variables = graph
        .find_all("/Objects/**/{Temperature,Pressure*}")?
        .variables()
        .leaves();
    let batch = OpcUaNodeReadBatch::new(variables.read_targets(), ua::AttributeId::VALUE);
    let outcomes = runtime.block_on(client.read_nodes(&batch))?;
    let samples = outcomes.into_iter().collect::<anyhow::Result<Vec<_>>>()?;

    println!("read {} samples", samples.len());
    runtime.block_on(client.disconnect())
}
```

`browse_root` eagerly returns one immutable route graph including the standard Root route at `/`. Queries remain tied to that graph and are reusable: `routes()` can be iterated repeatedly, while `find_all`, `variables`, and `leaves` return another reusable selection. Glob segments use globset's full grammar; a whole `**` segment recursively matches zero or more route segments.

All browse paths are rooted and absolute. `browse_path(path, options)` resolves every matching route from `/`, while `browse_from(node_id, path, options)` reads the node metadata and requires the asserted mount path to end in its actual browse name. `/` is reserved for the standard Root node; empty and relative paths are rejected when parsed.

All forward hierarchical references are expanded regardless of node class. Diamonds, duplicate node IDs, duplicate paths, and server order are preserved as routes, while each NodeId's complete continuation-drained child snapshot is fetched once per operation. An ancestry cycle inserts a terminal `Cycle` route and continues with siblings. Routes stopped by `max_depth` are `DepthLimited`; only fully `Expanded` routes with no children are leaves.

Browse operations have no hidden route or fetch ceiling. Use `OpcUaBrowseOptions::with_max_depth` when the caller needs an explicit limit.

## Development

The integration harness depends on monorepo-only test helper crates and is excluded from the crates.io archive. Run the full harness from the repository root with:

```bash
cargo test -p instro-opcua --test client_harness
```
