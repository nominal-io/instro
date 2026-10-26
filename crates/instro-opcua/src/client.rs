//! # Instro OPC UA Client
//!
//! ## [`ClientBuilder`] & [`Client`]
//!
//! [`ClientBuilder`] wraps the [`open62541::ClientBuilder`] and provides additional flexibility
//! when configuring the connection.
//!
//! [`Client`] wraps an [`AsyncClient`] with a performant, streaming- and configuration-oriented API.
//! Access to the underlying client is provided via [`Client::with_client`], so the client can be
//! used directly for other operations.
//!
//! ## Streaming Sessions
//!
//! Each session runs on its own thread, inside of a single-threaded tokio
//! [`Runtime`](tokio::runtime::Runtime) started when the session is constructed. Calling
//! [`StreamSession::stop`], [`StreamSession::stop_timeout`], or dropping the session will
//! trigger a cooperative shutdown of the runtime and thread.
//!
//! There are two modes of streaming: polling and subscribing. Regardless of mode, both accept
//! an `on_data` callback, which is invoked with each batch of unfiltered ([`ua::NodeId`],
//! [`open62541::DataValue`]) pairs. The session is managed opaquely by the [`StreamSession`] type.
//!
//! ### Polling
//!
//! Polling is started with [`Client::start_polling`], which configures a [`StreamSession`] to
//! periodically read the `Value` attribute for every node in `nodes` at the specified
//! `polling_interval`. It's the simplest mode, but comes with the overhead of polling all nodes
//! at every tick, which may be unnecessary for nodes that are not changing frequently.
//!
//! ### Subscriptions
//!
//! Subscriptions are started with [`Client::start_subscription`], which configures a
//! [`StreamSession`] to forward monitored-item notifications via a merged [`Stream`] of all
//! monitored items, delivering each sample through the same `on_data` callback.
//!
//! In general, subscriptions are more efficient than polling in terms of network traffic and
//! client resource usage and more generally applicable than imperatively polling. However, this
//! benefit comes at a cost: configuration of a subscription is more complex, and may introduce more
//! work for the server, depending on the system implementation.
//!

mod batch;
mod stream;

use std::collections::HashMap;
use std::collections::HashSet;
use std::fmt;
use std::future;
use std::num::NonZeroU32;
use std::str::FromStr;
use std::sync::Arc;
use std::sync::Weak;
use std::time::Duration;

use futures_util::FutureExt as _;
use futures_util::Stream;
use futures_util::StreamExt;
use futures_util::select_biased;
use futures_util::stream::select_all;
use itertools::Itertools as _;
use open62541::AsyncClient;
use open62541::AsyncMonitoredItem;
use open62541::Certificate;
use open62541::ClientBuilder as UaClientBuilder;
use open62541::DataType;
use open62541::DataValue;
use open62541::MonitoredItemCreateRequestBuilder;
use open62541::PrivateKey;
use open62541::PrivateKeyPasswordCallback;
use open62541::SubscriptionBuilder;
use open62541::create_certificate;
use open62541::ua;
use serde::Deserialize;
use serde::Serialize;
use tokio::sync::RwLock;
use tokio::task::yield_now;
use tokio::time::interval;
use zeroize::Zeroize as _;

pub use self::batch::NodeReadBatch;
use self::stream::ClientNodeReader;
use self::stream::IntervalPollTimer;
use self::stream::NodeReader;
use self::stream::PollTimer;
pub use self::stream::StreamSession;
use crate::Result;
use crate::error::ErrorExt as _;
use crate::error::OpcUaError;
use crate::error::UaErrorExt as _;
use crate::error::bail;
use crate::metrics::NodeReadCounts;
use crate::metrics::PollLoopMetricsLogger;

pub fn get_endpoints(endpoint_url: &str) -> Result<ua::Array<ua::EndpointDescription>> {
    open62541::ClientBuilder::default()
        .get_endpoints(endpoint_url)
        .with_ua_context(|| format!("getting endpoints for '{endpoint_url}'"))
}

#[derive(Debug)]
/// Wraps an [`AsyncClient`] with high-performance telemetry-streaming-oriented API designed for long-lived applications.
///
/// The client is configured and created via [`ClientBuilder`].
/// Streaming sessions started from this client own their own tokio runtime; see [`StreamSession`].
pub struct Client {
    client: Arc<RwLock<Option<AsyncClient>>>,
}

type VariantValue = DataValue<ua::Variant>;
type ReadNodeItem<'batch> = ((&'batch ua::NodeId, &'batch ua::AttributeId), VariantValue);

impl Client {
    pub(crate) fn upgrade(client: Weak<RwLock<Option<AsyncClient>>>) -> Option<Self> {
        client.upgrade().map(|client| Self {
            client: Arc::clone(&client),
        })
    }

    /// Gracefully disconnects from the server.
    ///
    /// This method will synchronize with disconnection of the client from the server.
    pub async fn disconnect(&self) {
        let client = {
            // tokio's `RwLock` should give us fair write-starvation protection
            let Some(client) = self.client.write().await.take() else {
                return;
            };

            client
        };

        client.disconnect().await;
    }

    /// Attempts to access the underlying [`AsyncClient`] and invoke `f` with it.
    ///
    /// Returns [`Err`] if the client has been disconnected before the function could be invoked.
    pub async fn with_client<F, T>(&self, f: F) -> Result<T>
    where
        F: for<'a> AsyncFnOnce(&'a AsyncClient) -> T,
    {
        let client_guard = self.client.read().await;
        let Some(client) = client_guard.as_ref() else {
            return Err(OpcUaError::ClientDisconnected);
        };

        Ok(f(client).await)
    }

    /// Starts polling the `VALUE` attribute of `nodes` at `polling_interval`,
    /// invoking `on_data` with each batch of unfiltered ([`ua::NodeId`],
    /// [`open62541::DataValue`]) pairs.
    ///
    /// The polling loop runs on a dedicated single-threaded runtime started by the returned
    /// [`StreamSession`].
    ///
    /// The runtime and thread are shut down when the session is dropped.
    #[must_use = "dropping the returned session will stop the polling loop"]
    pub fn start_polling<'nodes, N, F>(
        &self,
        nodes: N,
        polling_interval: Duration,
        on_data: F,
    ) -> Result<StreamSession>
    where
        N: IntoIterator<Item = &'nodes ua::NodeId>,
        F: FnMut(Box<dyn Iterator<Item = (ua::NodeId, VariantValue)> + 'static>) + Send + 'static,
    {
        let reader = ClientNodeReader::new(&self.client);

        let nodes = NodeReadBatch::new_cartesian(nodes, &[ua::AttributeId::VALUE]);

        StreamSession::new("opcua-poll-loop", async move || {
            // Downgrade the client to a weak reference to avoid holding onto the strong reference.
            // Increases the chances of success when using `Arc::into_inner` for shutdown.
            Self::poll_loop(reader, nodes, polling_interval, on_data).await;
        })
    }

    /// Reads the configured attribute for every node in `node_list`.
    ///
    /// Returns exactly one value for each node and attribute pair, in request order. Values are
    /// returned unfiltered; callers must inspect [`DataValue::status`] and [`DataValue::value`].
    ///
    /// Returns [`Err`] if the underlying batched [`AsyncClient::read_many_attributes`] call fails
    /// or returns a different number of values than requested.
    pub async fn read_nodes<'batch>(
        &self,
        node_list: &'batch NodeReadBatch,
    ) -> Result<impl Iterator<Item = ReadNodeItem<'batch>> + use<'batch>> {
        let read_result = self
            .with_client(async |client| {
                client
                    .read_many_attributes(node_list.pairs())
                    .await
                    .ua_context("requesting values from remote server")
            })
            .await??;

        if read_result.len() != node_list.len() {
            bail!(
                internal,
                "length mismatch between requested and received values: {} != {}",
                read_result.len(),
                node_list.len()
            )
        }

        Ok(node_list.keys().zip(read_result))
    }

    /// Starts a server-push subscription for the given `nodes`, invoking
    /// `on_data` as new samples arrive.
    ///
    /// Subscription creation and monitored-item setup are performed eagerly
    /// before this method returns; the ongoing pump runs on a dedicated single-threaded runtime
    /// started by the returned [`StreamSession`].
    ///
    /// The runtime and thread are shut down when the session is dropped.
    #[must_use = "dropping the returned session will deregister the subscription"]
    pub async fn start_subscription<'nodes, N, F>(
        &self,
        nodes: N,
        sub_config: SubscriptionConfig,
        item: SubscriptionItemConfig,
        on_data: F,
    ) -> Result<StreamSession>
    where
        N: IntoIterator<Item = &'nodes ua::NodeId>,
        F: FnMut(Box<dyn Iterator<Item = (ua::NodeId, VariantValue)> + 'static>) + Send + 'static,
    {
        let subscription_builder = SubscriptionBuilder::from(sub_config);

        let (_, subscription) = self
            .with_client(async |client| {
                subscription_builder
                    .create(client)
                    .await
                    .context("creating subscription")
            })
            .await??;

        let nodes = nodes.into_iter().collect_vec();

        // Configure the `AsyncSubscription` and per-node monitoring config
        let item_builder = item.apply_to_builder(MonitoredItemCreateRequestBuilder::new(
            nodes.iter().map(|&node| node.clone()),
        ));

        let nodes = nodes.into_iter().cloned().collect_vec();

        let item_results = AsyncMonitoredItem::create(&subscription, item_builder)
            .await
            .context("creating monitored items")?;

        if item_results.len() != nodes.len() {
            bail!(
                internal,
                "length mismatch between requested and registered monitored items: {} != {}",
                item_results.len(),
                nodes.len()
            )
        }

        // Merge monitored-item streams into one pump to avoid per-node tasks; the pump does not
        // block on IO, so one task can service all items.
        let requested_count = nodes.len();
        let mut valid_streams = Vec::with_capacity(requested_count);

        for (node, result) in nodes.iter().cloned().zip(item_results) {
            match result {
                Ok((create_result, monitored_item)) => {
                    // validation does nothing here beyond emitting warnings
                    item.check_result(&node, &create_result);
                    valid_streams.push(monitored_item.map(move |value| (node.clone(), value)));
                }

                Err(e) => {
                    tracing::warn!(
                        target: "opcua::client::subscribe",
                        error = ?e,
                        "skipping monitored item creation for node {node}"
                    );
                }
            }
        }

        if valid_streams.is_empty() {
            bail!(
                internal,
                "no valid monitored item streams were created; all {requested_count} requested \
                 node(s) failed"
            )
        }

        let reader = ClientNodeReader::new(&self.client);

        let timer = sub_config
            .background_poll_interval
            .filter(|period| !period.is_zero())
            .map(IntervalPollTimer::new);

        let nodes = NodeReadBatch::new_cartesian(&nodes, &[ua::AttributeId::VALUE]);

        StreamSession::new("opcua-subscription-pump", async move || {
            let _subscription = subscription;
            Self::subscription_loop(reader, nodes, select_all(valid_streams), on_data, timer).await;
        })
    }

    /// How often the poll loop emits the aggregated metrics log line.
    const POLL_LOOP_METRICS_FLUSH_INTERVAL: Duration = Duration::from_secs(5);

    /// Polls `nodes` at `polling_interval`, yielding unfiltered samples through `on_data`.
    async fn poll_loop<R: NodeReader>(
        reader: R,
        nodes: NodeReadBatch,
        polling_interval: Duration,
        mut on_data: impl FnMut(Box<dyn Iterator<Item = (ua::NodeId, VariantValue)> + 'static>),
    ) {
        let mut metrics = PollLoopMetricsLogger::new(Self::POLL_LOOP_METRICS_FLUSH_INTERVAL);

        let mut maybe_interval = if polling_interval.is_zero() {
            None
        } else {
            // missed-tick behavior defaults to `Burst`, which is what we want
            Some(interval(polling_interval))
        };

        loop {
            if let Some(ref mut interval) = maybe_interval {
                interval.tick().await;
            } else {
                yield_now().await;
            }

            let read_start = metrics.start_read();

            let (outcome, samples) = match reader.read_nodes(&nodes).await {
                Ok(values) => {
                    let values = values
                        .map(|((id, _), value)| (id.clone(), value))
                        .collect_vec();

                    let sample_count = values.len() as u64;
                    let invalid_samples = values
                        .iter()
                        .filter(|(_, value)| {
                            value.value().is_none()
                                || value.status().is_some_and(ua::StatusCode::is_bad)
                        })
                        .count() as u64;

                    (
                        Some(NodeReadCounts {
                            valid_samples: sample_count.saturating_sub(invalid_samples),
                            invalid_samples,
                        }),
                        Some(values),
                    )
                }

                Err(OpcUaError::ClientDisconnected) => {
                    tracing::info!(target: "opcua::client::poll", "stopping poll loop");
                    break;
                }

                Err(e) => {
                    tracing::error!(
                        target: "opcua::client::poll",
                        err = ?e,
                        "error reading nodes from poll loop"
                    );

                    (None, None)
                }
            };

            metrics.finish_read(read_start, outcome);

            if let Some(samples) = samples {
                on_data(Box::new(samples.into_iter()));
            }
        }
    }

    /// Pumps subscription notifications and optionally polls nodes that go quiet.
    ///
    /// Notifications are emitted as they arrive and mark their node active for the current
    /// interval. On a poll tick, nodes that did not notify are read directly and buffered for one
    /// tick, giving late notifications a chance to win deduplication by timestamp.
    async fn subscription_loop<R, S, F, T>(
        reader: R,
        batch: NodeReadBatch,
        stream: S,
        mut on_data: F,
        mut timer: Option<T>,
    ) where
        R: NodeReader,
        S: Stream<Item = (ua::NodeId, VariantValue)> + Send + Unpin + 'static,
        F: FnMut(Box<dyn Iterator<Item = (ua::NodeId, VariantValue)> + 'static>),
        T: PollTimer,
    {
        // Nodes still quiet in the current interval; notifications remove nodes until the next tick.
        let mut quiet_nodes = batch.nodes().cloned().collect::<HashSet<_>>();

        // Poll results wait one tick before flushing so late notifications can dedup by timestamp.
        let mut polled_nodes = HashMap::<ua::NodeId, VariantValue>::new();

        let mut stream = stream.ready_chunks(10);

        loop {
            let maybe_tick = async {
                if let Some(timer) = timer.as_mut() {
                    timer.tick().await;
                } else {
                    future::pending::<()>().await;
                }
            };

            select_biased! {
                // Ticks are first so ready poll work is not starved by a burst of notifications.
                _ = maybe_tick.fuse() => {
                    let polled_samples_to_flush = polled_nodes
                        .drain()
                        .collect_vec();

                    on_data(Box::new(polled_samples_to_flush.into_iter()));

                    if !quiet_nodes.is_empty() {
                        #[allow(clippy::unwrap_used, reason = "nodes are valid because they were previously validated in the original batch")]
                        let nodes = NodeReadBatch::new_cartesian(&quiet_nodes, &[ua::AttributeId::VALUE]);

                        let reads = match reader.read_nodes(&nodes).await {
                            Ok(reads) => reads,
                            Err(OpcUaError::ClientDisconnected) => break,

                            Err(e) => {
                                tracing::error!(
                                    target: "opcua::client::subscribe",
                                    error = ?e,
                                    "error reading nodes in subscription loop"
                                );

                                continue;
                            }
                        };

                        let to_flush = reads
                            .into_iter()
                            .map(|((id, _), sample)| (id.clone(), sample));

                        polled_nodes.extend(to_flush);
                    }

                    quiet_nodes = batch
                        .nodes()
                        .cloned()
                        .collect();
                },

                chunk = stream.next().fuse() => if let Some(chunk) = chunk {
                    let mut samples = Vec::with_capacity(chunk.len() * 2);

                    for (node_id, data) in chunk {
                        quiet_nodes.remove(&node_id);

                        // Older poll values preserve ordering; same-or-newer poll values are stale.
                        if let Some(polled_sample) = polled_nodes.remove(&node_id)
                            && let Some(polled_sample) = polled_sample_filter(polled_sample, &data)
                        {
                            samples.push((node_id.clone(), polled_sample));
                        }

                        samples.push((node_id, data));
                    }

                    if !samples.is_empty() {
                        on_data(Box::new(samples.into_iter()));
                    }
                } else {
                    tracing::info!(
                        target: "opcua::client::subscribe",
                        "subscription pump received no more items, stopping"
                    );

                    break;
                },
            }
        }

        tracing::info!(
            target: "opcua::client::subscribe",
            "subscription loop stopped"
        );

        on_data(Box::new(polled_nodes.into_iter()));
    }
}

/// Takes removed polled sample and returns it if the subscription data is newer.
/// Otherwise, the polled sample is forgotten and this function returns `None`.
fn polled_sample_filter(polled: VariantValue, sub_data: &VariantValue) -> Option<VariantValue> {
    if let Some(poll_src_ts) = polled.source_timestamp()
        && let Some(sub_src_ts) = sub_data.source_timestamp()
        && poll_src_ts < sub_src_ts
    {
        Some(polled)
    } else if let Some(poll_srv_ts) = polled.server_timestamp()
        && let Some(sub_srv_ts) = sub_data.server_timestamp()
        && poll_srv_ts < sub_srv_ts
    {
        Some(polled)
    } else {
        None
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum SecurityPolicyUri {
    None,
    Basic128Rsa15,
    Basic256,
    Basic256Sha256,
    Aes128Sha256RsaOaep,
    Aes256Sha256RsaPss,
}

impl FromStr for SecurityPolicyUri {
    type Err = OpcUaError;

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
            _ => bail!(internal, "invalid security policy URI: {s}"),
        })
    }
}

impl fmt::Display for SecurityPolicyUri {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> std::fmt::Result {
        self.as_policy_uri_str().fmt(f)
    }
}

impl SecurityPolicyUri {
    pub const fn as_policy_uri_str(self) -> &'static str {
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

#[derive(Debug)]
/// Builder for [`Client`].
///
/// Provides a fluent API for configuring the both the [`Client`] and the backing [`AsyncClient`]
/// before connecting to an OPC-UA endpoint.
// Unfortunately there's no easy way to layer builders that take self by move
// so we need to re-implement the entire builder API here.
pub struct ClientBuilder(UaClientBuilder);

impl ClientBuilder {
    /// Creates a new builder for an unencrypted client.
    pub fn new(pki: PkiConfig) -> Result<Self> {
        let builder = match pki {
            PkiConfig::UseProvided(certificate, private_key) => {
                UaClientBuilder::default_encryption(&certificate, &private_key)
                    .context("creating encrypted client builder")?
            }

            PkiConfig::GenerateSelfSigned => {
                let (certificate, private_key) = generate_self_signed_cert()?;
                UaClientBuilder::default_encryption(&certificate, &private_key)
                    .context("creating encrypted client builder")?
            }

            PkiConfig::None => UaClientBuilder::default()
        };

        Ok(Self(builder))
    }

    /// Sets whether to accept any server certificate.
    pub fn with_trust_server_certs(self) -> Self {
        Self(self.0.accept_all())
    }

    /// Sets the user identity token to use for the client.
    pub fn with_identity_token(self, token: &ua::UserIdentityToken) -> Self {
        Self(self.0.user_identity_token(token))
    }

    /// Sets the security mode to use for the client.
    pub fn with_security_mode(self, mode: ua::MessageSecurityMode) -> Self {
        Self(self.0.security_mode(mode))
    }

    /// Sets the security policy to use for the client.
    pub fn with_security_policy(self, security_policy: SecurityPolicyUri) -> Self {
        #[expect(clippy::expect_used)]
        let uri = ua::String::new(security_policy.as_policy_uri_str()).expect(
            "security policy URIs are statics and well-formed, failure is likely an allocation \
             error from open62541",
        );

        Self(self.0.security_policy_uri(uri))
    }

    /// Sets the timeout to use for the client.
    pub fn with_timeout(self, timeout: Duration) -> Self {
        Self(self.0.timeout(timeout))
    }

    /// Sets the certificate verification to use for the client.
    pub fn with_verification(self, verification: ua::CertificateVerification) -> Self {
        Self(self.0.certificate_verification(verification))
    }

    /// Sets the private key password callback to use for the client.
    pub fn with_private_key_password_callback<F>(self, callback: F) -> Self
    where
        F: PrivateKeyPasswordCallback + 'static,
    {
        Self(self.0.private_key_password_callback(callback))
    }

    /// Sets the connectivity check interval to use for the client.
    pub fn with_connectivity_check_interval(self, interval: Duration) -> Self {
        Self(self.0.connectivity_check_interval(Some(interval)))
    }

    /// Sets the requested session timeout to use for the client.
    pub fn with_requested_session_timeout(self, timeout: Duration) -> Self {
        Self(self.0.requested_session_timeout(timeout))
    }

    /// Sets the secure channel lifetime to use for the client.
    pub fn with_secure_channel_lifetime(self, lifetime: Duration) -> Self {
        Self(self.0.secure_channel_life_time(lifetime))
    }

    fn instro_app_description() -> ua::ApplicationDescription {
        ua::ApplicationDescription::init()
            .with_application_name("en-US", "Nominal OPC UA Client")
            .with_product_uri("urn:nominal:opcua-client")
            .with_application_type(ua::ApplicationType::CLIENT)
            .with_application_uri("urn:nominal:opcua-client")
    }

    /// Consumes the builder and connects to the endpoint at the given URL, returning a [`Client`].
    #[must_use = "dropping the returned client will immediately disconnect from the OPC UA server"]
    pub fn connect(self, endpoint_url: &str) -> Result<Client> {
        let Self(builder) = self;

        let client = builder
            .client_description(Self::instro_app_description())
            .connect(endpoint_url)
            .ua_context("connecting to endpoint")?
            .into_async();

        Ok(Client::new(client))
    }
}

impl Client {
    fn new(client: AsyncClient) -> Self {
        Self {
            client: Arc::new(RwLock::new(Some(client))),
        }
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
pub struct SubscriptionConfig {
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

impl From<SubscriptionConfig> for SubscriptionBuilder {
    fn from(config: SubscriptionConfig) -> Self {
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
pub struct SubscriptionItemConfig {
    /// Interval at which the server samples the underlying node for value changes.
    pub sampling_interval: Duration,

    /// Maximum number of samples the server buffers for this item between publish cycles.
    pub queue_size: u32,

    /// When the queue is full, whether the server drops the oldest sample (`true`) or the newest
    /// one (`false`).
    pub discard_oldest: bool,
}

impl SubscriptionItemConfig {
    /// Validates the result of a monitored item creation request, emitting warnings for any revisions that are not as requested.
    pub(crate) fn check_result(
        &self,
        node_id: &ua::NodeId,
        create_result: &ua::MonitoredItemCreateResult,
    ) {
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

    /// Applies this configuration's knobs to an existing [`MonitoredItemCreateRequestBuilder`],
    /// preserving its type-state and node-id selection.
    #[must_use]
    pub(crate) fn apply_to_builder<B>(
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

#[derive(Debug, Default, Clone, Serialize, Deserialize, PartialEq, Eq)]
enum RawPki {
    #[default]
    None,
    GenerateSelfSigned,
    UseProvided(Vec<u8>, Vec<u8>),
}

impl TryFrom<RawPki> for PkiConfig {
    type Error = OpcUaError;

    fn try_from(raw: RawPki) -> Result<Self> {
        Ok(match raw {
            RawPki::None => Self::None,
            RawPki::GenerateSelfSigned => Self::GenerateSelfSigned,
            RawPki::UseProvided(certificate, mut private_key) => {
                let cert = Certificate::from_bytes(&certificate);

                // construction of the certificate does not validate the certificate
                // validation only occurs when materializing the certificate via `x509_certificate`
                // basically a sanity check on deserialization
                let pki_result = cert
                    .clone()
                    .into_x509()
                    .context("converting provided certificate to x509")
                    .map(|_| Self::UseProvided(cert, PrivateKey::from_bytes(&private_key)));

                private_key.zeroize();

                pki_result?
            }
        })
    }
}

impl From<PkiConfig> for RawPki {
    fn from(pki: PkiConfig) -> Self {
        match pki {
            PkiConfig::None => RawPki::None,
            PkiConfig::GenerateSelfSigned => RawPki::GenerateSelfSigned,
            PkiConfig::UseProvided(certificate, private_key) => RawPki::UseProvided(
                certificate.as_bytes().to_vec(),
                private_key.as_bytes().to_vec(),
            ),
        }
    }
}

#[derive(Debug, Default, Clone, Serialize, Deserialize)]
#[serde(try_from = "RawPki", into = "RawPki")]
pub enum PkiConfig {
    #[default]
    None,
    GenerateSelfSigned,
    UseProvided(Certificate, PrivateKey),
}

impl PartialEq for PkiConfig {
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

impl Eq for PkiConfig {}

/// Generates a self-signed X.509 certificate/key pair suitable for OPC UA client authentication.
///
/// Returns a tuple containing the certificate and private key or an error if the certificate generation fails.
pub fn generate_self_signed_cert() -> Result<(Certificate, PrivateKey)> {
    let subject = ua::Array::from_slice(&[
        ua::String::new("C=US").map_err(OpcUaError::Ua)?,
        ua::String::new("O=Nominal").map_err(OpcUaError::Ua)?,
        ua::String::new("CN=Nominal@localhost").map_err(OpcUaError::Ua)?,
    ]);

    let subject_alt_name = ua::Array::from_slice(&[
        ua::String::new("DNS:localhost").map_err(OpcUaError::Ua)?,
        ua::String::new("URI:urn:nominal:instro-opcua-client").map_err(OpcUaError::Ua)?,
    ]);

    create_certificate(
        &subject,
        &subject_alt_name,
        &ua::CertificateFormat::PEM,
        None,
    )
    .map_err(OpcUaError::Ua)
}

#[cfg(test)]
mod tests {
    //! Deterministic unit tests for [`OpcUaClient::subscription_loop`].
    //!
    //! The loop is driven against in-memory [`NodeReader`]/[`PollTimer`] mocks and a
    //! channel-backed notification stream, so poll ticks, reads, and notifications are
    //! sequenced explicitly rather than racing a live server's wall-clock timers. Each test
    //! spawns the loop, drives inputs behind barriers (a read-completion signal and the
    //! emitted output batches), then ends the loop by closing the stream (or, for the
    //! dropped-client test, by flipping the reader's liveness flag) and asserts on the
    //! collected output.

    use std::collections::HashSet;
    use std::sync::Arc;
    use std::sync::Mutex;
    use std::time::Duration;

    use futures_util::Stream;
    use futures_util::StreamExt as _;
    use futures_util::stream;
    use open62541::ua;
    use tokio::sync::mpsc;
    use tokio::time::timeout;

    use super::Client;
    use super::NodeReadBatch;
    use super::NodeReader;
    use super::PollTimer;
    use super::ReadNodeItem;
    use super::VariantValue;
    use super::generate_self_signed_cert;
    use crate::Result;
    use crate::error::OpcUaError;

    /// Generous deadline; the deterministic loop should make progress near-instantly, so this
    /// only fires if the loop wedges (which is itself a test failure worth surfacing).
    const TEST_TIMEOUT: Duration = Duration::from_secs(5);

    /// Externally-controllable state shared between the test and its [`MockNodeReader`].
    struct ReaderState {
        /// Drives [`NodeReader::is_alive`]; flip to `false` to simulate a dropped client.
        is_alive: bool,
        /// Server timestamp stamped on the next read's samples; incremented per read.
        next_timestamp: u64,
        /// The set of node ids requested on each `read_nodes` call, in call order.
        requested: Vec<HashSet<ua::NodeId>>,
    }

    /// In-memory [`NodeReader`] returning canned samples with test-controlled timestamps.
    struct MockNodeReader {
        state: Arc<Mutex<ReaderState>>,
        /// Pulsed after every completed read so the test can barrier on "the buffer is populated".
        read_done: mpsc::UnboundedSender<()>,
    }

    impl MockNodeReader {
        fn new(
            initial_timestamp: u64,
        ) -> (Self, Arc<Mutex<ReaderState>>, mpsc::UnboundedReceiver<()>) {
            let state = Arc::new(Mutex::new(ReaderState {
                is_alive: true,
                next_timestamp: initial_timestamp,
                requested: Vec::new(),
            }));

            let (read_done, read_done_rx) = mpsc::unbounded_channel();

            let reader = Self {
                state: Arc::clone(&state),
                read_done,
            };

            (reader, state, read_done_rx)
        }
    }

    impl NodeReader for MockNodeReader {
        async fn read_nodes<'batch>(
            &self,
            batch: &'batch NodeReadBatch,
        ) -> Result<impl Iterator<Item = ReadNodeItem<'batch>>> {
            let samples = {
                let mut state = self.state.lock().unwrap();

                if !state.is_alive {
                    return Err(OpcUaError::ClientDisconnected);
                }

                let timestamp = state.next_timestamp;
                state.next_timestamp = state.next_timestamp.saturating_add(100);

                let requested = batch.nodes().cloned().collect::<HashSet<_>>();

                state.requested.push(requested);

                batch
                    .keys()
                    .map(move |item| (item, datapoint(timestamp, 0.0)))
            };

            // Signal *after* the lock is released so the barrier reflects committed state.
            let _ = self.read_done.send(());

            Ok(samples)
        }
    }

    /// [`PollTimer`] that fires exactly once per test-issued pulse.
    struct MockPollTimer {
        pulses: mpsc::UnboundedReceiver<()>,
    }

    impl PollTimer for MockPollTimer {
        async fn tick(&mut self) {
            // Once the test drops the pulse sender, park forever so the loop simply waits on
            // its other branches rather than spinning on a closed channel.
            if self.pulses.recv().await.is_none() {
                std::future::pending::<()>().await;
            }
        }
    }

    fn datapoint(ts: u64, value: f64) -> VariantValue {
        let timestamp = ua::DateTime::try_from_unix_timestamp_nanos(ts as i128).unwrap();
        let variant = ua::Variant::scalar(ua::Double::new(value));
        ua::DataValue::new(variant)
            .with_server_timestamp(&timestamp)
            .cast::<ua::Variant>()
    }

    /// Wraps an unbounded channel as a notification [`Stream`]; the stream ends when the sender
    /// is dropped, after draining any buffered items (exercising the loop's exit drain).
    fn notification_stream(
        rx: mpsc::UnboundedReceiver<(ua::NodeId, VariantValue)>,
    ) -> impl Stream<Item = (ua::NodeId, VariantValue)> + Send + Unpin + 'static {
        stream::unfold(rx, |mut rx| async move {
            rx.recv().await.map(|item| (item, rx))
        })
        .boxed()
    }

    /// Collects emitted samples into batches the test forwards through `on_data`.
    fn output_sink() -> (
        impl FnMut(Box<dyn Iterator<Item = (ua::NodeId, VariantValue)> + 'static>) + Send + 'static,
        mpsc::UnboundedReceiver<Vec<(ua::NodeId, VariantValue)>>,
    ) {
        let (tx, rx) = mpsc::unbounded_channel::<Vec<_>>();

        let on_data = move |samples: Box<dyn Iterator<Item = _> + 'static>| {
            let _ = tx.send(samples.collect::<Vec<_>>());
        };

        (on_data, rx)
    }

    async fn await_signal(rx: &mut mpsc::UnboundedReceiver<()>, what: &str) {
        timeout(TEST_TIMEOUT, rx.recv())
            .await
            .unwrap_or_else(|_| panic!("timed out waiting for {what}"))
            .unwrap_or_else(|| panic!("channel closed waiting for {what}"));
    }

    /// Awaits the next non-empty output batch, used as a barrier that a notification (or flush)
    /// has been emitted. Empty flush batches are skipped.
    async fn recv_nonempty(
        rx: &mut mpsc::UnboundedReceiver<Vec<(ua::NodeId, VariantValue)>>,
        what: &str,
    ) -> Vec<(ua::NodeId, VariantValue)> {
        loop {
            let batch = timeout(TEST_TIMEOUT, rx.recv())
                .await
                .unwrap_or_else(|_| panic!("timed out waiting for {what}"))
                .unwrap_or_else(|| panic!("channel closed waiting for {what}"));

            if !batch.is_empty() {
                return batch;
            }
        }
    }

    /// Synchronously drains whatever batches remain after the loop has terminated.
    fn drain_batches(
        rx: &mut mpsc::UnboundedReceiver<Vec<(ua::NodeId, VariantValue)>>,
    ) -> Vec<Vec<(ua::NodeId, VariantValue)>> {
        let mut batches = Vec::new();

        while let Ok(batch) = rx.try_recv() {
            batches.push(batch);
        }

        batches
    }

    async fn await_loop(handle: tokio::task::JoinHandle<()>) {
        timeout(TEST_TIMEOUT, handle)
            .await
            .expect("subscription loop did not terminate")
            .unwrap();
    }

    /// A node that never receives a subscription notification is polled once per tick: every
    /// emitted sample is therefore a background poll. K ticks yield K distinct polled samples
    /// (K-1 via the next tick's flush, the last via the exit drain).
    #[tokio::test]
    async fn polls_static_node_each_tick() -> Result<()> {
        let x = ua::NodeId::string(1, "Static");
        let (reader, state, mut read_done_rx) = MockNodeReader::new(100);
        let (note_tx, note_rx) = mpsc::unbounded_channel();
        let (pulse_tx, pulse_rx) = mpsc::unbounded_channel();
        let (on_data, mut out_rx) = output_sink();

        let handle = tokio::spawn(Client::subscription_loop(
            reader,
            NodeReadBatch::new_cartesian([&x], &[ua::AttributeId::VALUE]),
            notification_stream(note_rx),
            on_data,
            Some(MockPollTimer { pulses: pulse_rx }),
        ));

        // Three ticks, each fully processed (barrier on the read) before the next.
        for _ in 0..3 {
            pulse_tx.send(()).unwrap();
            await_signal(&mut read_done_rx, "background poll read").await;
        }

        drop(note_tx); // close the stream -> loop breaks and drains the final buffered sample
        await_loop(handle).await;

        let samples = drain_batches(&mut out_rx)
            .into_iter()
            .flatten()
            .collect::<Vec<_>>();

        assert_eq!(samples.len(), 3, "expected one polled sample per tick");

        for (node_id, _) in &samples {
            assert_eq!(node_id, &x);
        }

        let timestamps = samples
            .iter()
            .map(|(_, sample)| sample.server_timestamp().unwrap().as_unix_timestamp_nanos())
            .collect::<Vec<_>>();

        assert_eq!(
            timestamps,
            vec![100, 200, 300],
            "polled samples should carry successive read timestamps",
        );

        // Three reads happened, each requesting the single static node.
        let requested = state.lock().unwrap().requested.clone();

        assert_eq!(requested.len(), 3);

        Ok(())
    }

    /// When a buffered polled sample is not older than an arriving notification, it is dropped
    /// in favour of the live notification (the dedup window).
    #[tokio::test]
    async fn dedup_drops_stale_polled_when_notification_not_newer() -> Result<()> {
        let x = ua::NodeId::string(1, "Node");
        let (reader, state, mut read_done_rx) = MockNodeReader::new(100);
        let (note_tx, note_rx) = mpsc::unbounded_channel();
        let (pulse_tx, pulse_rx) = mpsc::unbounded_channel();
        let (on_data, mut out_rx) = output_sink();

        let handle = tokio::spawn(Client::subscription_loop(
            reader,
            NodeReadBatch::new_cartesian([&x], &[ua::AttributeId::VALUE]),
            notification_stream(note_rx),
            on_data,
            Some(MockPollTimer { pulses: pulse_rx }),
        ));

        // Tick buffers a polled sample at ts=100.
        pulse_tx.send(()).unwrap();
        await_signal(&mut read_done_rx, "background poll read").await;

        // Notification at ts=100 (not newer) -> buffered polled sample dropped, only this emitted.
        note_tx
            .send((x.clone(), datapoint(100, 1.0)))
            .expect("sending notification");

        drop(note_tx);
        await_loop(handle).await;

        let samples = drain_batches(&mut out_rx)
            .into_iter()
            .flatten()
            .collect::<Vec<_>>();

        assert_eq!(samples.len(), 1, "stale polled sample should be dropped");
        let (node_id, sample) = samples.first().expect("missing notification sample");
        assert_eq!(node_id, &x);
        assert_eq!(
            sample
                .server_timestamp()
                .map(ua::DateTime::as_unix_timestamp_nanos),
            Some(100)
        );
        assert_eq!(
            sample.value().unwrap(),
            &ua::Variant::scalar(ua::Double::new(1.0))
        );

        let _ = state; // shared handle kept alive for the loop's duration
        Ok(())
    }

    /// When a buffered polled sample is older than an arriving notification, both are emitted,
    /// polled first, preserving temporal continuity.
    #[tokio::test]
    async fn emits_both_when_polled_older_than_notification() -> Result<()> {
        let x = ua::NodeId::string(1, "Node");
        let (reader, _state, mut read_done_rx) = MockNodeReader::new(100);
        let (note_tx, note_rx) = mpsc::unbounded_channel();
        let (pulse_tx, pulse_rx) = mpsc::unbounded_channel();
        let (on_data, mut out_rx) = output_sink();

        let handle = tokio::spawn(Client::subscription_loop(
            reader,
            NodeReadBatch::new_cartesian([&x], &[ua::AttributeId::VALUE]),
            notification_stream(note_rx),
            on_data,
            Some(MockPollTimer { pulses: pulse_rx }),
        ));

        // Tick buffers a polled sample at ts=100.
        pulse_tx.send(()).expect("sending tick should not fail");
        await_signal(&mut read_done_rx, "background poll read").await;

        // Notification at ts=200 (newer) -> emit polled@100 then notification@200.
        note_tx
            .send((x, datapoint(200, 2.0)))
            .expect("sending notification should not fail");

        drop(note_tx);
        await_loop(handle).await;

        let samples = drain_batches(&mut out_rx)
            .into_iter()
            .flatten()
            .collect::<Vec<_>>();

        assert_eq!(samples.len(), 2, "both polled and notification should emit");

        let (_, sample) = samples.first().expect("should have a polled sample");
        let (_, notification) = samples.get(1).expect("should have a notification sample");

        assert_eq!(
            sample
                .server_timestamp()
                .map(ua::DateTime::as_unix_timestamp_nanos),
            Some(100),
            "polled sample emitted first"
        );
        assert_eq!(
            notification
                .server_timestamp()
                .map(ua::DateTime::as_unix_timestamp_nanos),
            Some(200)
        );

        Ok(())
    }

    /// Buffered polled samples that never meet a notification are flushed when the stream closes.
    #[tokio::test]
    async fn flush_on_exit_drains_buffer() -> Result<()> {
        let x = ua::NodeId::string(1, "Static");
        let (reader, _state, mut read_done_rx) = MockNodeReader::new(100);
        let (note_tx, note_rx) = mpsc::unbounded_channel();
        let (pulse_tx, pulse_rx) = mpsc::unbounded_channel();
        let (on_data, mut out_rx) = output_sink();

        let handle = tokio::spawn(Client::subscription_loop(
            reader,
            NodeReadBatch::new_cartesian([&x], &[ua::AttributeId::VALUE]),
            notification_stream(note_rx),
            on_data,
            Some(MockPollTimer { pulses: pulse_rx }),
        ));

        // One tick buffers a polled sample; close the stream before any further tick flushes it.
        pulse_tx.send(()).expect("sending tick should not fail");
        await_signal(&mut read_done_rx, "background poll read").await;
        drop(note_tx);
        await_loop(handle).await;

        let samples = drain_batches(&mut out_rx)
            .into_iter()
            .flatten()
            .collect::<Vec<_>>();

        assert_eq!(
            samples.len(),
            1,
            "exit drain should flush the buffered sample"
        );

        let (node_id, sample) = samples.first().expect("should have a drained sample");
        assert_eq!(node_id, &x);
        assert_eq!(
            sample
                .server_timestamp()
                .map(ua::DateTime::as_unix_timestamp_nanos),
            Some(100)
        );

        Ok(())
    }

    /// A node that notifies during one interval is excluded from that interval's poll, but is
    /// polled again once it goes quiet (the `quiet_nodes` reset each tick).
    #[tokio::test]
    async fn quiet_node_repolled_after_going_quiet() -> Result<()> {
        let x = ua::NodeId::string(1, "Static");
        let y = ua::NodeId::string(2, "Active");
        let (reader, state, mut read_done_rx) = MockNodeReader::new(1);
        let (note_tx, note_rx) = mpsc::unbounded_channel();
        let (pulse_tx, pulse_rx) = mpsc::unbounded_channel();
        let (on_data, mut out_rx) = output_sink();

        let handle = tokio::spawn(Client::subscription_loop(
            reader,
            NodeReadBatch::new_cartesian([&x, &y], &[ua::AttributeId::VALUE]),
            notification_stream(note_rx),
            on_data,
            Some(MockPollTimer { pulses: pulse_rx }),
        ));

        // Tick 1: both nodes quiet -> both polled.
        pulse_tx.send(()).expect("sending tick should not fail");
        await_signal(&mut read_done_rx, "tick 1 read").await;

        // Y notifies -> removed from quiet for the current interval.
        note_tx
            .send((y.clone(), datapoint(10, 1.0)))
            .expect("sending Y notification should not fail");

        recv_nonempty(&mut out_rx, "Y notification batch").await;

        // Tick 2: Y excluded (it notified), only X polled.
        pulse_tx.send(()).expect("sending tick should not fail");
        await_signal(&mut read_done_rx, "tick 2 read").await;

        // Tick 3: Y has gone quiet again -> polled once more.
        pulse_tx.send(()).expect("sending tick should not fail");
        await_signal(&mut read_done_rx, "tick 3 read").await;

        drop(note_tx);
        await_loop(handle).await;
        let _ = drain_batches(&mut out_rx);

        let requested = state.lock().unwrap().requested.clone();

        assert_eq!(requested.len(), 3, "expected three poll reads");

        let both = HashSet::from([x.clone(), y.clone()]);
        let only_x = HashSet::from([x.clone()]);
        assert_eq!(requested.first(), Some(&both), "tick 1 polls both nodes");
        assert_eq!(
            requested.get(1),
            Some(&only_x),
            "tick 2 excludes the active node"
        );
        assert_eq!(
            requested.get(2),
            Some(&both),
            "tick 3 re-polls the now-quiet node"
        );

        Ok(())
    }

    /// Without a poll interval there is no timer, so no background reads occur: only subscription
    /// notifications are emitted. This is the control proving polling — not the subscription —
    /// drives the periodic samples in the other tests.
    #[tokio::test]
    async fn none_interval_emits_only_notifications() -> Result<()> {
        let x = ua::NodeId::string(1, "Static");
        let (reader, state, _read_done_rx) = MockNodeReader::new(1);
        let (note_tx, note_rx) = mpsc::unbounded_channel();
        let (on_data, mut out_rx) = output_sink();

        let handle = tokio::spawn(Client::subscription_loop(
            reader,
            NodeReadBatch::new_cartesian([&x], &[ua::AttributeId::VALUE]),
            notification_stream(note_rx),
            on_data,
            // No poll timer.
            Option::<MockPollTimer>::None,
        ));

        note_tx
            .send((x.clone(), datapoint(100, 9.5)))
            .expect("sending notification should not fail");

        drop(note_tx);
        await_loop(handle).await;

        let samples = drain_batches(&mut out_rx)
            .into_iter()
            .flatten()
            .collect::<Vec<_>>();

        assert_eq!(samples.len(), 1, "only the notification should be emitted");
        let (node_id, sample) = samples.first().expect("should have a notification sample");
        assert_eq!(node_id, &x);
        assert_eq!(
            sample.server_timestamp().unwrap().as_unix_timestamp_nanos(),
            100
        );

        let requested = state.lock().unwrap().requested.clone();
        assert!(
            requested.is_empty(),
            "no background reads should occur without a poll interval"
        );

        Ok(())
    }

    /// A static node keeps producing polled samples while an active node's notifications are
    /// delivered, validating the mixed subscription + polling case.
    #[tokio::test]
    async fn mixed_static_and_active() -> Result<()> {
        let x = ua::NodeId::string(1, "Static");
        let y = ua::NodeId::string(2, "Active");
        let (reader, _state, mut read_done_rx) = MockNodeReader::new(100);
        let (note_tx, note_rx) = mpsc::unbounded_channel();
        let (pulse_tx, pulse_rx) = mpsc::unbounded_channel();
        let (on_data, mut out_rx) = output_sink();

        let handle = tokio::spawn(Client::subscription_loop(
            reader,
            NodeReadBatch::new_cartesian([&x, &y], &[ua::AttributeId::VALUE]),
            notification_stream(note_rx),
            on_data,
            Some(MockPollTimer { pulses: pulse_rx }),
        ));

        let mut batches: Vec<Vec<(ua::NodeId, VariantValue)>> = Vec::new();

        // Active node notifies.
        note_tx
            .send((y.clone(), datapoint(200, 1.0)))
            .expect("sending Y notification should not fail");
        batches.push(recv_nonempty(&mut out_rx, "first Y notification").await);

        // Poll tick produces a sample for the static node.
        pulse_tx.send(()).expect("sending tick should not fail");
        await_signal(&mut read_done_rx, "tick 1 read").await;

        // Active node notifies again.
        note_tx
            .send((y.clone(), datapoint(300, 2.0)))
            .expect("sending second Y notification should not fail");
        batches.push(recv_nonempty(&mut out_rx, "second Y notification").await);

        // Another poll tick for the static node.
        pulse_tx.send(()).expect("sending tick should not fail");
        await_signal(&mut read_done_rx, "tick 2 read").await;

        drop(note_tx);
        await_loop(handle).await;
        batches.extend(drain_batches(&mut out_rx));

        let samples = batches.into_iter().flatten().collect::<Vec<_>>();

        let static_count = samples.iter().filter(|(node_id, _)| node_id == &x).count();
        let active_timestamps = samples
            .iter()
            .filter_map(|(node_id, sample)| {
                if node_id == &y {
                    Some(sample.server_timestamp().map(|ts| {
                        let ticks = ts.as_unix_timestamp_nanos();
                        let picos = sample.server_picoseconds().unwrap_or(0) as i128;
                        ticks + picos / 1000
                    }))
                } else {
                    None
                }
            })
            .collect::<HashSet<_>>();

        assert!(
            static_count >= 2,
            "static node should keep being polled (got {static_count})"
        );
        assert!(
            active_timestamps.contains(&Some(200)) && active_timestamps.contains(&Some(300)),
            "both active-node notifications should be delivered, got {active_timestamps:?}",
        );

        Ok(())
    }

    /// On a poll tick after the client is dropped, the loop breaks *before* flushing or reading:
    /// it terminates without a stream close and issues no further read. The single buffered
    /// sample is still emitted by the exit drain.
    #[tokio::test]
    async fn breaks_before_flush_on_dropped_client() -> Result<()> {
        let x = ua::NodeId::string(1, "Static");
        let (reader, state, mut read_done_rx) = MockNodeReader::new(1);
        // Keep the stream sender alive (`_note_tx`) so the stream never closes: the loop must
        // terminate via the liveness check, not by the stream ending.
        let (_note_tx, note_rx) = mpsc::unbounded_channel();
        let (pulse_tx, pulse_rx) = mpsc::unbounded_channel();
        let (on_data, mut out_rx) = output_sink();

        let handle = tokio::spawn(Client::subscription_loop(
            reader,
            NodeReadBatch::new_cartesian([&x], &[ua::AttributeId::VALUE]),
            notification_stream(note_rx),
            on_data,
            Some(MockPollTimer { pulses: pulse_rx }),
        ));

        // First tick buffers a polled sample.
        pulse_tx.send(()).expect("sending tick 1 should not fail");
        await_signal(&mut read_done_rx, "tick 1 read").await;

        // Simulate the client being dropped, then tick again. The loop should break.
        state.lock().unwrap().is_alive = false;
        pulse_tx.send(()).expect("sending tick 2 should not fail");

        // The stream is never closed; the loop must terminate solely via the liveness check.
        await_loop(handle).await;

        let samples = drain_batches(&mut out_rx)
            .into_iter()
            .flatten()
            .collect::<Vec<_>>();

        // Only the exit drain emits, carrying the single buffered sample from tick 1.
        assert_eq!(
            samples.len(),
            1,
            "only the buffered sample should be drained on exit"
        );
        let (_, sample) = samples.first().expect("should have a drained sample");
        assert_eq!(
            sample.server_timestamp(),
            Some(&ua::DateTime::try_from_unix_timestamp_nanos(1).unwrap())
        );

        // Tick 2 broke before reading, so exactly one read (from tick 1) was issued.
        let requested = state.lock().unwrap().requested.clone();
        assert_eq!(
            requested.len(),
            1,
            "dropped-client tick must not issue a read"
        );

        // No second read-completion signal should have been produced.
        assert!(
            read_done_rx.try_recv().is_err(),
            "tick 2 must not complete a read"
        );

        Ok(())
    }

    #[test]
    fn generate_cert_produces_nonempty_keypair() {
        let (certificate, private_key) =
            generate_self_signed_cert().expect("failed to generate self-signed certificate");

        assert!(!certificate.as_bytes().is_empty(), "certificate is empty");
        assert!(!private_key.as_bytes().is_empty(), "private key is empty");
    }
}
