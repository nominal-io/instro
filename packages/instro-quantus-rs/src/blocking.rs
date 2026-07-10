//! Blocking facade over the async engine (PLAN.md D5). This is the surface the
//! PyO3 bindings wrap in Phase 5.

use crate::config::RackConfig;
use crate::error::{Error, Result};
use crate::reconcile::{DeviceTree, Engine, ReconcileReport};
use crate::rest::RestClient;
use serde_json::Value;

pub struct QuantusClient {
    runtime: tokio::runtime::Runtime,
    engine: Engine,
    config: RackConfig,
}

impl QuantusClient {
    /// Connect to the device: ping it and assert a Q2.x QServer. No settings
    /// are written until `reconcile()`.
    pub fn connect(config: RackConfig) -> Result<Self> {
        let connection = config.connection.clone().ok_or_else(|| {
            Error::Config(
                "no connection section in the rack config; add one or supply it at runtime".into(),
            )
        })?;
        let runtime = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(2)
            .enable_all()
            .build()
            .map_err(|e| Error::Transport(e.to_string()))?;
        let engine = Engine::new(RestClient::new(&connection.host, connection.rest_port));
        runtime.block_on(engine.check_connection())?;
        Ok(QuantusClient {
            runtime,
            engine,
            config,
        })
    }

    /// Full declarative reconcile: write every declared setting, apply once,
    /// report snapped rates and epoch impact.
    pub fn reconcile(&self) -> Result<ReconcileReport> {
        self.runtime.block_on(self.engine.reconcile(&self.config))
    }

    pub fn discover(&self) -> Result<DeviceTree> {
        self.runtime.block_on(self.engine.discover())
    }

    pub fn data_stream_setup(&self) -> Result<Value> {
        self.runtime
            .block_on(self.engine.rest().data_stream_setup())
    }

    pub fn suspend_stream(&self) -> Result<()> {
        self.runtime
            .block_on(self.engine.rest().suspend_stream())
            .map(|_| ())
    }

    pub fn resume_stream(&self) -> Result<()> {
        self.runtime
            .block_on(self.engine.rest().resume_stream())
            .map(|_| ())
    }

    /// Cache a CAN transmit message list on a CAN channel (by ItemId).
    pub fn put_can_message_list(&self, item_id: i64, doc: &Value) -> Result<()> {
        self.runtime
            .block_on(self.engine.rest().put_can_message_list(item_id, doc))
            .map(|_| ())
    }

    /// Transmit the cached message list (channel must be in Participate mode).
    pub fn can_transmit(&self, item_id: i64) -> Result<()> {
        self.runtime
            .block_on(self.engine.rest().can_transmit(item_id))
            .map(|_| ())
    }

    /// Action-plane write: auto-zero one item, or the whole system when None.
    pub fn auto_zero(&self, item_id: Option<i64>) -> Result<()> {
        self.runtime
            .block_on(self.engine.rest().auto_zero_apply(item_id))
            .map(|_| ())
    }

    /// Action-plane write: balance WSB bridges (one channel or system-wide).
    pub fn bridge_balance(&self, item_id: Option<i64>) -> Result<()> {
        self.runtime
            .block_on(self.engine.rest().bridge_balance_apply(item_id))
            .map(|_| ())
    }

    pub fn bridge_balance_reset(&self, item_id: Option<i64>) -> Result<()> {
        self.runtime
            .block_on(self.engine.rest().bridge_balance_reset(item_id))
            .map(|_| ())
    }

    /// Settings-plane write (PLAN.md D12): write settings on one item and
    /// apply immediately. Returns true when the apply restarts the streaming
    /// epoch (StatusCode 4/14) — callers must expect a timestamp rebase.
    pub fn write_settings(
        &self,
        item_id: i64,
        values: &std::collections::BTreeMap<String, crate::config::SettingValue>,
    ) -> Result<bool> {
        self.runtime.block_on(async {
            let rest = self.engine.rest();
            let mut doc = rest.item_settings(item_id).await?;
            for (name, value) in values {
                crate::settings::set_value(&mut doc["Settings"], name, value)?;
            }
            rest.put_item_settings(item_id, &doc).await?;
            let status = rest.apply().await?;
            match status.status_code {
                1 | 3 => Ok(false),
                4 | 14 => Ok(true),
                _ => Err(Error::Api {
                    http_status: 200,
                    status,
                }),
            }
        })
    }
}
