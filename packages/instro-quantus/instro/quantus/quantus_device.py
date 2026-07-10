"""Config-driven Mecalc QuantusSeries device."""

import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from instro.quantus._quantus import QuantusClient, StreamReader

from instro.lib import Instrument, InstrumentNotOpenError, Measurement
from instro.lib.instrument import publish_measurement
from instro.lib.publishers import Publisher

logger = logging.getLogger(__name__)


class QuantusDevice(Instrument):
    """Mecalc QuantusSeries mainframe: declarative rack config, streamed data.

    Wraps the Rust-backed ``quantus`` wheel. ``open()`` connects and asserts a
    Q2.x QServer; ``reconcile()`` writes every declared setting and applies
    once; ``start()`` connects the binary stream and publishes Measurements.
    """

    def __init__(
        self,
        config: dict | str | Path,
        connection: dict | None = None,
        name: str | None = None,
        publishers: list[Publisher] | None = None,
        dbc: dict[str, str] | None = None,
        autostart: bool = False,
        **kwargs,
    ):
        """Initialize a QuantusDevice.

        Args:
            config: Rack config as a dict, a path to a .json/.toml file, or
                inline JSON text (see the quantus repo's fixtures/rack/).
            connection: Overrides the config's ``connection`` section (merged
                key-by-key, e.g. ``{"host": "10.0.0.202"}``). Required if the
                config has no ``connection`` section.
            name: Channel-name prefix; falls back to ``config.device.name``,
                then ``"quantus"``.
            publishers: Publishers that receive emitted Measurement data.
            dbc: Optional map of CAN channel alias -> DBC file path; frames on
                those channels are decoded to per-signal channels (requires
                the ``can`` extra / cantools).
            autostart: When True, open the connection, reconcile the rack, and
                start background streaming.
            **kwargs: Default tags applied to every emitted Measurement.

        Raises:
            ValueError: No connection (with a host) in the config or ``connection`` argument.
        """
        resolved = self._load_config(config)
        if connection is not None:
            resolved["connection"] = {**resolved.get("connection", {}), **connection}
        if not resolved.get("connection", {}).get("host"):
            raise ValueError(
                "No connection configuration provided. Either include a 'connection' section "
                "in the config or pass a 'connection' argument to QuantusDevice()."
            )
        instrument_name = name or resolved.get("device", {}).get("name") or "quantus"
        super().__init__(instrument_name, publishers=publishers, **kwargs)
        self._config_text = json.dumps(resolved)
        self._dbc_paths = dict(dbc or {})
        self._dbc_databases: dict[str, Any] = {}
        self._client: QuantusClient | None = None
        self._reader: StreamReader | None = None
        self._report: dict | None = None
        self._alias_by_item: dict[int, str] = {}
        self._rate_by_item: dict[int, float | None] = {}
        self._item_by_alias: dict[str, int] = {}
        self._last_tacho_ms: dict[int, float] = {}
        self._unknown_can_counts: dict[str, int] = {}
        self._epoch_anchor_ns: int | None = None
        self._is_open = False
        # The blocking stream read paces the daemon loop.
        self._background_config.interval = 0

        if autostart:
            self.open()
            self.start()

    @staticmethod
    def _load_config(config: dict | str | Path) -> dict:
        """Normalize any accepted config shape to a dict (for overrides/naming)."""
        if isinstance(config, dict):
            return json.loads(json.dumps(config))
        text = str(config)
        if text.lstrip().startswith("{"):
            return json.loads(text)
        path = Path(text)
        raw = path.read_text()
        if path.suffix.lower() == ".json":
            return json.loads(raw)
        if path.suffix.lower() == ".toml":
            try:
                import tomllib
            except ImportError as exc:
                raise ValueError("TOML rack configs require Python >= 3.11; use a JSON config instead.") from exc
            return tomllib.loads(raw)
        raise ValueError(f"Unsupported config extension for {path}; use .json or .toml.")

    def _require_open(self) -> None:
        if not self._is_open:
            raise InstrumentNotOpenError(f"QuantusDevice '{self.name}' is not open. Call open() first.")

    def _require_client(self) -> "QuantusClient":
        self._require_open()
        if self._client is None:
            raise InstrumentNotOpenError(f"QuantusDevice '{self.name}' is not open. Call open() first.")
        return self._client

    def open(self):
        """Connect to the device (ping + Q2.x version check). Writes nothing."""
        # Deferred: the private PyO3 module is loaded at first use, like
        # instro.ethernetip._ethernetip.
        import instro.quantus._quantus as _quantus

        logger.info("Opening QuantusDevice '%s'", self.name)
        self._client = _quantus.QuantusClient(self._config_text)
        for alias, path in self._dbc_paths.items():
            self._dbc_databases[alias] = self._load_dbc(path)
        self._is_open = True

    @staticmethod
    def _load_dbc(path: str) -> Any:
        try:
            import cantools  # type: ignore[import-not-found,import-untyped]
        except ImportError as exc:
            raise RuntimeError(
                "CAN decoding requires cantools. Install with `pip install 'instro-quantus[can]'`."
            ) from exc
        return cantools.database.load_file(path)

    def reconcile(self) -> dict:
        """Write every declared setting, apply once, and return the report."""
        report = self._require_client().reconcile()
        self._report = report
        self._alias_by_item = {c["item_id"]: c["alias"] for c in report["channels"]}
        self._rate_by_item = {c["item_id"]: c["sample_rate_hz"] for c in report["channels"]}
        self._item_by_alias = {c["alias"]: c["item_id"] for c in report["channels"]}
        if report["restart_required"]:
            logger.info("QuantusDevice '%s': settings applied, streaming epoch restarts", self.name)
        for module in report["modules"]:
            if module["requested_hz"] and module["achieved_hz"] != module["requested_hz"]:
                logger.warning(
                    "QuantusDevice '%s': module %s snapped %s Hz -> %s Hz",
                    self.name,
                    module["name"],
                    module["requested_hz"],
                    module["achieved_hz"],
                )
        return report

    def start(self, background: bool = True):
        """Connect the data stream; with ``background`` spin the publish daemon."""
        client = self._require_client()
        if self._report is None:
            self.reconcile()
        self._reader = client.open_stream()
        self._epoch_anchor_ns = None
        if background:
            already = any(m == self._pump for m, _, _ in self._background_methods)
            if not already:
                self.add_background_daemon_function(self._pump)
            super().start()

    def stop(self, **kwargs):
        """Stop the publish daemon and close the stream connection."""
        super().stop()
        if self._reader is not None:
            self._reader.close()
            self._reader = None

    def close(self):
        """Full teardown: daemon, publishers, stream."""
        logger.info("Closing QuantusDevice '%s'", self.name)
        super().close()
        if self._reader is not None:
            self._reader.close()
            self._reader = None
        self._client = None
        self._is_open = False

    # ========  Streaming -> Measurements  ========

    @publish_measurement
    def _pump(self) -> Measurement | list[Measurement] | None:
        """Pull one stream event and convert it to Measurement(s)."""
        if self._reader is None:
            return None
        event = self._reader.next_event(timeout_ms=1000)
        if event is None:
            return None
        kind = event["type"]
        if kind == "analog":
            return self._analog_measurement(event)
        if kind == "tacho":
            return self._tacho_measurement(event)
        if kind == "can":
            return self._can_measurements(event)
        if kind == "epoch_restart":
            logger.warning("QuantusDevice '%s': streaming epoch restarted", self.name)
            self._epoch_anchor_ns = None
            self._last_tacho_ms.clear()
            return None
        if kind == "gap":
            logger.warning("QuantusDevice '%s': server discarded %d packets", self.name, event["missing"])
            return self._package_measurement("stream.missing_packets", event["missing"], time.time_ns())
        if kind == "disconnected":
            logger.error("QuantusDevice '%s': stream disconnected: %s", self.name, event["reason"])
            self._background_stop_event.set()
            return None
        return None

    def _anchor(self, epoch_relative_ns: int) -> int:
        """Wall-clock anchor for epoch-relative stream timestamps."""
        if self._epoch_anchor_ns is None:
            self._epoch_anchor_ns = time.time_ns() - epoch_relative_ns
        return self._epoch_anchor_ns

    def _alias(self, item_id: int) -> str:
        return self._alias_by_item.get(item_id, str(item_id))

    def _analog_measurement(self, event: dict) -> Measurement | None:
        samples = event["samples"]
        if len(samples) == 0:
            return None
        anchor = self._anchor(event["timestamp_ns"])
        rate = self._rate_by_item.get(event["channel_id"])
        dt_ns = round(1e9 / rate) if rate else 0
        t0 = anchor + event["timestamp_ns"]
        timestamps = [t0 + i * dt_ns for i in range(len(samples))]
        alias = self._alias(event["channel_id"])
        return Measurement(
            channel_data={f"{self.name}.{alias}": [float(s) for s in samples]},
            timestamps=timestamps,
            tags=dict(self.default_tags),
        )

    def _tacho_measurement(self, event: dict) -> Measurement | None:
        """Publish RPM computed from successive edge intervals (1 pulse/rev)."""
        events_ms = list(event["events_ms"])
        if not events_ms:
            return None
        channel_id = event["channel_id"]
        anchor = self._anchor(int(events_ms[0] * 1e6))
        previous = self._last_tacho_ms.get(channel_id)
        rpms, timestamps = [], []
        for edge_ms in events_ms:
            if previous is not None and edge_ms > previous:
                rpms.append(60_000.0 / (edge_ms - previous))
                timestamps.append(anchor + int(edge_ms * 1e6))
            previous = edge_ms
        self._last_tacho_ms[channel_id] = float(events_ms[-1])
        if not rpms:
            return None
        alias = self._alias(channel_id)
        return Measurement(
            channel_data={f"{self.name}.{alias}": rpms},
            timestamps=timestamps,
            tags=dict(self.default_tags),
        )

    def _can_measurements(self, event: dict) -> list[Measurement] | None:
        alias = self._alias(event["channel_id"])
        database = self._dbc_databases.get(alias)
        measurements: list[Measurement] = []
        unknown = 0
        for frame in event["frames"]:
            anchor = self._anchor(int(frame["timestamp_s"] * 1e9))
            timestamp = anchor + int(frame["timestamp_s"] * 1e9)
            if database is None:
                unknown += 1
                continue
            try:
                message = database.get_message_by_frame_id(frame["id"])
                signals = message.decode(bytes(frame["data"]))
            except (KeyError, ValueError):
                unknown += 1
                continue
            channel_data = {
                f"{self.name}.{alias}.{signal}": [float(value)]
                for signal, value in signals.items()
                if isinstance(value, (int, float))
            }
            if channel_data:
                measurements.append(
                    Measurement(
                        channel_data=channel_data,
                        timestamps=[timestamp],
                        tags=dict(self.default_tags),
                    )
                )
        if unknown:
            self._unknown_can_counts[alias] = self._unknown_can_counts.get(alias, 0) + unknown
            measurements.append(
                self._package_measurement(f"{alias}.unknown_frames", self._unknown_can_counts[alias], time.time_ns())
            )
        return measurements or None

    # ========  Runtime writes (quantus repo PLAN.md D12)  ========

    def _item_id(self, channel: str) -> int:
        self._require_open()
        if self._report is None:
            raise RuntimeError("Call reconcile() before addressing channels by alias.")
        if (item_id := self._item_by_alias.get(channel)) is None:
            raise KeyError(
                f"Channel '{channel}' is not configured. Configured channels: {sorted(self._item_by_alias)}."
            )
        return item_id

    def write_settings(self, channel: str, values: dict[str, str | float]) -> bool:
        """Settings-plane write: set values on ``channel`` (alias) and apply.

        Returns True when the streaming epoch restarts (expect a data gap).
        """
        return self._require_client().write_settings(self._item_id(channel), values)

    def auto_zero(self, channel: str | None = None):
        """Auto-zero one channel (alias) or the whole system."""
        self._require_client().auto_zero(self._item_id(channel) if channel else None)

    def bridge_balance(self, channel: str | None = None):
        """Balance WSB bridges on one channel (alias) or system-wide."""
        self._require_client().bridge_balance(self._item_id(channel) if channel else None)

    def can_transmit(self, channel: str, messages: list[dict]):
        """Cache ``messages`` on CAN ``channel`` (alias) and transmit."""
        client = self._require_client()
        item_id = self._item_id(channel)
        client.put_can_message_list(item_id, {"MessageList": messages})
        client.can_transmit(item_id)
