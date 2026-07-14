//! Declarative specifications for the test server's address space.
//!
//! These types describe nodes _before_ a server exists. They are translated
//! into `open62541` calls inside [`TestServerBuilder::start`].
//!
//! [`TestServerBuilder::start`]: crate::server::TestServerBuilder::start

use std::fmt;

use open62541::DataSource;
use open62541::DataType as _;
use open62541::MethodCallback;
use open62541::ua;

/// Identifier for a node in the test namespace.
///
/// The builder maps this onto a concrete [`ua::NodeId`] in the namespace
/// allocated by [`TestServerBuilder::namespace_uri`]. Use [`TestNodeId::Auto`]
/// to let the builder pick the next free numeric id.
///
/// The namespace index is bound late — open62541 only assigns it inside
/// [`TestServerBuilder::start`] via `add_namespace`, so [`TestNodeId::resolve`]
/// receives it as an argument rather than carrying it on the variant.
///
/// [`TestServerBuilder::namespace_uri`]: crate::server::TestServerBuilder::namespace_uri
/// [`TestServerBuilder::start`]: crate::server::TestServerBuilder::start
#[derive(Debug, Clone)]
pub enum TestNodeId {
    /// Builder allocates the next free numeric id in the test namespace.
    Auto,
    /// Numeric identifier (`ns=<test>;i=<n>`).
    Numeric(u32),
    /// String identifier (`ns=<test>;s=<s>`).
    String(String),
}

impl TestNodeId {
    /// Lower into a concrete [`ua::NodeId`] in `namespace_index`. `auto_id` is
    /// only consulted by the [`Self::Auto`] variant; other variants discard it.
    pub(crate) fn resolve(&self, namespace_index: u16, auto_id: u32) -> ua::NodeId {
        match self {
            Self::Auto => ua::NodeId::numeric(namespace_index, auto_id),
            Self::Numeric(n) => ua::NodeId::numeric(namespace_index, *n),
            Self::String(s) => ua::NodeId::string(namespace_index, s),
        }
    }
}

impl From<u32> for TestNodeId {
    fn from(n: u32) -> Self {
        Self::Numeric(n)
    }
}

impl From<&str> for TestNodeId {
    fn from(s: &str) -> Self {
        Self::String(s.to_owned())
    }
}

impl From<String> for TestNodeId {
    fn from(s: String) -> Self {
        Self::String(s)
    }
}

/// Parent under which a node is registered.
///
/// Defaults to [`ParentRef::ObjectsFolder`] for top-level nodes.
#[derive(Debug, Clone, Default)]
pub enum ParentRef {
    /// The standard `ObjectsFolder` (`ns=0;i=85`).
    #[default]
    ObjectsFolder,
    /// The standard `Server` object (`ns=0;i=2253`).
    Server,
    /// An already-resolved [`ua::NodeId`] (escape hatch for advanced cases).
    Node(ua::NodeId),
    /// A previously declared node, looked up by browse name at `start()` time.
    Label(String),
}

/// Access permissions exposed on a variable node.
///
/// Maps to OPC UA's `AccessLevel` bitmap.
#[derive(Debug, Clone, Default)]
pub enum Access {
    ReadOnly,
    #[default]
    ReadWrite,
    WriteOnly,
    /// Use a fully custom bitmap (history flags, status writes, etc.).
    Custom(ua::AccessLevelType),
}

impl Access {
    /// Lower into the open62541 bitmap.
    #[must_use]
    pub fn as_level(&self) -> ua::AccessLevelType {
        match self {
            Self::ReadOnly => ua::AccessLevelType::NONE.with_current_read(true),
            Self::ReadWrite => ua::AccessLevelType::NONE
                .with_current_read(true)
                .with_current_write(true),
            Self::WriteOnly => ua::AccessLevelType::NONE.with_current_write(true),
            Self::Custom(level) => level.clone(),
        }
    }
}

/// Where a variable's value comes from on read/write.
pub enum ValueSource {
    /// Server stores the variant; mutations go through
    /// [`TestServer::set_value`](crate::server::TestServer::set_value).
    Stored(ua::Variant),
    /// Each read/write hits the user-supplied callback.
    DataSource(Box<dyn DataSource + Send + 'static>),
}

impl fmt::Debug for ValueSource {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Stored(v) => f.debug_tuple("Stored").field(v).finish(),
            Self::DataSource(_) => f.debug_struct("DataSource").finish_non_exhaustive(),
        }
    }
}

/// Folder under the address space.
#[derive(Debug, Clone)]
pub struct FolderSpec {
    pub id: TestNodeId,
    pub browse_name: String,
    pub display_name: Option<String>,
    pub parent: ParentRef,
}

impl FolderSpec {
    pub fn new(id: impl Into<TestNodeId>, browse_name: impl Into<String>) -> Self {
        Self {
            id: id.into(),
            browse_name: browse_name.into(),
            display_name: None,
            parent: ParentRef::default(),
        }
    }

    #[must_use]
    pub fn parent(mut self, parent: ParentRef) -> Self {
        self.parent = parent;
        self
    }

    #[must_use]
    pub fn display_name(mut self, name: impl Into<String>) -> Self {
        self.display_name = Some(name.into());
        self
    }
}

/// Variable node — a typed slot that clients can read and (optionally) write.
pub struct VariableSpec {
    pub id: TestNodeId,
    pub browse_name: String,
    pub display_name: Option<String>,
    pub parent: ParentRef,
    pub access: Access,
    pub source: ValueSource,
    /// Optional explicit data type. When `None`, the data type is inferred from
    /// a [`ValueSource::Stored`] variant; required for [`ValueSource::DataSource`].
    pub data_type: Option<ua::NodeId>,
}

impl fmt::Debug for VariableSpec {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("VariableSpec")
            .field("id", &self.id)
            .field("browse_name", &self.browse_name)
            .field("display_name", &self.display_name)
            .field("parent", &self.parent)
            .field("access", &self.access)
            .field("source", &self.source)
            .field("data_type", &self.data_type)
            .finish()
    }
}

impl VariableSpec {
    /// Defaults: parent = `ObjectsFolder`, access = `ReadWrite`,
    /// source = `Stored(Variant::init())` (empty until `value()` or `source()` is set).
    pub fn new(id: impl Into<TestNodeId>, browse_name: impl Into<String>) -> Self {
        Self {
            id: id.into(),
            browse_name: browse_name.into(),
            display_name: None,
            parent: ParentRef::default(),
            access: Access::default(),
            source: ValueSource::Stored(ua::Variant::init()),
            data_type: None,
        }
    }

    #[must_use]
    pub fn parent(mut self, parent: ParentRef) -> Self {
        self.parent = parent;
        self
    }

    #[must_use]
    pub fn display_name(mut self, name: impl Into<String>) -> Self {
        self.display_name = Some(name.into());
        self
    }

    #[must_use]
    pub fn access(mut self, access: Access) -> Self {
        self.access = access;
        self
    }

    #[must_use]
    pub fn source(mut self, source: ValueSource) -> Self {
        self.source = source;
        self
    }

    /// Shortcut for `.source(ValueSource::Stored(value))`.
    #[must_use]
    pub fn value(self, value: ua::Variant) -> Self {
        self.source(ValueSource::Stored(value))
    }

    #[must_use]
    pub fn data_type(mut self, data_type: ua::NodeId) -> Self {
        self.data_type = Some(data_type);
        self
    }
}

/// Method node — a callable RPC endpoint.
pub struct MethodSpec {
    pub id: TestNodeId,
    pub browse_name: String,
    pub display_name: Option<String>,
    pub parent: ParentRef,
    pub input_arguments: Vec<ua::Argument>,
    pub output_arguments: Vec<ua::Argument>,
    pub callback: Box<dyn MethodCallback + Send + 'static>,
}

impl fmt::Debug for MethodSpec {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("MethodSpec")
            .field("id", &self.id)
            .field("browse_name", &self.browse_name)
            .field("display_name", &self.display_name)
            .field("parent", &self.parent)
            .field("input_arguments", &self.input_arguments.len())
            .field("output_arguments", &self.output_arguments.len())
            .finish_non_exhaustive()
    }
}

impl MethodSpec {
    pub fn new(
        id: impl Into<TestNodeId>,
        browse_name: impl Into<String>,
        callback: impl MethodCallback + Send + 'static,
    ) -> Self {
        Self {
            id: id.into(),
            browse_name: browse_name.into(),
            display_name: None,
            parent: ParentRef::default(),
            input_arguments: Vec::new(),
            output_arguments: Vec::new(),
            callback: Box::new(callback),
        }
    }

    #[must_use]
    pub fn parent(mut self, parent: ParentRef) -> Self {
        self.parent = parent;
        self
    }

    #[must_use]
    pub fn display_name(mut self, name: impl Into<String>) -> Self {
        self.display_name = Some(name.into());
        self
    }

    #[must_use]
    pub fn input_arguments(mut self, args: Vec<ua::Argument>) -> Self {
        self.input_arguments = args;
        self
    }

    #[must_use]
    pub fn output_arguments(mut self, args: Vec<ua::Argument>) -> Self {
        self.output_arguments = args;
        self
    }
}
