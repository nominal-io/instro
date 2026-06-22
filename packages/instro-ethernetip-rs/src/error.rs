use std::error::Error as StdError;

use rust_ethernet_ip::BatchError;
use thiserror::Error;

/// Per-tag failure surfaced by a batched read.
///
/// Mirrors the variants of the upstream batch error so callers can branch on the specific
/// failure kind without depending on the backend transport types. Used as the typed `source`
/// of [`Error::BatchReadItem`].
#[derive(Debug, Clone, Error)]
pub enum BatchReadError {
    #[error("tag not found: {0}")]
    TagNotFound(String),
    #[error("data type mismatch: expected {expected}, got {actual}")]
    DataTypeMismatch { expected: String, actual: String },
    #[error("network error: {0}")]
    Network(String),
    #[error("CIP error (0x{status:02X}): {message}")]
    Cip { status: u8, message: String },
    #[error("tag path error: {0}")]
    TagPath(String),
    #[error("serialization error: {0}")]
    Serialization(String),
    #[error("operation timeout")]
    Timeout,
    #[error("error: {0}")]
    Other(String),
}

impl From<BatchError> for BatchReadError {
    fn from(value: BatchError) -> Self {
        match value {
            BatchError::TagNotFound(tag) => Self::TagNotFound(tag),
            BatchError::DataTypeMismatch { expected, actual } => {
                Self::DataTypeMismatch { expected, actual }
            }
            BatchError::NetworkError(msg) => Self::Network(msg),
            BatchError::CipError { status, message } => Self::Cip { status, message },
            BatchError::TagPathError(msg) => Self::TagPath(msg),
            BatchError::SerializationError(msg) => Self::Serialization(msg),
            BatchError::Timeout => Self::Timeout,
            BatchError::Other(msg) => Self::Other(msg),
        }
    }
}

impl BatchReadError {
    pub(crate) fn is_retriable(&self) -> bool {
        matches!(self, Self::Network(_) | Self::Timeout)
    }
}

/// Errors returned by the explicit EtherNet/IP tag API.
#[derive(Debug, Error)]
pub enum Error {
    #[cfg(feature = "blocking")]
    #[error("failed to create Tokio runtime for blocking EtherNet/IP session: {source}")]
    CreateRuntime {
        #[source]
        source: Box<dyn StdError + Send + Sync>,
    },
    #[error("failed to connect to EtherNet/IP device at {addr}: {source}")]
    Connect {
        addr: String,
        // Preserve the backend error as a source while keeping it out of the public API surface.
        #[source]
        source: Box<dyn StdError + Send + Sync>,
    },
    #[error("failed to read tag '{tag_name}' from {addr}: {source}")]
    ReadTag {
        addr: String,
        tag_name: String,
        #[source]
        source: Box<dyn StdError + Send + Sync>,
    },
    #[error("failed to read batch of tags from {addr}: {source}")]
    BatchRead {
        addr: String,
        #[source]
        source: Box<dyn StdError + Send + Sync>,
    },
    #[error("failed to read tag '{tag_name}' from {addr}: {source}")]
    BatchReadItem {
        addr: String,
        tag_name: String,
        #[source]
        source: BatchReadError,
    },
    #[error("failed to decode structured tag '{tag_name}' from {addr} as {target_type}: {source}")]
    DecodeStructuredTag {
        addr: String,
        tag_name: String,
        target_type: &'static str,
        #[source]
        source: Box<dyn StdError + Send + Sync>,
    },
    #[error(
        "failed to decode structured tag '{tag_name}' from {addr}: expected structured value, got {actual_type}"
    )]
    UnexpectedValueType {
        addr: String,
        tag_name: String,
        actual_type: &'static str,
    },
    #[error("failed to write tag '{tag_name}' on {addr}: {source}")]
    WriteTag {
        addr: String,
        tag_name: String,
        #[source]
        source: Box<dyn StdError + Send + Sync>,
    },
    #[error("failed to unregister explicit EtherNet/IP session for {addr}: {source}")]
    Unregister {
        addr: String,
        #[source]
        source: Box<dyn StdError + Send + Sync>,
    },
}
