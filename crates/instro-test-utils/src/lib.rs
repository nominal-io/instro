pub mod assertions {
    use std::fmt::Debug;

    use serde::Serialize;
    use serde::Deserialize;

    /// Assert that a serializable value can be serialized and deserialized without loss of information.
    ///
    /// This function **_PANICS_** if the value cannot be serialized or deserialized.
    /// Meant for use in unit tests.
    #[track_caller]
    pub fn assert_serde_json_roundtrip_eq<T>(serializable: &T)
    where
        for<'a> T: Debug + PartialEq + Serialize + Deserialize<'a>,
    {
        #[expect(
            clippy::expect_used,
            reason = "panicking assertions expected to fail if serialization fails"
        )]
        let json = serde_json::to_string(serializable).expect("serialize");
        #[expect(
            clippy::expect_used,
            reason = "expected to fail if deserialization fails"
        )]
        let back: T = serde_json::from_str(&json).expect("deserialize");
        assert_eq!(back, *serializable);
    }
}
