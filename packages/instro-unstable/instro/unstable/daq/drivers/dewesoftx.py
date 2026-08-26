"""DewesoftX DCOM DAQ driver: streams live synchronous channels from a running DewesoftX instance."""

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from instro.daq import DAQDriverBase
from instro.daq.types import AnalogChannel, DAQChannel, DigitalChannel, HWTimingConfig, Logic
from instro.lib.types import Measurement

logger = logging.getLogger(__name__)

_PROG_ID = "Dewesoft.App"
_CT_NEW = 3  # IChannelConnection.AType: each read returns only samples not read yet


@dataclass
class _ChannelCursor:
    """Read cursor for one bound DewesoftX channel."""

    com_channel: Any
    # Cursors are per-channel in DCOM (IChannel.CreateConnection); each tracks its channel's own unread position
    connection: Any
    buf_size: int
    pos: int
    total: int
    dt_ns: float


@dataclass
class DewesoftXData:
    """Per-alias ``(values, timestamps_ns)`` drained from the DewesoftX ring buffers."""

    channels: dict[str, tuple[list[float], list[int]]]


class DewesoftX(DAQDriverBase):
    """Streams live synchronous channels from a running DewesoftX instance over DCOM."""

    def __init__(self) -> None:
        super().__init__()
        self._app: Any = None
        self._t0_ns = 0
        self._cursors: dict[str, _ChannelCursor] = {}
        # Thread that owns the COM proxies; COM refuses cross-thread calls (see _attach_current_thread)
        self._thread_id = 0

    # ====== Lifecycle ======

    def open(self):
        """Attach to the running DewesoftX instance."""
        import win32com.client  # type: ignore[import-untyped, import-not-found]

        # Attach to Dewesoft app
        app = win32com.client.Dispatch(_PROG_ID)
        app.Init()
        self._app = app

        # Anchor absolute time axis if a storing session is running
        # Dewesoft only gives us the session start time in absolute time. Rest is relative
        if app.StoreEngine.Storing:
            self._t0_ns = int(app.Data.StartStoreTimeUTC.replace(tzinfo=timezone.utc).timestamp() * 1e9)
        else:
            self._t0_ns = 0

        self._thread_id = threading.get_ident()

    def close(self):
        """Drop the COM references; channels must be reconfigured after a reopen."""
        self._cursors.clear()
        self._app = None

    def start(self, **kwargs):
        """Start a stored session named run_<date>_<time>, or attach to one already running."""
        if threading.get_ident() != self._thread_id:
            self._attach_current_thread()
        # Attach to exsiting storing session
        if self._app.StoreEngine.Storing:
            logger.info("DewesoftX already storing to '%s'; attaching to that session", self._app.UsedDatafile)
            return

        # Start a storing session
        name = f"run_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"
        self._app.StartStoring(name)
        logger.info("Started DewesoftX stored session '%s'", name)

    def stop(self, **kwargs):
        """Stop the stored session; a no-op when not storing so InstroDAQ teardown stays safe."""
        if self._app is None:
            return
        if threading.get_ident() != self._thread_id:
            self._attach_current_thread()
        if not self._app.StoreEngine.Storing:
            return
        self._app.StopStoring()
        logger.info("Stopped DewesoftX stored session")

    # ====== Configuration ======

    def configure_ai_channel(self, channel: AnalogChannel):
        """Bind an existing DewesoftX channel by name; DewesoftX owns all channel setup."""
        com_channel = self._find_used_channel(channel.physical_channel)
        # TODO: Add support for async channels
        if com_channel.Async:
            raise ValueError(
                f"DewesoftX channel '{channel.physical_channel}' is asynchronous; "
                "only synchronous channels are supported."
            )
        # A zero-size direct buffer means the channel is not acquiring
        if com_channel.DBBufSize == 0:
            raise ValueError(
                f"DewesoftX channel '{channel.physical_channel}' has no live buffer; check that DewesoftX is acquiring."
            )
        # Seed the read cursor so that we record timestamps from the right index
        self._cursors[channel.alias] = self._seed_cursor(com_channel)
        self._ai_channels[channel.alias] = channel

    def configure_ai_hw_timing(self, hw_timing_config: HWTimingConfig):
        """Discard the requested config and rebuild it from the device's actual sample rate."""
        # Use configured DewesoftX sample rate instead of user passed one
        rate = self._app.Data.SampleRate
        # Mirror the InstroDAQ samples_per_channel default
        self._ai_hw_timing_config = HWTimingConfig(
            sample_rate=rate,
            sample_period=round(1e9 / rate),
            samples_per_channel=max(1, rate // 10),
        )

    # ====== Reading ======

    def read_analog(self) -> DewesoftXData:
        """Drain every new sample from each bound channel."""
        # Re-attach if there's a new thread
        if threading.get_ident() != self._thread_id:
            self._attach_current_thread()
        # Re-anchor (and skip this batch) when the store session changed under us
        if not self._resync_session():
            return DewesoftXData(channels={})
        drained: dict[str, tuple[list[float], list[int]]] = {}
        # Sync channels stream with buffer ring index, so we need to convert index -> relative -> absolute time
        for alias, cursor in self._cursors.items():
            # Count new samples from the ring write index (DBPos wraps at buf_size)
            new = (cursor.com_channel.DBPos - cursor.pos) % cursor.buf_size
            if new == 0:
                continue
            # Get new values from cursor
            data = cursor.connection.GetDataValues(new)
            if data is None:
                continue
            values = list(data)
            # Derive timestamps
            # Sync channels stream with buffer ring index, so we need to convert index -> relative -> absolute time
            timestamps = [self._t0_ns + round((cursor.total + k) * cursor.dt_ns) for k in range(len(values))]
            drained[alias] = (values, timestamps)
            cursor.total += len(values)
            cursor.pos = (cursor.pos + len(values)) % cursor.buf_size
        # Discard a batch that straddles a store restart
        if not self._resync_session():
            return DewesoftXData(channels={})
        return DewesoftXData(channels=drained)

    def fetch_analog(self) -> DewesoftXData:
        """Block until ``samples_per_channel`` new samples arrive on every channel, then drain."""
        if self._ai_hw_timing_config is None:
            raise RuntimeError("configure_ai_sample_rate() must be called before fetching analog data.")
        if threading.get_ident() != self._thread_id:
            self._attach_current_thread()
        if not self._cursors:
            return DewesoftXData(channels={})
        # Wait until every bound channel has a full batch pending
        target = self._ai_hw_timing_config.samples_per_channel
        waited = 0
        while True:
            available = [(c.com_channel.DBPos - c.pos) % c.buf_size for c in self._cursors.values()]
            self.points_in_buffer = max(available)
            # Drain buffers through read_analog
            if min(available) >= target:
                return self.read_analog()
            time.sleep(0.001)
            waited += 1

            if waited % 500 == 0 and not self._resync_session():
                return DewesoftXData(channels={})

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

    def _seed_cursor(self, com_channel: Any) -> _ChannelCursor:
        """Create a channel's read cursor: a server-side connection plus wrap-proof sample indexing."""
        # Ring-index bulk reads slide with acquisition, so all data flows through a server-side cursor
        connection = com_channel.CreateConnection()
        connection.AType = _CT_NEW
        # The device's own sample count since store start; DBPos alone loses count at every wrap
        blocks, partial = self._app.Data.GetSamplesAcquired()
        acquired = (blocks * self._app.Data.Samples + partial) / com_channel.SRDiv
        # Snap that count onto the ring position
        pos = com_channel.DBPos
        total = pos + round((acquired - pos) / com_channel.DBBufSize) * com_channel.DBBufSize
        return _ChannelCursor(
            com_channel=com_channel,
            connection=connection,
            buf_size=com_channel.DBBufSize,
            pos=pos,
            total=total,
            dt_ns=1e9 * com_channel.SRDiv / self._app.Data.SampleRate,
        )

    def _resync_session(self) -> bool:
        """Return True while the store session is unchanged; re-anchor everything on a restart."""
        # StartStoreTimeUTC keeps its stale value after a stop; Storing is the reliable signal
        if not self._app.StoreEngine.Storing:
            raise RuntimeError("DewesoftX stopped storing; timestamps have no anchor until storing restarts.")
        # A changed store-start anchor means the session restarted
        anchor = self._app.Data.StartStoreTimeUTC
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
        """Rebuild every COM reference on the calling thread."""
        import pythoncom  # type: ignore[import-untyped, import-not-found]
        import win32com.client  # type: ignore[import-untyped, import-not-found]

        # Quirk: COM proxies refuse cross-thread calls, and the InstroDAQ daemon reads from its own thread
        pythoncom.CoInitialize()
        self._app = win32com.client.Dispatch(_PROG_ID)
        for alias in self._cursors:
            self._cursors[alias] = self._seed_cursor(self._find_used_channel(self._ai_channels[alias].physical_channel))
        self._thread_id = threading.get_ident()

    def _find_used_channel(self, name: str) -> Any:
        """Find a channel by name among the ones set to "Used" in DewesoftX."""
        channels = self._app.Data.UsedChannels
        for i in range(channels.Count):
            com_channel = channels.Item(i)
            if com_channel.Name == name:
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
