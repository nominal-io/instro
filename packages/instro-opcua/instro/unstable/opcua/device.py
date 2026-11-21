"""Config-driven lifecycle for a synchronous OPC UA client."""

from __future__ import annotations

from contextlib import nullcontext
from os import getenv
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Self
from urllib.parse import urlsplit

import pydantic

from instro.lib import Instrument
from instro.lib.config import load_config
from instro.lib.publishers import Publisher

from .types import RelativePath, SecurityPolicy

if TYPE_CHECKING:
    from . import IdentityToken, OpcUaClient
else:
    from instro.unstable._opcua import IdentityToken, OpcUaClient


class _ConfigModel(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid", allow_inf_nan=False)


class OpcUaAnonymousAuth(_ConfigModel):
    """Connect without a user identity."""

    mode: Literal["anonymous"] = "anonymous"


class OpcUaUsernameAuth(_ConfigModel):
    """Read a username identity's password from the environment at open time."""

    mode: Literal["username"] = "username"
    username: str = pydantic.Field(min_length=1)
    password_env: str = pydantic.Field(min_length=1)


class OpcUaConnectionConfig(_ConfigModel):
    """Endpoint, user identity, and application security settings."""

    endpoint_url: str
    authentication: OpcUaAnonymousAuth | OpcUaUsernameAuth = pydantic.Field(
        default_factory=OpcUaAnonymousAuth, discriminator="mode"
    )
    security_mode: Literal["None", "Sign", "SignAndEncrypt"] = "None"
    security_policy: str = "None"
    pki_dir: Path | None = None
    certificate_path: Path | None = None
    private_key_path: Path | None = None
    request_timeout_s: float = pydantic.Field(default=60.0, gt=0, strict=True)

    @pydantic.field_validator("endpoint_url")
    @classmethod
    def _validate_endpoint(cls, value: str) -> str:
        endpoint = urlsplit(value)
        if endpoint.scheme != "opc.tcp" or not endpoint.hostname:
            raise ValueError("Expected an opc.tcp endpoint with a hostname")
        if endpoint.port == 0:
            raise ValueError("Endpoint port must be positive")
        return value

    @pydantic.model_validator(mode="after")
    def _validate_security(self) -> Self:
        unsecured = SecurityPolicy(self.security_policy) == SecurityPolicy.NONE
        if unsecured != (self.security_mode == "None"):
            raise ValueError("Use the None security policy and mode together")
        if isinstance(self.authentication, OpcUaUsernameAuth) and self.security_mode != "SignAndEncrypt":
            raise ValueError("Username authentication requires SignAndEncrypt")
        if (self.certificate_path is None) != (self.private_key_path is None):
            raise ValueError("Supply the application certificate and private key together")
        return self


class OpcUaDeviceConfig(_ConfigModel):
    """Connection settings and named browse paths to check on open."""

    connection: OpcUaConnectionConfig
    nodes: dict[str, str] = pydantic.Field(default_factory=dict)

    @pydantic.field_validator("nodes")
    @classmethod
    def _validate_paths(cls, nodes: dict[str, str]) -> dict[str, str]:
        for name, path in nodes.items():
            try:
                elements = RelativePath.parse(path).elements
            except ValueError as exc:
                raise ValueError(f"Invalid browse path for {name!r}: {exc}") from exc
            if not elements:
                raise ValueError(f"Node {name!r} needs a nonempty browse path")
        return nodes


def _connect(config: OpcUaConnectionConfig) -> OpcUaClient:
    auth = config.authentication
    identity = None
    if isinstance(auth, OpcUaUsernameAuth):
        password = getenv(auth.password_env)
        if password is None:
            raise RuntimeError(f"Environment variable {auth.password_env!r} is unset")
        identity = IdentityToken.user_name(auth.username, password)

    return OpcUaClient.connect(
        config.endpoint_url,
        identity=identity,
        security_mode=config.security_mode,
        security_policy=config.security_policy,
        pki_dir=config.pki_dir,
        certificate_path=config.certificate_path,
        private_key_path=config.private_key_path,
        request_timeout=config.request_timeout_s,
    )


class OpcUaDevice(Instrument):
    """Own a configured OPC UA client and validate configured paths on open."""

    def __init__(
        self,
        name: str,
        config: OpcUaDeviceConfig | dict[str, object] | Path | str,
        publishers: list[Publisher] | None = None,
    ) -> None:
        """Validate a config and initialize the device.

        Args:
            name (str): Instrument name.
            config (OpcUaDeviceConfig | dict[str, object] | Path | str): Config model,
                mapping, or JSON file path.
            publishers (list[Publisher] | None): Publishers owned by the instrument.

        Examples:
            >>> with OpcUaDevice("line_1", "line_1.json") as device:
            ...     sample = device.client.get_node("/Objects/2:Setpoint").value
        """
        # A caller can mutate a Pydantic model or its nested fields after validation.
        self._config = load_config(
            config.model_dump() if isinstance(config, OpcUaDeviceConfig) else config, OpcUaDeviceConfig
        )
        self._client: OpcUaClient | None = None
        super().__init__(name, publishers=publishers)

    @property
    def client(self) -> OpcUaClient:
        """Return the client for the open device.

        Raises:
            RuntimeError: The device has not opened or has closed.
        """
        if self._client is None:
            raise RuntimeError("Call open() before accessing the OPC UA client")
        return self._client

    def open(self) -> None:
        """Connect and resolve all configured browse paths in one request."""
        if self._client is not None:
            return

        client = _connect(self._config.connection)
        try:
            client.translate(self._config.nodes.values())
        except BaseException:
            # The native context preserves the binding exception if disconnect also fails.
            with client:
                raise
        self._client = client

    def close(self) -> None:
        """Close the instrument and disconnect its OPC UA client."""
        client, self._client = self._client, None
        with client if client is not None else nullcontext():
            super().close()

    def __enter__(self) -> Self:
        """Open and return this device."""
        self.open()
        return self
