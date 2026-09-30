use std::{any::type_name, fmt::Debug};

use open62541::Error as UaError;
use thiserror::Error;

use crate::types::OpcUaNodeId;

pub type Result<T> = core::result::Result<T, OpcUaError>;

#[derive(Debug, Error)]
pub enum ClientError {
    #[error("failed to build client: {0}{source}", source = format_source(.1.as_ref()))]
    Builder(String, #[source] Option<UaError>),
    #[error("failed to connect to OPC UA server")]
    Connect(#[source] UaError),
    #[error("client disconnected")]
    ClientDisconnect,
    #[error("failure while processing OPC UA subscription stream")]
    Subscription(#[source] UaError),
    #[error("response from OPC UA service was malformed: {0}")]
    MalformedServiceResponse(String),
    #[error("failure during OPC UA poll stream: {0}{source}", source = format_source(.1.as_ref()))]
    Poll(String, #[source] Option<UaError>),
    #[error("failure while reading node attribute values: {0}{source}", source = format_source(.1.as_ref()))]
    ReadNodes(String, #[source] Option<UaError>),
    #[error("failed to stop stream before timeout")]
    StreamStopTimeout(#[source] tokio::time::error::Elapsed),
    #[error("failed to spawn background streaming task")]
    StreamInit(#[source] std::io::Error),
    #[error("failed to browse node: {0}{source}", source = format_source(Some(.1)))]
    BrowseNode(OpcUaNodeId, #[source] UaError),
    #[error("node limit exceeded when traversing node '{1}' (limit: {2}, browse root: {0})")]
    BrowsedNodeLimitExceeded(OpcUaNodeId, OpcUaNodeId, usize),
    #[error("node reference cycle detected at node: {0} (browse root: {1})")]
    BrowseCycleDetected(OpcUaNodeId, OpcUaNodeId),
}

#[derive(Debug, Error)]
pub enum OpcUaError {
    #[error("client error: {0}")]
    Client(#[from] ClientError),
    #[error("failed to generate self-signed certificate{source}", source = format_source(.0.as_ref()))]
    GenerateSelfSignedCert(#[source] Option<UaError>),
    #[error("failed to convert value: {0} ({1:?})")]
    TypeConversion(String, Box<dyn Debug + 'static>),
    #[error("error: {0}")]
    Other(String),
}

fn format_source(e: Option<&UaError>) -> String {
    e.map(|e| format!(": ({:?})", e)).unwrap_or_default()
}

impl OpcUaError {
    pub(crate) fn ua_conversion<From, To>(src: impl Debug + 'static) -> Self {
        Self::TypeConversion(
            format!(
                "failed to convert value: from type '{}' into type '{}'",
                type_name::<From>(),
                type_name::<To>(),
            ),
            Box::new(src),
        )
    }

    pub(crate) fn other(ctx: impl std::fmt::Display) -> Self {
        Self::Other(ctx.to_string())
    }

    pub(crate) const fn client_disconnected() -> Self {
        Self::Client(ClientError::ClientDisconnect)
    }
}
