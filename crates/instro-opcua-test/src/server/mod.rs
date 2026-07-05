//! In-process OPC-UA test server primitives.
//!
//! This module wraps `open62541::Server` behind a declarative builder so that
//! tests at any layer (the low-level `opcua` crate, the higher-level
//! `connect-opcua` driver, future integration tests) can stand up a real
//! endpoint with named nodes and known values, without rolling their own
//! protocol implementation.
//!
//! The pattern is loosely modelled on `ads-rs`'s in-test server (lazy port,
//! background thread, drop-cleans-up), translated to OPC-UA's typed address
//! space.
//!
//! # Example
//!
//! ```no_run
//! use opcua_test::Access;
//! use opcua_test::TestNodeId;
//! use opcua_test::TestServer;
//! use opcua_test::ValueSource;
//! use opcua_test::VariableSpec;
//! use opcua_test::ua;
//!
//! let server = TestServer::builder()
//!     .variable(
//!         TestNodeId::Numeric(1000),
//!         "Temperature",
//!         ua::Variant::scalar(ua::Double::new(72.5)),
//!     )
//!     .add_variable(
//!         VariableSpec::new(TestNodeId::String("Pressure".into()), "Pressure")
//!             .access(Access::ReadOnly)
//!             .source(ValueSource::Stored(ua::Variant::scalar(ua::UInt32::new(101_325)))),
//!     )
//!     .start()
//!     .expect("test server starts");
//!
//! let endpoint = server.endpoint_url().to_owned();
//! // ... drive `endpoint` from a real OPC-UA client ...
//! drop(server); // cleanly stops the runner
//! # let _ = endpoint;
//! ```

mod builder;
mod spec;

use std::collections::BTreeMap;
use std::sync::Arc;
use std::sync::atomic::AtomicBool;
use std::sync::atomic::Ordering;
use std::thread;
use std::thread::sleep;
use std::time::Duration;
use std::time::Instant;

use anyhow::Result;
use anyhow::anyhow;
pub use builder::LIFETIME_TIMEOUT;
pub use builder::TestServerBuilder;
use open62541::Server;
use open62541::ua;
pub use spec::Access;
pub use spec::FolderSpec;
pub use spec::MethodSpec;
pub use spec::ParentRef;
pub use spec::TestNodeId;
pub use spec::ValueSource;
pub use spec::VariableSpec;

/// Handle to a running test OPC-UA server.
///
/// Construct via [`TestServer::builder`]. Drop to stop.
pub struct TestServer {
    /// Cached at construction time so repeated calls don't re-acquire the
    /// open62541 server lock. The endpoint URL is fixed for the server's
    /// lifetime - the bound port doesn't change after startup.
    endpoint_url: String,
    /// Cached because `node_id(...)` runs in tight test loops; re-querying
    /// open62541 on every lookup would be wasteful.
    namespace_index: u16,
    namespace_uri: String,
    nodes: BTreeMap<String, ua::NodeId>,
    server: Server,
    cancel: Arc<AtomicBool>,
    runner_thread: Option<thread::JoinHandle<open62541::Result<()>>>,
}

impl TestServer {
    pub fn builder() -> TestServerBuilder {
        TestServerBuilder::default()
    }

    pub(crate) fn from_running(
        endpoint_url: String,
        namespace_index: u16,
        namespace_uri: String,
        nodes: BTreeMap<String, ua::NodeId>,
        server: Server,
        cancel: Arc<AtomicBool>,
        runner_thread: thread::JoinHandle<open62541::Result<()>>,
    ) -> Self {
        Self {
            endpoint_url,
            namespace_index,
            namespace_uri,
            nodes,
            server,
            cancel,
            runner_thread: Some(runner_thread),
        }
    }

    /// `opc.tcp://<host>:<port>` — pass directly to a client builder.
    pub fn endpoint_url(&self) -> &str {
        &self.endpoint_url
    }

    /// Index of the namespace into which builder-declared nodes were placed.
    pub fn namespace_index(&self) -> u16 {
        self.namespace_index
    }

    /// URI of the namespace allocated for builder-declared nodes.
    pub fn namespace_uri(&self) -> &str {
        &self.namespace_uri
    }

    /// Look up a node by browse name.
    pub fn node_id(&self, browse_name: &str) -> Option<&ua::NodeId> {
        self.nodes.get(browse_name)
    }

    /// Push a new value to a previously declared `Stored` variable.
    ///
    /// The variable's data type must be compatible with the new variant — the
    /// open62541 server enforces this and surfaces any mismatch as an error.
    pub fn set_value(&self, node_id: &ua::NodeId, value: ua::Variant) -> Result<()> {
        self.server
            .write_value(node_id, &value)
            .map_err(|err| anyhow!("open62541 write_value: {err}"))
    }

    /// Add a folder while the server is running. Returns the resolved NodeId.
    ///
    /// Note: nodes added at runtime are not added to [`Self::node_id`]'s lookup
    /// map — callers should track the returned id themselves.
    pub fn add_folder(&self, _spec: FolderSpec) -> Result<ua::NodeId> {
        #[expect(
            clippy::todo,
            reason = "intentional placeholder for behavior to land separately"
        )]
        {
            todo!("runtime folder addition is not yet implemented");
        }
    }

    /// Add a variable while the server is running. Returns the resolved NodeId.
    pub fn add_variable(&self, _spec: VariableSpec) -> Result<ua::NodeId> {
        #[expect(
            clippy::todo,
            reason = "intentional placeholder for behavior to land separately"
        )]
        {
            todo!("runtime variable addition is not yet implemented");
        }
    }

    /// Add a method while the server is running. Returns the resolved NodeId.
    pub fn add_method(&self, _spec: MethodSpec) -> Result<ua::NodeId> {
        #[expect(
            clippy::todo,
            reason = "intentional placeholder for behavior to land separately"
        )]
        {
            todo!("method nodes are not yet wired through to open62541");
        }
    }
}

impl Drop for TestServer {
    #[expect(
        clippy::expect_used,
        reason = "test server panics on shutdown timeout or server shutdown error"
    )]
    #[expect(
        clippy::panic,
        reason = "test server panics on shutdown timeout or server shutdown error"
    )]
    fn drop(&mut self) {
        self.cancel.store(true, Ordering::Release);
        let handle = self
            .runner_thread
            .take()
            .expect("test server runner thread was not spawned");

        let deadline = Instant::now() + LIFETIME_TIMEOUT;

        while !handle.is_finished() && Instant::now() < deadline {
            sleep(Duration::from_millis(10));
        }

        if !handle.is_finished() {
            tracing::error!(
                endpoint = %self.endpoint_url,
                "test server shutdown timed out"
            );

            panic!("test server shutdown timed out");
        } else {
            handle
                .join()
                .expect("test server runner thread panicked")
                .expect("test server runner returned an error");
        }
    }
}
