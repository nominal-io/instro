//! Async REST layer for QServer Q2.x. Thin: JSON in/out plus the status-body
//! error mapping; document manipulation lives in `settings`, orchestration in
//! `reconcile`.

use crate::error::{ApiStatus, Error, Result};
use serde_json::Value;

pub struct RestClient {
    http: reqwest::Client,
    base: String,
}

impl RestClient {
    pub fn new(host: &str, port: u16) -> Self {
        RestClient {
            http: reqwest::Client::new(),
            base: format!("http://{host}:{port}"),
        }
    }

    async fn read_json(response: reqwest::Response) -> Result<Value> {
        let http_status = response.status().as_u16();
        let body: Value = response
            .json()
            .await
            .map_err(|e| Error::Transport(e.to_string()))?;
        if (200..300).contains(&http_status) {
            return Ok(body);
        }
        // Non-2xx bodies are the {TypeCode, StatusCode, Message} document.
        let status: ApiStatus = serde_json::from_value(body.clone())
            .map_err(|_| Error::Transport(format!("HTTP {http_status}: {body}")))?;
        if status.status_code == 6 {
            return Err(Error::VersionMismatch(status.message));
        }
        Err(Error::Api {
            http_status,
            status,
        })
    }

    pub async fn get(&self, path: &str) -> Result<Value> {
        let response = self
            .http
            .get(format!("{}{path}", self.base))
            .send()
            .await
            .map_err(|e| Error::Transport(e.to_string()))?;
        Self::read_json(response).await
    }

    pub async fn put(&self, path: &str, body: Option<&Value>) -> Result<Value> {
        let mut request = self.http.put(format!("{}{path}", self.base));
        if let Some(body) = body {
            request = request.json(body);
        }
        let response = request
            .send()
            .await
            .map_err(|e| Error::Transport(e.to_string()))?;
        Self::read_json(response).await
    }

    pub async fn ping(&self) -> Result<Value> {
        self.get("/info/ping/").await
    }

    pub async fn version(&self) -> Result<String> {
        let body = self.get("/version/").await?;
        body.get("Version")
            .and_then(Value::as_str)
            .map(str::to_string)
            .ok_or_else(|| Error::Stream(format!("unexpected /version body: {body}")))
    }

    pub async fn item_list(&self) -> Result<Value> {
        self.get("/item/list/").await
    }

    pub async fn item_settings(&self, item_id: i64) -> Result<Value> {
        self.get(&format!("/item/settings/?itemId={item_id}")).await
    }

    pub async fn put_item_settings(&self, item_id: i64, doc: &Value) -> Result<Value> {
        self.put(&format!("/item/settings/?itemId={item_id}"), Some(doc))
            .await
    }

    pub async fn item_operation_mode(&self, item_id: i64) -> Result<Value> {
        self.get(&format!("/item/operationMode/?itemId={item_id}"))
            .await
    }

    pub async fn put_item_operation_mode(&self, item_id: i64, doc: &Value) -> Result<Value> {
        self.put(&format!("/item/operationMode/?itemId={item_id}"), Some(doc))
            .await
    }

    /// Returns the parsed apply status; StatusCodes 1/3/4 are success-class
    /// (4 = the measurement/streaming epoch will restart).
    pub async fn apply(&self) -> Result<ApiStatus> {
        let body = self.put("/system/settings/apply/", None).await?;
        serde_json::from_value(body.clone())
            .map_err(|_| Error::Stream(format!("unexpected apply body: {body}")))
    }

    pub async fn data_stream_setup(&self) -> Result<Value> {
        self.get("/dataStream/setup/").await
    }

    pub async fn suspend_stream(&self) -> Result<Value> {
        self.put("/dataStream/suspend/", None).await
    }

    pub async fn resume_stream(&self) -> Result<Value> {
        self.put("/dataStream/resume/", None).await
    }

    pub async fn can_message_list(&self, item_id: i64) -> Result<Value> {
        self.get(&format!("/canfd/message/list/?itemId={item_id}"))
            .await
    }

    pub async fn put_can_message_list(&self, item_id: i64, doc: &Value) -> Result<Value> {
        self.put(&format!("/canfd/message/list/?itemId={item_id}"), Some(doc))
            .await
    }

    pub async fn can_transmit(&self, item_id: i64) -> Result<Value> {
        self.put(&format!("/canfd/message/transmit/?itemId={item_id}"), None)
            .await
    }

    /// Action-plane write: auto-zero one item, or the whole system when None.
    pub async fn auto_zero_apply(&self, item_id: Option<i64>) -> Result<Value> {
        let path = match item_id {
            Some(id) => format!("/autoZero/settings/apply/?itemId={id}"),
            None => "/autoZero/settings/apply/".to_string(),
        };
        self.put(&path, None).await
    }

    /// Action-plane write: balance WSB bridges (one channel or system-wide).
    pub async fn bridge_balance_apply(&self, item_id: Option<i64>) -> Result<Value> {
        let path = match item_id {
            Some(id) => format!("/wsb/bridgeBalance/apply/?itemId={id}"),
            None => "/wsb/bridgeBalance/apply/".to_string(),
        };
        self.put(&path, None).await
    }

    pub async fn bridge_balance_reset(&self, item_id: Option<i64>) -> Result<Value> {
        let path = match item_id {
            Some(id) => format!("/wsb/bridgeBalance/reset/?itemId={id}"),
            None => "/wsb/bridgeBalance/reset/".to_string(),
        };
        self.put(&path, None).await
    }
}
