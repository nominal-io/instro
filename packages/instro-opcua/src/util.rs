use std::cell::Cell;
use std::ffi::c_ulong;
use std::time::Duration;

use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use tokio::runtime::Handle;
use tokio::runtime::Runtime;
use tokio::runtime::RuntimeFlavor;
use tokio::task::unconstrained;
use tokio::time::Instant;
use tokio::time::MissedTickBehavior;
use tokio::time::interval_at;

// PyO3 doesn't expose this symbol, so Windows needs an explicit stable-ABI DLL import.
#[cfg_attr(
    all(windows, target_arch = "x86"),
    link(name = "python3", kind = "raw-dylib", import_name_type = "undecorated")
)]
#[cfg_attr(
    all(windows, not(target_arch = "x86")),
    link(name = "python3", kind = "raw-dylib")
)]
unsafe extern "C" {
    fn PyThread_get_thread_ident() -> c_ulong;
}

thread_local! {
    static DRIVING: Cell<bool> = const { Cell::new(false) };
}

struct Driving;

impl Driving {
    fn enter() -> PyResult<Self> {
        if DRIVING.replace(true) {
            return Err(PyRuntimeError::new_err(
                "cannot start a blocking OPC UA wait inside another blocking OPC UA wait",
            ));
        }

        Ok(Self)
    }
}

impl Drop for Driving {
    fn drop(&mut self) {
        DRIVING.set(false);
    }
}

// In 3.11-3.12, the first import of the `threading` module initializes the
// thread info for `threading.main_thread`. Since the edge case of users
// spawning threads without importing `threading` first is extremely unlikely,
// not going to change the impl here, just documenting it. This is no longer an
// issue in 3.13+, though
pub fn is_main_thread(py: Python<'_>) -> bool {
    static MAIN_THREAD_ID: PyOnceLock<c_ulong> = PyOnceLock::new();

    let id_result = MAIN_THREAD_ID.get_or_try_init(py, || {
        py.import("threading")?
            .getattr("main_thread")?
            .call0()?
            .getattr("ident")?
            .extract::<c_ulong>()
    });

    matches!(id_result, Ok(id) if *id == unsafe { PyThread_get_thread_ident() })
}

pub(crate) fn get_runtime(py: Python<'_>) -> PyResult<&'static Runtime> {
    static RUNTIME: PyOnceLock<Runtime> = PyOnceLock::new();

    RUNTIME.get_or_try_init(
        py,
        #[cold]
        || Runtime::new().map_err(|error| PyRuntimeError::new_err(error.to_string())),
    )
}

const INTERRUPT_INTERVAL: Duration = Duration::from_millis(50);

/// Wait for a spawned operation, leaving it running if Python interrupts the wait.
pub fn wait_detached<F, T>(py: Python<'_>, fut: F) -> PyResult<T>
where
    F: Future<Output = PyResult<T>> + Send + 'static,
    T: Send + 'static,
{
    wait_cancellable(py, run_detached(fut))
}

/// Wait with the GIL released through completion, without dispatching Python signals.
pub fn wait<F, T>(py: Python<'_>, fut: F) -> PyResult<T>
where
    F: Future<Output = PyResult<T>> + Send,
    T: Send,
{
    let _driving = Driving::enter()?;
    // A blocked caller cannot yield to replenish its Tokio task budget.
    blocking(py, || pollster::block_on(unconstrained(fut)))?
}

/// Wait for a future, dropping it if Python interrupts the wait.
pub fn wait_cancellable<F, T>(py: Python<'_>, fut: F) -> PyResult<T>
where
    F: Future<Output = PyResult<T>> + Send,
    T: Send,
{
    if !is_main_thread(py) {
        return wait(py, fut);
    }

    wait(py, wait_main_thread(fut))
}

pub async fn run_detached<F, T>(fut: F) -> PyResult<T>
where
    F: Future<Output = PyResult<T>> + Send + 'static,
    T: Send + 'static,
{
    tokio::spawn(fut)
        .await
        .map_err(|error| PyRuntimeError::new_err(error.to_string()))?
}

async fn wait_main_thread<F, T>(future: F) -> PyResult<T>
where
    F: Future<Output = PyResult<T>>,
{
    let mut interval = interval_at(Instant::now() + INTERRUPT_INTERVAL, INTERRUPT_INTERVAL);
    interval.set_missed_tick_behavior(MissedTickBehavior::Skip);

    tokio::pin!(future);

    loop {
        tokio::select! {
            biased;
            result = &mut future => return result,
            _ = interval.tick() => Python::attach(|py| py.check_signals())?,
        }
    }
}

pub(crate) fn blocking<F, T>(py: Python<'_>, operation: F) -> PyResult<T>
where
    F: FnOnce() -> T + Send,
    T: Send,
{
    let handle = Handle::try_current().ok();
    let runtime = get_runtime(py)?;
    let _enter = runtime.enter();

    let result = if let Some(handle) = handle
        && handle.runtime_flavor() == RuntimeFlavor::CurrentThread
    {
        if handle.id() == runtime.handle().id() {
            return Err(PyRuntimeError::new_err(
                "avoiding deadlock: cannot nest blocking operations on the same current thread \
                 runtime",
            ));
        }

        py.detach(operation)
    } else {
        tokio::task::block_in_place(|| py.detach(operation))
    };

    Ok(result)
}

#[cfg(test)]
#[allow(clippy::expect_used)]
mod wait_tests {
    use super::*;

    fn result_after_yield(py: Python<'_>) -> PyResult<u32> {
        wait_cancellable(py, async {
            tokio::task::yield_now().await;
            Ok(41)
        })
    }

    #[test]
    fn synchronous_bridge_crosses_foreign_runtime_contexts() -> PyResult<()> {
        Python::initialize();
        assert_eq!(Python::attach(result_after_yield)?, 41);

        let current_thread = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .expect("foreign current-thread runtime");

        let result = current_thread.block_on(async { Python::attach(result_after_yield) });
        assert_eq!(result?, 41);

        let multi_thread = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(1)
            .enable_all()
            .build()
            .expect("foreign multithread runtime");

        let result = multi_thread.block_on(async {
            tokio::spawn(async { Python::attach(result_after_yield) })
                .await
                .expect("foreign task completes")
        });

        assert_eq!(result?, 41);
        Ok(())
    }

    #[test]
    #[allow(clippy::panic)]
    fn owned_task_panic_is_a_python_runtime_error() {
        Python::initialize();
        Python::attach(|py| {
            let result: PyResult<()> = wait_detached(py, async { panic!("service task panic") });
            let error = result.expect_err("panic must be observed by the waiting caller");
            assert!(error.is_instance_of::<PyRuntimeError>(py));
        });
    }

    #[test]
    fn synchronous_bridge_can_wait_from_its_own_runtime_worker() -> PyResult<()> {
        Python::initialize();
        let runtime = Python::attach(get_runtime)?;
        let task = runtime.spawn(async { Python::attach(result_after_yield) });
        let result = Python::attach(|py| py.detach(|| pollster::block_on(task)));
        assert_eq!(result.expect("shared runtime task finishes")?, 41);
        Ok(())
    }

    #[test]
    fn foreign_task_budget_does_not_block_observation_or_cleanup() -> PyResult<()> {
        Python::initialize();
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .expect("foreign current-thread runtime");

        runtime.block_on(async {
            tokio::spawn(async {
                while tokio::task::coop::has_budget_remaining() {
                    tokio::task::consume_budget().await;
                }

                let value = Python::attach(|py| {
                    wait_cancellable(py, async {
                        tokio::time::sleep(Duration::from_millis(125)).await;
                        Ok(41_u32)
                    })
                })?;

                assert_eq!(value, 41);
                assert!(!tokio::task::coop::has_budget_remaining());

                let cleaned = Python::attach(|py| wait_cancellable(py, async { Ok(42_u32) }))?;
                assert_eq!(cleaned, 42);
                assert!(!tokio::task::coop::has_budget_remaining());
                Ok(())
            })
            .await
            .expect("foreign task completes")
        })
    }
}
