//! Integration tests for `instro-ethernetip-rs` explicit sessions against an EtherNet/IP test target.
//!
//! The same tag definitions and write values are used against either the bundled cpppo simulator
//! or a live endpoint provided by `INSTRO_EIP_PLC_ENDPOINT`.
//!
//! Set `INSTRO_EIP_ROUTE_PATH_SLOTS` to a comma-separated list of backplane slots for targets that
//! require routed connections, for example `0`.
//!
//! Set `INSTRO_EIP_TARGET_L32E` for Allen-Bradley 1769-L32E targets; this omits unsigned integer
//! and `LREAL` tags from the expected test surface.
//!
//! Set `INSTRO_EIP_EXCLUDE_TYPES` to a comma-separated list of PLC data types to omit from the tag
//! surface, for example `LREAL`.
//!
//! Set `INSTRO_EIP_EXCLUDE_UNSIGNED_TYPES` for targets that do not expose unsigned integer tags.
//!
//! The simulator is seeded from the first write value for each tag. Tests then write each
//! configured value and read it back; the final value in each sequence is the post-test
//! resting value.
//!
//! This file intentionally keeps the test definition in Rust so the Rust crate does not need to
//! duplicate the richer Python `EtherNetIPConfig` schema.

#[tokio::test]
async fn connects_to_test_target() {
    let _guard = support::lock_tests().await;
    let target = support::start_test_target();
    let session = support::connect_explicit_session(&target).await;
    session
        .close()
        .await
        .expect("close should succeed after connect");
}

#[tokio::test]
async fn writes_and_reads_first_values_for_configured_scalar_tags() {
    let _guard = support::lock_tests().await;
    let target = support::start_test_target();
    let mut session = support::connect_explicit_session(&target).await;
    support::write_fixture_values(&mut session, support::tag_fixtures(), |fixture| {
        fixture.seed_value()
    })
    .await;
    support::assert_fixture_reads(&mut session, support::tag_fixtures(), |fixture| {
        fixture.seed_value()
    })
    .await;

    session.close().await.expect("close should succeed");
}

#[tokio::test]
async fn read_tags_preserves_input_order() {
    let _guard = support::lock_tests().await;
    let target = support::start_test_target();
    let mut session = support::connect_explicit_session(&target).await;
    let fixtures = support::tag_fixtures();
    let tag_names = fixtures
        .iter()
        .map(|fixture| fixture.name)
        .collect::<Vec<_>>();
    support::write_fixture_values(&mut session, fixtures, |fixture| fixture.seed_value()).await;

    let values = session
        .read_tags(&tag_names)
        .await
        .expect("batch read should succeed");

    assert_eq!(values.len(), fixtures.len());
    for ((tag_name, read_value), fixture) in values.iter().zip(fixtures) {
        assert_eq!(tag_name.as_str(), fixture.name);
        assert_eq!(
            read_value.as_ref().unwrap_or_else(|error| panic!(
                "read should succeed for {}: {error}",
                fixture.name
            )),
            fixture.seed_value()
        );
    }

    session.close().await.expect("close should succeed");
}

#[tokio::test]
async fn writes_and_reads_back_configured_scalar_tags() {
    let _guard = support::lock_tests().await;
    let target = support::start_test_target();
    let mut session = support::connect_explicit_session(&target).await;
    let fixtures = support::tag_fixtures();

    for fixture in fixtures {
        for write_value in &fixture.write_values {
            session
                .write_tag(fixture.name, write_value.clone())
                .await
                .unwrap_or_else(|error| {
                    panic!(
                        "write should succeed for {}={write_value:?}: {error}",
                        fixture.name
                    )
                });

            let read_value = session
                .read_tag(fixture.name)
                .await
                .unwrap_or_else(|error| {
                    panic!(
                        "readback should succeed for {}={write_value:?}: {error}",
                        fixture.name
                    )
                });

            assert_eq!(
                read_value, *write_value,
                "unexpected readback for {} after writing {write_value:?}",
                fixture.name
            );
        }
    }

    session.close().await.expect("close should succeed");
}

#[tokio::test]
async fn restores_test_tags_to_default_state() {
    let _guard = support::lock_tests().await;
    let target = support::start_test_target();
    let mut session = support::connect_explicit_session(&target).await;

    support::restore_default_fixture_state(&mut session, support::tag_fixtures()).await;

    session.close().await.expect("close should succeed");
}

mod support {
    use std::env;
    use std::sync::OnceLock;

    use instro_ethernetip_rs::{ExplicitSession, Value};
    use tokio::sync::{Mutex, MutexGuard};

    fn test_lock() -> &'static Mutex<()> {
        static LOCK: OnceLock<Mutex<()>> = OnceLock::new();
        LOCK.get_or_init(|| Mutex::new(()))
    }

    /// Acquires a lock that prevents concurrent test execution.
    ///
    /// Useful since tests might be run in parallel with nextest
    pub(super) async fn lock_tests() -> MutexGuard<'static, ()> {
        test_lock().lock().await
    }

    /// Required PLC tag on the target endpoint plus values the test will write and read back.
    ///
    /// For live endpoints, these names and types must already exist on the PLC. For the bundled
    /// simulator, this same definition is used to create matching cpppo tags.
    #[derive(Clone)]
    pub(super) struct TagFixture {
        pub(super) name: &'static str,
        pub(super) type_name: &'static str,
        pub(super) write_values: Vec<Value>,
    }

    impl TagFixture {
        /// First write value, also used to seed the cpppo simulator.
        pub(super) fn seed_value(&self) -> &Value {
            self.write_values
                .first()
                .unwrap_or_else(|| panic!("{} must define at least one write value", self.name))
        }
    }

    /// EtherNet/IP endpoint under test, backed by either live PLC or local cpppo process.
    pub(super) struct TestTarget {
        endpoint: String,
        _process: Option<cpppo_simulator::Process>,
    }

    const PLC_ENDPOINT_ENV_VAR: &str = "INSTRO_EIP_PLC_ENDPOINT";
    const ROUTE_PATH_SLOTS_ENV_VAR: &str = "INSTRO_EIP_ROUTE_PATH_SLOTS";
    const TARGET_L32E_ENV_VAR: &str = "INSTRO_EIP_TARGET_L32E";
    const EXCLUDE_TYPES_ENV_VAR: &str = "INSTRO_EIP_EXCLUDE_TYPES";
    const EXCLUDE_UNSIGNED_TYPES_ENV_VAR: &str = "INSTRO_EIP_EXCLUDE_UNSIGNED_TYPES";

    /// Resolve the target endpoint without exposing simulator/live details to tests.
    pub(super) fn start_test_target() -> TestTarget {
        if let Some(endpoint) = configured_plc_endpoint() {
            return TestTarget {
                endpoint,
                _process: None,
            };
        }

        let (endpoint, process) = cpppo_simulator::start(tag_fixtures());
        TestTarget {
            endpoint,
            _process: Some(process),
        }
    }

    fn configured_plc_endpoint() -> Option<String> {
        let endpoint = env::var(PLC_ENDPOINT_ENV_VAR).ok()?;
        let endpoint = endpoint.trim().to_owned();
        if endpoint.is_empty() {
            panic!("{PLC_ENDPOINT_ENV_VAR} must not be empty when set");
        }
        Some(endpoint)
    }

    fn exclude_unsigned_types() -> bool {
        truthy_env(EXCLUDE_UNSIGNED_TYPES_ENV_VAR) || target_l32e()
    }

    fn is_unsigned_type(type_name: &str) -> bool {
        matches!(type_name, "USINT" | "UINT" | "UDINT" | "ULINT")
    }

    fn excluded_type_names() -> Vec<String> {
        let mut type_names = Vec::new();

        if let Ok(value) = env::var(EXCLUDE_TYPES_ENV_VAR) {
            type_names.extend(
                value
                    .split(',')
                    .map(str::trim)
                    .filter(|type_name| !type_name.is_empty())
                    .map(str::to_ascii_uppercase),
            );
        }

        if target_l32e() {
            type_names.push("LREAL".to_owned());
        }

        type_names
    }

    fn target_l32e() -> bool {
        truthy_env(TARGET_L32E_ENV_VAR)
    }

    fn truthy_env(name: &str) -> bool {
        let Ok(value) = env::var(name) else {
            return false;
        };
        !matches!(
            value.trim().to_ascii_lowercase().as_str(),
            "" | "0" | "false" | "no" | "off"
        )
    }

    fn route_path_slots() -> Vec<u8> {
        let Ok(value) = env::var(ROUTE_PATH_SLOTS_ENV_VAR) else {
            return Vec::new();
        };

        value
            .split(',')
            .filter_map(|slot| {
                let slot = slot.trim();
                if slot.is_empty() {
                    return None;
                }
                Some(slot.parse::<u8>().unwrap_or_else(|error| {
                    panic!("{ROUTE_PATH_SLOTS_ENV_VAR} must contain comma-separated u8 slot values: {error}")
                }))
            })
            .collect()
    }

    pub(super) async fn assert_fixture_reads(
        session: &mut ExplicitSession,
        fixtures: &[TagFixture],
        expected_value: impl Fn(&TagFixture) -> &Value,
    ) {
        for fixture in fixtures {
            let value = session
                .read_tag(fixture.name)
                .await
                .unwrap_or_else(|error| {
                    panic!("read should succeed for {}: {error}", fixture.name)
                });
            assert_eq!(
                value,
                expected_value(fixture).clone(),
                "unexpected value for {}",
                fixture.name
            );
        }
    }

    pub(super) async fn write_fixture_values(
        session: &mut ExplicitSession,
        fixtures: &[TagFixture],
        value: impl Fn(&TagFixture) -> &Value,
    ) {
        for fixture in fixtures {
            session
                .write_tag(fixture.name, value(fixture).clone())
                .await
                .unwrap_or_else(|error| {
                    panic!("write should succeed for {}: {error}", fixture.name)
                });
        }
    }

    pub(super) async fn restore_default_fixture_state(
        session: &mut ExplicitSession,
        fixtures: &[TagFixture],
    ) {
        write_fixture_values(session, fixtures, |fixture| fixture.seed_value()).await;
        assert_fixture_reads(session, fixtures, |fixture| fixture.seed_value()).await;
    }

    pub(super) async fn connect_explicit_session(target: &TestTarget) -> ExplicitSession {
        let route_path_slots = route_path_slots();
        let session = if route_path_slots.is_empty() {
            ExplicitSession::connect(&target.endpoint).await
        } else {
            ExplicitSession::connect_with_route_path_slots(&target.endpoint, &route_path_slots)
                .await
        };

        session.unwrap_or_else(|error| {
            panic!(
                "failed to connect to {} with route path slots {:?}: {error}",
                target.endpoint, route_path_slots
            )
        })
    }

    /// Expected tag surface for any EtherNet/IP endpoint used by this test.
    ///
    /// Set `INSTRO_EIP_TARGET_L32E` for 1769-L32E targets that lack unsigned integer and `LREAL`
    /// tag support.
    /// Set `INSTRO_EIP_EXCLUDE_TYPES` to omit any target-specific unsupported data types.
    /// Set `INSTRO_EIP_EXCLUDE_UNSIGNED_TYPES` when the target endpoint does not define
    /// `USINT`, `UINT`, `UDINT`, or `ULINT` tags.
    pub(super) fn tag_fixtures() -> &'static [TagFixture] {
        static FIXTURES: OnceLock<Vec<TagFixture>> = OnceLock::new();
        FIXTURES.get_or_init(|| {
            let mut fixtures = vec![
                TagFixture {
                    name: "test_bool",
                    type_name: "BOOL",
                    write_values: vec![Value::Bool(false), Value::Bool(true), Value::Bool(false)],
                },
                TagFixture {
                    name: "test_sint",
                    type_name: "SINT",
                    write_values: vec![Value::Sint(-3), Value::Sint(-8), Value::Sint(-3)],
                },
                TagFixture {
                    name: "test_int",
                    type_name: "INT",
                    write_values: vec![Value::Int(-12), Value::Int(123), Value::Int(-12)],
                },
                TagFixture {
                    name: "test_dint",
                    type_name: "DINT",
                    write_values: vec![Value::Dint(10), Value::Dint(42), Value::Dint(10)],
                },
                TagFixture {
                    name: "test_lint",
                    type_name: "LINT",
                    write_values: vec![
                        Value::Lint(-5678),
                        Value::Lint(987_654_321),
                        Value::Lint(-5678),
                    ],
                },
                TagFixture {
                    name: "test_usint",
                    type_name: "USINT",
                    write_values: vec![Value::Usint(7), Value::Usint(9), Value::Usint(7)],
                },
                TagFixture {
                    name: "test_uint",
                    type_name: "UINT",
                    write_values: vec![Value::Uint(42), Value::Uint(128), Value::Uint(42)],
                },
                TagFixture {
                    name: "test_udint",
                    type_name: "UDINT",
                    write_values: vec![Value::Udint(99), Value::Udint(456), Value::Udint(99)],
                },
                TagFixture {
                    name: "test_ulint",
                    type_name: "ULINT",
                    write_values: vec![
                        Value::Ulint(123_456),
                        Value::Ulint(987_654),
                        Value::Ulint(123_456),
                    ],
                },
                TagFixture {
                    name: "test_real",
                    type_name: "REAL",
                    write_values: vec![Value::Real(1.25), Value::Real(3.5), Value::Real(1.25)],
                },
                TagFixture {
                    name: "test_lreal",
                    type_name: "LREAL",
                    write_values: vec![Value::Lreal(-9.5), Value::Lreal(6.25), Value::Lreal(-9.5)],
                },
            ];

            if exclude_unsigned_types() {
                fixtures.retain(|fixture| !is_unsigned_type(fixture.type_name));
            }

            let excluded_type_names = excluded_type_names();
            if !excluded_type_names.is_empty() {
                fixtures.retain(|fixture| {
                    !excluded_type_names
                        .iter()
                        .any(|name| name == fixture.type_name)
                });
            }

            fixtures
        })
    }

    mod cpppo_simulator {
        use std::io::{BufRead, BufReader};
        #[cfg(unix)]
        use std::os::unix::process::CommandExt;
        use std::path::PathBuf;
        use std::process::{Child, Command, Stdio};
        use std::sync::mpsc;
        use std::thread;
        use std::time::Duration;

        use instro_ethernetip_rs::Value;

        use super::TagFixture;

        const STARTUP_TIMEOUT: Duration = Duration::from_secs(30);

        /// Local simulator process kept alive for the duration of the target.
        pub(super) struct Process {
            child: Child,
        }

        impl Drop for Process {
            fn drop(&mut self) {
                kill_process_tree(&mut self.child);
                let _ = self.child.wait(); // wait for the child to exit completely
            }
        }

        // `uv run` spawns python as a grandchild, so killing only `self.child`
        // (the `uv` process) orphans the simulator (INSTRO-418). Kill the tree.
        #[cfg(windows)]
        fn kill_process_tree(child: &mut Child) {
            let _ = Command::new("taskkill")
                .args(["/F", "/T", "/PID", &child.id().to_string()])
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status();
        }

        #[cfg(unix)]
        fn kill_process_tree(child: &mut Child) {
            // The child leads its own process group (see `start`); signal the whole
            // group. `--` keeps the negative pgid from being parsed as an option.
            let _ = Command::new("kill")
                .args(["-KILL", "--", &format!("-{}", child.id())])
                .status();
            let _ = child.kill();
        }

        /// Start cpppo with the same tag definition used by live-target tests.
        pub(super) fn start(fixtures: &[TagFixture]) -> (String, Process) {
            let script = script_path();
            let mut command = Command::new("uv");
            command
                .args(["run", "python"])
                .arg(&script)
                .stdin(Stdio::null())
                .stdout(Stdio::piped())
                .stderr(Stdio::inherit())
                .args(tag_args(fixtures));
            // Lead a new process group so teardown can kill the whole tree (INSTRO-418).
            #[cfg(unix)]
            command.process_group(0);
            let mut child = command.spawn().unwrap_or_else(|error| {
                panic!("failed to start cpppo simulator process via `uv run python`: {error}")
            });

            let endpoint = read_endpoint_from_stdout(&mut child);
            (endpoint, Process { child })
        }

        fn read_endpoint_from_stdout(child: &mut Child) -> String {
            let stdout = child
                .stdout
                .take()
                .expect("simulator process stdout should be piped");
            let (sender, receiver) = mpsc::channel();
            thread::spawn(move || {
                let mut reader = BufReader::new(stdout);
                let mut endpoint = String::new();
                let result = reader
                    .read_line(&mut endpoint)
                    .map(|_| endpoint.trim().to_owned());
                let _ = sender.send(result);
            });

            let endpoint = match receiver.recv_timeout(STARTUP_TIMEOUT) {
                Ok(Ok(endpoint)) => endpoint,
                Ok(Err(error)) => {
                    panic!("failed to read simulator endpoint: {error}");
                }
                Err(mpsc::RecvTimeoutError::Timeout) => {
                    let _ = child.kill();
                    let status = child.wait().unwrap_or_else(|error| {
                        panic!("failed to wait for simulator process after timeout: {error}")
                    });
                    panic!(
                        "failed waiting for the simulator to indicate it started by sending its ip/port to stdout within {STARTUP_TIMEOUT:?}; process exited with {status}",
                    );
                }
                Err(mpsc::RecvTimeoutError::Disconnected) => {
                    let status = child.wait().unwrap_or_else(|error| {
                        panic!(
                            "failed to wait for simulator process after stdout disconnect: {error}"
                        )
                    });
                    panic!(
                        "failed waiting for the simulator to indicate it started by sending its ip/port to stdout; process exited with {status}"
                    );
                }
            };
            if endpoint.is_empty() {
                let status = child.wait().unwrap_or_else(|error| {
                    panic!("failed to wait for simulator process: {error}")
                });
                panic!(
                    "simulator did not print an endpoint before returning; process exited with {status}"
                );
            }
            endpoint
        }

        fn tag_args(fixtures: &[TagFixture]) -> Vec<String> {
            let mut args = Vec::with_capacity(fixtures.len() * 2);
            for fixture in fixtures {
                args.push("--tag".to_owned());
                args.push(format!(
                    "{},{},{}",
                    fixture.name,
                    fixture.type_name,
                    start_value(fixture.seed_value())
                ));
            }
            args
        }

        fn start_value(value: &Value) -> String {
            match value {
                Value::Bool(value) => value.to_string(),
                Value::Sint(value) => value.to_string(),
                Value::Int(value) => value.to_string(),
                Value::Dint(value) => value.to_string(),
                Value::Lint(value) => value.to_string(),
                Value::Usint(value) => value.to_string(),
                Value::Uint(value) => value.to_string(),
                Value::Udint(value) => value.to_string(),
                Value::Ulint(value) => value.to_string(),
                Value::Real(value) => value.to_string(),
                Value::Lreal(value) => value.to_string(),
                Value::String(_) | Value::Struct(_) => {
                    panic!(
                        "string and structured values are not supported by the cpppo integration simulator"
                    )
                }
            }
        }

        fn script_path() -> PathBuf {
            PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .join("..")
                .join("..")
                .join("tests")
                .join("cpppo_sim_server.py")
        }
    }
}
