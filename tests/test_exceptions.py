from instro.lib.exceptions import FeatureNotSupportedError, InstroError


def test_feature_not_supported_error_is_instro_error() -> None:
    assert issubclass(FeatureNotSupportedError, InstroError)
