//! Integration tests for `instro-ethernetip-rs` explicit sessions against the bundled simulator.
//!
//! Each test starts a cpppo-based simulator process exposing these tags:
//!
//! - `test_bool`: `BOOL`, initial value `false`
//! - `test_sint`: `SINT`, initial value `-3`
//! - `test_int`: `INT`, initial value `-12`
//! - `test_dint`: `DINT`, initial value `10`
//! - `test_lint`: `LINT`, initial value `-5678`
//! - `test_usint`: `USINT`, initial value `7`
//! - `test_uint`: `UINT`, initial value `42`
//! - `test_udint`: `UDINT`, initial value `99`
//! - `test_ulint`: `ULINT`, initial value `123456`
//! - `test_real`: `REAL`, initial value `1.25`
//! - `test_lreal`: `LREAL`, initial value `-9.5`
//!
//! The tests also mutate those tags and read them back:
//!
//! - `test_bool = true`
//! - `test_sint = -8`
//! - `test_int = 123`
//! - `test_dint = 42`
//! - `test_lint = 987654321`
//! - `test_usint = 9`
//! - `test_uint = 128`
//! - `test_udint = 456`
//! - `test_ulint = 987654`
//! - `test_real = 3.5`
//! - `test_lreal = 6.25`
//!
//! Before finishing, the write test restores all tags to their initial values and verifies the
//! readback.

#[tokio::test]
async fn connects_to_simulator() {
    let _guard = support::lock_tests().await;
    let simulator = support::start_simulator();
    let session = support::connect_explicit_session(&simulator).await;
    session
        .close()
        .await
        .expect("close should succeed after connect");
}

#[tokio::test]
async fn reads_configured_scalar_tags() {
    let _guard = support::lock_tests().await;
    let simulator = support::start_simulator();
    let mut session = support::connect_explicit_session(&simulator).await;
    support::assert_fixture_reads(&mut session, support::simulator_fixtures(), |fixture| {
        &fixture.initial
    })
    .await;

    session.close().await.expect("close should succeed");
}

#[tokio::test]
async fn read_tags_preserves_input_order() {
    let _guard = support::lock_tests().await;
    let simulator = support::start_simulator();
    let mut session = support::connect_explicit_session(&simulator).await;
    let fixtures = support::simulator_fixtures();
    let tag_names = fixtures
        .iter()
        .map(|fixture| fixture.name)
        .collect::<Vec<_>>();

    let values = session
        .read_tags(&tag_names)
        .await
        .expect("batch read should succeed");

    assert_eq!(values.len(), fixtures.len());
    for ((name, value), fixture) in values.into_iter().zip(fixtures.iter()) {
        assert_eq!(name, fixture.name);
        let fixture_name = fixture.name;
        assert_eq!(
            value.unwrap_or_else(|error| {
                panic!("batch read should succeed for {fixture_name}: {error}")
            }),
            fixture.initial
        );
    }

    session.close().await.expect("close should succeed");
}

#[tokio::test]
async fn writes_and_reads_back_configured_scalar_tags() {
    let _guard = support::lock_tests().await;
    let simulator = support::start_simulator();
    let mut session = support::connect_explicit_session(&simulator).await;
    let fixtures = support::simulator_fixtures();

    for fixture in fixtures {
        let name = fixture.name;
        session
            .write_tag(name, fixture.write.clone())
            .await
            .unwrap_or_else(|error| panic!("write should succeed for {name}: {error}"));
    }
    support::assert_fixture_reads(&mut session, fixtures, |fixture| &fixture.write).await;

    support::restore_default_fixture_state(&mut session, fixtures).await;

    session.close().await.expect("close should succeed");
}

#[tokio::test]
async fn restores_test_tags_to_default_state() {
    let _guard = support::lock_tests().await;
    let simulator = support::start_simulator();
    let mut session = support::connect_explicit_session(&simulator).await;

    support::restore_default_fixture_state(&mut session, support::simulator_fixtures()).await;

    session.close().await.expect("close should succeed");
}

mod support {
    use std::io::{BufRead, BufReader};
    use std::path::PathBuf;
    use std::process::{Child, Command, Stdio};
    use std::sync::OnceLock;
    use std::sync::mpsc;
    use std::thread;
    use std::time::Duration;

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

    #[derive(Clone)]
    pub(super) struct TagFixture {
        pub(super) name: &'static str,
        pub(super) type_name: &'static str,
        pub(super) initial: Value,
        pub(super) write: Value,
    }

    pub(super) struct Simulator {
        endpoint: String,
        _process: SimulatorProcess,
    }

    struct SimulatorProcess {
        child: Child,
    }

    const SIMULATOR_STARTUP_TIMEOUT: Duration = Duration::from_secs(30);

    impl Drop for SimulatorProcess {
        fn drop(&mut self) {
            let _ = self.child.kill();
            let _ = self.child.wait(); // wait for the child to exit completely
        }
    }

    pub(super) fn start_simulator() -> Simulator {
        let script = simulator_script_path();
        let mut child = Command::new("uv")
            .args(["run", "python"])
            .arg(&script)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .args(simulator_tag_args(simulator_fixtures()))
            .spawn()
            .unwrap_or_else(|error| {
                panic!("failed to start cpppo simulator process via `uv run python`: {error}")
            });

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

        let endpoint = match receiver.recv_timeout(SIMULATOR_STARTUP_TIMEOUT) {
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
                    "failed waiting for the simulator to indicate it started by sending its ip/port to stdout within {SIMULATOR_STARTUP_TIMEOUT:?}; process exited with {status}"
                );
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                let status = child.wait().unwrap_or_else(|error| {
                    panic!("failed to wait for simulator process after stdout disconnect: {error}")
                });
                panic!(
                    "failed waiting for the simulator to indicate it started by sending its ip/port to stdout; process exited with {status}"
                );
            }
        };
        if endpoint.is_empty() {
            let status = child
                .wait()
                .unwrap_or_else(|error| panic!("failed to wait for simulator process: {error}"));
            panic!(
                "simulator did not print an endpoint before returning; process exited with {status}"
            );
        }

        Simulator {
            endpoint,
            _process: SimulatorProcess { child },
        }
    }

    pub(super) async fn assert_fixture_reads(
        session: &mut ExplicitSession,
        fixtures: &[TagFixture],
        expected_value: impl Fn(&TagFixture) -> &Value,
    ) {
        for fixture in fixtures {
            let name = fixture.name;
            let value = session
                .read_tag(name)
                .await
                .unwrap_or_else(|error| panic!("read should succeed for {name}: {error}"));
            assert_eq!(
                value,
                expected_value(fixture).clone(),
                "unexpected value for {name}"
            );
        }
    }

    pub(super) async fn restore_default_fixture_state(
        session: &mut ExplicitSession,
        fixtures: &[TagFixture],
    ) {
        for fixture in fixtures {
            let name = fixture.name;
            session
                .write_tag(name, fixture.initial.clone())
                .await
                .unwrap_or_else(|error| panic!("restore should succeed for {name}: {error}"));
        }

        assert_fixture_reads(session, fixtures, |fixture| &fixture.initial).await;
    }

    pub(super) async fn connect_explicit_session(simulator: &Simulator) -> ExplicitSession {
        ExplicitSession::connect(&simulator.endpoint)
            .await
            .unwrap_or_else(|error| {
                let endpoint = &simulator.endpoint;
                panic!("failed to connect to {endpoint}: {error}")
            })
    }

    pub(super) fn simulator_fixtures() -> &'static [TagFixture] {
        static FIXTURES: OnceLock<Vec<TagFixture>> = OnceLock::new();
        FIXTURES.get_or_init(|| {
            vec![
                TagFixture {
                    name: "test_bool",
                    type_name: "BOOL",
                    initial: Value::Bool(false),
                    write: Value::Bool(true),
                },
                TagFixture {
                    name: "test_sint",
                    type_name: "SINT",
                    initial: Value::Sint(-3),
                    write: Value::Sint(-8),
                },
                TagFixture {
                    name: "test_int",
                    type_name: "INT",
                    initial: Value::Int(-12),
                    write: Value::Int(123),
                },
                TagFixture {
                    name: "test_dint",
                    type_name: "DINT",
                    initial: Value::Dint(10),
                    write: Value::Dint(42),
                },
                TagFixture {
                    name: "test_lint",
                    type_name: "LINT",
                    initial: Value::Lint(-5678),
                    write: Value::Lint(987_654_321),
                },
                TagFixture {
                    name: "test_usint",
                    type_name: "USINT",
                    initial: Value::Usint(7),
                    write: Value::Usint(9),
                },
                TagFixture {
                    name: "test_uint",
                    type_name: "UINT",
                    initial: Value::Uint(42),
                    write: Value::Uint(128),
                },
                TagFixture {
                    name: "test_udint",
                    type_name: "UDINT",
                    initial: Value::Udint(99),
                    write: Value::Udint(456),
                },
                TagFixture {
                    name: "test_ulint",
                    type_name: "ULINT",
                    initial: Value::Ulint(123_456),
                    write: Value::Ulint(987_654),
                },
                TagFixture {
                    name: "test_real",
                    type_name: "REAL",
                    initial: Value::Real(1.25),
                    write: Value::Real(3.5),
                },
                TagFixture {
                    name: "test_lreal",
                    type_name: "LREAL",
                    initial: Value::Lreal(-9.5),
                    write: Value::Lreal(6.25),
                },
            ]
        })
    }

    fn simulator_tag_args(fixtures: &[TagFixture]) -> Vec<String> {
        let mut args = Vec::with_capacity(fixtures.len() * 2);
        for fixture in fixtures {
            args.push("--tag".to_owned());
            args.push(format!(
                "{},{},{}",
                fixture.name,
                fixture.type_name,
                simulator_start_value(&fixture.initial)
            ));
        }
        args
    }

    fn simulator_start_value(value: &Value) -> String {
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

    fn simulator_script_path() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("..")
            .join("..")
            .join("tests")
            .join("cpppo_sim_server.py")
    }
}
