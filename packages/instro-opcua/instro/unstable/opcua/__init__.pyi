from .types import StatusCode

class OpcUaError(Exception):
    status_code: StatusCode

__all__ = ["OpcUaError"]
