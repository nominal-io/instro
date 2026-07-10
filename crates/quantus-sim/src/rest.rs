//! The QServer Q2.x REST plane. Paths are matched case-insensitively and
//! tolerate trailing slashes (the vendor client appends them everywhere).

use crate::config::SimConfig;
use crate::model::{ApplyOutcome, SimState};
use crate::stream::{StreamShared, spawn_stream_server};
use crate::templates::build_state;
use serde_json::{Value, json};
use std::net::{SocketAddr, TcpListener};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::Duration;

// QProtocolCSharp MessageType: Status=0, Info=1, Error=2.
const TYPE_STATUS: i64 = 0;
const TYPE_INFO: i64 = 1;
const TYPE_ERROR: i64 = 2;

// QProtocolCSharp StatusCodes used by the sim.
const STATUS_SUCCESS: i64 = 1;
const STATUS_INVALID_CONFIGURATION: i64 = 2;
const STATUS_UPDATED: i64 = 3;
const STATUS_REQUIRES_RESTART: i64 = 4;
const STATUS_INVALID_ID: i64 = 5;
const STATUS_ACTION_NOT_FOUND: i64 = 7;
const STATUS_CANFD_CHANNEL_ONLY: i64 = 19;

fn status_body(type_code: i64, status_code: i64, message: &str) -> Value {
    json!({ "TypeCode": type_code, "StatusCode": status_code, "Message": message })
}

struct Reply {
    http_status: u16,
    body: Value,
}

impl Reply {
    fn ok(body: Value) -> Self {
        Reply {
            http_status: 200,
            body,
        }
    }
    fn status(http_status: u16, type_code: i64, status_code: i64, message: &str) -> Self {
        Reply {
            http_status,
            body: status_body(type_code, status_code, message),
        }
    }
    fn invalid_id() -> Self {
        Reply::status(
            400,
            TYPE_ERROR,
            STATUS_INVALID_ID,
            "Invalid item id, refer to the systemSettings/itemList for an overview",
        )
    }
}

/// Runtime context shared by the REST routes: actual (possibly ephemeral)
/// stream port, suspend flag, and fault knobs.
struct RouteCtx {
    config: SimConfig,
    stream_port: u16,
    shared: Arc<StreamShared>,
}

pub struct SimServer {
    rest_addr: SocketAddr,
    stream_port: u16,
    stop: Arc<AtomicBool>,
    handles: Vec<JoinHandle<()>>,
    pub state: Arc<Mutex<SimState>>,
    pub shared: Arc<StreamShared>,
}

impl SimServer {
    pub fn start(config: SimConfig) -> Result<Self, String> {
        let state = Arc::new(Mutex::new(build_state(&config)?));
        let server = tiny_http::Server::http(("127.0.0.1", config.server.rest_port))
            .map_err(|e| format!("failed to bind REST port: {e}"))?;
        let rest_addr = server
            .server_addr()
            .to_ip()
            .ok_or("REST server has no IP address")?;

        let stream_listener = TcpListener::bind(("127.0.0.1", config.server.stream_port))
            .map_err(|e| format!("failed to bind stream port: {e}"))?;
        let stream_port = stream_listener
            .local_addr()
            .map_err(|e| e.to_string())?
            .port();

        let stop = Arc::new(AtomicBool::new(false));
        let shared = Arc::new(StreamShared::new());
        let mut handles = Vec::new();

        handles.push(spawn_stream_server(
            stream_listener,
            state.clone(),
            config.clone(),
            shared.clone(),
            stop.clone(),
        ));

        let ctx = RouteCtx {
            config,
            stream_port,
            shared: shared.clone(),
        };
        let thread_stop = stop.clone();
        let thread_state = state.clone();
        handles.push(std::thread::spawn(move || {
            while !thread_stop.load(Ordering::Relaxed) {
                match server.recv_timeout(Duration::from_millis(50)) {
                    Ok(Some(request)) => handle_request(&thread_state, &ctx, request),
                    Ok(None) => {}
                    Err(_) => break,
                }
            }
        }));

        Ok(SimServer {
            rest_addr,
            stream_port,
            stop,
            handles,
            state,
            shared,
        })
    }

    pub fn rest_port(&self) -> u16 {
        self.rest_addr.port()
    }

    pub fn stream_port(&self) -> u16 {
        self.stream_port
    }
}

impl Drop for SimServer {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        for handle in self.handles.drain(..) {
            let _ = handle.join();
        }
    }
}

fn handle_request(state: &Mutex<SimState>, ctx: &RouteCtx, mut request: tiny_http::Request) {
    let url = request.url().to_string();
    let (path, query) = url.split_once('?').unwrap_or((url.as_str(), ""));
    let path = path.trim_end_matches('/').to_ascii_lowercase();
    let method = request.method().to_string().to_ascii_uppercase();
    let item_id = query_item_id(query);

    let mut body = String::new();
    let _ = request.as_reader().read_to_string(&mut body);

    let reply = route(state, ctx, &method, &path, item_id, &body);

    let response = tiny_http::Response::from_string(reply.body.to_string())
        .with_status_code(reply.http_status)
        .with_header(
            tiny_http::Header::from_bytes(&b"Content-Type"[..], &b"application/json"[..]).unwrap(),
        );
    let _ = request.respond(response);
}

fn query_item_id(query: &str) -> Option<i64> {
    query.split('&').find_map(|pair| {
        let (key, value) = pair.split_once('=')?;
        if key.eq_ignore_ascii_case("itemid") {
            value.parse().ok()
        } else {
            None
        }
    })
}

fn route(
    state: &Mutex<SimState>,
    ctx: &RouteCtx,
    method: &str,
    path: &str,
    item_id: Option<i64>,
    body: &str,
) -> Reply {
    // Apply latency fault: sleep before taking the lock, like a slow firmware.
    if method == "PUT" && path == "/system/settings/apply" && ctx.config.faults.apply_delay_ms > 0 {
        std::thread::sleep(Duration::from_millis(ctx.config.faults.apply_delay_ms));
    }
    let mut state = state.lock().unwrap();
    match (method, path) {
        ("GET", "/info/ping") => Reply::ok(json!({
            "Code": 0,
            "Message": "System is operational"
        })),
        // Version string format is assumption A2 (docs/assumptions.md).
        ("GET", "/version") => Reply::ok(json!({ "Version": "Q2.4.15" })),
        ("GET", "/item/list") => Reply::ok(state.item_list_json()),
        ("GET", "/system/settings") => Reply::ok(state.system_settings_json()),
        ("GET", "/item/settings") => match item_id.and_then(|id| state.find(id)) {
            Some(idx) => Reply::ok(state.item_settings_json(idx)),
            None => Reply::invalid_id(),
        },
        ("PUT", "/item/settings") => {
            put_document(&mut state, item_id, body, SimState::put_item_settings)
        }
        ("GET", "/item/operationmode") => match item_id.and_then(|id| state.find(id)) {
            Some(idx) => Reply::ok(state.item_op_mode_json(idx)),
            None => Reply::invalid_id(),
        },
        ("PUT", "/item/operationmode") => {
            put_document(&mut state, item_id, body, SimState::put_item_op_mode)
        }
        ("PUT", "/system/settings/apply") => match state.apply() {
            ApplyOutcome::AppliedWithRestart => Reply::status(
                200,
                TYPE_INFO,
                STATUS_REQUIRES_RESTART,
                "Settings applied. The measurement will be restarted",
            ),
            ApplyOutcome::Applied => Reply::status(200, TYPE_STATUS, STATUS_SUCCESS, "Success"),
        },
        ("GET", "/datastream/setup") => {
            let ip = "127.0.0.1";
            Reply::ok(json!({
                "IPAddresses": [ip],
                "TCPPort": ctx.stream_port,
                "WebSocketPort": ctx.config.server.websocket_port,
                // Deprecated blocks still emitted by Q2.4.x per the manual.
                "TCP": { "IP Addresses": [ip], "Port": ctx.stream_port },
                "Websocket": { "IP Addresses": [ip], "Port": ctx.config.server.websocket_port }
            }))
        }
        ("GET", "/canfd/message/list")
        | ("PUT", "/canfd/message/list")
        | ("DELETE", "/canfd/message/list")
        | ("PUT", "/canfd/message/transmit")
        | ("PUT", "/canfd/message/aborttransmission") => {
            canfd_route(&mut state, method, path, item_id, body)
        }
        ("GET", "/canfd/bus/status/list") => Reply::ok(json!([
            { "BusStatus": "Active", "TransmitErrorCount": 0, "ReceiveErrorCount": 0 }
        ])),
        // Action-plane writes (PLAN.md D12): no system apply involved.
        ("PUT", "/autozero/settings/apply") => {
            Reply::status(200, TYPE_STATUS, STATUS_SUCCESS, "Auto-zero applied")
        }
        ("PUT", "/wsb/bridgebalance/apply") | ("PUT", "/wsb/bridgebalance/reset") => {
            Reply::status(200, TYPE_STATUS, STATUS_SUCCESS, "Success")
        }
        ("PUT", "/datastream/suspend") => {
            ctx.shared.suspended.store(true, Ordering::Relaxed);
            Reply::status(200, TYPE_STATUS, STATUS_SUCCESS, "Success")
        }
        ("PUT", "/datastream/resume") => {
            ctx.shared.suspended.store(false, Ordering::Relaxed);
            Reply::status(200, TYPE_STATUS, STATUS_SUCCESS, "Success")
        }
        _ => Reply::status(404, TYPE_ERROR, STATUS_ACTION_NOT_FOUND, "Action not found"),
    }
}

fn canfd_route(
    state: &mut SimState,
    method: &str,
    path: &str,
    item_id: Option<i64>,
    body: &str,
) -> Reply {
    use crate::model::CAN_CHANNEL_IDENTIFIERS;
    let Some(idx) = item_id.and_then(|id| state.find(id)) else {
        return Reply::invalid_id();
    };
    let item = &state.items[idx];
    if !CAN_CHANNEL_IDENTIFIERS.contains(&item.item_name_identifier) {
        return Reply::status(
            400,
            TYPE_ERROR,
            STATUS_CANFD_CHANNEL_ONLY,
            "The requested action is only allowed for CAN FD channels",
        );
    }
    let item_id = item.item_id;
    match (method, path) {
        ("GET", "/canfd/message/list") => Reply::ok(
            state
                .can_message_lists
                .get(&item_id)
                .cloned()
                .unwrap_or_else(|| json!({ "MessageList": [] })),
        ),
        ("PUT", "/canfd/message/list") => match serde_json::from_str::<Value>(body) {
            Ok(doc) => {
                state.can_message_lists.insert(item_id, doc);
                Reply::status(200, TYPE_INFO, STATUS_UPDATED, "Message list updated")
            }
            Err(e) => Reply::status(
                400,
                TYPE_ERROR,
                STATUS_INVALID_CONFIGURATION,
                &format!("Invalid JSON body: {e}"),
            ),
        },
        ("DELETE", "/canfd/message/list") => {
            state.can_message_lists.remove(&item_id);
            Reply::status(200, TYPE_STATUS, STATUS_SUCCESS, "Message list cleared")
        }
        ("PUT", "/canfd/message/transmit") => {
            // Real firmware requires Participate mode for transmit.
            let participate = state.items[idx]
                .modes
                .iter()
                .any(|m| m.id == state.items[idx].current_mode && m.description == "Participate");
            if !participate {
                return Reply::status(
                    400,
                    TYPE_ERROR,
                    20,
                    "The requested action is only allowed when the CAN FD channel is in PARTICIPATE mode",
                );
            }
            *state.can_transmits.entry(item_id).or_default() += 1;
            Reply::status(200, TYPE_STATUS, STATUS_SUCCESS, "Success")
        }
        ("PUT", "/canfd/message/aborttransmission") => {
            Reply::status(200, TYPE_STATUS, STATUS_SUCCESS, "Success")
        }
        _ => Reply::status(404, TYPE_ERROR, STATUS_ACTION_NOT_FOUND, "Action not found"),
    }
}

fn put_document(
    state: &mut SimState,
    item_id: Option<i64>,
    body: &str,
    apply: fn(&mut SimState, usize, &Value) -> Result<(), String>,
) -> Reply {
    let Some(idx) = item_id.and_then(|id| state.find(id)) else {
        return Reply::invalid_id();
    };
    let doc: Value = match serde_json::from_str(body) {
        Ok(doc) => doc,
        Err(e) => {
            return Reply::status(
                400,
                TYPE_ERROR,
                STATUS_INVALID_CONFIGURATION,
                &format!("Invalid JSON body: {e}"),
            );
        }
    };
    match apply(state, idx, &doc) {
        Ok(()) => Reply::status(200, TYPE_INFO, STATUS_UPDATED, "Settings have been updated"),
        Err(msg) => Reply::status(400, TYPE_ERROR, STATUS_INVALID_CONFIGURATION, &msg),
    }
}
