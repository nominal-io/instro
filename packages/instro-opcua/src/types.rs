//! Serializable OPC-UA domain types and FFI conversion shims.
//!
//! This module serves two purposes:
//!
//! 1. **OPC-UA domain types** — Rust-native, [`serde`]-compatible representations
//!    of OPC-UA concepts: security configuration ([`OpcUaSecurityMode`],
//!    [`OpcUaSecurityPolicy`]), authentication ([`OpcUaUserToken`],
//!    [`OpcUaUserTokenType`], [`OpcUaUserTokenPolicy`]), node identity
//!    ([`OpcUaNodeId`] (with its [`NodeIdInner`] variants), [`OpcUaNode`],
//!    [`OpcUaNodeClass`]), scalar values ([`OpcUaValue`]), timestamped sample
//!    data ([`OpcUaDataPoint`], [`OpcUaSample`]), and server/endpoint metadata
//!    ([`OpcUaServerDescription`], [`OpcUaEndpointInfo`]).
//!
//!    Scalar value types correspond to the IEC 61131-3 types used across PLC
//!    protocols, not just OPC-UA.
//!
//! 2. **[`open62541`] / [`open62541_sys`] conversions** — [`TryFrom`] and [`From`]
//!    implementations that translate between the types above and their C-level
//!    counterparts in the [`open62541`] Rust wrapper and raw [`open62541_sys`] FFI
//!    bindings. The [`read_inner`] helper provides safe access to the
//!    `#[repr(transparent)]` wrapper internals, and [`scalar_to_variant`] /
//!    [`variant_to_value`] bridge scalar values through [`ua::Variant`] for
//!    read and write operations.

use std::borrow::Cow;
use std::fmt::Display;
use std::fmt::Formatter;
use std::num::NonZeroU32;
use std::str::FromStr;
use std::time::Duration;

use anyhow::Context as _;
use anyhow::Error;
use anyhow::Result;
use anyhow::anyhow;
use anyhow::bail;
use open62541::Certificate;
use open62541::DataType;
use open62541::DataValue;
use open62541::MonitoredItemCreateRequestBuilder;
use open62541::PrivateKey;
use open62541::ScalarValue;
use open62541::SubscriptionBuilder;
use open62541::ua;
use open62541::ua::AnonymousIdentityToken;
use open62541::ua::Boolean;
use open62541::ua::Byte;
use open62541::ua::DateTime;
use open62541::ua::Double;
use open62541::ua::EndpointDescription;
use open62541::ua::Float;
use open62541::ua::Int16;
use open62541::ua::Int32;
use open62541::ua::Int64;
use open62541::ua::MessageSecurityMode;
use open62541::ua::NodeId;
use open62541::ua::SByte;
use open62541::ua::UInt16;
use open62541::ua::UInt32;
use open62541::ua::UInt64;
use open62541::ua::UserIdentityToken;
use open62541::ua::UserNameIdentityToken;
use open62541::ua::X509IdentityToken;
use open62541_sys::UA_NodeClass;
use open62541_sys::UA_UserTokenPolicy;
use open62541_sys::UA_UserTokenType;
use serde::Deserialize;
use serde::Serialize;
use time::UtcDateTime;
use zeroize::Zeroize as _;

/// Convenience function to read values from raw `open62541-sys` types.
/// This is necessary in some cases to access values from the raw C
/// types that are not exposed by the Rust wrapper.
pub(crate) fn read_inner<T, U, F>(wrapper: &T, f: F) -> U
where
    T: DataType,
    F: for<'a> FnOnce(&'a T::Inner) -> U,
{
    // SAFETY: All open62541 wrapper types are #[repr(transparent)], so the inner type is guaranteed
    // to be aligned
    f(unsafe { wrapper.as_ref() })
}

/// OPC-UA MessageSecurityMode enumeration.
///
/// The message security mode configuration is the baseline encryption/signing configuration for an OPC-UA connection.
///
/// See the OPC-UA specification for more details:
/// - OpenSecureChannel message specification: https://reference.opcfoundation.org/Core/Part4/v104/docs/5.5.2
/// - Values of MessageSecurityMode defined by the specification: https://reference.opcfoundation.org/Core/Part4/v104/docs/7.15#Table138
#[derive(Debug, Clone, Copy, Serialize, Deserialize, Default, PartialEq, Eq, Hash)]
#[serde(rename_all = "snake_case")]
pub enum OpcUaSecurityMode {
    /// Sentinel value to guard against underspecified security mode values.
    /// i.e. a connection to an OPC-UA server shouldn't automatically default to a totally insecure transport.
    /// See OPC-UA specification
    #[default]
    Invalid,
    /// No signing or encryption.
    None,
    /// Signing only.
    Sign,
    /// Encryption and signing.
    SignAndEncrypt,
}

impl OpcUaSecurityMode {
    /// Returns true if the security mode is invalid.
    ///
    /// The only case where this would be true is if the default instantiation was used directly without further specification.
    pub fn is_invalid(&self) -> bool {
        *self == OpcUaSecurityMode::Invalid
    }
}

impl From<&MessageSecurityMode> for OpcUaSecurityMode {
    fn from(mode: &MessageSecurityMode) -> Self {
        read_inner(mode, |mode| {
            let mode_discriminant = {
                #[cfg(target_os = "windows")]
                {
                    mode.0 as u32
                }
                #[cfg(not(target_os = "windows"))]
                {
                    mode.0
                }
            };

            match mode_discriminant {
                MessageSecurityMode::NONE_U32 => OpcUaSecurityMode::None,
                MessageSecurityMode::SIGN_U32 => OpcUaSecurityMode::Sign,
                MessageSecurityMode::SIGNANDENCRYPT_U32 => OpcUaSecurityMode::SignAndEncrypt,
                _ => OpcUaSecurityMode::Invalid,
            }
        })
    }
}

impl From<OpcUaSecurityMode> for MessageSecurityMode {
    fn from(mode: OpcUaSecurityMode) -> Self {
        match mode {
            OpcUaSecurityMode::Invalid => MessageSecurityMode::INVALID,
            OpcUaSecurityMode::None => MessageSecurityMode::NONE,
            OpcUaSecurityMode::Sign => MessageSecurityMode::SIGN,
            OpcUaSecurityMode::SignAndEncrypt => MessageSecurityMode::SIGNANDENCRYPT,
        }
    }
}

/// OPC-UA security policy.
/// The security policy is the specific encryption/signing algorithm used for an OPC-UA connection.
///
/// See the OPC-UA specification for more details:
/// - OpenSecureChannel message specification: https://reference.opcfoundation.org/Core/Part4/v104/docs/5.5.2
#[derive(Debug, Clone, Serialize, Deserialize, Default, PartialEq, Eq, Hash)]
#[serde(rename_all = "snake_case")]
pub enum OpcUaSecurityPolicy {
    None,
    Basic128Rsa15,
    Basic256,
    #[default]
    Basic256Sha256,
    Aes128Sha256RsaOaep,
    Aes256Sha256RsaPss,
}

impl FromStr for OpcUaSecurityPolicy {
    type Err = Error;
    fn from_str(s: &str) -> Result<Self> {
        Ok(match s {
            "http://opcfoundation.org/UA/SecurityPolicy#None" => Self::None,
            "http://opcfoundation.org/UA/SecurityPolicy#Basic128Rsa15" => Self::Basic128Rsa15,
            "http://opcfoundation.org/UA/SecurityPolicy#Basic256" => Self::Basic256,
            "http://opcfoundation.org/UA/SecurityPolicy#Basic256Sha256" => Self::Basic256Sha256,
            "http://opcfoundation.org/UA/SecurityPolicy#Aes128_Sha256_RsaOaep" => {
                Self::Aes128Sha256RsaOaep
            }
            "http://opcfoundation.org/UA/SecurityPolicy#Aes256_Sha256_RsaPss" => {
                Self::Aes256Sha256RsaPss
            }
            _ => bail!("unsupported security policy URI: {s}"),
        })
    }
}

impl OpcUaSecurityPolicy {
    pub fn as_uri(&self) -> &'static str {
        match self {
            Self::None => "http://opcfoundation.org/UA/SecurityPolicy#None",
            Self::Basic128Rsa15 => "http://opcfoundation.org/UA/SecurityPolicy#Basic128Rsa15",
            Self::Basic256 => "http://opcfoundation.org/UA/SecurityPolicy#Basic256",
            Self::Basic256Sha256 => "http://opcfoundation.org/UA/SecurityPolicy#Basic256Sha256",
            Self::Aes128Sha256RsaOaep => {
                "http://opcfoundation.org/UA/SecurityPolicy#Aes128_Sha256_RsaOaep"
            }
            Self::Aes256Sha256RsaPss => {
                "http://opcfoundation.org/UA/SecurityPolicy#Aes256_Sha256_RsaPss"
            }
        }
    }
}

impl From<OpcUaSecurityPolicy> for ua::String {
    fn from(val: OpcUaSecurityPolicy) -> Self {
        // SAFETY: `as_uri` is guaranteed to return a valid, non-NUL-containing URI string.
        #[expect(clippy::unwrap_used, reason = "uri is guaranteed to be valid")]
        ua::String::new(val.as_uri()).unwrap()
    }
}

/// OPC-UA UserTokenPolicy.
/// The user token policy describes the type of authentication token that the server endpoint accepts.
///
/// See the OPC-UA specification for more details:
/// - OpenSecureChannel message specification: https://reference.opcfoundation.org/Core/Part4/v104/docs/5.5.2
/// - Values of UserTokenPolicy defined by the specification: https://reference.opcfoundation.org/Core/Part4/v104/docs/7.37
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Hash)]
pub struct OpcUaUserTokenPolicy {
    /// Per-server unique identifier for the user token policy.
    pub policy_id: String,
    /// The type of authentication token that the server endpoint accepts.
    pub token_type: OpcUaUserTokenType,
}

impl TryFrom<&UA_UserTokenPolicy> for OpcUaUserTokenPolicy {
    type Error = Error;

    fn try_from(policy: &UA_UserTokenPolicy) -> Result<Self> {
        let policy_id = ua::String::clone_raw(&policy.policyId);

        Ok(Self {
            policy_id: policy_id.to_string(),
            token_type: TryFrom::try_from(&policy.tokenType)?,
        })
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Hash)]
pub struct OpcUaUserToken {
    policy_id: String,
    #[serde(flatten)]
    inner: OpcUaUserTokenInner,
}

impl OpcUaUserToken {
    fn new(policy_id: String, inner: OpcUaUserTokenInner) -> Self {
        Self { policy_id, inner }
    }

    /// Creates a new user token for anonymous authentication.
    pub fn anonymous(policy_id: String) -> Result<Self> {
        Ok(Self::new(policy_id, OpcUaUserTokenInner::Anonymous))
    }

    /// Creates a new user token for basic authentication.
    pub fn basic(user: String, pass: String, policy_id: String) -> Result<Self> {
        Ok(Self::new(
            policy_id,
            OpcUaUserTokenInner::Basic {
                username: user,
                password: pass,
            },
        ))
    }

    /// Creates a new user token for certificate-based authentication.
    pub fn certificate(cert: Vec<u8>, policy_id: String) -> Result<Self> {
        Ok(Self::new(
            policy_id,
            OpcUaUserTokenInner::Certificate { cert },
        ))
    }
}

impl TryFrom<OpcUaUserToken> for UserIdentityToken {
    type Error = Error;
    fn try_from(token: OpcUaUserToken) -> Result<Self> {
        Ok(match token.inner {
            OpcUaUserTokenInner::Anonymous => UserIdentityToken::Anonymous(
                AnonymousIdentityToken::init().with_policy_id(ua::String::new(&token.policy_id)?),
            ),

            OpcUaUserTokenInner::Basic { username, password } => UserIdentityToken::UserName(
                UserNameIdentityToken::init()
                    .with_user_name(
                        ua::String::new(&username)
                            .context("user identity token had nul byte(s)")?,
                    )
                    .with_password(ua::ByteString::new(password.as_bytes())),
            ),

            OpcUaUserTokenInner::Certificate { cert } => UserIdentityToken::X509(
                X509IdentityToken::init().with_certificate(Certificate::from_bytes(&cert))?,
            ),
        })
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, Default, PartialEq, Eq, Hash)]
#[serde(tag = "kind", rename_all = "snake_case")]
enum OpcUaUserTokenInner {
    #[default]
    Anonymous,

    Basic {
        username: String,
        password: String,
    },

    Certificate {
        cert: Vec<u8>,
    },
}

/// OPC-UA UserTokenType enumeration.
///
/// The user token type is the type of authentication token used for an OPC-UA connection.
/// We don't support issued tokens (yet). This might change in the future.
///
/// See the OPC-UA specification for more details:
/// - OpenSecureChannel message specification: https://reference.opcfoundation.org/Core/Part4/v104/docs/5.5.2
/// - Values of UserTokenType defined by the specification: https://reference.opcfoundation.org/Core/Part4/v104/docs/7.15#Table139
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Hash)]
#[serde(rename_all = "snake_case")]
pub enum OpcUaUserTokenType {
    /// Anonymous authentication.
    Anonymous,
    /// Username and password authentication.
    UserName,
    /// Certificate authentication.
    Certificate,
}

impl TryFrom<&UA_UserTokenType> for OpcUaUserTokenType {
    type Error = Error;
    fn try_from(token: &UA_UserTokenType) -> Result<Self> {
        let token_discriminant = {
            #[cfg(target_os = "windows")]
            {
                token.0 as u32
            }
            #[cfg(not(target_os = "windows"))]
            {
                token.0
            }
        };

        Ok(match token_discriminant {
            0 => Self::Anonymous,
            1 => Self::UserName,
            2 => Self::Certificate,
            token_t => bail!("unsupported user token type: {}", token_t),
        })
    }
}

/// OPC-UA endpoint information.
///
/// An OPC-UA server exposes a GetEndpoints service that returns the list of endpoints available to
/// connect to.
///
/// The OpcUaEndpointInfo struct is used to store the information about an endpoint.
///
/// See the OPC-UA specification for more details:
/// - GetEndpoints service specification: https://reference.opcfoundation.org/Core/Part4/v104/docs/5.6.2
/// - EndpointDescription type defined by the specification: https://reference.opcfoundation.org/Core/Part4/v104/docs/7.36
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct OpcUaEndpointInfo {
    pub endpoint_url: String,
    pub server_name: String,
    pub security_mode: OpcUaSecurityMode,
    pub user_token_policies: Vec<OpcUaUserTokenPolicy>,
    pub security_policy: OpcUaSecurityPolicy,
    pub server_certificate_bytes: Vec<u8>,
    pub transport_profile_uri: String,
}

impl OpcUaEndpointInfo {
    pub fn description_matches(&self, description: &OpcUaServerDescription) -> bool {
        self.endpoint_url == description.endpoint_url
            && self.server_name == description.server_name
            && self.security_mode == description.security_mode
            && self
                .user_token_policies
                .contains(&description.user_token_policy)
            && self.security_policy == description.security_policy
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Hash)]
pub struct OpcUaServerDescription {
    pub endpoint_url: String,
    pub server_name: String,
    pub security_mode: OpcUaSecurityMode,
    pub user_token_policy: OpcUaUserTokenPolicy,
    pub security_policy: OpcUaSecurityPolicy,
}

impl OpcUaServerDescription {
    pub fn device_id(&self) -> String {
        format!("{}-{}", self.server_name, self.endpoint_url)
    }
}

impl TryFrom<&EndpointDescription> for OpcUaEndpointInfo {
    type Error = Error;

    fn try_from(endpoint: &EndpointDescription) -> Result<Self> {
        let server_certificate_bytes = endpoint
            .server_certificate()
            .as_bytes()
            .unwrap_or_default()
            .to_vec();

        let server_name = endpoint.server().application_name().text().to_string();

        Ok(Self {
            endpoint_url: endpoint.endpoint_url().to_string(),
            security_mode: endpoint.security_mode().into(),
            transport_profile_uri: endpoint.transport_profile_uri().to_string(),
            user_token_policies: Self::extract_user_token_types(endpoint)?,
            security_policy: endpoint.security_policy_uri().to_string().parse()?,
            server_name,
            server_certificate_bytes,
        })
    }
}

impl OpcUaEndpointInfo {
    fn extract_user_token_types(
        endpoint: &EndpointDescription,
    ) -> Result<Vec<OpcUaUserTokenPolicy>> {
        read_inner(endpoint, |endpoint| {
            if endpoint.userIdentityTokens.is_null() || !endpoint.userIdentityTokens.is_aligned() {
                bail!("user identity tokens are null or not aligned");
            }

            let mut policies = Vec::new();

            for i in 0..endpoint.userIdentityTokensSize {
                // SAFETY: `userIdentityTokens` is valid for `userIdentityTokensSize` elements.
                let policy = unsafe { &*endpoint.userIdentityTokens.add(i) };

                policies.push(policy.try_into()?);
            }

            Ok(policies)
        })
    }
}

/// OPC-UA NodeId.
///
/// A [`NodeId`] is used to identify nodes in an OPC-UA address space. Nodes may hold a list of child nodes,
/// which are identified by their own [`NodeId`]s.
///
/// Serialized as the canonical OPC-UA string form `ns=N;i=K` (numeric) or
/// `ns=N;s=K` (string), so it can be used as a JSON map key.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord, Hash)]
#[serde(into = "String", try_from = "String")]
pub struct OpcUaNodeId {
    /// The namespace index of the node.
    /// See the OPC-UA specification for more details
    pub namespace: u16,

    /// The inner representation of the node id.
    /// See the OPC-UA NodeId specification.
    pub inner: NodeIdInner,
}

impl Display for OpcUaNodeId {
    fn fmt(&self, f: &mut Formatter<'_>) -> std::fmt::Result {
        match &self.inner {
            NodeIdInner::Numeric(n) => write!(f, "ns={};i={}", self.namespace, n),
            NodeIdInner::String(s) => write!(f, "ns={};s={}", self.namespace, s),
        }
    }
}

impl FromStr for OpcUaNodeId {
    type Err = Error;

    fn from_str(s: &str) -> Result<Self> {
        let rest = s
            .strip_prefix("ns=")
            .ok_or_else(|| anyhow!("OpcUaNodeId '{s}': missing 'ns=' prefix"))?;

        let (ns_str, rest) = rest
            .split_once(';')
            .ok_or_else(|| anyhow!("OpcUaNodeId '{s}': missing ';' after namespace"))?;

        let namespace: u16 = ns_str
            .parse()
            .with_context(|| format!("OpcUaNodeId '{s}': invalid namespace '{ns_str}'"))?;

        let inner = if let Some(num) = rest.strip_prefix("i=") {
            NodeIdInner::Numeric(
                num.parse()
                    .with_context(|| format!("OpcUaNodeId '{s}': invalid numeric id '{num}'"))?,
            )
        } else if let Some(string) = rest.strip_prefix("s=") {
            NodeIdInner::String(string.to_owned())
        } else {
            bail!("OpcUaNodeId '{s}': identifier must start with 'i=' or 's='");
        };

        Ok(OpcUaNodeId { namespace, inner })
    }
}

impl From<OpcUaNodeId> for String {
    fn from(id: OpcUaNodeId) -> Self {
        id.to_string()
    }
}

impl TryFrom<String> for OpcUaNodeId {
    type Error = Error;
    fn try_from(s: String) -> Result<Self> {
        s.parse()
    }
}

impl TryFrom<NodeId> for OpcUaNodeId {
    type Error = Error;
    fn try_from(node_id: NodeId) -> Result<Self> {
        Self::try_from(&node_id)
    }
}

impl TryFrom<&NodeId> for OpcUaNodeId {
    type Error = Error;
    fn try_from(node_id: &NodeId) -> Result<Self> {
        Ok(if let Some((ns, numeric)) = node_id.as_numeric() {
            OpcUaNodeId {
                namespace: ns,
                inner: NodeIdInner::Numeric(numeric),
            }
        } else if let Some((ns, string)) = node_id.as_string() {
            OpcUaNodeId {
                namespace: ns,
                inner: NodeIdInner::String(string.to_string()),
            }
        } else {
            bail!("node id wasn't valid: '{node_id:?}'");
        })
    }
}

impl From<OpcUaNodeId> for NodeId {
    fn from(other: OpcUaNodeId) -> Self {
        match other.inner {
            NodeIdInner::Numeric(n) => NodeId::numeric(other.namespace, n),
            NodeIdInner::String(ref s) => NodeId::string(other.namespace, s),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum NodeIdInner {
    Numeric(u32),
    String(String),
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct OpcUaNode {
    pub node_id: OpcUaNodeId,
    pub browse_name: String,
    pub display_name: String,
    pub node_class: OpcUaNodeClass,
    pub children: Vec<OpcUaNode>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum OpcUaNodeClass {
    Object,
    Variable,
    Method,
    View,
    Other(u32),
}

impl From<OpcUaNodeClass> for ua::NodeClass {
    fn from(node_class: OpcUaNodeClass) -> Self {
        match node_class {
            OpcUaNodeClass::Object => ua::NodeClass::OBJECT,
            OpcUaNodeClass::Variable => ua::NodeClass::VARIABLE,
            OpcUaNodeClass::Method => ua::NodeClass::METHOD,
            OpcUaNodeClass::View => ua::NodeClass::VIEW,
            OpcUaNodeClass::Other(other) => {
                let inner;
                #[cfg(target_os = "windows")]
                {
                    inner = UA_NodeClass(other as i32);
                }
                #[cfg(not(target_os = "windows"))]
                {
                    inner = UA_NodeClass(other);
                }

                const {
                    if std::mem::size_of::<std::ffi::c_uint>() < 4 {
                        panic!("OPC-UA crate is not supported on AVR or MSP430 microcontrollers");
                    }
                }

                // SAFETY: `inner` is valid for the given `other` value.
                unsafe { ua::NodeClass::from_raw(inner) }
            }
        }
    }
}

impl From<&ua::NodeClass> for OpcUaNodeClass {
    fn from(node_class: &ua::NodeClass) -> Self {
        read_inner(node_class, |node_class| {
            let node_class_discriminant = {
                #[cfg(target_os = "windows")]
                {
                    node_class.0 as u32
                }
                #[cfg(not(target_os = "windows"))]
                {
                    node_class.0
                }
            };

            match node_class_discriminant {
                ua::NodeClass::OBJECT_U32 => OpcUaNodeClass::Object,
                ua::NodeClass::VARIABLE_U32 => OpcUaNodeClass::Variable,
                ua::NodeClass::METHOD_U32 => OpcUaNodeClass::Method,
                ua::NodeClass::VIEW_U32 => OpcUaNodeClass::View,
                other => OpcUaNodeClass::Other(other),
            }
        })
    }
}

/// OPC-UA subscription configuration.
///
/// Carries the tuning knobs for creating a server-side subscription: how often the server
/// publishes notifications, how long it keeps the subscription alive without a publish request,
/// and notification batching limits. All fields are required; factory methods with reasonable
/// presets are intentionally deferred until the field set is settled.
///
/// See the OPC-UA specification for more details:
/// - [CreateSubscriptionRequest](https://reference.opcfoundation.org/Core/Part4/v104/docs/5.13.2)
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, Hash)]
pub struct OpcUaSubscriptionConfig {
    /// Cyclic rate at which the subscription publishes accumulated notifications to the client.
    pub publishing_interval: Duration,

    /// Interval at which the server is polled for value changes.
    /// If `None`, selected nodes will not be polled.
    ///
    /// Intended for node sets with low-frequency/unfluctuating values that may appear stale
    /// in a streaming configuration.
    #[serde(skip_serializing_if = "Option::is_none")]
    #[serde(default)]
    pub background_poll_interval: Option<Duration>,

    /// Number of publish intervals the subscription persists on the server without a successful
    /// publish before the server deletes it.
    pub lifetime_count: u32,

    /// Number of publish intervals between server-initiated keep-alive messages when no changes
    /// are available to publish.
    pub max_keep_alive_count: NonZeroU32,

    /// Upper bound on the number of notifications the server delivers in a single publish
    /// response.
    pub max_notifications_per_publish: NonZeroU32,

    /// Relative priority of this subscription versus others on the same session.
    pub priority: u8,

    /// Whether the subscription starts publishing immediately after creation.
    pub publishing_enabled: bool,
}

impl From<OpcUaSubscriptionConfig> for SubscriptionBuilder {
    fn from(config: OpcUaSubscriptionConfig) -> Self {
        SubscriptionBuilder::default()
            .requested_publishing_interval(Some(config.publishing_interval))
            .requested_lifetime_count(config.lifetime_count)
            .requested_max_keep_alive_count(Some(config.max_keep_alive_count))
            .max_notifications_per_publish(Some(config.max_notifications_per_publish))
            .priority(config.priority)
            .publishing_enabled(config.publishing_enabled)
    }
}

/// OPC-UA monitored-item configuration.
///
/// Carries the sampling and queueing knobs that apply to each monitored item created under a
/// subscription. All fields are required; filters are intentionally omitted from this stub and
/// will be added in a follow-up ticket.
///
/// See the OPC-UA specification for more details:
/// - [MonitoredItemCreateRequest](https://reference.opcfoundation.org/Core/Part4/v104/docs/5.12.2)
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, Hash)]
pub struct OpcUaMonitoredItemConfig {
    /// Interval at which the server samples the underlying node for value changes.
    pub sampling_interval: Duration,

    /// Maximum number of samples the server buffers for this item between publish cycles.
    pub queue_size: u32,

    /// When the queue is full, whether the server drops the oldest sample (`true`) or the newest
    /// one (`false`).
    pub discard_oldest: bool,
}

impl OpcUaMonitoredItemConfig {
    /// Validates the result of a monitored item creation request, emitting warnings for any revisions that are not as requested.
    pub fn validate(&self, node_id: &OpcUaNodeId, create_result: &ua::MonitoredItemCreateResult) {
        match create_result.revised_sampling_interval() {
            Ok(revised_sampling_interval) => {
                if revised_sampling_interval != self.sampling_interval {
                    tracing::warn!(
                        "Server revised sampling interval for node {node_id} from {:?} to {:?}",
                        self.sampling_interval,
                        revised_sampling_interval,
                    );
                }
            }

            Err(e) => {
                tracing::warn!(
                    "Node {node_id} returned an invalid revised sampling interval: {e:?}"
                );
            }
        };

        match create_result.revised_queue_size() {
            0 => tracing::warn!("Node {node_id} returned an invalid revised queue size of 0"),
            revised if revised != self.queue_size => tracing::warn!(
                "Node {node_id} returned an invalid revised queue size of {revised} (requested {})",
                self.queue_size
            ),
            _ => (),
        }
    }
}

impl OpcUaMonitoredItemConfig {
    /// Applies this configuration's knobs to an existing [`MonitoredItemCreateRequestBuilder`],
    /// preserving its type-state and node-id selection.
    #[must_use]
    pub fn apply_to_builder<B>(
        self,
        builder: MonitoredItemCreateRequestBuilder<B>,
    ) -> MonitoredItemCreateRequestBuilder<B>
    where
        B: open62541::MonitoredItemKind,
    {
        builder
            .sampling_interval(Some(self.sampling_interval))
            .queue_size(self.queue_size)
            .discard_oldest(self.discard_oldest)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct OpcUaSample {
    pub node_id: OpcUaNodeId,
    #[serde(flatten)]
    pub data: OpcUaDataPoint,
}

impl OpcUaSample {
    pub fn new(node_id: OpcUaNodeId, data: OpcUaDataPoint) -> Self {
        Self { node_id, data }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct OpcUaDataPoint {
    pub server_timestamp: u64,
    pub source_timestamp: Option<u64>,
    pub value: OpcUaValue,
}

impl TryFrom<DataValue<ua::Variant>> for OpcUaDataPoint {
    type Error = Error;

    fn try_from(value: DataValue<ua::Variant>) -> Result<Self> {
        let server_timestamp = value
            .server_timestamp()
            .or_else(|| value.source_timestamp())
            .map(|ts| ts.as_unix_timestamp_nanos() as u64)
            .ok_or(anyhow!("no server or source timestamp found"))?;

        let source_timestamp = value
            .source_timestamp()
            .map(|ts| ts.as_unix_timestamp_nanos() as u64);

        Ok(Self {
            value: variant_to_value(value)?,
            server_timestamp,
            source_timestamp,
        })
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub enum OpcUaValue {
    Boolean(bool),
    Int8(i8),
    UInt8(u8),
    Int16(i16),
    UInt16(u16),
    Int32(i32),
    UInt32(u32),
    Int64(i64),
    UInt64(u64),
    Float(f32),
    Double(f64),
    String(Cow<'static, str>),
    DateTime(UtcDateTime),
}

impl TryFrom<OpcUaValue> for ScalarValue {
    type Error = Error;

    fn try_from(value: OpcUaValue) -> Result<Self> {
        Ok(match value {
            OpcUaValue::Boolean(b) => ScalarValue::Boolean(Boolean::new(b)),
            OpcUaValue::Int8(i) => ScalarValue::SByte(SByte::new(i)),
            OpcUaValue::UInt8(u) => ScalarValue::Byte(Byte::new(u)),
            OpcUaValue::Int16(i) => ScalarValue::Int16(Int16::new(i)),
            OpcUaValue::UInt16(u) => ScalarValue::UInt16(UInt16::new(u)),
            OpcUaValue::Int32(i) => ScalarValue::Int32(Int32::new(i)),
            OpcUaValue::UInt32(u) => ScalarValue::UInt32(UInt32::new(u)),
            OpcUaValue::Int64(i) => ScalarValue::Int64(Int64::new(i)),
            OpcUaValue::UInt64(u) => ScalarValue::UInt64(UInt64::new(u)),
            OpcUaValue::Float(f) => ScalarValue::Float(Float::new(f)),
            OpcUaValue::Double(d) => ScalarValue::Double(Double::new(d)),
            OpcUaValue::String(s) => ScalarValue::String(ua::String::new(s.as_ref())?),
            OpcUaValue::DateTime(dt) => ScalarValue::DateTime(DateTime::try_from(dt)?),
        })
    }
}

impl TryFrom<&ScalarValue> for OpcUaValue {
    type Error = Error;

    fn try_from(value: &ScalarValue) -> Result<Self> {
        Ok(match value {
            ScalarValue::Boolean(b) => OpcUaValue::Boolean(b.value()),
            ScalarValue::SByte(i) => OpcUaValue::Int8(i.value()),
            ScalarValue::Byte(u) => OpcUaValue::UInt8(u.value()),
            ScalarValue::Int16(i) => OpcUaValue::Int16(i.value()),
            ScalarValue::UInt16(u) => OpcUaValue::UInt16(u.value()),
            ScalarValue::Int32(i) => OpcUaValue::Int32(i.value()),
            ScalarValue::UInt32(u) => OpcUaValue::UInt32(u.value()),
            ScalarValue::Int64(i) => OpcUaValue::Int64(i.value()),
            ScalarValue::UInt64(u) => OpcUaValue::UInt64(u.value()),
            ScalarValue::Float(f) => OpcUaValue::Float(f.value()),
            ScalarValue::Double(d) => OpcUaValue::Double(d.value()),
            ScalarValue::String(s) => OpcUaValue::String(Cow::Owned(s.to_string())),
            ScalarValue::DateTime(dt) => OpcUaValue::DateTime(dt.clone().try_into()?),

            _ => bail!("Unsupported scalar value: {:?}", value),
        })
    }
}

pub fn scalar_to_variant(scalar: ScalarValue) -> Result<ua::Variant> {
    Ok(match scalar {
        ScalarValue::Boolean(v) => ua::Variant::scalar(v),
        ScalarValue::SByte(v) => ua::Variant::scalar(v),
        ScalarValue::Byte(v) => ua::Variant::scalar(v),
        ScalarValue::Int16(v) => ua::Variant::scalar(v),
        ScalarValue::UInt16(v) => ua::Variant::scalar(v),
        ScalarValue::Int32(v) => ua::Variant::scalar(v),
        ScalarValue::UInt32(v) => ua::Variant::scalar(v),
        ScalarValue::Int64(v) => ua::Variant::scalar(v),
        ScalarValue::UInt64(v) => ua::Variant::scalar(v),
        ScalarValue::Float(v) => ua::Variant::scalar(v),
        ScalarValue::Double(v) => ua::Variant::scalar(v),
        ScalarValue::String(v) => ua::Variant::scalar(v),
        ScalarValue::DateTime(v) => ua::Variant::scalar(v),
        _ => bail!("unsupported ScalarValue variant for OPC-UA write"),
    })
}

pub fn variant_to_value(value: DataValue<ua::Variant>) -> Result<OpcUaValue> {
    use open62541::VariantValue;

    value
        .value()
        .ok_or(anyhow!("value is null"))
        .and_then(|variant| {
            Ok(match variant.to_value() {
                VariantValue::Scalar(scalar) => match scalar {
                    ScalarValue::Boolean(b) => OpcUaValue::Boolean(b.value()),
                    ScalarValue::SByte(v) => OpcUaValue::Int8(v.value()),
                    ScalarValue::Byte(v) => OpcUaValue::UInt8(v.value()),
                    ScalarValue::Int16(v) => OpcUaValue::Int16(v.value()),
                    ScalarValue::UInt16(v) => OpcUaValue::UInt16(v.value()),
                    ScalarValue::Int32(v) => OpcUaValue::Int32(v.value()),
                    ScalarValue::UInt32(v) => OpcUaValue::UInt32(v.value()),
                    ScalarValue::Int64(v) => OpcUaValue::Int64(v.value()),
                    ScalarValue::UInt64(v) => OpcUaValue::UInt64(v.value()),
                    ScalarValue::Float(v) => OpcUaValue::Float(v.value()),
                    ScalarValue::Double(v) => OpcUaValue::Double(v.value()),
                    ScalarValue::String(v) => OpcUaValue::String(Cow::Owned(v.to_string())),
                    ScalarValue::DateTime(dt) => {
                        OpcUaValue::DateTime(dt.to_utc().ok_or(anyhow!("invalid date time"))?)
                    }

                    scalar_variant => {
                        bail!("unsupported OPC-UA scalar value read: '{scalar_variant:?}'")
                    }
                },

                variant => bail!("unsupported OPC-UA variant value read: '{variant:?}'"),
            })
        })
}

#[derive(Debug, Default, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub enum RawOpcUaPki {
    #[default]
    None,
    GenerateSelfSigned,
    UseProvided(Vec<u8>, Vec<u8>),
}

impl TryFrom<RawOpcUaPki> for OpcUaPki {
    type Error = Error;

    fn try_from(raw: RawOpcUaPki) -> Result<Self> {
        Ok(match raw {
            RawOpcUaPki::None => Self::None,
            RawOpcUaPki::GenerateSelfSigned => Self::GenerateSelfSigned,
            RawOpcUaPki::UseProvided(certificate, mut private_key) => {
                let cert = Certificate::from_bytes(&certificate);

                // construction of the certificate does not validate the certificate
                // validation only occurs when materializing the certificate via `x509_certificate`
                // basically a sanity check on deserialization
                let pki_result = cert
                    .clone()
                    .into_x509()
                    .context("validating certificate for use as OPC-UA client certificate")
                    .map(|_| Self::UseProvided(cert, PrivateKey::from_bytes(&private_key)));

                private_key.zeroize();

                pki_result?
            }
        })
    }
}

impl From<OpcUaPki> for RawOpcUaPki {
    fn from(pki: OpcUaPki) -> Self {
        match pki {
            OpcUaPki::None => RawOpcUaPki::None,
            OpcUaPki::GenerateSelfSigned => RawOpcUaPki::GenerateSelfSigned,
            OpcUaPki::UseProvided(certificate, private_key) => RawOpcUaPki::UseProvided(
                certificate.as_bytes().to_vec(),
                private_key.as_bytes().to_vec(),
            ),
        }
    }
}

#[derive(Debug, Default, Clone, Serialize, Deserialize)]
#[serde(try_from = "RawOpcUaPki", into = "RawOpcUaPki")]
pub enum OpcUaPki {
    #[default]
    None,
    GenerateSelfSigned,
    UseProvided(Certificate, PrivateKey),
}

impl PartialEq for OpcUaPki {
    fn eq(&self, other: &Self) -> bool {
        match (self, other) {
            (Self::None, Self::None) => true,
            (Self::GenerateSelfSigned, Self::GenerateSelfSigned) => true,
            (
                Self::UseProvided(certificate, private_key),
                Self::UseProvided(other_certificate, other_private_key),
            ) => {
                certificate.as_bytes() == other_certificate.as_bytes()
                    && private_key.as_bytes() == other_private_key.as_bytes()
            }
            _ => false,
        }
    }
}

impl Eq for OpcUaPki {}

#[cfg(test)]
mod tests {
    use std::borrow::Cow;
    use std::fmt::Debug;

    use anyhow::Error;
    use anyhow::Result;
    use instro_test_utils::assertions::assert_serde_json_roundtrip_eq;
    use itertools::Itertools as _;
    use opcua_test::assert_roundtrip;
    use open62541::ScalarValue;
    use open62541::VariantValue;
    use open62541::ua;
    use open62541::ua::MessageSecurityMode;
    use open62541::ua::NodeId;
    use open62541_sys::UA_UserTokenPolicy;
    use open62541_sys::UA_UserTokenType;

    use super::*;

    #[derive(Debug, Clone)]
    /// Newtype wrapper around ScalarValue that asserts equality of NaN values for float and double.
    /// Used in unit tests only.
    struct ScalarEq(ScalarValue);

    impl PartialEq for ScalarEq {
        fn eq(&self, other: &Self) -> bool {
            match (&self.0, &other.0) {
                (ScalarValue::Boolean(a), ScalarValue::Boolean(b)) => a.value() == b.value(),
                (ScalarValue::SByte(a), ScalarValue::SByte(b)) => a.value() == b.value(),
                (ScalarValue::Byte(a), ScalarValue::Byte(b)) => a.value() == b.value(),
                (ScalarValue::Int16(a), ScalarValue::Int16(b)) => a.value() == b.value(),
                (ScalarValue::UInt16(a), ScalarValue::UInt16(b)) => a.value() == b.value(),
                (ScalarValue::Int32(a), ScalarValue::Int32(b)) => a.value() == b.value(),
                (ScalarValue::UInt32(a), ScalarValue::UInt32(b)) => a.value() == b.value(),
                (ScalarValue::Int64(a), ScalarValue::Int64(b)) => a.value() == b.value(),
                (ScalarValue::UInt64(a), ScalarValue::UInt64(b)) => a.value() == b.value(),
                (ScalarValue::Float(a), ScalarValue::Float(b)) => a.value() == b.value(),
                (ScalarValue::Double(a), ScalarValue::Double(b)) => a.value() == b.value(),
                (ScalarValue::String(a), ScalarValue::String(b)) => a == b,
                (ScalarValue::DateTime(a), ScalarValue::DateTime(b)) => a == b,
                (ScalarValue::Guid(a), ScalarValue::Guid(b)) => a == b,
                (ScalarValue::ByteString(a), ScalarValue::ByteString(b)) => a == b,
                (ScalarValue::NodeId(a), ScalarValue::NodeId(b)) => a == b,
                (ScalarValue::ExpandedNodeId(a), ScalarValue::ExpandedNodeId(b)) => a == b,
                (ScalarValue::StatusCode(a), ScalarValue::StatusCode(b)) => a == b,
                (ScalarValue::QualifiedName(a), ScalarValue::QualifiedName(b)) => a == b,
                (ScalarValue::LocalizedText(a), ScalarValue::LocalizedText(b)) => a == b,
                (ScalarValue::Structure(a), ScalarValue::Structure(b)) => a == b,
                (ScalarValue::Enumeration(a), ScalarValue::Enumeration(b)) => a == b,
                (ScalarValue::Argument(a), ScalarValue::Argument(b)) => a == b,
                _ => false,
            }
        }
    }

    impl Eq for ScalarEq {}

    impl From<OpcUaValue> for ScalarEq {
        fn from(value: OpcUaValue) -> Self {
            ScalarEq(value.try_into().unwrap())
        }
    }

    impl From<&ScalarEq> for OpcUaValue {
        fn from(value: &ScalarEq) -> Self {
            (&value.0).try_into().unwrap()
        }
    }

    #[derive(Debug, Clone, PartialEq, Eq)]
    /// Newtype wrapper around ua::Variant to enable Eq assertions of scalar values only.
    /// Used in unit tests only.
    struct VariantEq(ua::Variant);

    impl TryFrom<&ScalarEq> for VariantEq {
        type Error = Error;
        fn try_from(s: &ScalarEq) -> Result<Self> {
            Ok(VariantEq(scalar_to_variant(s.0.clone())?))
        }
    }

    impl TryFrom<VariantEq> for ScalarEq {
        type Error = Error;
        fn try_from(v: VariantEq) -> Result<Self> {
            match v.0.to_value() {
                VariantValue::Scalar(s) => Ok(ScalarEq(s)),
                other => bail!("expected scalar variant, got {other:?}"),
            }
        }
    }

    #[test]
    fn security_mode_roundtrips() {
        assert_roundtrip(&MessageSecurityMode::NONE, OpcUaSecurityMode::None);
        assert_roundtrip(&MessageSecurityMode::SIGN, OpcUaSecurityMode::Sign);
        assert_roundtrip(
            &MessageSecurityMode::SIGNANDENCRYPT,
            OpcUaSecurityMode::SignAndEncrypt,
        );
    }

    #[test]
    fn node_id_roundtrips() {
        let back = NodeId::numeric(2, 1234);
        let numeric = OpcUaNodeId {
            namespace: 2,
            inner: NodeIdInner::Numeric(1234),
        };

        assert_roundtrip(&back, numeric);

        let back = NodeId::string(3, "MyNode");
        let string = OpcUaNodeId {
            namespace: 3,
            inner: NodeIdInner::String("MyNode".into()),
        };

        assert_roundtrip(&back, string);

        let back = NodeId::ns0(85);
        let ns0 = OpcUaNodeId {
            namespace: 0,
            inner: NodeIdInner::Numeric(85),
        };

        assert_roundtrip(&back, ns0);
    }

    #[test]
    fn node_class_conversions() {
        assert_roundtrip(&ua::NodeClass::OBJECT, OpcUaNodeClass::Object);
        assert_roundtrip(&ua::NodeClass::VARIABLE, OpcUaNodeClass::Variable);
        assert_roundtrip(&ua::NodeClass::METHOD, OpcUaNodeClass::Method);
        assert_roundtrip(&ua::NodeClass::VIEW, OpcUaNodeClass::View);

        // SAFETY: populating raw fields for test; node class lives on stack for duration of test
        assert_roundtrip(
            &unsafe { ua::NodeClass::from_raw(UA_NodeClass(99)) },
            OpcUaNodeClass::Other(99),
        );
    }

    #[test]
    fn opc_value_scalar_roundtrips() {
        assert_roundtrip(
            &ScalarEq(ScalarValue::Boolean(ua::Boolean::new(true))),
            OpcUaValue::Boolean(true),
        );
        assert_roundtrip(
            &ScalarEq(ScalarValue::SByte(ua::SByte::new(-42))),
            OpcUaValue::Int8(-42),
        );
        assert_roundtrip(
            &ScalarEq(ScalarValue::Byte(ua::Byte::new(255))),
            OpcUaValue::UInt8(255),
        );
        assert_roundtrip(
            &ScalarEq(ScalarValue::Int16(ua::Int16::new(-1000))),
            OpcUaValue::Int16(-1000),
        );
        assert_roundtrip(
            &ScalarEq(ScalarValue::UInt16(ua::UInt16::new(50000))),
            OpcUaValue::UInt16(50000),
        );
        assert_roundtrip(
            &ScalarEq(ScalarValue::Int32(ua::Int32::new(-100_000))),
            OpcUaValue::Int32(-100_000),
        );
        assert_roundtrip(
            &ScalarEq(ScalarValue::UInt32(ua::UInt32::new(3_000_000))),
            OpcUaValue::UInt32(3_000_000),
        );
        assert_roundtrip(
            &ScalarEq(ScalarValue::Int64(ua::Int64::new(i64::MIN))),
            OpcUaValue::Int64(i64::MIN),
        );
        assert_roundtrip(
            &ScalarEq(ScalarValue::UInt64(ua::UInt64::new(u64::MAX))),
            OpcUaValue::UInt64(u64::MAX),
        );
        assert_roundtrip(
            &ScalarEq(ScalarValue::Float(ua::Float::new(1.5))),
            OpcUaValue::Float(1.5),
        );
        assert_roundtrip(
            &ScalarEq(ScalarValue::Double(ua::Double::new(1.234))),
            OpcUaValue::Double(1.234),
        );
        assert_roundtrip(
            &ScalarEq(ScalarValue::String(ua::String::new("hello").unwrap())),
            OpcUaValue::String(Cow::Borrowed("hello")),
        );
    }

    #[test]
    fn scalar_variant_roundtrips() {
        assert_roundtrip(
            &ScalarEq(ScalarValue::Boolean(ua::Boolean::new(true))),
            VariantEq(ua::Variant::scalar(ua::Boolean::new(true))),
        );
        assert_roundtrip(
            &ScalarEq(ScalarValue::SByte(ua::SByte::new(-42))),
            VariantEq(ua::Variant::scalar(ua::SByte::new(-42))),
        );
        assert_roundtrip(
            &ScalarEq(ScalarValue::Byte(ua::Byte::new(255))),
            VariantEq(ua::Variant::scalar(ua::Byte::new(255))),
        );
        assert_roundtrip(
            &ScalarEq(ScalarValue::Int16(ua::Int16::new(-1000))),
            VariantEq(ua::Variant::scalar(ua::Int16::new(-1000))),
        );
        assert_roundtrip(
            &ScalarEq(ScalarValue::UInt16(ua::UInt16::new(50000))),
            VariantEq(ua::Variant::scalar(ua::UInt16::new(50000))),
        );
        assert_roundtrip(
            &ScalarEq(ScalarValue::Int32(ua::Int32::new(-100_000))),
            VariantEq(ua::Variant::scalar(ua::Int32::new(-100_000))),
        );
        assert_roundtrip(
            &ScalarEq(ScalarValue::UInt32(ua::UInt32::new(3_000_000))),
            VariantEq(ua::Variant::scalar(ua::UInt32::new(3_000_000))),
        );
        assert_roundtrip(
            &ScalarEq(ScalarValue::Int64(ua::Int64::new(i64::MIN))),
            VariantEq(ua::Variant::scalar(ua::Int64::new(i64::MIN))),
        );
        assert_roundtrip(
            &ScalarEq(ScalarValue::UInt64(ua::UInt64::new(u64::MAX))),
            VariantEq(ua::Variant::scalar(ua::UInt64::new(u64::MAX))),
        );
        assert_roundtrip(
            &ScalarEq(ScalarValue::Float(ua::Float::new(1.5))),
            VariantEq(ua::Variant::scalar(ua::Float::new(1.5))),
        );
        assert_roundtrip(
            &ScalarEq(ScalarValue::Double(ua::Double::new(1.234))),
            VariantEq(ua::Variant::scalar(ua::Double::new(1.234))),
        );
        assert_roundtrip(
            &ScalarEq(ScalarValue::String(ua::String::new("hello").unwrap())),
            VariantEq(ua::Variant::scalar(ua::String::new("hello").unwrap())),
        );
        assert_roundtrip(
            &ScalarEq(ScalarValue::DateTime(ua::DateTime::init())),
            VariantEq(ua::Variant::scalar(ua::DateTime::init())),
        );
    }

    #[test]
    fn convert_endpoint_with_none_security() {
        let mut endpoint = ua::EndpointDescription::init();

        let url = ua::String::new("opc.tcp://localhost:4840").expect("url");
        let policy_uri =
            ua::String::new("http://opcfoundation.org/UA/SecurityPolicy#None").expect("policy uri");
        let transport_uri =
            ua::String::new("http://opcfoundation.org/UA-Profile/Transport/uatcp-uasc-uabinary")
                .expect("transport uri");

        let app_desc =
            ua::ApplicationDescription::init().with_application_name("en-US", "TestServer");

        let anon_policy = UA_UserTokenPolicy {
            tokenType: UA_UserTokenType(0),
            policyId: ua::String::new("anonymous").expect("policy id").into_raw(),
            ..unsafe { std::mem::zeroed() }
        };

        // SAFETY: populating raw fields for test construction;
        // anon_policy lives on stack for the duration of this test
        unsafe {
            let raw = endpoint.as_mut();
            url.clone_into_raw(&mut raw.endpointUrl);
            policy_uri.clone_into_raw(&mut raw.securityPolicyUri);
            transport_uri.clone_into_raw(&mut raw.transportProfileUri);
            raw.securityMode = MessageSecurityMode::NONE.into_raw();
            app_desc.clone_into_raw(&mut raw.server);
            raw.userIdentityTokens = std::ptr::addr_of!(anon_policy).cast_mut();
            raw.userIdentityTokensSize = 1;
        }

        let info = OpcUaEndpointInfo::try_from(&endpoint).expect("conversion should succeed");

        // Prevent drop from freeing our stack-allocated policy
        unsafe {
            let raw = endpoint.as_mut();
            raw.userIdentityTokens = std::ptr::null_mut();
            raw.userIdentityTokensSize = 0;
        }

        assert_eq!(info.endpoint_url, "opc.tcp://localhost:4840");
        assert_eq!(info.security_mode, OpcUaSecurityMode::None);
        assert_eq!(info.security_policy, OpcUaSecurityPolicy::None);
        assert_eq!(info.server_name, "TestServer");
        assert_eq!(
            &info
                .user_token_policies
                .iter()
                .map(|policy| &policy.token_type)
                .collect_vec(),
            &[&OpcUaUserTokenType::Anonymous]
        );

        let UA_UserTokenPolicy { policyId, .. } = anon_policy;
        // SAFETY: cleaning up the UA_String
        _ = unsafe { ua::String::from_raw(policyId) };
    }

    #[test]
    fn extract_user_token_types_from_endpoint() {
        let mut endpoint = ua::EndpointDescription::init();

        let policy_uri_str_1 =
            ua::String::new("http://opcfoundation.org/UA/SecurityPolicy#None").expect("policy uri");

        let policy_uri_str_2 =
            ua::String::new("http://opcfoundation.org/UA/SecurityPolicy#Basic128Rsa15")
                .expect("policy uri");

        let policies = [
            UA_UserTokenPolicy {
                tokenType: UA_UserTokenType(0),
                policyId: policy_uri_str_1.into_raw(),
                ..unsafe { std::mem::zeroed() }
            },
            UA_UserTokenPolicy {
                tokenType: UA_UserTokenType(1),
                policyId: policy_uri_str_2.into_raw(),
                ..unsafe { std::mem::zeroed() }
            },
        ];

        // SAFETY: populating raw fields for test; policies lives on stack for duration of test
        unsafe {
            let raw = endpoint.as_mut();
            raw.userIdentityTokens = policies.as_ptr().cast_mut();
            raw.userIdentityTokensSize = policies.len();

            let policy_uri_str = ua::String::new("http://opcfoundation.org/UA/SecurityPolicy#None")
                .expect("policy uri");
            policy_uri_str.clone_into_raw(&mut raw.securityPolicyUri);
        }

        let types = OpcUaEndpointInfo::extract_user_token_types(&endpoint)
            .expect("extraction should succeed")
            .into_iter()
            .map(|policy| policy.token_type)
            .collect_vec();

        assert_eq!(
            types,
            vec![OpcUaUserTokenType::Anonymous, OpcUaUserTokenType::UserName,]
        );

        // Prevent drop from freeing our stack-allocated policies
        unsafe {
            let raw = endpoint.as_mut();
            raw.userIdentityTokens = std::ptr::null_mut();
            raw.userIdentityTokensSize = 0;
        }

        let [policy_1, policy_2] = policies;
        let _ = unsafe { ua::String::from_raw(policy_1.policyId) };
        let _ = unsafe { ua::String::from_raw(policy_2.policyId) };
    }

    #[test]
    fn serde_roundtrip_endpoint_info() {
        let info = OpcUaEndpointInfo {
            endpoint_url: "opc.tcp://localhost:4840".into(),
            server_name: "Test".into(),
            security_mode: OpcUaSecurityMode::Sign,
            security_policy: OpcUaSecurityPolicy::Basic256Sha256,
            user_token_policies: vec![OpcUaUserTokenPolicy {
                policy_id: "anonymous".into(),
                token_type: OpcUaUserTokenType::Anonymous,
            }],
            server_certificate_bytes: vec![1, 2, 3],
            transport_profile_uri: "http://example.com/transport".into(),
        };

        assert_serde_json_roundtrip_eq(&info);
    }

    #[test]
    fn serde_roundtrip_node() {
        let node = OpcUaNodeId {
            namespace: 2,
            inner: NodeIdInner::String("Temperature".into()),
        };

        assert_serde_json_roundtrip_eq(&node);
    }

    #[test]
    fn node_id_parses_string_form() {
        let node_name_str = "PLC1.MAIN.TEMP:SENSOR_0";
        let node_id_str = format!("ns=4;s={}", node_name_str);

        let parsed = node_id_str
            .parse::<OpcUaNodeId>()
            .expect("string node id should parse");

        assert_eq!(parsed.namespace, 4);
        assert_eq!(parsed.inner, NodeIdInner::String(node_name_str.into()));
    }

    #[test]
    fn node_id_parses_numeric_form() {
        let node_id_str = "ns=0;i=85";
        let parsed = node_id_str
            .parse::<OpcUaNodeId>()
            .expect("numeric node id should parse");

        assert_eq!(parsed.namespace, 0);
        assert_eq!(parsed.inner, NodeIdInner::Numeric(85));
        assert_eq!(parsed.to_string(), node_id_str);
    }

    #[test]
    fn node_id_fails_to_parse_malformed_identifier() {
        _ = "ns=0::t=85"
            .parse::<OpcUaNodeId>()
            .expect_err("malformed node id should fail to parse");
    }

    #[test]
    fn nod_id_fails_to_parse_malformed_namespace() {
        let parsed: Result<OpcUaNodeId, Error> = "nt=10000;i=85".parse();
        assert!(parsed.is_err());
    }

    #[test]
    fn serde_roundtrip_browse_node() {
        let browse = OpcUaNode {
            node_id: OpcUaNodeId {
                namespace: 0,
                inner: NodeIdInner::Numeric(85),
            },
            browse_name: "Objects".into(),
            display_name: "Objects".into(),
            node_class: OpcUaNodeClass::Object,
            children: vec![OpcUaNode {
                node_id: OpcUaNodeId {
                    namespace: 2,
                    inner: NodeIdInner::String("Temp".into()),
                },
                browse_name: "Temperature".into(),
                display_name: "Temperature".into(),
                node_class: OpcUaNodeClass::Variable,
                children: vec![],
            }],
        };

        assert_serde_json_roundtrip_eq(&browse);
    }

    #[test]
    fn serde_roundtrip_subscription_config() {
        let config = OpcUaSubscriptionConfig {
            publishing_interval: Duration::from_millis(500),
            background_poll_interval: Some(Duration::from_secs(1)),
            lifetime_count: 10_000,
            max_keep_alive_count: NonZeroU32::new(10).expect("nonzero"),
            max_notifications_per_publish: NonZeroU32::new(1_000).expect("nonzero"),
            priority: 0,
            publishing_enabled: true,
        };

        assert_serde_json_roundtrip_eq(&config);
    }

    #[test]
    fn serde_roundtrip_monitored_item_config() {
        let config = OpcUaMonitoredItemConfig {
            sampling_interval: Duration::from_millis(250),
            queue_size: 1,
            discard_oldest: true,
        };

        assert_serde_json_roundtrip_eq(&config);
    }

    #[test]
    fn serde_roundtrip_server_description() {
        let server_description = OpcUaServerDescription {
            endpoint_url: "opc.tcp://localhost:4840".into(),
            server_name: "Test".into(),
            security_mode: OpcUaSecurityMode::Sign,
            security_policy: OpcUaSecurityPolicy::Basic256Sha256,
            user_token_policy: OpcUaUserTokenPolicy {
                policy_id: "anonymous".into(),
                token_type: OpcUaUserTokenType::Anonymous,
            },
        };

        assert_serde_json_roundtrip_eq(&server_description);
    }

    #[test]
    fn user_token_anonymous_serializes_with_flattened_kind() {
        let token = OpcUaUserToken::anonymous("anon".into()).expect("token");

        let json = serde_json::to_value(&token).expect("serialize");
        assert_eq!(
            json,
            serde_json::json!({
                "policy_id": "anon",
                "kind": "anonymous",
            })
        );

        assert_serde_json_roundtrip_eq(&token);
    }

    #[test]
    fn user_token_basic_serializes_with_flattened_kind_and_credentials() {
        let token =
            OpcUaUserToken::basic("user".into(), "pw".into(), "basic".into()).expect("token");

        let json = serde_json::to_value(&token).expect("serialize");
        assert_eq!(
            json,
            serde_json::json!({
                "policy_id": "basic",
                "kind": "basic",
                "username": "user",
                "password": "pw",
            })
        );

        assert_serde_json_roundtrip_eq(&token);
    }

    #[test]
    fn user_token_certificate_serializes_with_flattened_kind_and_cert() {
        let token = OpcUaUserToken::certificate(vec![1, 2, 3], "cert".into()).expect("token");

        let json = serde_json::to_value(&token).expect("serialize");
        assert_eq!(
            json,
            serde_json::json!({
                "policy_id": "cert",
                "kind": "certificate",
                "cert": [1, 2, 3],
            })
        );

        assert_serde_json_roundtrip_eq(&token);
    }

    #[test]
    fn security_mode_serializes_as_snake_case() {
        assert_eq!(
            serde_json::to_value(OpcUaSecurityMode::SignAndEncrypt).expect("serialize"),
            serde_json::json!("sign_and_encrypt"),
        );
        assert_eq!(
            serde_json::to_value(OpcUaSecurityMode::None).expect("serialize"),
            serde_json::json!("none"),
        );
    }

    #[test]
    fn security_policy_serializes_as_snake_case() {
        assert_eq!(
            serde_json::to_value(OpcUaSecurityPolicy::Basic256Sha256).expect("serialize"),
            serde_json::json!("basic256_sha256"),
        );
        assert_eq!(
            serde_json::to_value(OpcUaSecurityPolicy::Aes256Sha256RsaPss).expect("serialize"),
            serde_json::json!("aes256_sha256_rsa_pss"),
        );
    }

    #[test]
    fn user_token_type_serializes_as_snake_case() {
        assert_eq!(
            serde_json::to_value(OpcUaUserTokenType::UserName).expect("serialize"),
            serde_json::json!("user_name"),
        );
        assert_eq!(
            serde_json::to_value(OpcUaUserTokenType::Anonymous).expect("serialize"),
            serde_json::json!("anonymous"),
        );
    }
}
