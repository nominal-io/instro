"""Synchronous OPC UA primitives backed by ``async-opcua``."""

from instro.unstable._opcua import IdentityToken, OpcUaClient, OpcUaError, get_endpoints

__all__ = ["OpcUaError", "OpcUaClient", "get_endpoints", "IdentityToken"]
