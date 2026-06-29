//! OPC-UA client wrapper and streaming session management.
//!
//! [`OpcUaClient`] wraps an [`open62541::AsyncClient`] and derefs to it for
//! transparent access to the underlying OPC-UA operations. Each session runs on its
//! own single-threaded tokio [`Runtime`](tokio::runtime::Runtime) and driving thread,
//! started when the session is constructed. Calling [`OpcUaStreamSession::stop`],
//! [`OpcUaStreamSession::stop_timeout`], or dropping the session will trigger a
//! cooperative shutdown of the runtime and thread.
//!
//! [`OpcUaClientBuilder`] provides a builder API for configuring security mode,
//! security policy, user authentication, PKI certificates, timeouts, and
//! server trust before connecting to an endpoint.
//!
//! [`OpcUaStreamSession`] manages background OPC-UA streaming. In polling mode it
//! periodically reads node attribute values via [`OpcUaClient::read_nodes`] and
//! delivers each batch of decoded [`OpcUaSample`](super::types::OpcUaSample)s
//! through a caller-supplied callback. In subscription mode it forwards
//! monitored-item notifications via a merged [`Stream`](futures_util::stream::Stream)
//! of all monitored items, delivering each sample through the same `on_data` callback.
//!
//! Streams are started with [`OpcUaClient::start_polling`] or
//! [`OpcUaClient::start_subscription`]. Both modes of streaming are stopped by
//! dropping the [`OpcUaStreamSession`].

use std::borrow::Cow;
use std::collections::HashMap;
use std::future;
use std::ops::Deref;
use std::ops::DerefMut;
use std::sync::Arc;
use std::sync::Weak;
use std::sync::mpsc;
use std::thread;
use std::time::Duration;

use anyhow::Context as _;
use anyhow::Result;
use anyhow::bail;
use futures_util::FutureExt as _;
use futures_util::Stream;
use futures_util::StreamExt;
use futures_util::select_biased;
use futures_util::stream::select_all;
use itertools::Itertools as _;
use open62541::AsyncClient;
use open62541::AsyncMonitoredItem;
use open62541::Certificate;
use open62541::ClientBuilder;
use open62541::DataType;
use open62541::MonitoredItemCreateRequestBuilder;
use open62541::PrivateKey;
use open62541::SubscriptionBuilder;
use open62541::ua;
use tokio::runtime;
use tokio::sync::oneshot;
use tokio::task::yield_now;
use tokio::time::Instant;
use tokio::time::Interval;
use tokio::time::MissedTickBehavior;
use tokio::time::interval;
use tokio::time::sleep;

use super::generate_self_signed_cert;
use super::metrics::NodeReadCounts;
use super::metrics::PollLoopMetricsLogger;
use super::types::OpcUaDataPoint;
use super::types::OpcUaMonitoredItemConfig;
use super::types::OpcUaNode;
use super::types::OpcUaPki;
use super::types::OpcUaSample;
use super::types::OpcUaSecurityMode;
use super::types::OpcUaSecurityPolicy;
use super::types::OpcUaSubscriptionConfig;
use super::types::OpcUaUserToken;
use crate::types::OpcUaNodeId;

/// Enables either borrow or move of the nodes via the default implementations, avoiding clones out-of-the-box.
/// Downstream implementations can choose to clone, but it's not the default.
pub trait OpcUaNodeListSource<'nodes> {
    /// Converts the source into a list of nodes, preserving the value category of the underlying source.
    fn into_node_list(self) -> Cow<'nodes, [OpcUaNode]>;
}

impl<'nodes, D: Deref<Target = [OpcUaNode]>> OpcUaNodeListSource<'nodes> for &'nodes D {
    fn into_node_list(self) -> Cow<'nodes, [OpcUaNode]> {
        Cow::Borrowed(self.deref())
    }
}

impl OpcUaNodeListSource<'static> for Vec<OpcUaNode> {
    fn into_node_list(self) -> Cow<'static, [OpcUaNode]> {
        Cow::Owned(self)
    }
}

/// A list of OPC-UA nodes, paired with an attribute ID.
/// Opinionated against taking ownership of nodes via a clone, instead accepting a borrow or move of the nodes.
/// Pre-calculates the list of node attribute pairs to avoid recalculating on every read request.
#[derive(Debug, Clone)]
pub struct OpcUaNodeReadBatch<'nodes> {
    nodes: Cow<'nodes, [OpcUaNode]>,
    node_attr_pairs: Vec<(ua::NodeId, ua::AttributeId)>,
}

impl<'nodes> OpcUaNodeReadBatch<'nodes> {
    /// Creates a new batch of nodes and attribute pairs.
    pub fn new<Src>(nodes: Src, attr: ua::AttributeId) -> Self
    where
        Src: OpcUaNodeListSource<'nodes>,
    {
        let nodes = nodes.into_node_list();
        Self {
            node_attr_pairs: nodes
                .iter()
                .map(|node| (node.node_id.clone().into(), attr.clone()))
                .collect_vec(),
            nodes,
        }
    }

    /// Returns the list of nodes, used in read requests.
    pub fn nodes(&self) -> &[OpcUaNode] {
        &self.nodes
    }

    /// Returns the list of node attribute pairs, used in read requests.
    pub fn pairs(&self) -> &[(ua::NodeId, ua::AttributeId)] {
        &self.node_attr_pairs
    }

    /// Returns the number of nodes in the batch.
    pub fn len(&self) -> usize {
        self.nodes.len()
    }

    /// Returns whether the batch is empty.
    pub fn is_empty(&self) -> bool {
        self.nodes.is_empty()
    }
}

// `mpsc::Receiver` let's us do non-async timeouts when waiting for the session to exit.
// Ideally we'd use `tokio::sync::oneshot::*` types for both ends of the synchronization pipe,
// but there's too many footguns (i.e., runtime lifetimes, nested `block_on`s, etc.)
type TerminationReceiver = mpsc::Receiver<()>;
// `oneshot::Sender` lets us wait in a select! block with the `stop_tx` sender next to whatever session-specific work is running
type StopSender = oneshot::Sender<()>;

#[derive(Debug)]
struct SessionHandle {
    stop_tx: StopSender,
    term_rx: TerminationReceiver,
}

impl SessionHandle {
    /// Sends a stop signal to the session, waiting until the session has exited
    fn stop(self) -> Result<()> {
        if let Err(e) = self.stop_tx.send(()) {
            tracing::warn!(
                target: "opcua::client::stream_session",
                error = ?e,
                "stream session likely terminated before stop signal could be sent"
            );
        }

        self.term_rx.recv().context("waiting for session to exit")
    }

    fn stop_timeout(self, timeout: Duration) -> Result<()> {
        if let Err(e) = self.stop_tx.send(()) {
            tracing::warn!(
                target: "opcua::client::stream_session",
                error = ?e,
                "stream session likely terminated before stop signal could be sent"
            );
        }

        self.term_rx
            .recv_timeout(timeout)
            .context("waiting for session to exit")
    }
}

#[derive(Debug)]
/// Wraps an [`AsyncClient`].
///
/// The client is created by the [`OpcUaClientBuilder::connect`] method, which returns an [`Arc`] of the client.
/// Streaming sessions started from this client own their own tokio runtime; see [`OpcUaStreamSession`].
pub struct OpcUaClient {
    client: AsyncClient,
}

impl OpcUaClient {
    /// Attempts to gracefully disconnect from the server, returning an error if the client has outstanding references.
    pub async fn disconnect(self: Arc<Self>) -> Result<()> {
        match Arc::into_inner(self) {
            None => bail!(
                "OPC-UA client has outstanding references; cannot perform graceful disconnect"
            ),

            Some(Self { client }) => {
                client.disconnect().await;
                Ok(())
            }
        }
    }

    /// Starts polling the `VALUE` attribute of `nodes` at `polling_interval`,
    /// invoking `on_data` with each batch of decoded [`OpcUaSample`]s.
    ///
    /// The polling loop runs on a dedicated single-threaded runtime started by the returned
    /// [`OpcUaStreamSession`].
    ///
    /// The runtime and thread are shut down when the session is dropped.
    #[must_use = "dropping the returned session will stop the polling loop"]
    pub fn start_polling(
        self: &Arc<Self>,
        nodes: Vec<OpcUaNode>,
        polling_interval: Duration,
        on_data: impl FnMut(Box<dyn Iterator<Item = OpcUaSample>>) + Send + 'static,
    ) -> Result<OpcUaStreamSession> {
        let this = Arc::downgrade(self);
        OpcUaStreamSession::new("opcua-poll-loop", async move || {
            // Downgrade the client to a weak reference to avoid holding onto the strong reference.
            // Increases the chances of success when using `Arc::into_inner` for shutdown.
            Self::poll_loop(this, nodes, polling_interval, on_data).await;
        })
    }

    /// Reads the configured attribute for every node in `node_list` and returns
    /// the successfully decoded samples in the same order as the input nodes.
    ///
    /// Returns [`Err`] if the underlying batched [`AsyncClient::read_many_attributes`] call fails.
    /// Per-node decoding failures are logged at [`tracing::warn`] and dropped from the
    /// returned vector, so the output may be shorter than `node_list.len()`.
    pub async fn read_nodes(&self, node_list: &OpcUaNodeReadBatch<'_>) -> Result<Vec<OpcUaSample>> {
        let read_result = self
            .read_many_attributes(node_list.pairs())
            .await
            .context("reading node attributes")?;

        if read_result.len() != node_list.nodes().len() {
            bail!(
                "read result length does not match node list length: {} != {}",
                read_result.len(),
                node_list.nodes().len()
            );
        }

        Ok(node_list
            .nodes()
            .iter()
            .zip(read_result)
            .enumerate()
            .filter_map(|(i, (node, value))| match OpcUaDataPoint::try_from(value) {
                Err(e) => {
                    tracing::warn!(
                        target: "opcua::client",
                        error = ?e,
                        node_index = i,
                        "discarding data due to error decoding value for node"
                    );

                    None
                }

                Ok(value) => Some(OpcUaSample::new(node.node_id.clone(), value)),
            })
            .collect_vec())
    }

    /// Starts a server-push subscription for the given `nodes`, invoking
    /// `on_data` as new samples arrive.
    ///
    /// Subscription creation and monitored-item setup are performed eagerly
    /// before this method returns; the ongoing pump runs on a dedicated single-threaded runtime
    /// started by the returned [`OpcUaStreamSession`].
    ///
    /// The runtime and thread are shut down when the session is dropped.
    #[must_use = "dropping the returned session will deregister the subscription"]
    pub async fn start_subscription<F>(
        self: &Arc<Self>,
        nodes: Vec<OpcUaNode>,
        sub_config: OpcUaSubscriptionConfig,
        item: OpcUaMonitoredItemConfig,
        on_data: F,
    ) -> Result<OpcUaStreamSession>
    where
        F: FnMut(Box<dyn Iterator<Item = OpcUaSample>>) + Send + 'static,
    {
        // Configure the `AsyncSubscription` and per-node monitoring config
        let subscription_builder = SubscriptionBuilder::from(sub_config);
        let item_builder = item.apply_to_builder(MonitoredItemCreateRequestBuilder::new(
            nodes.iter().map(|n| ua::NodeId::from(n.node_id.clone())),
        ));

        let (_, subscription) = subscription_builder
            .create(self)
            .await
            .context("creating OPC-UA subscription")?;

        let item_results = AsyncMonitoredItem::create(&subscription, item_builder)
            .await
            .context("creating OPC-UA monitored items")?;

        if item_results.len() != nodes.len() {
            bail!(
                "OPC-UA server returned {} monitored-item results for {} requested nodes",
                item_results.len(),
                nodes.len()
            );
        }

        // Merge monitored-item streams into one pump to avoid per-node tasks; the pump does not
        // block on IO, so one task can service all items.
        let requested_count = nodes.len();
        let mut valid_streams = Vec::with_capacity(requested_count);

        for (node, result) in nodes.iter().cloned().zip(item_results) {
            let node_id = node.node_id;

            match result {
                Ok((create_result, monitored_item)) => {
                    // validation does nothing here beyond emitting warnings
                    item.validate(&node_id, &create_result);

                    // Decode here so the subscription loop stays independent of open62541 values.
                    valid_streams.push(
                        monitored_item
                            .filter_map(move |value| {
                                let node_id = node_id.clone();
                                async move {
                                    match OpcUaDataPoint::try_from(value) {
                                        Ok(data) => Some((node_id, data)),

                                        Err(e) => {
                                            tracing::warn!(
                                                target: "opcua::client::subscribe",
                                                error = ?e,
                                                "Notification for node {node_id} returned an invalid value, discarding sample"
                                            );

                                            None
                                        }
                                    }
                                }
                            })
                            .boxed(),
                    );
                }

                Err(e) => {
                    // This should be handled more verbosely in the future
                    tracing::warn!(
                        target: "opcua::client::subscribe",
                        error = ?e,
                        "skipping monitored item creation for node {node_id}"
                    );
                }
            }
        }

        if valid_streams.is_empty() {
            bail!(
                "OPC-UA subscription has no valid monitored items; all {requested_count} \
                 requested node(s) failed",
            );
        }

        let reader = ClientNodeReader {
            client: Arc::downgrade(self),
        };

        let timer = sub_config
            .background_poll_interval
            .filter(|period| !period.is_zero())
            .map(IntervalPollTimer::new);

        OpcUaStreamSession::new("opcua-subscription-pump", async move || {
            let _subscription = subscription;
            Self::subscription_loop(reader, nodes, select_all(valid_streams), on_data, timer).await;
        })
    }

    /// How often the poll loop emits the aggregated metrics log line.
    const POLL_LOOP_METRICS_FLUSH_INTERVAL: Duration = Duration::from_secs(5);

    /// Polls `nodes` at `polling_interval`, yielding decoded samples through `on_data`.
    async fn poll_loop(
        this: Weak<Self>,
        nodes: Vec<OpcUaNode>,
        polling_interval: Duration,
        mut on_data: impl FnMut(Box<dyn Iterator<Item = OpcUaSample>>),
    ) {
        let node_list = OpcUaNodeReadBatch::new(&nodes, ua::AttributeId::VALUE);
        let total_nodes = node_list.nodes().len() as u64;
        let mut metrics = PollLoopMetricsLogger::new(Self::POLL_LOOP_METRICS_FLUSH_INTERVAL);

        loop {
            let start_time = Instant::now();

            let read_start = metrics.start_read();

            // All of the suspension points exposed by `AsyncClient` are logically cancel-safe,
            // since the IO operations are driven by their own OS thread spawned by `open62541`.
            // No comment on resource leakage, that'll have to be profiled.
            let read_result = if let Some(this) = this.upgrade() {
                // Holding a strong reference for the duration of read_nodes makes a concurrent
                // OpcUaClient::disconnect() bail. This is intentional, so the client isn't torn down mid-read.
                // Disconnect can succeed in the gap between iterations.
                this.read_nodes(&node_list)
                    .await
                    .context("reading nodes from poll loop")
            } else {
                tracing::warn!(
                    target: "opcua::client::poll",
                    "OPC-UA client has been dropped, stopping poll loop"
                );

                break;
            };

            let (outcome, samples) = match read_result {
                Ok(values) => {
                    let successful_reads = values.len() as u64;
                    let failed_reads = total_nodes.saturating_sub(successful_reads);

                    (
                        Some(NodeReadCounts {
                            valid_samples: successful_reads,
                            invalid_samples: failed_reads,
                        }),
                        Some(values),
                    )
                }

                Err(e) => {
                    tracing::error!(
                        target: "opcua::client::poll",
                        error = ?e,
                        "error reading node attributes"
                    );

                    (None, None)
                }
            };

            metrics.finish_read(read_start, outcome);

            if let Some(samples) = samples {
                on_data(Box::new(samples.into_iter()));
            }

            match polling_interval.checked_sub(start_time.elapsed()) {
                Some(duration) => sleep(duration).await,
                None => yield_now().await, // injected suspension point to allow cooperative cancellation before next server read
            }
        }
    }

    /// Pumps subscription notifications and optionally polls nodes that go quiet.
    ///
    /// Notifications are emitted as they arrive and mark their node active for the current
    /// interval. On a poll tick, nodes that did not notify are read directly and buffered for one
    /// tick, giving late notifications a chance to win deduplication by timestamp.
    async fn subscription_loop<R, N, S, F, T>(
        reader: R,
        nodes: N,
        stream: S,
        mut on_data: F,
        mut timer: Option<T>,
    ) where
        R: NodeReader,
        S: Stream<Item = (OpcUaNodeId, OpcUaDataPoint)> + Send + Unpin + 'static,
        F: FnMut(Box<dyn Iterator<Item = OpcUaSample>>) + Send + 'static,
        N: IntoIterator<Item = OpcUaNode>,
        T: PollTimer,
    {
        let all_nodes = nodes
            .into_iter()
            .map(|n| (n.node_id.clone(), n))
            .collect::<HashMap<_, _>>();

        // Nodes still quiet in the current interval; notifications remove nodes until the next tick.
        let mut quiet_nodes = all_nodes.clone();

        // Poll results wait one tick before flushing so late notifications can dedup by timestamp.
        let mut polled_nodes = HashMap::<OpcUaNodeId, OpcUaSample>::new();

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
                    // If the client is gone, stop before another read; the exit drain still flushes buffered polls.
                    if !reader.is_alive() {
                        break;
                    }

                    let polled_samples_to_flush = polled_nodes
                        .drain()
                        .map(|(_, sample)| sample)
                        .collect_vec();

                    on_data(Box::new(polled_samples_to_flush.into_iter()));

                    if !quiet_nodes.is_empty() {
                        let nodes = quiet_nodes.values().cloned().collect_vec();
                        let batch = OpcUaNodeReadBatch::new(&nodes, ua::AttributeId::VALUE);

                        let reads = match reader.read_nodes(&batch).await {
                            Ok(reads) => reads,

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
                            .map(|sample| (sample.node_id.clone(), sample));
                        polled_nodes.extend(to_flush);
                    }

                    quiet_nodes = all_nodes
                        .iter()
                        .map(|(node_id, n)| (node_id.clone(), n.clone()))
                        .collect();
                },

                chunk = stream.next().fuse() => if let Some(chunk) = chunk {
                    let mut samples = Vec::with_capacity(chunk.len() * 2);

                    for (node_id, data) in chunk {
                        quiet_nodes.remove(&node_id);

                        // Older poll values preserve ordering; same-or-newer poll values are stale.
                        if let Some(polled_sample) = polled_nodes.remove(&node_id)
                            && polled_sample.data.server_timestamp < data.server_timestamp
                        {
                            samples.push(polled_sample);
                        }

                        samples.push(OpcUaSample::new(node_id, data));
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

        let samples_to_flush = polled_nodes.drain().map(|(_, sample)| sample).collect_vec();

        on_data(Box::new(samples_to_flush.into_iter()));
    }
}

/// Abstraction over the background-poll read path used by
/// [`OpcUaClient::subscription_loop`].
///
/// Hiding the read behind a trait lets the subscription loop be unit-tested against a
/// deterministic in-memory reader instead of a live OPC-UA server. The production
/// implementation is [`ClientNodeReader`], which forwards to [`OpcUaClient::read_nodes`].
// The returned future is intentionally not `Send`-bound: the subscription loop runs on a
// single-threaded session runtime, so no work crosses threads after the timer/read seams.
#[allow(async_fn_in_trait)]
pub(crate) trait NodeReader {
    /// Returns `false` once the backing client has been dropped, signalling the loop to stop.
    fn is_alive(&self) -> bool;

    /// Reads the configured attribute for every node in `batch`, returning decoded samples.
    async fn read_nodes(&self, batch: &OpcUaNodeReadBatch<'_>) -> Result<Vec<OpcUaSample>>;
}

/// Production [`NodeReader`] backed by a [`Weak`] reference to the owning [`OpcUaClient`].
pub(crate) struct ClientNodeReader {
    client: Weak<OpcUaClient>,
}

impl NodeReader for ClientNodeReader {
    fn is_alive(&self) -> bool {
        self.client.strong_count() > 0
    }

    async fn read_nodes(&self, batch: &OpcUaNodeReadBatch<'_>) -> Result<Vec<OpcUaSample>> {
        // Holding the upgraded strong reference across the read makes a concurrent
        // `OpcUaClient::disconnect()` bail rather than tearing the client down mid-read,
        // matching the previous in-loop `Weak::upgrade` behaviour.
        match self.client.upgrade() {
            Some(client) => client.read_nodes(batch).await,
            None => bail!("OPC-UA client dropped before background poll read"),
        }
    }
}

/// Abstraction over the background-poll cadence used by
/// [`OpcUaClient::subscription_loop`].
///
/// The production implementation is [`IntervalPollTimer`]; tests substitute a manually
/// pulsed timer to drive poll ticks deterministically.
#[allow(async_fn_in_trait)]
pub(crate) trait PollTimer {
    /// Resolves once per poll interval.
    async fn tick(&mut self);
}

/// Production [`PollTimer`] backed by a [`Interval`].
///
/// Created on first tick so it binds to the session runtime, and skips missed ticks rather than
/// replaying stale poll intervals.
pub(crate) struct IntervalPollTimer {
    period: Duration,
    interval: Option<Interval>,
}

impl IntervalPollTimer {
    fn new(period: Duration) -> Self {
        Self {
            period,
            interval: None,
        }
    }
}

impl PollTimer for IntervalPollTimer {
    async fn tick(&mut self) {
        let period = self.period;
        let timer = self.interval.get_or_insert_with(|| {
            let mut timer = interval(period);
            timer.set_missed_tick_behavior(MissedTickBehavior::Skip);
            timer.reset();
            timer
        });

        timer.tick().await;
    }
}

#[derive(Debug, Default)]
/// Builder for [`OpcUaClient`].
///
/// Provides a fluent API for configuring the both the [`OpcUaClient`] and the backing [`AsyncClient`]
/// before connecting to an OPC-UA endpoint.
pub struct OpcUaClientBuilder {
    user_token: Option<OpcUaUserToken>,
    security_mode: Option<OpcUaSecurityMode>,
    security_policy: Option<OpcUaSecurityPolicy>,
    timeout: Option<Duration>,
    accept_any_cert: bool,
    pki: OpcUaPki,
}

impl OpcUaClientBuilder {
    /// Creates a new builder with default values.
    pub fn new() -> Self {
        Self::default()
    }

    /// Sets whether to accept any server certificate.
    pub fn trust_server_certs(self, accept_any_cert: bool) -> Self {
        Self {
            accept_any_cert,
            ..self
        }
    }

    /// Sets the user identity token to use for the client.
    pub fn user_identity_token(self, token: OpcUaUserToken) -> Self {
        Self {
            user_token: Some(token),
            ..self
        }
    }

    /// Sets the security mode to use for the client.
    pub fn security_mode(self, mode: OpcUaSecurityMode) -> Self {
        Self {
            security_mode: Some(mode),
            ..self
        }
    }

    /// Sets the security policy to use for the client.
    pub fn security_policy(self, security_policy: OpcUaSecurityPolicy) -> Self {
        Self {
            security_policy: Some(security_policy),
            ..self
        }
    }

    /// Sets the timeout to use for the client.
    pub fn timeout(self, timeout: Duration) -> Self {
        Self {
            timeout: Some(timeout),
            ..self
        }
    }

    /// Sets the PKI to use for the client.
    pub fn pki(self, pki: OpcUaPki) -> Self {
        Self { pki, ..self }
    }

    /// Sets the PKI to use for the client to use a provided certificate and private key.
    pub fn use_pki(self, certificate: Certificate, private_key: PrivateKey) -> Self {
        Self {
            pki: OpcUaPki::UseProvided(certificate, private_key),
            ..self
        }
    }

    /// Sets the PKI to use for the client to generate a self-signed certificate.
    pub fn generate_self_signed_pki(self) -> Self {
        Self {
            pki: OpcUaPki::GenerateSelfSigned,
            ..self
        }
    }

    fn connect_app_description() -> ua::ApplicationDescription {
        ua::ApplicationDescription::init()
            .with_application_name("en-US", "Nominal OPC UA Client")
            .with_product_uri("urn:nominal:opcua-client")
            .with_application_type(ua::ApplicationType::CLIENT)
            .with_application_uri("urn:nominal:opcua-client")
    }

    /// Consumes the builder and connects to the endpoint at the given URL, returning an [`OpcUaClient`].
    #[must_use = "dropping the returned client will immediately disconnect from the OPC-UA server"]
    pub fn connect(self, endpoint_url: &str) -> Result<Arc<OpcUaClient>> {
        let user_token = self.user_token.context("no user token provided")?;

        let security_mode = match self.security_mode {
            Some(mode) if !mode.is_invalid() => mode.into(),
            Some(_) => bail!("security mode was specified but was invalid"),
            None => bail!("security mode was not specified"),
        };

        let mut builder = match self.pki {
            OpcUaPki::UseProvided(certificate, private_key) => {
                ClientBuilder::default_encryption(&certificate, &private_key)?
            }

            OpcUaPki::GenerateSelfSigned => {
                let (certificate, private_key) = generate_self_signed_cert()?;
                ClientBuilder::default_encryption(&certificate, &private_key)?
            }

            OpcUaPki::None => ClientBuilder::default(),
        };

        builder = builder
            .secure_channel_life_time(Duration::from_millis(u32::MAX as u64))
            .user_identity_token(&user_token.try_into()?)
            .client_description(Self::connect_app_description())
            .security_mode(security_mode);

        if let Some(timeout) = self.timeout {
            builder = builder.timeout(timeout);
        }

        if let Some(security_policy) = self.security_policy {
            builder = builder.security_policy_uri(security_policy.into());
        }

        if self.accept_any_cert {
            builder = builder.accept_all();
        }

        let client = builder.connect(endpoint_url)?.into_async();

        Ok(OpcUaClient::new(client))
    }
}

impl OpcUaClient {
    fn new(client: AsyncClient) -> Arc<Self> {
        Arc::new(Self { client })
    }
}

impl Deref for OpcUaClient {
    type Target = AsyncClient;
    fn deref(&self) -> &Self::Target {
        &self.client
    }
}

impl DerefMut for OpcUaClient {
    fn deref_mut(&mut self) -> &mut Self::Target {
        &mut self.client
    }
}

#[derive(Debug)]
/// Manages a streaming session backed by an OPC-UA subscription or polling loop.
///
/// The session is created by [`OpcUaClient::start_polling`] or [`OpcUaClient::start_subscription`].
/// Dropping it signals shutdown cooperatively, with a 2 second timeout (see [`Drop`] impl).
pub struct OpcUaStreamSession {
    handle: Option<SessionHandle>,
}

impl OpcUaStreamSession {
    fn new<F>(name: &'static str, f: F) -> Result<Self>
    where
        F: AsyncFnOnce() -> () + Send + 'static,
    {
        let runtime = runtime::Builder::new_current_thread()
            .enable_time()
            .build()
            .context("creating tokio runtime for opcua task")?;

        let (stop_tx, stop_rx) = oneshot::channel();
        let (term_tx, term_rx) = mpsc::sync_channel(1);

        // don't care about joining the thread, since we're using `SessionHandle` to synchronize session exit
        let _ = thread::Builder::new()
            .name(format!("opcua-worker-thread-{name}"))
            .spawn(move || runtime.block_on(async move {
                tokio::select! {
                    _ = stop_rx => {
                        tracing::info!(target: "opcua::client::spawn_task", "task {name} stopped cooperatively");
                    }

                    _ = f() => ()
                }

                if let Err(e) = term_tx.send(()) {
                    tracing::error!(
                        target: "opcua::client::spawn_task",
                        err = ?e,
                        task = name,
                        "error sending finished signal to task"
                    );
                }
            }))
            .context("spawning thread for opcua task")?;

        Ok(Self {
            handle: Some(SessionHandle { stop_tx, term_rx }),
        })
    }

    /// Stops the stream session, blocking until the session exits.
    pub fn stop(mut self) -> Result<()> {
        if let Some(handle) = self.handle.take() {
            handle.stop()?;
        }

        Ok(())
    }

    /// Stops the stream session, returning an error if the session does not exit within the given timeout.
    pub fn stop_timeout(mut self, timeout: Duration) -> Result<()> {
        if let Some(handle) = self.handle.take() {
            handle.stop_timeout(timeout)?;
        }

        Ok(())
    }
}

impl Drop for OpcUaStreamSession {
    fn drop(&mut self) {
        // 2s is generous for a cooperatively-cancellable loop; if the worker is
        // wedged past that, accept the leak rather than block Drop indefinitely.
        if let Some(sync) = self.handle.take()
            && let Err(e) = sync.stop_timeout(Duration::from_secs(2))
        {
            tracing::error!(
                target: "opcua::client::stream_session",
                error = ?e,
                "error stopping stream session"
            );
        }
    }
}

#[cfg(test)]
mod subscription_loop_tests {
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

    use anyhow::Context as _;
    use anyhow::Result;
    use anyhow::anyhow;
    use futures_util::Stream;
    use futures_util::StreamExt as _;
    use tokio::sync::mpsc;
    use tokio::time::timeout;

    use super::NodeReader;
    use super::OpcUaClient;
    use super::OpcUaNodeReadBatch;
    use super::PollTimer;
    use crate::types::NodeIdInner;
    use crate::types::OpcUaDataPoint;
    use crate::types::OpcUaNode;
    use crate::types::OpcUaNodeClass;
    use crate::types::OpcUaNodeId;
    use crate::types::OpcUaSample;
    use crate::types::OpcUaValue;

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
        requested: Vec<HashSet<OpcUaNodeId>>,
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
        fn is_alive(&self) -> bool {
            self.state.lock().map(|s| s.is_alive).unwrap_or(false)
        }

        async fn read_nodes(&self, batch: &OpcUaNodeReadBatch<'_>) -> Result<Vec<OpcUaSample>> {
            let samples = {
                let mut state = self
                    .state
                    .lock()
                    .map_err(|_| anyhow!("reader state poisoned"))?;

                let timestamp = state.next_timestamp;
                state.next_timestamp = state.next_timestamp.saturating_add(1);

                let requested = batch
                    .nodes()
                    .iter()
                    .map(|node| node.node_id.clone())
                    .collect::<HashSet<_>>();
                state.requested.push(requested);

                batch
                    .nodes()
                    .iter()
                    .map(|node| OpcUaSample::new(node.node_id.clone(), datapoint(timestamp, 0.0)))
                    .collect::<Vec<_>>()
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

    fn test_node(id: u32, name: &str) -> OpcUaNode {
        OpcUaNode {
            node_id: OpcUaNodeId {
                namespace: 1,
                inner: NodeIdInner::Numeric(id),
            },
            browse_name: name.to_owned(),
            display_name: name.to_owned(),
            node_class: OpcUaNodeClass::Variable,
            children: Vec::new(),
        }
    }

    fn datapoint(server_timestamp: u64, value: f64) -> OpcUaDataPoint {
        OpcUaDataPoint {
            server_timestamp,
            source_timestamp: None,
            value: OpcUaValue::Double(value),
        }
    }

    /// Wraps an unbounded channel as a notification [`Stream`]; the stream ends when the sender
    /// is dropped, after draining any buffered items (exercising the loop's exit drain).
    fn notification_stream(
        rx: mpsc::UnboundedReceiver<(OpcUaNodeId, OpcUaDataPoint)>,
    ) -> impl Stream<Item = (OpcUaNodeId, OpcUaDataPoint)> + Send + Unpin + 'static {
        futures_util::stream::unfold(rx, |mut rx| async move {
            rx.recv().await.map(|item| (item, rx))
        })
        .boxed()
    }

    /// Collects emitted samples into batches the test forwards through `on_data`.
    fn output_sink() -> (
        impl FnMut(Box<dyn Iterator<Item = OpcUaSample>>) + Send + 'static,
        mpsc::UnboundedReceiver<Vec<OpcUaSample>>,
    ) {
        let (tx, rx) = mpsc::unbounded_channel::<Vec<OpcUaSample>>();
        let on_data = move |samples: Box<dyn Iterator<Item = OpcUaSample>>| {
            let _ = tx.send(samples.collect::<Vec<_>>());
        };
        (on_data, rx)
    }

    async fn await_signal(rx: &mut mpsc::UnboundedReceiver<()>, what: &str) -> Result<()> {
        timeout(TEST_TIMEOUT, rx.recv())
            .await
            .with_context(|| format!("timed out waiting for {what}"))?
            .with_context(|| format!("channel closed waiting for {what}"))?;
        Ok(())
    }

    /// Awaits the next non-empty output batch, used as a barrier that a notification (or flush)
    /// has been emitted. Empty flush batches are skipped.
    async fn recv_nonempty(
        rx: &mut mpsc::UnboundedReceiver<Vec<OpcUaSample>>,
        what: &str,
    ) -> Result<Vec<OpcUaSample>> {
        loop {
            let batch = timeout(TEST_TIMEOUT, rx.recv())
                .await
                .with_context(|| format!("timed out waiting for {what}"))?
                .with_context(|| format!("channel closed waiting for {what}"))?;

            if !batch.is_empty() {
                return Ok(batch);
            }
        }
    }

    /// Synchronously drains whatever batches remain after the loop has terminated.
    fn drain_batches(rx: &mut mpsc::UnboundedReceiver<Vec<OpcUaSample>>) -> Vec<Vec<OpcUaSample>> {
        let mut batches = Vec::new();
        while let Ok(batch) = rx.try_recv() {
            batches.push(batch);
        }
        batches
    }

    async fn await_loop(handle: tokio::task::JoinHandle<()>) -> Result<()> {
        timeout(TEST_TIMEOUT, handle)
            .await
            .context("subscription loop did not terminate")?
            .map_err(|e| anyhow!("subscription loop task failed: {e}"))
    }

    /// A node that never receives a subscription notification is polled once per tick: every
    /// emitted sample is therefore a background poll. K ticks yield K distinct polled samples
    /// (K-1 via the next tick's flush, the last via the exit drain).
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn polls_static_node_each_tick() -> Result<()> {
        let x = test_node(1, "Static");
        let (reader, state, mut read_done_rx) = MockNodeReader::new(1);
        let (note_tx, note_rx) = mpsc::unbounded_channel();
        let (pulse_tx, pulse_rx) = mpsc::unbounded_channel();
        let (on_data, mut out_rx) = output_sink();

        let handle = tokio::spawn(OpcUaClient::subscription_loop(
            reader,
            vec![x.clone()],
            notification_stream(note_rx),
            on_data,
            Some(MockPollTimer { pulses: pulse_rx }),
        ));

        // Three ticks, each fully processed (barrier on the read) before the next.
        for _ in 0..3 {
            pulse_tx.send(()).context("pulsing poll tick")?;
            await_signal(&mut read_done_rx, "background poll read").await?;
        }

        drop(note_tx); // close the stream -> loop breaks and drains the final buffered sample
        await_loop(handle).await?;

        let samples = drain_batches(&mut out_rx)
            .into_iter()
            .flatten()
            .collect::<Vec<_>>();

        assert_eq!(samples.len(), 3, "expected one polled sample per tick");
        for sample in &samples {
            assert_eq!(sample.node_id, x.node_id);
        }
        let timestamps = samples
            .iter()
            .map(|s| s.data.server_timestamp)
            .collect::<Vec<_>>();
        assert_eq!(
            timestamps,
            vec![1, 2, 3],
            "polled samples should carry successive read timestamps",
        );

        // Three reads happened, each requesting the single static node.
        let requested = state
            .lock()
            .map_err(|_| anyhow!("state poisoned"))?
            .requested
            .clone();
        assert_eq!(requested.len(), 3);

        Ok(())
    }

    /// When a buffered polled sample is not older than an arriving notification, it is dropped
    /// in favour of the live notification (the dedup window).
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn dedup_drops_stale_polled_when_notification_not_newer() -> Result<()> {
        let x = test_node(1, "Node");
        let (reader, state, mut read_done_rx) = MockNodeReader::new(100);
        let (note_tx, note_rx) = mpsc::unbounded_channel();
        let (pulse_tx, pulse_rx) = mpsc::unbounded_channel();
        let (on_data, mut out_rx) = output_sink();

        let handle = tokio::spawn(OpcUaClient::subscription_loop(
            reader,
            vec![x.clone()],
            notification_stream(note_rx),
            on_data,
            Some(MockPollTimer { pulses: pulse_rx }),
        ));

        // Tick buffers a polled sample at ts=100.
        pulse_tx.send(()).context("pulsing poll tick")?;
        await_signal(&mut read_done_rx, "background poll read").await?;

        // Notification at ts=100 (not newer) -> buffered polled sample dropped, only this emitted.
        note_tx
            .send((x.node_id.clone(), datapoint(100, 1.0)))
            .context("sending notification")?;
        drop(note_tx);
        await_loop(handle).await?;

        let samples = drain_batches(&mut out_rx)
            .into_iter()
            .flatten()
            .collect::<Vec<_>>();

        assert_eq!(samples.len(), 1, "stale polled sample should be dropped");
        let sample = samples.first().context("missing notification sample")?;
        assert_eq!(sample.node_id, x.node_id);
        assert_eq!(sample.data.server_timestamp, 100);
        assert_eq!(sample.data.value, OpcUaValue::Double(1.0));

        let _ = state; // shared handle kept alive for the loop's duration
        Ok(())
    }

    /// When a buffered polled sample is older than an arriving notification, both are emitted,
    /// polled first, preserving temporal continuity.
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn emits_both_when_polled_older_than_notification() -> Result<()> {
        let x = test_node(1, "Node");
        let (reader, _state, mut read_done_rx) = MockNodeReader::new(100);
        let (note_tx, note_rx) = mpsc::unbounded_channel();
        let (pulse_tx, pulse_rx) = mpsc::unbounded_channel();
        let (on_data, mut out_rx) = output_sink();

        let handle = tokio::spawn(OpcUaClient::subscription_loop(
            reader,
            vec![x.clone()],
            notification_stream(note_rx),
            on_data,
            Some(MockPollTimer { pulses: pulse_rx }),
        ));

        // Tick buffers a polled sample at ts=100.
        pulse_tx.send(()).context("pulsing poll tick")?;
        await_signal(&mut read_done_rx, "background poll read").await?;

        // Notification at ts=200 (newer) -> emit polled@100 then notification@200.
        note_tx
            .send((x.node_id.clone(), datapoint(200, 2.0)))
            .context("sending notification")?;
        drop(note_tx);
        await_loop(handle).await?;

        let samples = drain_batches(&mut out_rx)
            .into_iter()
            .flatten()
            .collect::<Vec<_>>();

        assert_eq!(samples.len(), 2, "both polled and notification should emit");
        let polled = samples.first().context("missing polled sample")?;
        let notification = samples.get(1).context("missing notification sample")?;
        assert_eq!(
            polled.data.server_timestamp, 100,
            "polled sample emitted first"
        );
        assert_eq!(notification.data.server_timestamp, 200);

        Ok(())
    }

    /// Buffered polled samples that never meet a notification are flushed when the stream closes.
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn flush_on_exit_drains_buffer() -> Result<()> {
        let x = test_node(1, "Static");
        let (reader, _state, mut read_done_rx) = MockNodeReader::new(7);
        let (note_tx, note_rx) = mpsc::unbounded_channel();
        let (pulse_tx, pulse_rx) = mpsc::unbounded_channel();
        let (on_data, mut out_rx) = output_sink();

        let handle = tokio::spawn(OpcUaClient::subscription_loop(
            reader,
            vec![x.clone()],
            notification_stream(note_rx),
            on_data,
            Some(MockPollTimer { pulses: pulse_rx }),
        ));

        // One tick buffers a polled sample; close the stream before any further tick flushes it.
        pulse_tx.send(()).context("pulsing poll tick")?;
        await_signal(&mut read_done_rx, "background poll read").await?;
        drop(note_tx);
        await_loop(handle).await?;

        let samples = drain_batches(&mut out_rx)
            .into_iter()
            .flatten()
            .collect::<Vec<_>>();

        assert_eq!(
            samples.len(),
            1,
            "exit drain should flush the buffered sample"
        );
        let sample = samples.first().context("missing drained sample")?;
        assert_eq!(sample.node_id, x.node_id);
        assert_eq!(sample.data.server_timestamp, 7);

        Ok(())
    }

    /// A node that notifies during one interval is excluded from that interval's poll, but is
    /// polled again once it goes quiet (the `quiet_nodes` reset each tick).
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn quiet_node_repolled_after_going_quiet() -> Result<()> {
        let x = test_node(1, "Static");
        let y = test_node(2, "Active");
        let (reader, state, mut read_done_rx) = MockNodeReader::new(1);
        let (note_tx, note_rx) = mpsc::unbounded_channel();
        let (pulse_tx, pulse_rx) = mpsc::unbounded_channel();
        let (on_data, mut out_rx) = output_sink();

        let handle = tokio::spawn(OpcUaClient::subscription_loop(
            reader,
            vec![x.clone(), y.clone()],
            notification_stream(note_rx),
            on_data,
            Some(MockPollTimer { pulses: pulse_rx }),
        ));

        // Tick 1: both nodes quiet -> both polled.
        pulse_tx.send(()).context("pulsing tick 1")?;
        await_signal(&mut read_done_rx, "tick 1 read").await?;

        // Y notifies -> removed from quiet for the current interval.
        note_tx
            .send((y.node_id.clone(), datapoint(10, 1.0)))
            .context("sending Y notification")?;
        recv_nonempty(&mut out_rx, "Y notification batch").await?;

        // Tick 2: Y excluded (it notified), only X polled.
        pulse_tx.send(()).context("pulsing tick 2")?;
        await_signal(&mut read_done_rx, "tick 2 read").await?;

        // Tick 3: Y has gone quiet again -> polled once more.
        pulse_tx.send(()).context("pulsing tick 3")?;
        await_signal(&mut read_done_rx, "tick 3 read").await?;

        drop(note_tx);
        await_loop(handle).await?;
        let _ = drain_batches(&mut out_rx);

        let requested = state
            .lock()
            .map_err(|_| anyhow!("state poisoned"))?
            .requested
            .clone();
        assert_eq!(requested.len(), 3, "expected three poll reads");

        let both = HashSet::from([x.node_id.clone(), y.node_id.clone()]);
        let only_x = HashSet::from([x.node_id.clone()]);
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
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn none_interval_emits_only_notifications() -> Result<()> {
        let x = test_node(1, "Static");
        let (reader, state, _read_done_rx) = MockNodeReader::new(1);
        let (note_tx, note_rx) = mpsc::unbounded_channel();
        let (on_data, mut out_rx) = output_sink();

        let handle = tokio::spawn(OpcUaClient::subscription_loop(
            reader,
            vec![x.clone()],
            notification_stream(note_rx),
            on_data,
            // No poll timer.
            Option::<MockPollTimer>::None,
        ));

        note_tx
            .send((x.node_id.clone(), datapoint(7, 9.5)))
            .context("sending notification")?;
        drop(note_tx);
        await_loop(handle).await?;

        let samples = drain_batches(&mut out_rx)
            .into_iter()
            .flatten()
            .collect::<Vec<_>>();

        assert_eq!(samples.len(), 1, "only the notification should be emitted");
        let sample = samples.first().context("missing notification sample")?;
        assert_eq!(sample.data.server_timestamp, 7);

        let requested = state
            .lock()
            .map_err(|_| anyhow!("state poisoned"))?
            .requested
            .clone();
        assert!(
            requested.is_empty(),
            "no background reads should occur without a poll interval"
        );

        Ok(())
    }

    /// A static node keeps producing polled samples while an active node's notifications are
    /// delivered, validating the mixed subscription + polling case.
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn mixed_static_and_active() -> Result<()> {
        let x = test_node(1, "Static");
        let y = test_node(2, "Active");
        let (reader, _state, mut read_done_rx) = MockNodeReader::new(1);
        let (note_tx, note_rx) = mpsc::unbounded_channel();
        let (pulse_tx, pulse_rx) = mpsc::unbounded_channel();
        let (on_data, mut out_rx) = output_sink();

        let handle = tokio::spawn(OpcUaClient::subscription_loop(
            reader,
            vec![x.clone(), y.clone()],
            notification_stream(note_rx),
            on_data,
            Some(MockPollTimer { pulses: pulse_rx }),
        ));

        let mut batches: Vec<Vec<OpcUaSample>> = Vec::new();

        // Active node notifies.
        note_tx
            .send((y.node_id.clone(), datapoint(5, 1.0)))
            .context("sending Y notification")?;
        batches.push(recv_nonempty(&mut out_rx, "first Y notification").await?);

        // Poll tick produces a sample for the static node.
        pulse_tx.send(()).context("pulsing tick 1")?;
        await_signal(&mut read_done_rx, "tick 1 read").await?;

        // Active node notifies again.
        note_tx
            .send((y.node_id.clone(), datapoint(6, 2.0)))
            .context("sending second Y notification")?;
        batches.push(recv_nonempty(&mut out_rx, "second Y notification").await?);

        // Another poll tick for the static node.
        pulse_tx.send(()).context("pulsing tick 2")?;
        await_signal(&mut read_done_rx, "tick 2 read").await?;

        drop(note_tx);
        await_loop(handle).await?;
        batches.extend(drain_batches(&mut out_rx));

        let samples = batches.into_iter().flatten().collect::<Vec<_>>();

        let static_count = samples.iter().filter(|s| s.node_id == x.node_id).count();
        let active_timestamps = samples
            .iter()
            .filter(|s| s.node_id == y.node_id)
            .map(|s| s.data.server_timestamp)
            .collect::<HashSet<_>>();

        assert!(
            static_count >= 2,
            "static node should keep being polled (got {static_count})"
        );
        assert!(
            active_timestamps.contains(&5) && active_timestamps.contains(&6),
            "both active-node notifications should be delivered, got {active_timestamps:?}",
        );

        Ok(())
    }

    /// On a poll tick after the client is dropped, the loop breaks *before* flushing or reading:
    /// it terminates without a stream close and issues no further read. The single buffered
    /// sample is still emitted by the exit drain.
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn breaks_before_flush_on_dropped_client() -> Result<()> {
        let x = test_node(1, "Static");
        let (reader, state, mut read_done_rx) = MockNodeReader::new(1);
        // Keep the stream sender alive (`_note_tx`) so the stream never closes: the loop must
        // terminate via the liveness check, not by the stream ending.
        let (_note_tx, note_rx) = mpsc::unbounded_channel();
        let (pulse_tx, pulse_rx) = mpsc::unbounded_channel();
        let (on_data, mut out_rx) = output_sink();

        let handle = tokio::spawn(OpcUaClient::subscription_loop(
            reader,
            vec![x.clone()],
            notification_stream(note_rx),
            on_data,
            Some(MockPollTimer { pulses: pulse_rx }),
        ));

        // First tick buffers a polled sample.
        pulse_tx.send(()).context("pulsing tick 1")?;
        await_signal(&mut read_done_rx, "tick 1 read").await?;

        // Simulate the client being dropped, then tick again. The loop should break.
        state
            .lock()
            .map_err(|_| anyhow!("state poisoned"))?
            .is_alive = false;
        pulse_tx.send(()).context("pulsing tick 2")?;

        // The stream is never closed; the loop must terminate solely via the liveness check.
        await_loop(handle).await?;

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
        let sample = samples.first().context("missing drained sample")?;
        assert_eq!(sample.data.server_timestamp, 1);

        // Tick 2 broke before reading, so exactly one read (from tick 1) was issued.
        let requested = state
            .lock()
            .map_err(|_| anyhow!("state poisoned"))?
            .requested
            .clone();
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
}
