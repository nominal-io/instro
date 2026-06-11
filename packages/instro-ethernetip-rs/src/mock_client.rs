use std::collections::VecDeque;
use std::sync::{Arc, Mutex};

use rust_ethernet_ip::{BatchError, EtherNetIpError, PlcValue};

use crate::{ClientFuture, ExplicitClient};

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
