use opcua::types as ua;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

#[pyclass(frozen, from_py_object, eq, ord, module = "instro.unstable._opcua")]
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub struct Duration(pub std::time::Duration);

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

    #[staticmethod]
    pub fn from_secs(seconds: u64) -> Self {
        Self(std::time::Duration::from_secs(seconds))
    }

    #[staticmethod]
    pub fn from_millis(milliseconds: u64) -> Self {
        Self(std::time::Duration::from_millis(milliseconds))
    }

    #[staticmethod]
    pub fn from_nanos(nanoseconds: u64) -> Self {
        Self(std::time::Duration::from_nanos(nanoseconds))
    }

    fn as_secs(&self) -> u64 {
        self.0.as_secs()
    }

    fn as_millis(&self) -> u128 {
        self.0.as_millis()
    }

    fn as_nanos(&self) -> u128 {
        self.0.as_nanos()
    }

    fn __repr__(&self) -> String {
        format!("Duration({}, {})", self.0.as_secs(), self.0.subsec_nanos())
    }
}

#[pyclass(frozen, from_py_object, eq, ord, module = "instro.unstable._opcua")]
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub struct DateTime(pub ua::DateTime);

#[pymethods]
impl DateTime {
    #[new]
    fn new(ticks: i64) -> Self {
        Self(ua::DateTime::from(ticks))
    }

    #[staticmethod]
    fn parse(text: &str) -> PyResult<Self> {
        ua::DateTime::parse_from_rfc3339(text)
            .map(Self)
            .map_err(|error| PyValueError::new_err(error.to_string()))
    }

    fn ticks(&self) -> i64 {
        self.0.ticks()
    }

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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn native_fraction_contributes_whole_nanoseconds() {
        let timestamp = ua::DateTime::from(116_444_736_000_000_001_i64);
        assert_eq!(unix_timestamp(Some(timestamp), Some(1_399)), Some(113));
        assert_eq!(unix_timestamp(None, Some(1_399)), None);
    }

    #[test]
    fn unix_timestamp_preserves_dates_before_unix_epoch() {
        let timestamp = ua::DateTime::from(116_444_735_999_999_999_i64);
        assert_eq!(unix_timestamp(Some(timestamp), None), Some(-100));
    }
}
