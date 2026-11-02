use pyo3::Python;
use pyo3::marker::Ungil;

pub trait RuntimeExt {
    fn unblock_on<F, T, U>(&self, py: Python<'_>, f: F) -> U
    where
        F: FnOnce() -> T + Send,
        T: Future<Output = U>,
        U: Ungil;
}

impl RuntimeExt for tokio::runtime::Runtime {
    fn unblock_on<F, T, U>(&self, py: Python<'_>, f: F) -> U
    where
        F: FnOnce() -> T + Send,
        T: Future<Output = U>,
        U: Ungil,
    {
        py.detach(|| self.block_on(f()))
    }
}

/// Convenience macro for chaining `cast` (or optionally `extract`) calls on one or more `PyAny` objects.
///
/// ```ignore
/// use instro_opcua::util::py_match;
///
/// let obj = PyAny::new(py, "123");
/// let result = py_match! {
///     i: u32 = obj => i,
///     else => Err(PyValueError::new_err("invalid value"))?
/// };
/// assert_eq!(result, Ok(123));
/// ```
macro_rules! py_match {
    // optional fall-through case
    (else => $else:expr $(,)?) => {
        $else
    };

    // escape hatch for using `extract`, incurs the perf hit, but allows for more flexible binding
    (#[extract] $binding:ident : $target_t:ty = $obj:ident => $body:expr, $($($rest:tt)+)?) => {
        if let Ok($binding) = $obj.extract::<$target_t>() {
            $body
        } $(else {
            $crate::util::py_match!($($rest)*)
        })?
    };

    // default binding case, use `cast` to avoid `PyDowncastError` -> `PyErr` conversion
    // see https://pyo3.rs/v0.30.1/performance.html#extract-versus-cast
    ($binding:ident : $target_t:ty = $obj:ident => $body:expr, $($($rest:tt)+)?) => {
        if let Ok($binding) = $obj.cast_exact::<$target_t>() {
            let $binding = Bound::clone(&$binding);
            $body
        } $(else {
            $crate::util::py_match!($($rest)*)
        })?
    }
}

pub(crate) use py_match;

/// Verifies that both options are provided together or not at all.
/// Returns the result of [`Option::zip`]ing them together if they both are simultaneously
/// vacant or populated, and a [`PyValueError`] if either one is provided
/// without the other.
///
/// Example:
/// ```ignore
/// use instro_opcua::util::require_both;
///
/// let a = Some(1);
/// let b = Some(2);
/// let c = require_both!(a, b);
/// assert_eq!(c, Ok(Some((1, 2))));
///
/// let a = Some(1);
/// let b = None;
/// ```
macro_rules! require_both {
    ($item1:ident, $item2:ident) => {{
        use ::pyo3::exceptions::PyValueError;

        if $item1.is_some() ^ $item2.is_some() {
            Err(PyValueError::new_err(format!(
                "`{}` and `{}` must be provided together or not at all",
                stringify!($item1),
                stringify!($item2)
            )))
        } else {
            Ok($item1.zip($item2))
        }
    }};
}

pub(crate) use require_both;
