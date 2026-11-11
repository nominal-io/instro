import datetime
import os
import threading
from abc import abstractmethod
from pathlib import Path
from types import TracebackType
from typing import Any, Callable, Concatenate, Literal, ParamSpec, Protocol, TypeVar, final, override

from pydantic import AnyUrl, BaseModel, ConfigDict, Field, PrivateAttr, ValidationError, model_validator

from instro.lib import Instrument, Measurement
from instro.lib.publishers import Publisher

from ._opcua import (
    Node,
    OpcUaClient,
    Session,
    get_endpoints,
)
from .types import DataValue
from .types import RelativePath as _RelativePath

OPCUA_PASSWORD_ENV = "INSTRO_OPCUA_PASSWORD"


class OpcUaBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class OpcUaAuthModel(OpcUaBaseModel):
    @abstractmethod
    def as_auth_params(self) -> Any:
        """Return the authentication parameters as a dictionary."""


class SubscriptionConfig(OpcUaAuthModel):
    """instro-opcua subscription configuration."""

    sampling_rate: float = Field(gt=0)
    """Frequency, in Hz, that the server should poll the underlying data source."""

    publish_rate: float | None = Field(default=None, gt=0)
    """Frequency, in Hz, that the server should publish data to the client. If not specified, `sample_rate` will be used."""

    queue_size: int | None = Field(default=None, ge=0)
    """Maximum number of values to store for each tag between notifications. If not provided, the server's default will be used."""

    background_poll_rate: float | None = Field(default=None, gt=0)
    """Frequency, in Hz, that the client should poll the server for data. See the docs for more details."""

    lifetime_count: int | None = Field(default=None, ge=0, le=2**32 - 1)
    """Maximum number of notifications to send before unsubscribing. If not provided, the server's default will be used."""

    max_keep_alive_count: int | None = Field(default=None, gt=0, le=2**32 - 1)
    """The maximum number of periods that can elapse without any value changes before the a keep-alive message is sent by the server."""

    max_notifications_per_publish: int | None = Field(default=None, gt=0, le=2**16 - 1)
    """The maximum number of notifications that can be sent in a single publish message."""

    priority: int | None = Field(default=None, ge=0, le=255)
    """The priority of the subscription. Can be any integer between 0 and 255, where 255 is the highest priority."""

    discard_oldest: bool = True
    """When `queue_size` > 1, the oldest value will be discarded when the server's queue is full."""


class OpcUaBasicAuthConfig(OpcUaAuthModel):
    """OPC UA username/password connection configuration."""

    mode: Literal["basic"] = "basic"

    username: str
    """Username to use for the connection."""

    password_env: str = Field(default=OPCUA_PASSWORD_ENV)
    """
    Environment variable containing the password to use for the connection.
    Defaults to `INSTRO_OPCUA_PASSWORD`.
    """

    _password: str | None = PrivateAttr(None)

    @model_validator(mode="after")
    def _validate(self) -> "OpcUaBasicAuthConfig":
        if (pw_env := os.getenv(self.password_env)) is None:
            raise ValidationError(f"Environment variable '{self.password_env}' is not set")

        self._password = pw_env

        return self

    @property
    def password(self) -> str:
        return self._password  # type: ignore[return-value]

    @override
    def as_auth_params(self) -> Any:
        return {
            "username": self.username,
            "password": self._password,
        }


class OpcUaAnonymousConfig(OpcUaAuthModel):
    """OPC UA anonymous connection configuration."""

    mode: Literal["anonymous"] = "anonymous"

    @override
    def as_auth_params(self) -> Any:
        return {}


class OpcUaConnectionConfig(OpcUaBaseModel):
    """OPC UA connection configuration."""

    endpoint_url: AnyUrl
    """Server endpoint URL to connect to."""

    timeout: float | datetime.timedelta | None = None
    """Timeout for the connection."""

    security_mode: Literal["none", "sign", "signandencrypt"]
    """
    Security mode to use for the connection.

    Must be one of: `none`, `sign`, `signandencrypt`, `encrypt`, `max`.
    """

    security_policy: Literal[
        "none", "basic128rsa15", "basic256", "basic256sha256", "aes128sha256rsaoaep", "aes256sha256rsapss"
    ]
    """
    The encryption policy to use for the connection.

    Must be one of: `none`, `basic128rsa15`, `basic256`, `basic256sha256`, `basic128sha256`, `none`.
    """

    unsafe_allow_unencrypted_connections: bool = True
    """
    Whether it should be allowed to connect to an unencrypted endpoint with anything other than anonymous auth.

    If `False`, connections to unencrypted endpoints (i.e. `security_mode` is `none` or `sign`)
    with username/password or ceritificate authentication will be allowed.

    This permits configurations where potentially insecure connections are allowed to carry
    sensitive information (i.e. passwords, private keys, etc.) in plaintext.

    Use with caution.
    """

    authentication: OpcUaBasicAuthConfig | OpcUaAnonymousConfig = Field(discriminator="mode")
    """Authentication configuration."""

    use_generated_certificate: bool = False
    """Whether to generate a self-signed certificate for the connection."""

    unsafe_ignore_server_cert: bool = False
    """Whether to ignore the server certificate for the connection."""

    @model_validator(mode="after")
    def _validate_endpoint_url(self) -> "OpcUaConnectionConfig":
        if self.endpoint_url.scheme not in ["opc.tcp", "opc.https"]:
            raise ValidationError(f"Invalid endpoint URL scheme: {self.endpoint_url.scheme}")

        return self

    @model_validator(mode="after")
    def _validate_security_policy(self) -> "OpcUaConnectionConfig":
        if self.security_policy == "none" and self.security_mode != "none":
            raise ValidationError("Security policy must be 'none' when security mode is 'none'")

        return self

    @model_validator(mode="after")
    def _validate_non_anon_auth_is_encrypted(self) -> "OpcUaConnectionConfig":
        if (
            not self.unsafe_allow_unencrypted_connections
            and self.authentication.mode != "anonymous"
            and (self.security_mode != "signandencrypt" or self.security_policy == "none")
        ):
            raise ValidationError("Non-anonymous authentication requires encryption")

        return self

    def as_connection_params(self) -> Any:
        return {
            "endpoint_url": self.endpoint_url.unicode_string(),
            "timeout": self.timeout,
            "security_mode": self.security_mode,
            "security_policy": self.security_policy,
            "use_generated_cert": self.use_generated_certificate,
            "ignore_server_cert": self.unsafe_ignore_server_cert,
            **self.authentication.as_auth_params(),
        }


class OpcUaConfig(OpcUaBaseModel):
    """OPC UA device connection configuration."""

    version: float = 1.0
    """instro-opcua configuration schema version."""

    protocol: str = "opcua"
    """Instro protocol name."""

    node_paths: list[str]
    """The nodes to sample from. Must be a fully-qualified OPC UA browse path."""

    connection: OpcUaConnectionConfig
    """OPC UA connection configuration."""

    sampling_strategy: SubscriptionConfig
    """Subscription configuration."""

    _paths: list[_RelativePath] = PrivateAttr(default_factory=list)

    @model_validator(mode="after")
    def _validate(self) -> "OpcUaConfig":
        try:
            self._paths = [_RelativePath.parse(path) for path in self.node_paths]

        except Exception as e:
            raise ValidationError(f"One or more paths were invalid: {e}") from e

        return self


ConfigLike = OpcUaConfig | Path | str


class Lockable(Protocol):
    _lock: threading.RLock


_L = TypeVar("_L", bound=Lockable)
_R = TypeVar("_R")
_P = ParamSpec("_P")


def interlocked(fn: Callable[Concatenate[_L, _P], _R]) -> Callable[Concatenate[_L, _P], _R]:
    """Decorator to lock the instance before calling the method."""

    def locked_wrapper(self: _L, *args: _P.args, **kwargs: _P.kwargs) -> _R:
        with self._lock:
            return fn(self, *args, **kwargs)

    return locked_wrapper


@final
class OpcUaDevice(Instrument):
    def __init__(
        self,
        name: str,
        config: ConfigLike,
        publishers: list[Publisher] | None = None,
        **kwargs: Any,
    ) -> None:
        self._lock = threading.RLock()
        self._config = (cfg := self._resolve_config(config))
        self._client: OpcUaClient | None = OpcUaClient.connect(**cfg.connection.as_connection_params())
        self._nodes = {path: self._client.get_node(path) for path in cfg.node_paths}
        self._session: Session | None = None

        super().__init__(name, publishers, **kwargs)

    def _resolve_config(self, maybe_config: ConfigLike) -> OpcUaConfig:
        match maybe_config:
            case OpcUaConfig() as config:
                return config

            case Path() as path:
                return OpcUaConfig.model_validate_json(path.read_text())

            case str(path_str):
                return OpcUaConfig.model_validate_json(Path(path_str).read_text())

            case _:
                raise ValueError(f"Invalid config type: {type(maybe_config)}")

    @property
    def config(self) -> OpcUaConfig:
        return self._config

    @property
    def client(self) -> OpcUaClient:
        return self._require_client()

    @interlocked
    def _require_client(self) -> OpcUaClient:
        if self._client is None:
            raise RuntimeError("must call `open()` before accessing the client")

        return self._client

    def _publish_batch(self, samples: list[tuple[Node, DataValue]]) -> None:
        for node, data in samples:
            if (ts := (data.source_timestamp or data.server_timestamp)) is None:
                raise ValueError("source or server timestamp is required")
            elif not isinstance((value := data.value), (bool, int, float, str)):
                raise ValueError("value must be a boolean, integer, float, or string")

            self.publish(
                Measurement(
                    channel_data={
                        str(node.browse_path): [float(value) if isinstance(value, (int, float, bool)) else value]  # type: ignore[dict-item]
                    },
                    timestamps=[ts],
                )
            )

    @interlocked
    def start(self) -> None:
        self._session = self.client.subscribe(
            on_data=self._publish_batch,
            nodes=list(self._nodes.values()),
            freq_hz=self.config.sampling_strategy.sampling_rate,
        )

    @interlocked
    def stop(self) -> None:
        self._session = None

    @interlocked
    def __enter__(self) -> "OpcUaDevice":
        """Enter the context manager, effectively a noop."""
        return self

    @interlocked
    def __exit__(
        self, _exc_type: type[BaseException] | None, _exc_value: BaseException | None, _traceback: TracebackType | None
    ) -> None:
        """Disconnects from the OPC UA server."""
        self.client.disconnect()
        self._client = None
        self._session = None
