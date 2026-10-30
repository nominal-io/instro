// Probably going to be complicated by capsules + versioning
// TODO(cartercanedy): generalize this for other pyo3 bridge packages.

use std::sync::Mutex;
use std::sync::OnceLock;

use pyo3::PyResult;
use pyo3::exceptions::PyRuntimeError;
use tokio::runtime::Runtime;

/// Returns a shared static runtime handle
pub(crate) fn get_runtime() -> PyResult<&'static Runtime> {
    static RT_INIT_GUARD: Mutex<()> = Mutex::new(());
    static RT: OnceLock<Runtime> = OnceLock::new();

    if let Some(rt) = RT.get() {
        return Ok(rt);
    }

    // acquire the lock to prevent concurrent attempts to initialize
    #[expect(
        clippy::expect_used,
        reason = "shouldn't attempt to retry on unknown panic"
    )]
    let _guard = RT_INIT_GUARD
        .lock()
        .expect("previous attempt to initialize async runtime failed with a panic, won't retry");

    if let Some(rt) = RT.get() {
        return Ok(rt);
    }

    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .map_err(|e| PyRuntimeError::new_err(format!("failed to build runtime: '{e}'")))?;

    Ok(RT.get_or_init(|| runtime))
}
