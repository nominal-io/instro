use std::sync::Arc;
use std::sync::Weak;
use std::thread;

use futures_util::FutureExt as _;
use futures_util::future;
use open62541::AsyncClient;
use tokio::runtime;
use tokio::sync::RwLock;
use tokio::sync::oneshot;
use tokio::sync::watch;
use tokio::time::Duration;
use tokio::time::Interval;
use tokio::time::MissedTickBehavior;
use tokio::time::interval;
use tokio::time::timeout;

use super::NodeReadBatch;
use crate::Result;
use crate::client::Client;
use crate::client::ReadNodeItem;
use crate::error::ErrorExt as _;
use crate::error::OpcUaError;

type TerminationReceiver = watch::Receiver<bool>;
type StopSender = oneshot::Sender<()>;

#[derive(Debug)]
struct SessionHandle {
    stop_tx: StopSender,
    term_rx: TerminationReceiver,
}

impl SessionHandle {
    /// Sends a stop signal to the session, synchronizing on session termination on the remote thread.
    fn stop(self) -> impl Future<Output = ()> {
        let Self {
            stop_tx,
            mut term_rx,
        } = self;
        if *term_rx.borrow_and_update() {
            return future::ready(()).left_future();
        }

        _ = stop_tx.send(());

        async move {
            match term_rx.changed().await {
                Ok(()) => (),
                Err(e) => tracing::warn!(
                    target: "opcua::client::session_handle",
                    err = ?e,
                    "synchronization with remote streaming session likely resulted in a panic on the streaming thread"
                ),
            }
        }
        .right_future()
    }

    /// Checks the status of the session and returns `true` if it has been stopped.
    fn has_stopped(&self) -> bool {
        if let Err(e) = self.term_rx.has_changed() {
            tracing::debug!(
                target: "opcua::client::session_handle",
                err = ?e,
                "stream session handle was dropped unexpectedly"
            );

            true
        } else {
            *self.term_rx.borrow()
        }
    }
}

/// A handle for a streaming session backed by an OPC UA subscription or polling loop.
///
/// The session is created by [`Client::start_polling`](crate::client::Client::start_polling) or
/// [`Client::start_subscription`](crate::client::Client::start_subscription), and can be stopped
/// using [`StreamSession::stop`] or [`StreamSession::stop_timeout`].
#[derive(Debug)]
pub struct StreamSession {
    handle: Option<SessionHandle>,
}

impl StreamSession {
    pub(crate) fn new<F>(name: &'static str, f: F) -> Result<Self>
    where
        F: AsyncFnOnce() -> () + Send + 'static,
    {
        let runtime = runtime::Builder::new_current_thread()
            .enable_time()
            .build()
            .context("creating runtime")?;

        let (stop_tx, stop_rx) = oneshot::channel();
        let (term_tx, term_rx) = watch::channel(false);

        // don't care about joining the thread, since we're using `SessionHandle` to synchronize session exit
        let _ = thread::Builder::new()
            .name(format!("opcua-worker-thread-{name}"))
            .spawn(move || {
                runtime.block_on(async move {
                    tokio::select! {
                        stop_result = stop_rx => {
                            match stop_result {
                                Ok(_) => tracing::debug!(
                                    target: "opcua::client::spawn_task",
                                    task = name,
                                    "stopped cooperatively",
                                ),
                                Err(e) => tracing::debug!(
                                    target: "opcua::client::spawn_task",
                                    task = name,
                                    err = ?e,
                                    "stop signal likely dropped before task completed",
                                ),
                            }
                        }

                        _ = f() => ()
                    }

                    if let Err(e) = term_tx.send(true) {
                        tracing::debug!(
                            target: "opcua::client::spawn_task",
                            err = ?e,
                            task = name,
                            "error sending finished signal to task"
                        );
                    }
                })
            })
            .context("creating runtime thread")?;

        Ok(Self {
            handle: Some(SessionHandle { stop_tx, term_rx }),
        })
    }

    /// Stops the stream session, waiting until the session exits.
    pub async fn stop(mut self) {
        if let Some(handle) = self.handle.take() {
            handle.stop().await;
        }
    }

    /// Stops the stream session, returning an error if the session does not exit within the given timeout.
    pub async fn stop_timeout(mut self, to: Duration) -> Result<()> {
        if let Some(handle) = self.handle.take() {
            timeout(to, handle.stop())
                .await
                .context("stopping stream session")?
        }

        Ok(())
    }

    /// Returns true if the remote streaming thread has completed, either cooperatively or due to a panic.
    pub fn has_stopped(&self) -> bool {
        self.handle
            .as_ref()
            .map(SessionHandle::has_stopped)
            .unwrap_or(true)
    }
}

impl Drop for StreamSession {
    fn drop(&mut self) {
        if let Some(handle) = self.handle.take() {
            // don't need to await the result, the stop signal was sent imperatively
            _ = handle.stop();
        }
    }
}

/// Abstraction over the background-poll read path used by
/// [`Client::subscription_loop`](crate::client::Client::subscription_loop).
///
/// Hiding the read behind a trait lets the subscription loop be unit-tested against a
/// deterministic in-memory reader instead of a live OPC UA server. The production
/// implementation is [`ClientNodeReader`], which forwards to [`Client::read_nodes`].
#[allow(async_fn_in_trait)]
pub trait NodeReader {
    /// Reads every configured node and attribute pair, returning one unfiltered value per pair.
    async fn read_nodes<'batch>(
        &self,
        batch: &'batch NodeReadBatch,
    ) -> Result<impl Iterator<Item = ReadNodeItem<'batch>>>;
}

/// Production [`NodeReader`] backed by a [`Weak`] reference to the owning [`Client`].
pub struct ClientNodeReader {
    client: Weak<RwLock<Option<AsyncClient>>>,
}

impl ClientNodeReader {
    pub fn new(client: &Arc<RwLock<Option<AsyncClient>>>) -> Self {
        Self {
            client: Arc::downgrade(client),
        }
    }
}

impl NodeReader for ClientNodeReader {
    async fn read_nodes<'batch>(
        &self,
        batch: &'batch NodeReadBatch,
    ) -> Result<impl Iterator<Item = ReadNodeItem<'batch>>> {
        // Holding the upgraded strong reference across the read makes a concurrent
        // `Client::disconnect()` bail rather than tearing the client down mid-read,
        // matching the previous in-loop `Weak::upgrade` behaviour.
        match Client::upgrade(Weak::clone(&self.client)) {
            Some(client) => client.read_nodes(batch).await,
            None => Err(OpcUaError::ClientDisconnected),
        }
    }
}

/// Abstraction over the background-poll cadence used by
/// [`Client::subscription_loop`].
///
/// The production implementation is [`IntervalPollTimer`]; tests substitute a manually
/// pulsed timer to drive poll ticks deterministically.
#[allow(async_fn_in_trait)]
pub trait PollTimer {
    /// Resolves once per poll interval.
    async fn tick(&mut self);
}

/// Production [`PollTimer`] backed by a [`Interval`].
///
/// Created on first tick so it binds to the session runtime, and skips missed ticks rather than
/// replaying stale poll intervals.
pub struct IntervalPollTimer {
    period: Duration,
    interval: Option<Interval>,
}

impl IntervalPollTimer {
    pub fn new(period: Duration) -> Self {
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

#[cfg(test)]
mod tests {
    use tokio::task::yield_now;

    use super::*;

    fn session(f: impl AsyncFnOnce() -> () + Send + 'static) -> StreamSession {
        StreamSession::new("test-session", f).unwrap()
    }

    #[tokio::test(flavor = "current_thread")]
    async fn stream_session_stops_with_no_work() -> Result<()> {
        let session = session(async || {});

        timeout(Duration::from_millis(2), session.stop())
            .await
            .expect("should stop immediately");

        Ok(())
    }

    #[tokio::test(flavor = "current_thread")]
    async fn stream_session_indefinite_loop_is_interruptible() -> Result<()> {
        let session = session(async || {
            loop {
                yield_now().await;
            }
        });

        timeout(Duration::from_millis(2), session.stop())
            .await
            .expect("should stop before timeout");

        Ok(())
    }

    #[tokio::test(flavor = "current_thread")]
    async fn stream_session_doesnt_propagate_panics() -> Result<()> {
        let session = session(async || panic!("shouldn't appear"));

        timeout(Duration::from_millis(2), session.stop())
            .await
            .expect("shouldn't timeout");

        Ok(())
    }
}
