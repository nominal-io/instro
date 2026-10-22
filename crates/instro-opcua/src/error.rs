use std::{error::Error, fmt::Debug};

use open62541::Error as UaError;
use open62541::Result as UaResult;

pub type Result<T> = core::result::Result<T, OpcUaError>;

type BoxedError = Box<dyn Error + Send + Sync>;

#[derive(Debug, thiserror::Error)]
pub enum OpcUaError {
    #[error("OPC UA error: {0}")]
    Ua(#[source] UaError),
    #[error("OPC UA error: {1}: {0}")]
    UaWithContext(#[source] UaError, BoxedError),
    #[error("attempted to use a disconnected client")]
    ClientDisconnected,
    #[error("internal error: {0}")]
    Internal(BoxedError),
}

impl From<UaError> for OpcUaError {
    fn from(err: UaError) -> Self {
        OpcUaError::Ua(err)
    }
}

pub(crate) trait UaErrorExt<T>: Sized {
    fn ua_context(self, ctx: impl Into<BoxedError>) -> Result<T>;
    fn with_ua_context<F: FnOnce() -> E, E: Into<BoxedError>>(self, f: F) -> Result<T>;
}

impl<T> UaErrorExt<T> for UaResult<T> {
    fn ua_context(self, ctx: impl Into<BoxedError>) -> Result<T> {
        self.map_err(|e| OpcUaError::UaWithContext(e, ctx.into()))
    }

    fn with_ua_context<F: FnOnce() -> E, E: Into<BoxedError>>(self, f: F) -> Result<T> {
        self.map_err(|e| OpcUaError::UaWithContext(e, f().into()))
    }
}

pub(crate) trait ErrorExt<T>: Sized {
    fn context(self, ctx: impl Into<BoxedError>) -> Result<T>;
    fn with_context<F: FnOnce() -> E, E: Into<BoxedError>>(self, f: F) -> Result<T>;
}

impl<T> ErrorExt<T> for Option<T> {
    fn context(self, ctx: impl Into<BoxedError>) -> Result<T> {
        self.ok_or_else(|| OpcUaError::internal(ctx.into()))
    }

    fn with_context<F: FnOnce() -> E, E: Into<BoxedError>>(self, f: F) -> Result<T> {
        self.ok_or_else(|| OpcUaError::internal(f().into()))
    }
}

impl<T, E: Error + Send + Sync> ErrorExt<T> for std::result::Result<T, E> {
    fn context(self, ctx: impl Into<BoxedError>) -> Result<T> {
        self.map_err(|e| OpcUaError::internal(format!("{}: {e}", ctx.into())))
    }

    fn with_context<F: FnOnce() -> _E, _E: Into<BoxedError>>(self, f: F) -> Result<T> {
        self.map_err(|e| OpcUaError::internal(format!("{}: {e}", f().into())))
    }
}

impl OpcUaError {
    pub(crate) fn internal(ctx: impl Into<BoxedError>) -> Self {
        Self::Internal(ctx.into())
    }
}

#[macro_export]
macro_rules! err {
    (internal, $($arg:tt)*) => {
        OpcUaError::internal(format!($($arg)*))
    };

    (ua, $err:expr) => {
        OpcUaError::Ua(format!($($arg)*))
    };

    (ua, $err:expr, $($arg:tt)*) => {
        OpcUaError::UaWithContext($err, format!($($arg)*))
    };
}

#[macro_export]
macro_rules! bail {
    ($($arg:tt)*) => {{
        use $crate::err;
        return Err(err!($($arg)*))
    }}
}
