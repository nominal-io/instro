"""DewesoftX DCOM DAQ driver: reads live synchronous channels from a running DewesoftX instance."""

import logging
import threading
from dataclasses import dataclass
from datetime import timezone
from typing import Any, Mapping

from instro.daq import DAQDriverBase
from instro.daq.types import AnalogChannel, DAQChannel, DigitalChannel, HWTimingConfig, Logic
from instro.lib.types import Measurement

logger = logging.getLogger(__name__)

_PROG_ID = "Dewesoft.App"


@dataclass
class _ChannelState:
    """Ring-buffer read cursor for one bound DewesoftX channel."""

    com_channel: Any
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
    """Read-only driver that attaches to a running DewesoftX instance over DCOM."""

    def __init__(self) -> None:
        super().__init__()
        self._app: Any = None
        self._t0_ns = 0
        self._states: dict[str, _ChannelState] = {}
        self._thread_id = 0

    def open(self):
        """Attach to the running DewesoftX instance and anchor the time axis at store start (UTC)."""
        import win32com.client  # type: ignore[import-untyped, import-not-found]

        app = win32com.client.Dispatch(_PROG_ID)
        app.Init()
        if not app.StoreEngine.Storing:
            raise RuntimeError("DewesoftX is not storing, so StartStoreTimeUTC has no absolute-time anchor.")
        self._app = app
        self._t0_ns = int(app.Data.StartStoreTimeUTC.replace(tzinfo=timezone.utc).timestamp() * 1e9)
        self._thread_id = threading.get_ident()

    def close(self):
        """Drop the COM references; channels must be reconfigured after a reopen."""
        self._states.clear()
        self._app = None

    def configure_ai_channel(self, channel: AnalogChannel):
        """Bind an already-configured DewesoftX sync channel by name and seed its read cursor."""
        com_channel = self._find_used_channel(channel.physical_channel)
        if com_channel.Async:
            raise ValueError(
                f"DewesoftX channel '{channel.physical_channel}' is asynchronous; "
                "only synchronous channels are supported."
            )
        if com_channel.DBBufSize == 0:
            raise ValueError(
                f"DewesoftX channel '{channel.physical_channel}' has no live buffer; check that DewesoftX is acquiring."
            )
        self._states[channel.alias] = self._seed_state(com_channel)
        self._ai_channels[channel.alias] = channel

    def read_analog(self) -> DewesoftXData:
        """Drain every new sample from each bound channel's ring buffer."""
        if threading.get_ident() != self._thread_id:
            self._attach_current_thread()
        if not self._resync_session():
            return DewesoftXData(channels={})
        drained: dict[str, tuple[list[float], list[int]]] = {}
        for alias, state in self._states.items():
            new = (state.com_channel.DBPos - state.pos) % state.buf_size
            if new == 0:
                continue
            # The connection's server-side cursor returns exactly the oldest `new` unread samples
            data = state.connection.GetDataValues(new)
            if data is None:
                continue
            values = list(data)
            # Derived: sync channels have no timestamp buffer, so time is implicit at index * dt
            timestamps = [self._t0_ns + round((state.total + k) * state.dt_ns) for k in range(len(values))]
            drained[alias] = (values, timestamps)
            state.total += len(values)
            state.pos = (state.pos + len(values)) % state.buf_size
        if not self._resync_session():
            return DewesoftXData(channels={})  # the batch straddles a store restart, so its timestamps are wrong
        return DewesoftXData(channels=drained)

    def _read_to_measurements(
        self,
        response: DewesoftXData,
        channel_list: Mapping[str, DAQChannel],
        daq_name: str,
        default_tags: dict[str, str],
        **kwargs,
    ) -> list[Measurement]:
        return [
            Measurement(
                channel_data={f"{daq_name}.{alias}": values},
                timestamps=timestamps,
                tags={**default_tags, **(kwargs or {})},
            )
            for alias, (values, timestamps) in response.channels.items()
        ]

    def _seed_state(self, com_channel: Any) -> _ChannelState:
        """Create a server-side read cursor and seed wrap-proof indexing from the device's sample count."""
        connection = com_channel.CreateConnection()
        connection.AType = 3  # ctNew: each read returns only samples not read yet
        blocks, partial = self._app.Data.GetSamplesAcquired()
        acquired = (blocks * self._app.Data.Samples + partial) / com_channel.SRDiv  # channel samples since store start
        pos = com_channel.DBPos
        total = pos + round((acquired - pos) / com_channel.DBBufSize) * com_channel.DBBufSize  # wrap-proof seed
        return _ChannelState(
            com_channel=com_channel,
            connection=connection,
            buf_size=com_channel.DBBufSize,
            pos=pos,
            total=total,
            dt_ns=1e9 * com_channel.SRDiv / self._app.Data.SampleRate,
        )

    def _resync_session(self) -> bool:
        """Return True while the store session is unchanged; on a restart, re-anchor and reseed every cursor."""
        if not self._app.StoreEngine.Storing:
            raise RuntimeError("DewesoftX stopped storing; timestamps have no anchor until storing restarts.")
        anchor = self._app.Data.StartStoreTimeUTC
        t0_ns = int(anchor.replace(tzinfo=timezone.utc).timestamp() * 1e9)
        if t0_ns == self._t0_ns:
            return True
        # A store restart resets DBPos and the sample counter, so the old cursors are meaningless
        logger.info("DewesoftX store session restarted at %s; re-anchoring %d channel(s)", anchor, len(self._states))
        self._t0_ns = t0_ns
        for alias, state in self._states.items():
            self._states[alias] = self._seed_state(state.com_channel)
        return False

    def _attach_current_thread(self):
        """Re-attach on the calling thread: COM proxies are apartment-bound, and the daemon reads from its own thread."""
        import pythoncom  # type: ignore[import-untyped, import-not-found]
        import win32com.client  # type: ignore[import-untyped, import-not-found]

        pythoncom.CoInitialize()
        self._app = win32com.client.Dispatch(_PROG_ID)
        for alias in self._states:
            self._states[alias] = self._seed_state(self._find_used_channel(self._ai_channels[alias].physical_channel))
        self._thread_id = threading.get_ident()

    def _find_used_channel(self, name: str) -> Any:
        channels = self._app.Data.UsedChannels
        for i in range(channels.Count):
            com_channel = channels.Item(i)
            if com_channel.Name == name:
                return com_channel
        raise ValueError(f"DewesoftX has no used channel named '{name}'.")

    # ====== Unsupported: DewesoftX owns acquisition, timing, and channel setup ======

    def stop(self, **kwargs):
        """No-op so InstroDAQ teardown succeeds: acquisition is controlled in the DewesoftX application."""

    def start(self, **kwargs):
        raise NotImplementedError("Acquisition is controlled in the DewesoftX application")

    def fetch_analog(self) -> Any:
        raise NotImplementedError("Hardware-timed fetch is not supported; poll with software timing instead")

    def configure_ai_hw_timing(self, hw_timing_config: HWTimingConfig):
        raise NotImplementedError("The sample rate is controlled in the DewesoftX application")

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
