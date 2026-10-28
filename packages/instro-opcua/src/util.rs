/// Verifies that both options are provided together or not at all.
/// Returns the result of [`Option::zip`]ing them together if they both are simultaneously
/// vacant or populated, and a [`PyValueError`] if either one is provided
/// without the other.
///
/// Example:
/// ```
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
                stringify!(item1),
                stringify!(item2)
            )))
        } else {
            Ok($item1.zip($item2))
        }
    }};
}

pub(crate) use require_both;
