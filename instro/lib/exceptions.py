"""Instro exception classes."""


class InstroError(Exception):
    """Base Instro error."""


class FeatureNotSupportedError(InstroError):
    """Unsupported feature error."""


class InstrumentNotOpenError(InstroError):
    """Raised when an instrument method is called before open()."""
