//! Builder for [`TestServer`].
//!
//! The builder collects declarative specs and translates them into open62541
//! server calls in [`start()`][TestServerBuilder::start].

use std::collections::BTreeMap;
use std::sync::Arc;
use std::sync::atomic::AtomicBool;
use std::sync::atomic::Ordering;
use std::thread;
use std::thread::sleep;
use std::time::Duration;
use std::time::Instant;

use anyhow::Context as _;
use anyhow::Result;
use anyhow::anyhow;
use anyhow::bail;
use open62541::Attributes as _;
use open62541::ObjectNode;
use open62541::Server;
use open62541::ServerBuilder;
use open62541::VariableNode;
use open62541::ua;
use open62541::ua::AccessLevelType;
use open62541_sys::UA_NS0ID_BASEDATAVARIABLETYPE;
use open62541_sys::UA_NS0ID_FOLDERTYPE;
use open62541_sys::UA_NS0ID_OBJECTSFOLDER;
use open62541_sys::UA_NS0ID_ORGANIZES;
use open62541_sys::UA_NS0ID_SERVER;

use super::FolderSpec;
use super::MethodSpec;
use super::ParentRef;
use super::TestNodeId;
use super::TestServer;
use super::ValueSource;
use super::VariableSpec;

/// How long to wait for the test server to start up and be ready for reads/writes.
pub const LIFETIME_TIMEOUT: Duration = Duration::from_secs(2);

const DEFAULT_NAMESPACE_URI: &str = "urn:nominal:opcua-test";
/// First numeric id handed out by [`TestNodeId::Auto`]. Picked high enough to
/// avoid collision with hand-rolled `TestNodeId::Numeric(...)` ids that tests
/// commonly choose (e.g. `1000`, `2000`).
const AUTO_ID_START: u32 = 50_000;
/// Loopback IP we bind the test server to and report in the cached endpoint
/// URL. We avoid `localhost` because it can resolve to an IPv6 address that
/// the OPC UA listener isn't bound to, producing flaky `BadConnectionRejected`
/// errors.
const LOOPBACK_HOST: &str = "127.0.0.1";

pub struct TestServerBuilder {
    port: Option<u16>,
    namespace_uri: String,
    folders: Vec<FolderSpec>,
    variables: Vec<VariableSpec>,
    methods: Vec<MethodSpec>,
}

impl Default for TestServerBuilder {
    fn default() -> Self {
        Self {
            port: None,
            namespace_uri: DEFAULT_NAMESPACE_URI.to_owned(),
            folders: Vec::new(),
            variables: Vec::new(),
            methods: Vec::new(),
        }
    }
}

impl TestServerBuilder {
    pub fn new() -> Self {
        Self::default()
    }

    /// Pin the listening port. By default the builder uses `port(0)` so the OS
    /// picks an ephemeral port and concurrent tests don't collide.
    #[must_use]
    pub fn port(mut self, port: u16) -> Self {
        self.port = Some(port);
        self
    }

    #[must_use]
    pub fn namespace_uri(mut self, uri: impl Into<String>) -> Self {
        self.namespace_uri = uri.into();
        self
    }

    #[must_use]
    pub fn add_folder(mut self, spec: FolderSpec) -> Self {
        self.folders.push(spec);
        self
    }

    #[must_use]
    pub fn add_variable(mut self, spec: VariableSpec) -> Self {
        self.variables.push(spec);
        self
    }

    #[must_use]
    pub fn add_method(mut self, spec: MethodSpec) -> Self {
        self.methods.push(spec);
        self
    }

    /// One-call shortcut for the common case: a `ReadWrite`, `Stored`-source
    /// variable parented to `ObjectsFolder`.
    #[must_use]
    pub fn variable(
        self,
        id: impl Into<TestNodeId>,
        browse_name: impl Into<String>,
        value: ua::Variant,
    ) -> Self {
        self.add_variable(VariableSpec::new(id, browse_name).value(value))
    }

    pub fn start(self) -> Result<TestServer> {
        if !self.methods.is_empty() {
            #[expect(
                clippy::todo,
                reason = "intentional placeholder for behavior to land separately"
            )]
            {
                todo!("method nodes are not yet wired through to open62541");
            }
        }

        // specify host since the open62541 default of mac otherwise won't resolve correctly
        let port = self.port.unwrap_or(0);
        let server_url = format!("opc.tcp://{LOOPBACK_HOST}:{port}");
        let (server, runner) = ServerBuilder::default()
            .server_urls(&[&server_url])
            .accept_all()
            .build();

        let namespace_index = server.add_namespace(&self.namespace_uri);

        let mut registrar = Registrar::new(server, namespace_index);

        for folder in &self.folders {
            registrar
                .register_folder(folder)
                .with_context(|| format!("registering folder `{}`", folder.browse_name))?;
        }

        for var in &self.variables {
            registrar
                .register_variable(var)
                .with_context(|| format!("registering variable `{}`", var.browse_name))?;
        }

        let (server, nodes) = registrar.finish();

        let cancel = Arc::new(AtomicBool::new(false));
        let cancel_chk = Arc::clone(&cancel);

        let runner_thread = thread::Builder::new()
            .name("opcua-test-server".to_owned())
            .spawn(move || runner.run_until_cancelled(|| cancel_chk.load(Ordering::Acquire)))
            .context("spawning opcua-test-server runner thread")?;

        let runner_thread_handle = runner_thread;

        // `discovery_urls()` blocks until startup completes (or fails) and
        // returns `None` if the server failed to bind. We read the discovery
        // URL primarily to recover the OS-assigned port when `port == 0` —
        // the host portion is already `127.0.0.1` because we bound there
        // explicitly above.
        let endpoint_url = match server.discovery_urls() {
            Some(urls) => match urls.iter().next().and_then(ua::String::as_str) {
                Some(url) => url.to_owned(),

                None => {
                    cancel.store(true, Ordering::Release);
                    shutdown_runner_on_startup_failure(runner_thread_handle, "<unknown>");
                    bail!("test server reported empty discovery URL list");
                }
            },

            None => {
                cancel.store(true, Ordering::Release);
                shutdown_runner_on_startup_failure(runner_thread_handle, "<unknown>");
                bail!("test server failed to start (no discovery URLs available)");
            }
        };

        Ok(TestServer::from_running(
            endpoint_url,
            namespace_index,
            self.namespace_uri,
            nodes,
            server,
            cancel,
            runner_thread_handle,
        ))
    }
}

/// Best-effort teardown when `start()` fails after spawning the runner thread:
/// wait briefly for the cancel flag to take effect, otherwise log and detach.
fn shutdown_runner_on_startup_failure(
    handle: thread::JoinHandle<open62541::Result<()>>,
    endpoint_url: &str,
) {
    let deadline = Instant::now() + LIFETIME_TIMEOUT;

    while !handle.is_finished() && Instant::now() < deadline {
        sleep(Duration::from_millis(10));
    }

    if handle.is_finished() {
        let _ = handle.join();
    } else {
        tracing::warn!(
            endpoint = %endpoint_url,
            "test server runner did not exit after startup failure; detaching",
        );
    }
}

/// Owns the open62541 [`Server`] handle while builder specs are translated
/// into address-space nodes. Each `register_*` call allocates an auto-id
/// (wasted for `Numeric`/`String` variants, harmless given the ~4B range from
/// [`AUTO_ID_START`]) and updates the browse-name lookup map.
struct Registrar {
    server: Server,
    namespace_index: u16,
    allocator: AutoIdAllocator,
    nodes: BTreeMap<String, ua::NodeId>,
}

impl Registrar {
    fn new(server: Server, namespace_index: u16) -> Self {
        Self {
            server,
            namespace_index,
            allocator: AutoIdAllocator::new(),
            nodes: BTreeMap::new(),
        }
    }

    fn register_folder(&mut self, spec: &FolderSpec) -> Result<()> {
        let parent_node_id = resolve_parent(&spec.parent, &self.nodes)?;
        let auto_id = self.allocator.next_id();
        let requested_id = spec.id.resolve(self.namespace_index, auto_id);

        let mut attributes = ua::ObjectAttributes::default();
        let display_name = spec.display_name.as_deref().unwrap_or(&spec.browse_name);

        attributes = attributes.with_display_name(&localized_text(display_name)?);

        let object = ObjectNode {
            requested_new_node_id: Some(requested_id),
            parent_node_id,
            reference_type_id: ua::NodeId::ns0(UA_NS0ID_ORGANIZES),
            browse_name: qualified_name(self.namespace_index, &spec.browse_name)?,
            type_definition: ua::NodeId::ns0(UA_NS0ID_FOLDERTYPE),
            attributes,
        };

        let resolved = self
            .server
            .add_object_node(object)
            .map_err(|err| anyhow!("open62541 add_object_node: {err}"))?;

        if let Some(prev) = self.nodes.insert(spec.browse_name.clone(), resolved) {
            // Two specs sharing a browse name would silently shadow in the lookup map;
            // that's a programming error in the test, not a runtime condition.
            bail!(
                "folder browse name `{}` is declared more than once (previous: {:?})",
                spec.browse_name,
                prev
            );
        }

        Ok(())
    }

    fn register_variable(&mut self, spec: &VariableSpec) -> Result<()> {
        let parent_node_id = resolve_parent(&spec.parent, &self.nodes)?;
        let auto_id = self.allocator.next_id();
        let requested_id = spec.id.resolve(self.namespace_index, auto_id);

        let initial = match &spec.source {
            ValueSource::Stored(v) => v,
            ValueSource::DataSource(_) => {
                #[expect(
                    clippy::todo,
                    reason = "intentional placeholder for behavior to land separately"
                )]
                {
                    todo!("data-source variables are not yet wired through to open62541");
                }
            }
        };

        let data_type = match (spec.data_type.as_ref(), initial.type_id()) {
            (Some(explicit), _) => explicit.clone(),
            (None, Some(inferred)) => inferred.clone(),
            (None, None) => {
                bail!(
                    "variable `{}` has neither an explicit data_type nor an initial value to \
                     infer from",
                    spec.browse_name
                );
            }
        };

        let access: AccessLevelType = spec.access.as_level();

        let mut attributes = ua::VariableAttributes::default()
            .with_data_type(&data_type)
            .with_access_level(&access);

        let display_name = spec.display_name.as_deref().unwrap_or(&spec.browse_name);
        attributes = attributes.with_display_name(&localized_text(display_name)?);

        let variable = VariableNode {
            requested_new_node_id: Some(requested_id),
            parent_node_id,
            reference_type_id: ua::NodeId::ns0(UA_NS0ID_ORGANIZES),
            browse_name: qualified_name(self.namespace_index, &spec.browse_name)?,
            type_definition: ua::NodeId::ns0(UA_NS0ID_BASEDATAVARIABLETYPE),
            attributes,
        };

        let node_id = self
            .server
            .add_variable_node(variable)
            .map_err(|err| anyhow!("open62541 add_variable_node: {err}"))?;

        // Set the initial value. We do this even when access is WriteOnly so the
        // server has a defined slot — the access level still prevents reads.
        self.server.write_value(&node_id, initial).map_err(|err| {
            anyhow!(
                "open62541 write_value (initial) on `{}`: {err}",
                spec.browse_name
            )
        })?;

        if let Some(prev) = self.nodes.insert(spec.browse_name.clone(), node_id) {
            bail!(
                "variable browse name `{}` is declared more than once (previous: {:?})",
                spec.browse_name,
                prev
            );
        }

        Ok(())
    }

    fn finish(self) -> (Server, BTreeMap<String, ua::NodeId>) {
        (self.server, self.nodes)
    }
}

struct AutoIdAllocator {
    next: u32,
}

impl AutoIdAllocator {
    fn new() -> Self {
        Self::starting_at(AUTO_ID_START)
    }

    pub(crate) const fn starting_at(start: u32) -> Self {
        Self { next: start }
    }

    fn next_id(&mut self) -> u32 {
        let id = self.next;
        #[expect(
            clippy::expect_used,
            reason = "auto-ID allocator overflow is unreachable in practice (~4B allocations from \
                      AUTO_ID_START)"
        )]
        let next = id.checked_add(1).expect("auto-ID allocator overflow");
        self.next = next;
        id
    }
}

fn resolve_parent(parent: &ParentRef, nodes: &BTreeMap<String, ua::NodeId>) -> Result<ua::NodeId> {
    match parent {
        ParentRef::ObjectsFolder => Ok(ua::NodeId::ns0(UA_NS0ID_OBJECTSFOLDER)),
        ParentRef::Server => Ok(ua::NodeId::ns0(UA_NS0ID_SERVER)),
        ParentRef::Node(id) => Ok(id.clone()),
        ParentRef::Label(name) => nodes.get(name).cloned().ok_or_else(|| {
            anyhow!(
                "ParentRef::Label(`{name}`) does not match any earlier-declared folder/variable"
            )
        }),
    }
}

fn qualified_name(namespace_index: u16, browse_name: &str) -> Result<ua::QualifiedName> {
    if browse_name.contains('\0') {
        bail!("browse name `{browse_name}` contains NUL bytes");
    }

    Ok(ua::QualifiedName::new(namespace_index, browse_name))
}

fn localized_text(text: &str) -> Result<ua::LocalizedText> {
    ua::LocalizedText::new("en-US", text).map_err(|err| anyhow!("building LocalizedText: {err}"))
}

#[cfg(test)]
mod tests {
    use super::AUTO_ID_START;
    use super::AutoIdAllocator;

    #[test]
    fn new_starts_at_auto_id_start_constant() {
        let mut alloc = AutoIdAllocator::new();
        assert_eq!(alloc.next_id(), AUTO_ID_START);
    }

    #[test]
    fn next_id_is_monotonic() {
        let mut alloc = AutoIdAllocator::new();
        let mut prev = alloc.next_id();

        for _ in 0..16 {
            let cur = alloc.next_id();
            assert_eq!(cur, prev + 1, "ids should increase by exactly 1");
            prev = cur;
        }
    }

    #[test]
    #[should_panic(expected = "auto-ID allocator overflow")]
    fn overflow_panics() {
        let mut alloc = AutoIdAllocator::starting_at(u32::MAX);
        assert_eq!(alloc.next_id(), u32::MAX);
        let _ = alloc.next_id();
    }
}
