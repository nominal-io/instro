use std::error::Error as StdError;

use thiserror::Error;

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
