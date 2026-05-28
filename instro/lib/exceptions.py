"""Instro exception classes."""


class InstroError(Exception):
    """Base Instro error."""


class FeatureNotSupportedError(InstroError):
    """Unsupported feature error."""
