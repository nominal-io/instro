use std::collections::VecDeque;
use std::sync::{Arc, Mutex};

use rust_ethernet_ip::{EtherNetIpError, PlcValue};

use crate::{ClientFuture, ExplicitClient};

#[derive(Debug, Default)]
pub(crate) struct MockState {
    pub(crate) read_calls: Vec<String>,
    pub(crate) write_calls: Vec<(String, PlcValue)>,
    pub(crate) unregister_calls: usize,
}

pub(crate) struct MockClient {
    state: Arc<Mutex<MockState>>,
    read_results: VecDeque<std::result::Result<PlcValue, EtherNetIpError>>,
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
            write_results: write_results.into(),
            unregister_result: Some(unregister_result),
        }
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
