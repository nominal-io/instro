use std::collections::HashMap;
use std::sync::mpsc;
use std::time::Duration;

use futures_util::future::BoxFuture;
use opcua::types as ua;
use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use tokio::sync::mpsc as async_mpsc;
use tokio::sync::oneshot;
use tokio::sync::watch;
use tokio::time::Instant;
use tokio::time::Interval;

use crate::client::ClientState;
use crate::error;
use crate::node::Node;

pub(super) enum Input {
    Sample(ua::NodeId, ua::DataValue),
    End(ua::StatusCode),
}

pub(super) enum Output {
    Sample(Node, ua::DataValue),
    End(Option<PyErr>),
}

pub(super) struct Producer {
    nodes: Vec<Node>,
    indices: HashMap<ua::NodeId, usize>,
    reads: Vec<ua::ReadValueId>,
    notifications: async_mpsc::UnboundedReceiver<Input>,
    completion: watch::Receiver<ClientState>,
    stopped: oneshot::Receiver<()>,
    output: mpsc::Sender<Output>,
    period: Option<Duration>,
}

impl Producer {
    pub(super) fn new(
        nodes: Vec<Node>,
        reads: Vec<ua::ReadValueId>,
        notifications: async_mpsc::UnboundedReceiver<Input>,
        completion: watch::Receiver<ClientState>,
        stopped: oneshot::Receiver<()>,
        output: mpsc::Sender<Output>,
        period: Option<Duration>,
    ) -> Self {
        let indices = nodes
            .iter()
            .enumerate()
            .map(|(index, node)| (node.id().clone(), index))
            .collect();

        Self {
            nodes,
            indices,
            reads,
            notifications,
            completion,
            stopped,
            output,
            period,
        }
    }

    fn emit(&self, index: usize, value: ua::DataValue) {
        if let Some(node) = self.nodes.get(index) {
            let _ = self.output.send(Output::Sample(node.clone(), value));
        }
    }

    fn flush(&self, pending: &mut [Option<ua::DataValue>]) {
        for (index, value) in pending.iter_mut().enumerate() {
            if let Some(value) = value.take() {
                self.emit(index, value);
            }
        }
    }

    pub(super) async fn run(
        mut self,
        mut read: impl FnMut(Vec<ua::ReadValueId>) -> BoxFuture<'static, PyResult<Vec<ua::DataValue>>>,
    ) {
        let mut timer = self.period.map(|period| {
            let mut timer = tokio::time::interval_at(Instant::now() + period, period);
            timer.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);

            timer
        });

        let mut last_notification: Vec<Option<Instant>> = vec![None; self.nodes.len()];
        let mut pending = vec![None; self.nodes.len()];

        let mut terminal = None;

        loop {
            let finished = self.completion.borrow().finished_result();
            if let Some(result) = finished {
                terminal = result.err();
                break;
            }

            tokio::select! {
                changed = self.completion.changed() => {
                    if changed.is_err() {
                        terminal = Some(PyRuntimeError::new_err(
                            "native session supervisor ended without a result",
                        ));
                        break;
                    }
                }

                _ = &mut self.stopped => break,

                _ = tick(&mut timer) => {
                    self.flush(&mut pending);

                    let selected: Vec<_> = self
                        .reads
                        .iter()
                        .enumerate()
                        .filter(|(index, _)| {
                            last_notification
                                .get(*index)
                                .copied()
                                .flatten()
                                .is_none_or(|last| {
                                    self.period.is_some_and(|period| last.elapsed() >= period)
                                })
                        })
                        .map(|(index, read)| (index, read.clone()))
                        .collect();

                    if !selected.is_empty() {
                        let requests = selected
                            .iter()
                            .map(|(_, request)| request.clone())
                            .collect();

                        match read(requests).await {
                            Err(error) => tracing::warn!(%error, "OPC UA background poll failed"),

                            Ok(values) if values.len() == selected.len() => {
                                for ((index, _), value) in selected.into_iter().zip(values) {
                                    if let Some(poll) = pending.get_mut(index) {
                                        *poll = Some(value);
                                    }
                                }
                            }

                            Ok(values) => tracing::warn!(
                                count = values.len(),
                                expected = selected.len(),
                                "OPC UA background poll returned an unexpected result count",
                            ),
                        }
                    }
                }

                notification = self.notifications.recv() => match notification {
                    None => break,

                    Some(Input::Sample(id, value)) => {
                        if let Some(&index) = self.indices.get(&id) {
                            if let Some(last) = last_notification.get_mut(index) {
                                *last = Some(Instant::now());
                            }

                            if let Some(poll) = pending.get_mut(index).and_then(Option::take)
                                && poll_is_older(&poll, &value)
                            {
                                self.emit(index, poll);
                            }

                            self.emit(index, value);
                        }
                    }

                    Some(Input::End(code)) => {
                        terminal = status_failure(code);
                        break;
                    }
                },
            }
        }

        if let Some(Err(error)) = self.completion.borrow().finished_result() {
            terminal.get_or_insert(error);
        }

        while let Ok(input) = self.notifications.try_recv() {
            if let Input::End(code) = input
                && let Some(error) = status_failure(code)
            {
                terminal.get_or_insert(error);
            }
        }

        self.flush(&mut pending);

        let _ = self.output.send(Output::End(terminal));
    }
}

async fn tick(timer: &mut Option<Interval>) {
    match timer {
        None => std::future::pending().await,

        Some(timer) => {
            timer.tick().await;
        }
    }
}

fn status_failure(code: ua::StatusCode) -> Option<PyErr> {
    (!code.is_good()).then(|| error::status(code, "native subscription terminated"))
}

fn poll_is_older(poll: &ua::DataValue, notification: &ua::DataValue) -> bool {
    let timestamp = |time: Option<ua::DateTime>, fraction: Option<u16>| {
        time.map(|time| (time, fraction.unwrap_or(0).min(9999)))
    };

    let older = |poll, notification| {
        matches!(
            (poll, notification),
            (Some(poll), Some(notification)) if poll < notification
        )
    };

    older(
        timestamp(poll.source_timestamp, poll.source_picoseconds),
        timestamp(
            notification.source_timestamp,
            notification.source_picoseconds,
        ),
    ) || older(
        timestamp(poll.server_timestamp, poll.server_picoseconds),
        timestamp(
            notification.server_timestamp,
            notification.server_picoseconds,
        ),
    )
}
