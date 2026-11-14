use pyo3::IntoPyObjectExt;

use super::*;
use crate::test_support::with_python;

#[test]
fn security_policy_names_are_bounded_and_case_insensitive() -> PyResult<()> {
    with_python(|py| {
        let names = [
            (
                "aES128-sHA256-rSAoAEP",
                UaSecurityPolicy::Aes128Sha256RsaOaep,
            ),
            (
                "http://opcfoundation.org/UA/SecurityPolicy#Basic256Sha256",
                UaSecurityPolicy::Basic256Sha256,
            ),
        ];

        for (name, expected) in names {
            assert_eq!(parse_security_policy(name)?, expected);
        }

        let failure = parse_security_policy("Basic256_SHA256").unwrap_err();
        assert!(failure.is_instance_of::<PyValueError>(py));

        Ok(())
    })
}

#[test]
fn security_mode_rejects_invalid_native_values_and_normalizes_names() -> PyResult<()> {
    with_python(|py| {
        let invalid = <MessageSecurityMode as UaType>::new(ua::MessageSecurityMode::Invalid);
        let invalid = Py::new(py, invalid)?;
        let failure = invalid.extract::<SecurityModeInput>(py).unwrap_err();
        assert!(failure.is_instance_of::<PyValueError>(py));

        let name = "sIGNaNDeNCRYPT".into_bound_py_any(py)?;
        assert_eq!(
            name.extract::<SecurityModeInput>()?.into_native(),
            ua::MessageSecurityMode::SignAndEncrypt
        );

        Ok(())
    })
}

#[test]
fn duration_integer_extraction_preserves_precision_above_float_range() -> PyResult<()> {
    with_python(|py| {
        let integer = 9_007_199_254_740_993_u64.into_bound_py_any(py)?;
        assert_eq!(
            integer.extract::<DurationInput>()?.into_native().as_secs(),
            9_007_199_254_740_993
        );

        Ok(())
    })
}

#[test]
fn duration_errors_distinguish_invalid_values_from_wrong_types() -> PyResult<()> {
    with_python(|py| {
        let invalid_values = [(-1).into_bound_py_any(py)?, f64::NAN.into_bound_py_any(py)?];

        for value in invalid_values {
            let failure = value.extract::<DurationInput>().unwrap_err();
            assert!(failure.is_instance_of::<PyValueError>(py));
        }

        let text = "1.25".into_bound_py_any(py)?;
        let failure = text.extract::<DurationInput>().unwrap_err();
        assert!(failure.is_instance_of::<PyTypeError>(py));

        Ok(())
    })
}

#[test]
fn native_integer_extraction_enforces_type_and_range() -> PyResult<()> {
    with_python(|py| {
        let overflow = 4_294_967_296_u64.into_bound_py_any(py)?;
        let failure = overflow.extract::<NativeInteger<u32>>().unwrap_err();
        assert!(failure.is_instance_of::<PyValueError>(py));

        let float = 1.0.into_bound_py_any(py)?;
        let failure = float.extract::<NativeInteger<u32>>().unwrap_err();
        assert!(failure.is_instance_of::<PyTypeError>(py));

        Ok(())
    })
}

#[test]
fn application_certificate_source_requires_a_complete_pair() -> PyResult<()> {
    with_python(|py| {
        let certificate = Path::new("own/cert.der");
        let failure = validate_certificate_paths(Some(certificate), None).unwrap_err();
        assert!(failure.is_instance_of::<PyValueError>(py));

        Ok(())
    })
}
