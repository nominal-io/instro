use std::collections::VecDeque;
use std::sync::{Arc, Mutex};

use rust_ethernet_ip::{BatchError, EtherNetIpError, PlcValue};

use crate::{ClientFuture, ConnectFuture, ExplicitClient, ExplicitConnector};

pub(crate) type BatchReadResult =
    std::result::Result<Vec<std::result::Result<PlcValue, BatchError>>, EtherNetIpError>;

#[derive(Debug, Default)]
pub(crate) struct MockState {
    pub(crate) read_calls: Vec<String>,
    pub(crate) batch_read_calls: Vec<Vec<String>>,
    pub(crate) write_calls: Vec<(String, PlcValue)>,
    pub(crate) unregister_calls: usize,
}

pub(crate) struct MockClient {
    state: Arc<Mutex<MockState>>,
    read_results: VecDeque<std::result::Result<PlcValue, EtherNetIpError>>,
    batch_read_results: VecDeque<BatchReadResult>,
    write_results: VecDeque<std::result::Result<(), EtherNetIpError>>,
    unregister_result: Option<std::result::Result<(), EtherNetIpError>>,
}

#[derive(Debug, Default)]
pub(crate) struct MockConnectorState {
    pub(crate) connect_calls: Vec<(String, Vec<u8>)>,
}

pub(crate) struct MockConnector {
    state: Arc<Mutex<MockConnectorState>>,
    // FIFO of connection outcomes, allowing tests to control the initial client and reconnects.
    connect_results: Mutex<VecDeque<std::result::Result<MockClient, EtherNetIpError>>>,
}

impl MockClient {
    pub(crate) fn new(
        state: Arc<Mutex<MockState>>,
        read_results: Vec<std::result::Result<PlcValue, EtherNetIpError>>,
        write_results: Vec<std::result::Result<(), EtherNetIpError>>,
        unregister_result: std::result::Result<(), EtherNetIpError>,
    ) -> Self {
        Self {
            state,
            read_results: read_results.into(),
            batch_read_results: VecDeque::new(),
            write_results: write_results.into(),
            unregister_result: Some(unregister_result),
        }
    }

    pub(crate) fn with_batch_read_results(mut self, results: Vec<BatchReadResult>) -> Self {
        self.batch_read_results = results.into();
        self
    }
}

impl MockConnector {
    pub(crate) fn new(
        state: Arc<Mutex<MockConnectorState>>,
        connect_results: Vec<std::result::Result<MockClient, EtherNetIpError>>,
    ) -> Self {
        Self {
            state,
            connect_results: Mutex::new(connect_results.into()),
        }
    }
}

impl ExplicitConnector for MockConnector {
    fn connect<'a>(&'a self, addr: &'a str, route_path_slots: &'a [u8]) -> ConnectFuture<'a> {
        self.state
            .lock()
            .expect("mock connector state poisoned")
            .connect_calls
            .push((addr.to_owned(), route_path_slots.to_vec()));
        let result = self
            .connect_results
            .lock()
            .expect("mock connect results poisoned")
            .pop_front()
            .expect("mock connect result missing");

        Box::pin(async move { result.map(|client| Box::new(client) as Box<dyn ExplicitClient>) })
    }
}

impl ExplicitClient for MockClient {
    fn read_tag<'a>(&'a mut self, tag_name: &'a str) -> ClientFuture<'a, PlcValue> {
        let result = self
            .read_results
            .pop_front()
            .expect("mock read result missing");
        self.state
            .lock()
            .expect("mock state poisoned")
            .read_calls
            .push(tag_name.to_owned());

        Box::pin(async move { result })
    }

    fn read_tags_batch<'a>(
        &'a mut self,
        tag_names: &'a [&'a str],
    ) -> ClientFuture<'a, Vec<(String, std::result::Result<PlcValue, BatchError>)>> {
        let names: Vec<String> = tag_names.iter().map(|name| (*name).to_owned()).collect();
        let result = self
            .batch_read_results
            .pop_front()
            .expect("mock batch read result missing");
        self.state
            .lock()
            .expect("mock state poisoned")
            .batch_read_calls
            .push(names.clone());

        Box::pin(async move { result.map(|values| names.into_iter().zip(values).collect()) })
    }

    fn write_tag<'a>(&'a mut self, tag_name: &'a str, value: PlcValue) -> ClientFuture<'a, ()> {
        let result = self
            .write_results
            .pop_front()
            .expect("mock write result missing");
        self.state
            .lock()
            .expect("mock state poisoned")
            .write_calls
            .push((tag_name.to_owned(), value));

        Box::pin(async move { result })
    }

    fn unregister_session<'a>(&'a mut self) -> ClientFuture<'a, ()> {
        self.state
            .lock()
            .expect("mock state poisoned")
            .unregister_calls += 1;
        let result = self
            .unregister_result
            .take()
            .expect("mock unregister result missing");

        Box::pin(async move { result })
    }
}
