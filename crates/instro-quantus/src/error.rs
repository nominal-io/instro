//! Error taxonomy: transport failures, API-rejected requests, and protocol
//! violations are distinct because callers recover from them differently.

/// QServer's JSON status body, returned alongside HTTP status codes.
///
/// `StatusCode` 4 ("applying will restart the measurement") and 14 ("applying
/// affects other items") arrive with HTTP 200 — they are successes with side
/// effects and are surfaced as data, not as `Error::Api`.
#[derive(Debug, Clone, serde::Deserialize)]
pub struct ApiStatus {
    #[serde(rename = "TypeCode")]
    pub type_code: i32,
    #[serde(rename = "StatusCode")]
    pub status_code: i32,
    #[serde(rename = "Message")]
    pub message: String,
}

#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error("transport: {0}")]
    Transport(String),
    #[error("QServer rejected request (HTTP {http_status}): {status:?}")]
    Api { http_status: u16, status: ApiStatus },
    #[error("API major version incompatibility (StatusCode 6): {0}")]
    VersionMismatch(String),
    #[error("stream protocol violation: {0}")]
    Stream(String),
    #[error("invalid config: {0}")]
    Config(String),
}

pub type Result<T> = std::result::Result<T, Error>;
