"""Unstable config-driven EtherNet/IP device."""

from __future__ import annotations

import functools
import logging
import threading
import time
import warnings
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from instro.lib import Command, Instrument, Measurement
from instro.lib.publishers import Publisher
from instro.unstable.ethernetip.ethernetip_types import (
    BOOL_DATA_TYPES,
    INTEGER_DATA_TYPES,
    EtherNetIPConfig,
    EtherNetIPConnectionInfo,
    TagDef,
)

logger = logging.getLogger(__name__)


def _load_native_ethernetip() -> SimpleNamespace:
    try:
        from instro.unstable._ethernetip import EtherNetIpSession, PlcKind, PlcValue
    except ImportError as exc:
        raise RuntimeError(
            "EtherNet/IP support requires the native package 'instro-ethernetip'. "
            'Install it with `pip install "instro-unstable[ethernetip]"` or '
            '`uv add "instro-unstable[ethernetip]"`.'
        ) from exc

    return SimpleNamespace(
        EtherNetIpSession=EtherNetIpSession,
        PlcKind=PlcKind,
        PlcValue=PlcValue,
    )


def _eip_op(fn):
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            if self._background_stop_event.is_set():
                raise RuntimeError("Instrument is shutting down.")
            if self._client is None:
                raise RuntimeError("EtherNet/IP client not connected. Call open() first.")
            return fn(self, *args, **kwargs)

    return wrapper


class EtherNetIPDevice(Instrument):
    """Unstable EtherNet/IP device with config-driven tag access."""

    def __init__(
        self,
        config: EtherNetIPConfig | dict | Path | str,
        connection: EtherNetIPConnectionInfo | dict | None = None,
        name: str | None = None,
        publishers: list[Publisher] | None = None,
        autostart: bool = False,
        **kwargs,
    ):
        if isinstance(config, EtherNetIPConfig):
            resolved_config = config
        elif isinstance(config, dict):
            resolved_config = EtherNetIPConfig(**config)
        else:
            resolved_config = EtherNetIPConfig.from_json(config)

        if connection is not None:
            resolved_connection_info = (
                EtherNetIPConnectionInfo(**connection) if isinstance(connection, dict) else connection
            )
        elif resolved_config.connection is not None:
            resolved_connection_info = resolved_config.connection
        else:
            raise ValueError(
                "No connection configuration provided. Either include a 'connection' section "
                "in the config or pass a 'connection' argument to EtherNetIPDevice()."
            )

        instrument_name = name or resolved_config.device.name
        super().__init__(name=instrument_name, publishers=publishers, **kwargs)

        self._config = resolved_config
        self._connection_info = resolved_connection_info
        self._client: Any | None = None
        self._native: SimpleNamespace | None = None
        self._lock = threading.RLock()

        self._define_background_daemon()

        if self._config.timing is not None:
            self.background_interval = self._config.timing.poll_interval
            if not autostart and not self._config.polled_tags:
                warnings.warn(
                    self._no_pollable_tags_message(),
                    RuntimeWarning,
                    stacklevel=2,
                )

        if autostart:
            if self._config.timing is None:
                raise ValueError(
                    "autostart=True requires a 'timing' section in the config (with poll_interval). "
                    "Without polling configured, autostart has no effect; call open() manually instead."
                )
            if not self._config.polled_tags:
                raise ValueError(self._no_pollable_tags_message())
            self.open()
            self.start()

    def _define_background_daemon(self) -> None:
        self._polled_tags = self._config.polled_tags
        if self._polled_tags:
            self.add_background_daemon_function(self._poll_batched_tags)

    def _no_pollable_tags_message(self) -> str:
        if not self._config.tags:
            return "No EtherNet/IP tags are configured; background polling requires at least one tag with poll=true."
        return (
            f"EtherNet/IP background polling requested, but all {len(self._config.tags)} configured tags "
            "have poll=false."
        )

    def _validate_polling_config_for_start(self) -> None:
        if not self._config.polled_tags:
            raise RuntimeError(self._no_pollable_tags_message())

    def start(self) -> None:
        """Start background polling for poll-enabled EtherNet/IP tags."""
        self._validate_polling_config_for_start()
        super().start()

    @property
    def address(self) -> str:
        """EtherNet/IP endpoint address."""
        return self._connection_info.address

    def open(self) -> None:
        """Open the EtherNet/IP session."""
        with self._lock:
            self._background_stop_event.clear()
            if self._client is not None:
                return

            native = _load_native_ethernetip()
            route_path_slots = None
            if self._connection_info.route_path is not None and self._connection_info.route_path.hops:
                route_path_slots = [hop.slot for hop in self._connection_info.route_path.hops]

            if route_path_slots is None:
                self._client = native.EtherNetIpSession(self._connection_info.address)
            else:
                self._client = native.EtherNetIpSession(self._connection_info.address, route_path_slots)
            self._native = native

    def close(self) -> None:
        """Close the EtherNet/IP session and stop background polling."""
        self._background_stop_event.set()
        super().close()
        with self._lock:
            if self._client is not None:
                try:
                    self._client.close()
                except Exception as exc:
                    logger.warning("Failed to close EtherNet/IP session cleanly: %s", exc)
                self._client = None

    def read_tag(self, alias: str, **kwargs) -> Measurement:
        """Read one configured tag by alias and publish the result."""
        tag = self._config.get_tag(alias)
        raw_value = self._read_tag_raw(tag.tag_name)
        timestamp = time.time_ns()
        value = self._decode_plc_value(raw_value, tag)
        return self._publish_measurement(
            {f"{self.name}.{tag.alias}": [value]},
            timestamp,
            **kwargs,
        )

    def read(self, alias: str, **kwargs) -> Measurement:
        """Read one configured tag by alias and publish the result."""
        return self.read_tag(alias, **kwargs)

    def _poll_batched_tags(self, **kwargs) -> Measurement | None:
        """Read every polled tag in one batched request."""
        if not self._polled_tags:
            return None

        tag_by_name = {tag.tag_name: tag for tag in self._polled_tags}
        results = self._read_tags_raw([tag.tag_name for tag in self._polled_tags])
        timestamp = time.time_ns()

        channel_data: dict[str, list[Any]] = {}
        for name, result in results:
            tag = tag_by_name[name]
            if isinstance(result, Exception):
                logger.warning("Failed to read EtherNet/IP tag %r: %s", tag.alias, result)
                continue
            try:
                value = self._decode_plc_value(result, tag)
            except (TypeError, ValueError) as exc:
                logger.warning("Failed to decode EtherNet/IP tag %r: %s", tag.alias, exc)
                continue
            channel_data[f"{self.name}.{tag.alias}"] = [value]

        if not channel_data:
            return None

        return self._publish_measurement(channel_data, timestamp, **kwargs)

    def write_tag(self, alias: str, value: bool | int | float, **kwargs) -> Command:
        """Write one configured tag by alias and publish the command."""
        tag = self._config.get_tag(alias)

        tag.validate_write_value(value)
        plc_value = self._build_plc_value(value, tag)

        self._write_tag_raw(tag.tag_name, plc_value)
        timestamp = time.time_ns()

        command = Command(
            channel_data={f"{self.name}.{tag.alias}.cmd": value},
            timestamp=timestamp,
            tags={**self.default_tags, **(kwargs or {})},
        )
        self.publish(command)
        return command

    def write(self, alias: str, value: bool | int | float, **kwargs) -> Command:
        """Write one configured tag by alias and publish the command."""
        return self.write_tag(alias, value, **kwargs)

    @_eip_op
    def _read_tag_raw(self, tag_name: str) -> Any:
        assert self._client is not None
        return self._client.read_tag(tag_name)

    @_eip_op
    def _read_tags_raw(self, tag_names: list[str]) -> list[tuple[str, Any]]:
        assert self._client is not None
        return self._client.read_tags(tag_names)

    @_eip_op
    def _write_tag_raw(self, tag_name: str, value: Any) -> None:
        assert self._client is not None
        self._client.write_tag(tag_name, value)

    def _decode_plc_value(self, plc_value: Any, tag: TagDef) -> Any:
        self._validate_read_kind(plc_value, tag)
        tag.validate_streamable_read(plc_value.kind)
        value = plc_value.value

        if tag.data_type in BOOL_DATA_TYPES:
            if not isinstance(value, bool):
                raise TypeError(f"Tag '{tag.alias}' expected PLC BOOL value but read {type(value).__name__}.")
            return int(value)

        return value

    def _validate_read_kind(self, plc_value: Any, tag: TagDef) -> None:
        expected_kind_name = tag.expected_plc_kind_name
        native = self._require_native()
        expected_kind = getattr(native.PlcKind, expected_kind_name)
        if plc_value.kind != expected_kind:
            raise TypeError(f"Tag '{tag.alias}' expected PLC kind {expected_kind_name} but read {plc_value.kind!r}.")

    def _build_plc_value(self, raw_value: bool | int | float, tag: TagDef) -> Any:
        native = self._require_native()
        data_type = tag.data_type

        if data_type in BOOL_DATA_TYPES:
            return native.PlcValue.bool(bool(raw_value))

        if data_type in INTEGER_DATA_TYPES:
            return getattr(native.PlcValue, data_type)(cast(int, raw_value))

        if data_type == "real":
            return native.PlcValue.real(float(cast(int | float, raw_value)))

        if data_type == "lreal":
            return native.PlcValue.lreal(float(cast(int | float, raw_value)))

        raise ValueError(f"Unsupported EtherNet/IP data_type '{data_type}' for tag '{tag.alias}'.")

    def _publish_measurement(
        self,
        channel_data: dict[str, list[Any]],
        timestamp: int,
        **kwargs,
    ) -> Measurement:
        measurement = Measurement(
            channel_data=channel_data,
            timestamps=[timestamp],
            tags={**self.default_tags, **(kwargs or {})},
        )
        self.publish(measurement)
        return measurement

    def _require_native(self) -> SimpleNamespace:
        if self._native is None:
            raise RuntimeError("EtherNet/IP native module has not been loaded. Call open() first.")
        return self._native
