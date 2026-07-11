"""Config-driven Mecalc QuantusSeries device."""

import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from instro.quantus._quantus import QuantusClient, StreamReader

from instro.lib import Command, Instrument, InstrumentNotOpenError, Measurement
from instro.lib.instrument import publish_command, publish_measurement
from instro.lib.publishers import Publisher

logger = logging.getLogger(__name__)


def _setting_key(setting_name: str) -> str:
    """QServer setting name -> command channel segment ("Voltage Range" -> "voltage_range")."""
    return setting_name.lower().replace(" ", "_")


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
        autostart: bool = False,
        **kwargs,
    ):
        """Initialize a QuantusDevice.

        Args:
            config: Rack config as a dict, a path to a .json/.toml file, or
                inline JSON text (see ``examples/quantus/``). Top-level shape
                matches the Modbus/EtherNet-IP configs: ``version`` /
                ``protocol`` / ``device`` / ``connection`` + rack payload.
                CAN channels with a ``dbc`` entry are decoded natively to
                per-signal channels (``{name}.{alias}.{signal}``).
            connection: Overrides the config's ``connection`` section (merged
                key-by-key, e.g. ``{"host": "10.0.0.202"}``). Required if the
                config has no ``connection`` section.
            name: Channel-name prefix; falls back to ``config.device.name``,
                then ``"quantus"``.
            publishers: Publishers that receive emitted Measurement data.
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
        for module in resolved.get("modules", []):
            if "CAN" not in module.get("name", "").upper():
                continue
            for channel in module.get("channels", []):
                if channel.get("streaming") and not channel.get("dbc"):
                    alias = channel.get("alias", f"channel {channel.get('index')}")
                    logger.warning(
                        "QuantusDevice '%s': CAN channel '%s' streams without a dbc entry; its raw "
                        "frames are counted on %s.%s.unknown_frames and NOT published (use "
                        "QuantusClient/StreamReader for raw capture)",
                        instrument_name,
                        alias,
                        instrument_name,
                        alias,
                    )
        self._config_text = json.dumps(resolved)
        self._client: QuantusClient | None = None
        self._reader: StreamReader | None = None
        self._report: dict | None = None
        self._alias_by_item: dict[int, str] = {}
        self._rate_by_item: dict[int, float | None] = {}
        self._item_by_alias: dict[str, int] = {}
        self._ppr_by_item: dict[int, float] = {}
        self._last_tacho_ms: dict[int, float] = {}
        self._epoch_anchor_ns: int | None = None
        self._max_published_ns = 0
        self._warned_unconfigured: set[int] = set()
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
            parsed = json.loads(raw)
        elif path.suffix.lower() == ".toml":
            try:
                import tomllib
            except ImportError as exc:
                raise ValueError("TOML rack configs require Python >= 3.11; use a JSON config instead.") from exc
            parsed = tomllib.loads(raw)
        else:
            raise ValueError(f"Unsupported config extension for {path}; use .json or .toml.")
        return QuantusDevice._resolve_dbc_paths(parsed, path.parent)

    @staticmethod
    def _resolve_dbc_paths(config: dict, base: Path) -> dict:
        """Resolve relative per-channel ``dbc`` paths against the config file's directory."""
        for module in config.get("modules", []):
            for channel in module.get("channels", []):
                dbc = channel.get("dbc")
                if dbc and not Path(dbc).is_absolute():
                    channel["dbc"] = str(base / dbc)
        return config

    def _require_open(self) -> None:
        if not self._is_open:
            raise InstrumentNotOpenError(f"QuantusDevice '{self.name}' is not open. Call open() first.")

    def _require_client(self) -> "QuantusClient":
        self._require_open()
        if self._client is None:
            raise InstrumentNotOpenError(f"QuantusDevice '{self.name}' is not open. Call open() first.")
        return self._client

    def open(self):
        """Connect to the device (ping + Q2.x version check). Writes nothing.

        No-op when already open (so ``with QuantusDevice(..., autostart=True)``
        does not build a second client mid-stream).
        """
        if self._is_open:
            return
        # Deferred: the private PyO3 module is loaded at first use, like
        # instro.ethernetip._ethernetip.
        import instro.quantus._quantus as _quantus

        logger.info("Opening QuantusDevice '%s'", self.name)
        self._client = _quantus.QuantusClient(self._config_text)
        self._is_open = True

    @property
    def report(self) -> dict | None:
        """The latest reconcile report (achieved rates, channel map); None before reconcile()."""
        return self._report

    def reconcile(self) -> dict:
        """Write every declared setting, apply once, and return the report."""
        report = self._require_client().reconcile()
        self._report = report
        self._alias_by_item = {c["item_id"]: c["alias"] for c in report["channels"]}
        self._rate_by_item = {c["item_id"]: c["sample_rate_hz"] for c in report["channels"]}
        self._item_by_alias = {c["alias"]: c["item_id"] for c in report["channels"]}
        self._ppr_by_item = {c["item_id"]: c.get("pulses_per_rev", 1.0) for c in report["channels"]}
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
        """Connect the data stream; with ``background`` spin the publish daemon.

        With ``background=False`` the stream is connected but nothing consumes
        it: the caller MUST drain via ``read_event()`` continuously, or QServer
        discards data once its buffer passes 45% (visible as gap events).
        """
        client = self._require_client()
        if self._background_thread and self._background_thread.is_alive():
            logger.info("QuantusDevice '%s' is already streaming; start() is a no-op", self.name)
            return
        if self._report is None:
            self.reconcile()
        if self._reader is not None:
            # Re-start: release the old connection first (the device allows a
            # single streaming client).
            self._reader.close()
        self._reader = client.open_stream()
        self._epoch_anchor_ns = None
        self._last_tacho_ms.clear()
        if background:
            already = any(m == self._pump for m, _, _ in self._background_methods)
            if not already:
                self.add_background_daemon_function(self._pump)
            super().start()

    def read_event(self, timeout_ms: int = 1000) -> dict | None:
        """Pull one raw stream event (foreground use with ``start(background=False)``).

        Returns None on timeout. Raises when the background daemon owns the
        stream or the stream is not connected.
        """
        if self._background_thread and self._background_thread.is_alive():
            raise RuntimeError("The background daemon is consuming the stream; read_event() is foreground-only.")
        if self._reader is None:
            raise InstrumentNotOpenError(f"QuantusDevice '{self.name}' has no open stream. Call start() first.")
        return self._reader.next_event(timeout_ms=timeout_ms)

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
        """Drain a batch of stream events and convert them to Measurements.

        One blocking read paces the loop; whatever else is already queued is
        drained non-blocking so the daemon's per-iteration overhead amortizes
        over many events at high packet rates.
        """
        if self._reader is None:
            return None
        measurements: list[Measurement] = []
        event = self._reader.next_event(timeout_ms=1000)
        drained = 0
        while event is not None:
            result = self._event_measurements(event)
            if isinstance(result, list):
                measurements.extend(result)
            elif result is not None:
                measurements.append(result)
            drained += 1
            if self._reader is None or drained >= 64:
                break
            event = self._reader.next_event(timeout_ms=0)
        return measurements or None

    def _event_measurements(self, event: dict) -> Measurement | list[Measurement] | None:
        kind = event["type"]
        if kind in ("analog", "tacho", "can") and event["channel_id"] not in self._alias_by_item:
            # Not a channel this config declared (e.g. enabled mid-session by
            # another client): a silent drop would hide it, publishing it
            # would emit unidentifiable series — count it loudly instead.
            if event["channel_id"] not in self._warned_unconfigured:
                self._warned_unconfigured.add(event["channel_id"])
                logger.warning(
                    "QuantusDevice '%s': dropping data from unconfigured channel item %d "
                    "(counted on %s.stream.unconfigured_events)",
                    self.name,
                    event["channel_id"],
                    self.name,
                )
            return self._package_measurement("stream.unconfigured_events", 1.0, event["received_ns"])
        if kind == "analog":
            return self._analog_measurement(event)
        if kind == "tacho":
            return self._tacho_measurement(event)
        if kind == "can":
            return self._can_measurements(event)
        if kind == "epoch_restart":
            logger.warning("QuantusDevice '%s': streaming epoch restarted", self.name)
            # The restart packet's receive time anchors the new epoch (its
            # stream-relative time is zero by definition).
            self._epoch_anchor_ns = None
            self._anchor(0, event["received_ns"])
            self._last_tacho_ms.clear()
            return None
        if kind == "gap":
            logger.warning("QuantusDevice '%s': server discarded %d packets", self.name, event["missing"])
            # Edge intervals across the gap are unknowable; drop the tacho
            # continuity rather than publish a spurious slow-RPM sample.
            self._last_tacho_ms.clear()
            return self._package_measurement("stream.missing_packets", event["missing"], event["received_ns"])
        if kind == "disconnected":
            logger.error("QuantusDevice '%s': stream disconnected: %s", self.name, event["reason"])
            self._background_stop_event.set()
            return None
        return None

    def _anchor(self, epoch_relative_ns: int, received_ns: int) -> int:
        """Wall-clock anchor for epoch-relative stream timestamps.

        Anchored from the packet's Rust-side receive time (not this thread's
        processing time, which lags under consumer backlog), once per epoch,
        clamped so a new epoch never time-travels behind already-published
        samples.
        """
        if self._epoch_anchor_ns is None:
            anchor = received_ns - epoch_relative_ns
            if anchor + epoch_relative_ns <= self._max_published_ns:
                anchor = self._max_published_ns + 1 - epoch_relative_ns
            self._epoch_anchor_ns = anchor
        return self._epoch_anchor_ns

    def _alias(self, item_id: int) -> str:
        return self._alias_by_item.get(item_id, str(item_id))

    def _analog_measurement(self, event: dict) -> Measurement | None:
        samples = event["samples"]
        if len(samples) == 0:
            return None
        anchor = self._anchor(event["timestamp_ns"], event["received_ns"])
        rate = self._rate_by_item.get(event["channel_id"])
        dt_ns = round(1e9 / rate) if rate else 0
        t0 = anchor + event["timestamp_ns"]
        timestamps = [t0 + i * dt_ns for i in range(len(samples))]
        self._max_published_ns = max(self._max_published_ns, timestamps[-1])
        alias = self._alias(event["channel_id"])
        return Measurement(
            channel_data={f"{self.name}.{alias}": [float(s) for s in samples]},
            timestamps=timestamps,
            tags=dict(self.default_tags),
        )

    def _tacho_measurement(self, event: dict) -> Measurement | None:
        """Publish RPM from edge intervals: 60000 / (dt_ms * pulses_per_rev)."""
        events_ms = list(event["events_ms"])
        if not events_ms:
            return None
        channel_id = event["channel_id"]
        anchor = self._anchor(int(events_ms[0] * 1e6), event["received_ns"])
        pulses_per_rev = self._ppr_by_item.get(channel_id, 1.0)
        previous = self._last_tacho_ms.get(channel_id)
        rpms, timestamps = [], []
        for edge_ms in events_ms:
            if previous is not None and edge_ms > previous:
                rpms.append(60_000.0 / ((edge_ms - previous) * pulses_per_rev))
                timestamps.append(anchor + int(edge_ms * 1e6))
            previous = edge_ms
        self._last_tacho_ms[channel_id] = float(events_ms[-1])
        if not rpms:
            return None
        self._max_published_ns = max(self._max_published_ns, timestamps[-1])
        alias = self._alias(channel_id)
        return Measurement(
            channel_data={f"{self.name}.{alias}": rpms},
            timestamps=timestamps,
            tags=dict(self.default_tags),
        )

    def _can_measurements(self, event: dict) -> list[Measurement] | None:
        """Publish natively decoded per-signal series; count what wasn't decodable.

        The Rust layer decodes frames on channels with a ``dbc`` config entry
        into ``signals``; channels without one deliver raw ``frames``, which
        are only counted (no DBC means nothing to decode them with).
        ``unknown_frames`` is a per-batch delta, matching
        ``stream.missing_packets`` semantics.
        """
        alias = self._alias(event["channel_id"])
        measurements: list[Measurement] = []
        for signal, series in event.get("signals", {}).items():
            timestamps_s = series["timestamps_s"]
            if len(timestamps_s) == 0:
                continue
            anchor = self._anchor(int(timestamps_s[0] * 1e9), event["received_ns"])
            timestamps = [anchor + int(t * 1e9) for t in timestamps_s]
            self._max_published_ns = max(self._max_published_ns, timestamps[-1])
            measurements.append(
                Measurement(
                    channel_data={f"{self.name}.{alias}.{signal}": [float(v) for v in series["values"]]},
                    timestamps=timestamps,
                    tags=dict(self.default_tags),
                )
            )
        unknown = event.get("unknown_frames", 0) + len(event.get("frames", []))
        if unknown:
            measurements.append(self._package_measurement(f"{alias}.unknown_frames", unknown, event["received_ns"]))
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

    def write_settings(self, channel: str, values: dict[str, str | float], **kwargs) -> bool:
        """Settings-plane write: set values on ``channel`` (alias) and apply.

        Publishes one Command carrying every written setting on
        ``{name}.{channel}.{setting}.cmd``. Returns True when the streaming
        epoch restarts (expect a data gap).

        A failure AFTER the PUT leaves the values cached device-side: QServer
        activates all cached settings on the next apply, whichever call
        triggers it — hence the loud warning on that path.
        """
        try:
            restarted = self._require_client().write_settings(self._item_id(channel), values)
        except Exception:
            logger.warning(
                "QuantusDevice '%s': write_settings('%s') failed mid-flight; the values may be "
                "cached on the device and will activate on the NEXT apply (e.g. a later "
                "write_settings). Re-run this write or reconcile() to reach a known state.",
                self.name,
                channel,
            )
            raise
        if values:
            self.publish(
                Command(
                    channel_data={
                        f"{self.name}.{channel}.{_setting_key(name)}.cmd": (
                            value if isinstance(value, str) else float(value)
                        )
                        for name, value in values.items()
                    },
                    timestamp=time.time_ns(),
                    tags={**self.default_tags, **kwargs},
                )
            )
        return restarted

    @publish_command
    def auto_zero(self, channel: str | None = None, **kwargs) -> Command:
        """Auto-zero one channel (alias) or the whole system; publishes the command."""
        self._require_client().auto_zero(self._item_id(channel) if channel else None)
        descriptor = f"{channel}.auto_zero.cmd" if channel else "auto_zero.cmd"
        return self._package_command(descriptor, 1.0, time.time_ns(), **kwargs)

    @publish_command
    def bridge_balance(self, channel: str | None = None, **kwargs) -> Command:
        """Balance WSB bridges on one channel (alias) or system-wide; publishes the command."""
        self._require_client().bridge_balance(self._item_id(channel) if channel else None)
        descriptor = f"{channel}.bridge_balance.cmd" if channel else "bridge_balance.cmd"
        return self._package_command(descriptor, 1.0, time.time_ns(), **kwargs)

    @publish_command
    def can_transmit(self, channel: str, messages: list[dict], **kwargs) -> Command:
        """Cache ``messages`` on CAN ``channel`` (alias), transmit, and publish the command."""
        client = self._require_client()
        item_id = self._item_id(channel)
        client.put_can_message_list(item_id, {"MessageList": messages})
        client.can_transmit(item_id)
        return self._package_command(f"{channel}.can_transmit.cmd", float(len(messages)), time.time_ns(), **kwargs)
