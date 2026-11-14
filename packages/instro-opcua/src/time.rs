use opcua::types as ua;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

/// A nonnegative duration with nanosecond precision.
///
/// Construct a duration from seconds and an additional nanosecond count.
///
/// Args:
///     seconds (int): Whole nonnegative seconds.
///     nanoseconds (int): Additional nonnegative nanoseconds. Defaults to zero.
///
/// Returns:
///     Duration: Their sum, within the native duration range.
///
/// Examples:
///     >>> Duration(1, 500_000_000).as_millis()
///     1500
#[pyclass(
    frozen,
    skip_from_py_object,
    eq,
    ord,
    module = "instro.unstable.opcua.types"
)]
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub(crate) struct Duration(std::time::Duration);

impl<'a, 'py> FromPyObject<'a, 'py> for Duration {
    type Error = pyo3::pyclass::PyClassGuardError<'a, 'py>;

    fn extract(obj: Borrowed<'a, 'py, PyAny>) -> Result<Self, Self::Error> {
        Ok(*obj.extract::<PyClassGuard<'_, Self>>()?)
    }
}

#[pymethods]
impl Duration {
    #[new]
    #[pyo3(signature = (seconds, nanoseconds=0))]
    fn new(seconds: u64, nanoseconds: u32) -> PyResult<Self> {
        std::time::Duration::from_secs(seconds)
            .checked_add(std::time::Duration::from_nanos(u64::from(nanoseconds)))
            .map(Self)
            .ok_or_else(|| PyValueError::new_err("duration exceeds the native range"))
    }

    /// Construct a duration from a nonnegative integer count of seconds.
    #[staticmethod]
    pub fn from_secs(seconds: u64) -> Self {
        Self(std::time::Duration::from_secs(seconds))
    }

    /// Construct a duration from a nonnegative integer count of milliseconds.
    #[staticmethod]
    pub fn from_millis(milliseconds: u64) -> Self {
        Self(std::time::Duration::from_millis(milliseconds))
    }

    /// Construct a duration from a nonnegative integer count of nanoseconds.
    #[staticmethod]
    pub fn from_nanos(nanoseconds: u64) -> Self {
        Self(std::time::Duration::from_nanos(nanoseconds))
    }

    /// Return total whole seconds, truncating any smaller unit.
    fn as_secs(&self) -> u64 {
        self.0.as_secs()
    }

    /// Return total whole milliseconds, truncating any smaller unit.
    fn as_millis(&self) -> u128 {
        self.0.as_millis()
    }

    /// Return total whole nanoseconds, truncating any smaller unit.
    fn as_nanos(&self) -> u128 {
        self.0.as_nanos()
    }

    fn __repr__(&self) -> String {
        format!("Duration({}, {})", self.0.as_secs(), self.0.subsec_nanos())
    }
}

/// A native OPC UA timestamp used by attribute metadata.
///
/// Value-attribute DateTime payloads instead use integer Unix nanoseconds.
///
/// Construct a native timestamp from ticks since 1601-01-01 UTC.
///
/// Args:
///     ticks (int): Signed count of 100-nanosecond intervals.
///
/// Returns:
///     DateTime: The native timestamp.
///
/// Examples:
///     >>> DateTime(116_444_736_000_000_000).unix_timestamp_nanos()
///     0
#[pyclass(
    frozen,
    skip_from_py_object,
    eq,
    ord,
    module = "instro.unstable.opcua.types"
)]
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub(crate) struct DateTime(ua::DateTime);

impl<'a, 'py> FromPyObject<'a, 'py> for DateTime {
    type Error = pyo3::pyclass::PyClassGuardError<'a, 'py>;

    fn extract(obj: Borrowed<'a, 'py, PyAny>) -> Result<Self, Self::Error> {
        Ok(*obj.extract::<PyClassGuard<'_, Self>>()?)
    }
}

#[pymethods]
impl DateTime {
    #[new]
    fn new(ticks: i64) -> Self {
        Self(ua::DateTime::from(ticks))
    }

    /// Parse an RFC 3339 timestamp.
    ///
    /// Args:
    ///     text (str): Timestamp including its UTC offset.
    ///
    /// Returns:
    ///     DateTime: Parsed native timestamp.
    #[staticmethod]
    fn parse(text: &str) -> PyResult<Self> {
        ua::DateTime::parse_from_rfc3339(text)
            .map(Self)
            .map_err(|error| PyValueError::new_err(error.to_string()))
    }

    /// Return 100-nanosecond ticks since 1601-01-01 UTC.
    fn ticks(&self) -> i64 {
        self.0.ticks()
    }

    /// Return integer nanoseconds since 1970-01-01 UTC.
    pub fn unix_timestamp_nanos(&self) -> i128 {
        let value = self.0.as_chrono();
        i128::from(value.timestamp()) * 1_000_000_000 + i128::from(value.timestamp_subsec_nanos())
    }

    fn __repr__(&self) -> String {
        format!("DateTime({})", self.0.ticks())
    }
}

pub fn unix_timestamp(value: Option<ua::DateTime>, fraction: Option<u16>) -> Option<i128> {
    value.map(|value| {
        DateTime(value).unix_timestamp_nanos() + i128::from(fraction.unwrap_or(0) / 100)
    })
}

impl crate::values::UaType for DateTime {
    type Inner = ua::DateTime;

    fn new(value: Self::Inner) -> Self {
        Self(value)
    }

    fn as_ua_ref(&self) -> &Self::Inner {
        &self.0
    }
}

impl crate::values::UaType for Duration {
    type Inner = std::time::Duration;

    fn new(value: Self::Inner) -> Self {
        Self(value)
    }

    fn as_ua_ref(&self) -> &Self::Inner {
        &self.0
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unix_timestamp_preserves_dates_before_unix_epoch() {
        let timestamp = ua::DateTime::from(116_444_735_999_999_999_i64);
        assert_eq!(unix_timestamp(Some(timestamp), None), Some(-100));
    }
}
