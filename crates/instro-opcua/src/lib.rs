pub mod browse;
pub mod client;
pub mod error;
pub(crate) mod metrics;

pub use open62541;

pub type Result<T> = core::result::Result<T, self::error::OpcUaError>;

pub mod prelude {
    pub use open62541::ua;

    pub use crate::browse::Browse as _;
    pub use crate::browse::BrowseAll as _;
    pub use crate::client::Client;
    pub use crate::client::ClientBuilder;
    pub use crate::client::NodeReadBatch;
    pub use crate::client::PkiConfig;
    pub use crate::client::SecurityPolicyUri;
    pub use crate::client::StreamSession;
    pub use crate::client::SubscriptionConfig;
    pub use crate::client::SubscriptionItemConfig;
}
