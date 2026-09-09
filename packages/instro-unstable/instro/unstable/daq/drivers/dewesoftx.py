"""DewesoftX DCOM DAQ driver: streams live sync and async channels from a running DewesoftX instance."""

import logging
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Mapping

from instro.daq import DAQDriverBase
from instro.daq.types import AnalogChannel, AnalogVoltageChannel, DAQChannel, DigitalChannel, HWTimingConfig, Logic
from instro.lib.types import Measurement

logger = logging.getLogger(__name__)

_PROG_ID = "Dewesoft.App"
_CT_NEW = 3  # IChannelConnection.AType: each read returns only samples not read yet


@dataclass
class _SyncChannelCursor:
    """Read cursor for one bound synchronous DewesoftX channel."""

    com_channel: Any
    # Cursors are per-channel in DCOM (IChannel.CreateConnection); each tracks its channel's own unread position
    connection: Any
    buf_size: int
    # Samples consumed since store start; times each batch as anchor + total * dt_ns
    total: int
    dt_ns: float


@dataclass
class _AsyncChannelCursor:
    """Read cursor for one bound asynchronous DewesoftX channel (per-sample timestamps, no fixed rate)."""

    com_channel: Any
    connection: Any
    buf_size: int


@dataclass
class DewesoftXData:
    """Per-alias ``(values, timestamps_ns)`` drained from the DewesoftX ring buffers."""

    channels: dict[str, tuple[list[float], list[int]]]


class DewesoftXDriver(DAQDriverBase):
    """Streams live sync and async channels from a running DewesoftX instance over DCOM."""

    def __init__(self, timer_interval_ms: int | None = None) -> None:
        super().__init__()
        if timer_interval_ms is not None and timer_interval_ms < 1:
            raise ValueError(f"timer_interval_ms must be at least 1 ms, got {timer_interval_ms}.")
        # DewesoftX's acquisition loop period; the ring gains samples once per loop, so it caps any poll rate
        self._timer_interval_ms = timer_interval_ms
        self._app: Any = None
        # Cache DCOM properties
        self._data: Any = None
        self._store: Any = None
        self._t0_ns = 0
        self._cursors: dict[str, _SyncChannelCursor | _AsyncChannelCursor] = {}
        # Thread that owns the COM proxies; COM refuses cross-thread calls (see _attach_current_thread)
        self._thread_id = 0
        # True after warning that storing stopped; keeps the warning to one line per outage
        self._storing_paused = False

    # ====== Lifecycle ======

    def open(self):
        """Attach to the running DewesoftX instance."""
        import win32com.client  # type: ignore[import-untyped, import-not-found]

        # Attach to Dewesoft app
        app = win32com.client.Dispatch(_PROG_ID)
        app.Init()
        self._app = app
        self._data = app.Data
        self._store = app.StoreEngine

        # Debug full channel inventory
        if logger.isEnabledFor(logging.DEBUG):
            channels = self._data.AllChannels
            logger.debug("DewesoftX AllChannels (%d):", channels.Count)
            for i in range(channels.Count):
                ch = channels.Item(i)
                logger.debug(
                    "  [%3d] name=%r long=%r used=%s async=%s buf_size=%d sr_div=%d unit=%r type=%s/%s",
                    i,
                    ch.Name,
                    ch.LongName,
                    bool(ch.Used),
                    bool(ch.Async),
                    ch.DBBufSize,
                    ch.SRDiv,
                    ch.Unit_,
                    ch.ChannelType,
                    ch.DataType,
                )

        # Global DewesoftX setting: storing, math and the display share this loop
        if self._timer_interval_ms is not None and app.TimerInterval != self._timer_interval_ms:
            # The running loop keeps its old period until acquisition re-arms, and Stop() would end a live session
            if self._store.Storing:
                logger.warning(
                    "DewesoftX is storing; leaving its acquisition loop at %d ms instead of the requested %d ms. "
                    "Construct the driver before the store session starts to apply it.",
                    app.TimerInterval,
                    self._timer_interval_ms,
                )
            else:
                rearm = app.Acquiring
                if rearm:
                    app.Stop()
                app.TimerInterval = self._timer_interval_ms
                if rearm:
                    app.Start()
                logger.info("Set DewesoftX acquisition loop period to %d ms", self._timer_interval_ms)

        # Anchor absolute time axis if a storing session is running
        # Dewesoft only gives us the session start time in absolute time. Rest is relative
        if self._store.Storing:
            self._t0_ns = int(self._data.StartStoreTimeUTC.replace(tzinfo=timezone.utc).timestamp() * 1e9)
        else:
            self._t0_ns = 0

        self._thread_id = threading.get_ident()

    def close(self):
        """Drop the COM references; channels must be reconfigured after a reopen."""
        self._cursors.clear()
        self._app = None
        self._data = None
        self._store = None

    def start(self, **kwargs):
        """Start a stored session named run_<date>_<time>, or attach to one already running."""
        self._attach_current_thread()
        # Attach to exsiting storing session
        if self._store.Storing:
            logger.info("DewesoftX already storing to '%s'; attaching to that session", self._app.UsedDatafile)
        else:
            # Start a storing session
            name = f"run_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"
            self._app.StartStoring(name)
            if not self._store.Storing:
                raise RuntimeError(f"DewesoftX failed to start storing session '{name}'; check the DewesoftX setup.")
            logger.info("Started DewesoftX stored session '%s'", name)

        self._check_session()

    def stop(self, **kwargs):
        """Stop the stored session; a no-op when not storing so InstroDAQ teardown stays safe."""
        if self._app is None:
            return
        self._attach_current_thread()
        if not self._store.Storing:
            return
        self._app.StopStoring()
        logger.info("Stopped DewesoftX stored session")

    # ====== Configuration ======

    def configure_ai_voltage_channel(self, channel: AnalogVoltageChannel):
        """Bind an existing DewesoftX channel by name; DewesoftX owns all channel setup."""
        # range_min/range_max are ignored: DewesoftX owns scaling (a user scaler still applies HAL-side)
        com_channel = self._find_used_channel(channel.physical_channel)
        # A zero-size direct buffer means the channel is not acquiring
        if com_channel.DBBufSize == 0:
            raise ValueError(
                f"DewesoftX channel '{channel.physical_channel}' has no live buffer; check that DewesoftX is acquiring."
            )
        # Seed the read cursor so that we record timestamps from the right index
        self._cursors[channel.alias] = self._seed_cursor(com_channel)
        self._ai_channels[channel.alias] = channel

    def configure_ai_channel(self, channel: AnalogChannel):
        raise NotImplementedError("configure_analog_channel is deprecated; use configure_voltage_input instead")

    def configure_ai_hw_timing(self, hw_timing_config: HWTimingConfig):
        """Keep the requested config, but take the sample rate from DewesoftX, which owns the sample clock."""
        rate = self._data.SampleRate
        logger.info(
            "Replacing requested AI sample rate %s Hz with DewesoftX sample rate %s Hz",
            hw_timing_config.sample_rate,
            rate,
        )
        self._ai_hw_timing_config = replace(hw_timing_config, sample_rate=rate, sample_period=round(1e9 / rate))

    def get_actual_sample_rate(self) -> float | None:
        """The device's rate captured at configure time; None before configure_ai_sample_rate()."""
        return self._ai_hw_timing_config.sample_rate if self._ai_hw_timing_config else None

    # ====== Reading ======

    def read_analog(self) -> DewesoftXData:
        """Drain every new sample from each bound channel."""
        self._attach_current_thread()
        # Re-anchor (and skip this batch) when the store session changed under us
        if not self._check_session():
            return DewesoftXData(channels={})
        drained: dict[str, tuple[list[float], list[int]]] = {}

        for alias, cursor in self._cursors.items():
            # The server-side cursor tracks the exact unread count for sync and async channels alike
            pending = cursor.connection.NumValues
            if pending <= 0:
                continue
            # A full ring means the oldest unread samples were (or are about to be) overwritten
            if pending >= cursor.buf_size:
                logger.warning("DewesoftX channel '%s' overran its buffer; dropping %d pending samples", alias, pending)
                self._cursors[alias] = self._seed_cursor(cursor.com_channel)
                continue
            if isinstance(cursor, _AsyncChannelCursor):
                # GetTSValues peeks the oldest samples; GetDataValues consumes them, so timestamps read first
                rel_ts = cursor.connection.GetTSValues(pending)
                data = cursor.connection.GetDataValues(pending)
                if data is None:
                    continue
                # Async timestamps are seconds since store start, the same epoch as the anchor
                drained[alias] = (list(data), [self._t0_ns + round(t * 1e9) for t in rel_ts])
            else:
                data = cursor.connection.GetDataValues(pending)
                if data is None:
                    continue
                values = list(data)
                # Sync channels store no timestamps; time is implicit from the consumed-sample index and the rate
                timestamps = [self._t0_ns + round((cursor.total + k) * cursor.dt_ns) for k in range(len(values))]
                drained[alias] = (values, timestamps)
                cursor.total += len(values)
        # Discard a batch that straddles a store restart: a cursor from before the restart returns garbage
        if not self._check_session():
            return DewesoftXData(channels={})
        return DewesoftXData(channels=drained)

    def fetch_analog(self) -> DewesoftXData:
        """Block until ``samples_per_channel`` new samples arrive on every sync channel, then drain."""
        if self._ai_hw_timing_config is None:
            raise RuntimeError("configure_ai_sample_rate() must be called before fetching analog data.")
        self._attach_current_thread()
        if not self._cursors:
            return DewesoftXData(channels={})
        # Wait until every bound sync channel has a full batch pending; async channels have no rate to pace on
        target = self._ai_hw_timing_config.samples_per_channel
        rate = self._ai_hw_timing_config.sample_rate
        sync_cursors = [c for c in self._cursors.values() if isinstance(c, _SyncChannelCursor)]
        while True:
            if not self._check_session():
                # Pace the empty return so the daemon regains control (and its stop event) without spinning
                time.sleep(0.5)
                return DewesoftXData(channels={})
            # With only async channels bound, drain once per batch period instead
            if not sync_cursors:
                time.sleep(min(0.5, target / rate))
                return self.read_analog()
            available = [c.connection.NumValues for c in sync_cursors]
            self.points_in_buffer = max(available)
            if min(available) >= target:
                return self.read_analog()
            # Sleep about half the remaining fill time; the floor bounds the poll cost, the cap keeps stops responsive
            time.sleep(min(0.5, max(0.001, (target - min(available)) / rate / 2)))

    def _read_to_measurements(
        self,
        response: DewesoftXData,
        channel_list: Mapping[str, DAQChannel],
        daq_name: str,
        default_tags: dict[str, str],
        **kwargs,
    ) -> list[Measurement]:
        # One Measurement per channel: cursors drain independently, so lengths differ across channels
        return [
            Measurement(
                channel_data={f"{daq_name}.{alias}": values},
                timestamps=timestamps,
                tags={**default_tags, **(kwargs or {})},
            )
            for alias, (values, timestamps) in response.channels.items()
        ]

    # ====== Internals ======

    def _seed_cursor(self, com_channel: Any) -> _SyncChannelCursor | _AsyncChannelCursor:
        """Create a channel's read cursor: a server-side connection plus wrap-proof sample indexing."""
        # Ring-index bulk reads slide with acquisition, so all data flows through a server-side cursor
        connection = com_channel.CreateConnection()
        connection.AType = _CT_NEW
        # A fresh ctNew connection starts at "now", so an async cursor needs no position seeding
        if com_channel.Async:
            return _AsyncChannelCursor(com_channel=com_channel, connection=connection, buf_size=com_channel.DBBufSize)
        # The device's own sample count since store start; DBPos alone loses count at every wrap
        acquired = self._samples_acquired() / com_channel.SRDiv
        # Snap that count onto the ring position
        pos = com_channel.DBPos
        total = pos + round((acquired - pos) / com_channel.DBBufSize) * com_channel.DBBufSize
        return _SyncChannelCursor(
            com_channel=com_channel,
            connection=connection,
            buf_size=com_channel.DBBufSize,
            total=total,
            dt_ns=1e9 * com_channel.SRDiv / self._data.SampleRate,
        )

    def _samples_acquired(self) -> int:
        """Master-rate sample count acquired since store start."""
        blocks, partial = self._data.GetSamplesAcquired()
        return blocks * self._data.Samples + partial

    def _check_session(self) -> bool:
        """Return True while the store session is unchanged; False (skip the batch) when storing is off or restarted."""
        # StartStoreTimeUTC keeps its stale value after a stop; Storing is the reliable signal
        if not self._store.Storing:
            # Warn once; reads return empty batches until storing resumes and the anchor change below re-anchors
            if not self._storing_paused:
                logger.warning("DewesoftX stopped storing; discarding samples until storing resumes")
                self._storing_paused = True
            return False
        self._storing_paused = False
        # A changed store-start anchor means the session restarted
        anchor = self._data.StartStoreTimeUTC
        t0_ns = int(anchor.replace(tzinfo=timezone.utc).timestamp() * 1e9)
        if t0_ns == self._t0_ns:
            return True
        # Reseed every cursor on the new anchor
        logger.info("DewesoftX store session restarted at %s; re-anchoring %d channel(s)", anchor, len(self._cursors))
        self._t0_ns = t0_ns
        for alias, cursor in self._cursors.items():
            self._cursors[alias] = self._seed_cursor(cursor.com_channel)
        return False

    def _attach_current_thread(self):
        """Rebuild every COM reference on the calling thread; no-op when already attached."""
        if threading.get_ident() == self._thread_id:
            return
        import pythoncom  # type: ignore[import-untyped, import-not-found]
        import win32com.client  # type: ignore[import-untyped, import-not-found]

        # Quirk: COM proxies refuse cross-thread calls, and the InstroDAQ daemon reads from its own thread
        pythoncom.CoInitialize()
        self._app = win32com.client.Dispatch(_PROG_ID)
        self._data = self._app.Data
        self._store = self._app.StoreEngine
        for alias in self._cursors:
            self._cursors[alias] = self._seed_cursor(self._find_used_channel(self._ai_channels[alias].physical_channel))
        self._thread_id = threading.get_ident()

    def _find_used_channel(self, name: str) -> Any:
        """Find a channel by Name or LongName among the ones set to "Used" in DewesoftX."""
        channels = self._data.UsedChannels
        for i in range(channels.Count):
            com_channel = channels.Item(i)
            # CAN channels share a generic Name ('Message', 'Channel'); LongName carries the full path
            if name in (com_channel.Name, com_channel.LongName):
                return com_channel
        raise ValueError(f"DewesoftX has no used channel named '{name}'.")

    # ====== Unsupported: DewesoftX owns channel setup ======

    def configure_di_line_channel(
        self,
        physical_channel: str,
        logic: Logic,
        logic_level: float | None = None,
        alias: str | None = None,
    ):
        raise NotImplementedError("Digital input has not been configured for this driver")

    def configure_do_line_channel(
        self,
        physical_channel: str,
        logic: Logic,
        logic_level: float | None = None,
        alias: str | None = None,
    ):
        raise NotImplementedError("Digital output has not been configured for this driver")

    def write_digital_line(self, channel: DigitalChannel, data: int):
        raise NotImplementedError("Digital output has not been configured for this driver")

    def read_digital_line(self, channel: DigitalChannel) -> int:
        raise NotImplementedError("Digital input has not been configured for this driver")

    def write_digital_port(self, channel: DigitalChannel, data: int):
        raise NotImplementedError("Digital output has not been configured for this driver")

    def read_digital_port(self, channel: DigitalChannel) -> int:
        raise NotImplementedError("Digital input has not been configured for this driver")
