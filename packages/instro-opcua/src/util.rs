use std::sync::mpsc;
use std::time::Duration;

use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use tokio::runtime::Handle;
use tokio::runtime::Runtime;
use tokio::runtime::RuntimeFlavor;

pub(crate) fn wait<F, T>(py: Python<'_>, future: F) -> PyResult<T>
where
    F: Future<Output = PyResult<T>> + Send + 'static,
    T: Send + 'static,
{
    let runtime = crate::shared_rt::get(py)?;
    block_on(runtime, py, future)?
}

fn block_on<F, T>(runtime: &Runtime, py: Python<'_>, future: F) -> PyResult<T>
where
    F: Future<Output = T> + Send + 'static,
    T: Send + 'static,
{
    py.detach(|| {
        if let Ok(current) = Handle::try_current()
            && current.runtime_flavor() == RuntimeFlavor::CurrentThread
        {
            if current.id() == runtime.handle().id() {
                return Err(PyRuntimeError::new_err(
                    "cannot synchronously wait on the same current-thread Tokio runtime",
                ));
            }

            return pollster::block_on(runtime.spawn(future))
                .map_err(|error| PyRuntimeError::new_err(error.to_string()));
        }

        tokio::task::block_in_place(|| Ok(runtime.block_on(future)))
    })
}

pub(crate) fn blocking<T>(operation: impl FnOnce() -> T) -> T {
    let on_current_thread_runtime = Handle::try_current()
        .is_ok_and(|handle| handle.runtime_flavor() == RuntimeFlavor::CurrentThread);

    if on_current_thread_runtime {
        operation()
    } else {
        tokio::task::block_in_place(operation)
    }
}

pub(crate) fn receive<T: Send>(py: Python<'_>, mut receiver: mpsc::Receiver<T>) -> PyResult<T> {
    loop {
        py.check_signals()?;

        let (next_receiver, result) = py.detach(move || {
            let receive = || receiver.recv_timeout(Duration::from_millis(50));
            let result = blocking(receive);

            (receiver, result)
        });

        receiver = next_receiver;

        match result {
            Err(mpsc::RecvTimeoutError::Timeout) => (),

            Ok(value) => {
                py.check_signals()?;
                return Ok(value);
            }

            Err(mpsc::RecvTimeoutError::Disconnected) => {
                return Err(PyRuntimeError::new_err(
                    "native setup task ended unexpectedly",
                ));
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use super::*;

    #[test]
    fn bridge_offloads_foreign_current_thread_and_rejects_same_runtime() -> PyResult<()> {
        Python::initialize();
        let foreign = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .map_err(|error| PyRuntimeError::new_err(error.to_string()))?;

        Python::attach(|py| {
            foreign.block_on(async {
                let foreign_runtime_result = wait(py, async {
                    tokio::time::sleep(Duration::from_millis(1)).await;
                    Ok(17)
                })?;

                assert_eq!(foreign_runtime_result, 17);
                let failure = block_on(&foreign, py, async { 0 })
                    .err()
                    .ok_or_else(|| PyRuntimeError::new_err("same-runtime wait was admitted"))?;

                assert!(failure.is_instance_of::<PyRuntimeError>(py));
                PyResult::Ok(())
            })
        })
    }
}
