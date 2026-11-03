"""SDR instrument API.

This submodule defines the generic SDR contract used throughout the unstable
instrument layer and a thin wrapper around a vendor driver. The design follows the
repo's pattern: the low-level driver owns the hardware transport and device
lifecycle, while the high-level ``InstroSDR`` object publishes measurements and
commands using the shared core measurement model.

The data model intentionally batches IQ samples into a single ``Measurement`` per
acquisition block instead of creating one measurement object per sample. The
``Measurement`` type in ``instro.lib.types`` is meant for a block with a common
shared timebase, not for millions of individual sample-level events.
"""

from __future__ import annotations

import abc
import logging
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

import numpy as np

from instro.lib import Command, Instrument, Measurement
from instro.lib.instrument import publish_command, publish_measurement
from instro.unstable.sdr.types import Direction

logger = logging.getLogger(__name__)


@dataclass(frozen=True, eq=False)
class IQCapture:
    """One time-aligned block of IQ samples across one or more channels of a signal path.

    Every row shares one timebase, so a multi-channel capture is aligned by construction.

    Attributes:
        samples (numpy.ndarray): Complex IQ samples shaped ``(n_channels, n_samples)``. Always
            two-dimensional, even for a single channel.
        sample_period_ns (float): Time between samples, in nanoseconds, shared by every row.
        channels (tuple[str, ...]): Channel each row of ``samples`` came from.
        center_freq_hz (tuple[float, ...]): Frequency each channel was tuned to, in Hz.
        t0_ns (int | None): Hardware timestamp of the first sample, in nanoseconds since the Unix
            epoch. ``None`` when the device has no clock of its own, in which case
            `InstroSDR` anchors the block to the host clock.
        dropped_samples (int): Samples lost before this block because the stream could not keep up.

    Raises:
        ValueError: If ``samples`` is not two-dimensional, or its row count does not match
            ``channels`` and ``center_freq_hz``.

    Example:
        ::

            capture = IQCapture(
                samples=np.zeros((1, 4096), dtype=np.complex128),
                sample_period_ns=1e9 / 2.4e6,
                channels=("0",),
                center_freq_hz=(100e6,),
            )
            row = capture.samples_for("0")
    """

    samples: np.ndarray
    sample_period_ns: float
    channels: tuple[str, ...]
    center_freq_hz: tuple[float, ...]
    t0_ns: int | None = None
    # How many samples went missing before this block; the stream could not keep up.
    dropped_samples: int = 0

    def __post_init__(self) -> None:
        """Reject rows that do not line up with the channels describing them."""
        if self.samples.ndim != 2:
            raise ValueError(f"samples must be 2-D (n_channels, n_samples), got shape {self.samples.shape}")
        if len(self.channels) != self.samples.shape[0] or len(self.center_freq_hz) != self.samples.shape[0]:
            raise ValueError(
                f"{self.samples.shape[0]} sample rows but {len(self.channels)} channels "
                f"and {len(self.center_freq_hz)} centre frequencies"
            )

    @property
    def overflow(self) -> bool:
        """Whether any samples were dropped before this block.

        Returns:
            bool: ``True`` when ``dropped_samples`` is greater than zero.
        """
        return self.dropped_samples > 0

    def samples_for(self, channel: str) -> np.ndarray:
        """Return the row of samples belonging to one channel.

        Args:
            channel (str): Channel to read.

        Returns:
            numpy.ndarray: The channel's complex IQ samples, one-dimensional.

        Raises:
            ValueError: If the capture holds no such channel.
        """
        return self.samples[self.channels.index(channel)]

    def center_freq_for(self, channel: str) -> float:
        """Return the frequency a channel was tuned to when these samples were taken.

        Args:
            channel (str): Channel to read.

        Returns:
            float: Center frequency in Hz.

        Raises:
            ValueError: If the capture holds no such channel.
        """
        return self.center_freq_hz[self.channels.index(channel)]

    def select(self, channels: Sequence[str]) -> "IQCapture":
        """Narrow this capture to a subset of its channels, keeping the shared timebase.

        Args:
            channels (Sequence[str]): Channels to keep, in the order the rows should appear.

        Returns:
            IQCapture: A capture holding only ``channels``, or this capture unchanged if they
            already match.

        Raises:
            ValueError: If any of ``channels`` is not in the capture.
        """
        wanted = tuple(channels)
        if wanted == self.channels:
            return self
        missing = [c for c in wanted if c not in self.channels]
        if missing:
            raise ValueError(f"capture holds channels {self.channels!r}, asked for {missing!r}")
        rows = [self.channels.index(c) for c in wanted]
        return IQCapture(
            samples=self.samples[rows],
            sample_period_ns=self.sample_period_ns,
            channels=wanted,
            center_freq_hz=tuple(self.center_freq_hz[r] for r in rows),
            t0_ns=self.t0_ns,
            dropped_samples=self.dropped_samples,
        )


class SDRDriverBase(abc.ABC):
    """Contract every vendor SDR driver implements.

    Abstract methods cover what every radio can do: open, tune, set a rate, and return samples.
    The rest raise `NotImplementedError` by default, and a driver overrides only those its
    hardware supports. A driver owns its own transport and holds no reference back to
    `InstroSDR`.

    Configuration methods take keyword-only ``direction`` and ``channel``, defaulting to the
    first receive path. Acquisition (`read_iq`, `fetch_iq`, `get_backlog`) is receive-only.

    Example:
        A receive-only, single-channel driver over a vendor SDK::

            class MyVendorSDR(SDRDriverBase):
                def __init__(self, device_index: int = 0):
                    self._device_index = device_index
                    self._device = None

                def open(self) -> None:
                    from vendor_sdk import Radio  # keeps the SDK optional at import time

                    self._device = Radio(self._device_index)

                def close(self) -> None:
                    self._device.close()

                def set_center_freq(self, frequency_hz, *, direction=Direction.RX, channel="0"):
                    self._device.center_freq = frequency_hz

                ...  # get_center_freq, set_sample_rate, get_sample_rate

                def read_iq(self, n_samples, *, channels=("0",)) -> IQCapture:
                    samples = np.asarray(self._device.read_samples(n_samples), dtype=np.complex128)
                    return IQCapture(
                        samples=samples.reshape(1, -1),
                        sample_period_ns=1e9 / self._device.sample_rate,
                        channels=("0",),
                        center_freq_hz=(self._device.center_freq,),
                    )
    """

    # --- Required ---

    @abc.abstractmethod
    def open(self) -> None:
        """Open the underlying transport or SDR handle."""

    @abc.abstractmethod
    def close(self) -> None:
        """Close the underlying transport or SDR handle."""

    @abc.abstractmethod
    def set_center_freq(self, frequency_hz: float, *, direction: Direction = Direction.RX, channel: str = "0") -> None:
        """Tune the RF center frequency.

        Args:
            frequency_hz (float): Center frequency in Hz.
            direction (Direction): Signal path, ``Direction.RX`` or ``Direction.TX``.
            channel (str): Channel on that path, e.g. ``"0"``.
        """

    @abc.abstractmethod
    def get_center_freq(self, *, direction: Direction = Direction.RX, channel: str = "0") -> float:
        """Return the RF center frequency the radio is tuned to.

        Args:
            direction (Direction): Signal path, ``Direction.RX`` or ``Direction.TX``.
            channel (str): Channel on that path, e.g. ``"0"``.

        Returns:
            float: Center frequency in Hz.
        """

    @abc.abstractmethod
    def set_sample_rate(
        self, sample_rate_hz: float, *, direction: Direction = Direction.RX, channel: str = "0"
    ) -> None:
        """Set the IQ sample rate.

        Args:
            sample_rate_hz (float): Sample rate in samples per second.
            direction (Direction): Signal path, ``Direction.RX`` or ``Direction.TX``.
            channel (str): Channel on that path, e.g. ``"0"``.
        """

    @abc.abstractmethod
    def get_sample_rate(self, *, direction: Direction = Direction.RX, channel: str = "0") -> float:
        """Return the IQ sample rate the radio accepted.

        Args:
            direction (Direction): Signal path, ``Direction.RX`` or ``Direction.TX``.
            channel (str): Channel on that path, e.g. ``"0"``.

        Returns:
            float: Sample rate in samples per second.
        """

    @abc.abstractmethod
    def read_iq(self, n_samples: int, *, channels: Sequence[str] = ("0",)) -> IQCapture:
        """Read one time-aligned receive block, with the timebase it was taken on.

        ``sample_period_ns`` on the result must describe the block returned, not the rate last
        requested: `InstroSDR` derives every IQ timestamp from it.

        Args:
            n_samples (int): Samples to read per channel.
            channels (Sequence[str]): Receive channels to read, e.g. ``("0",)``.

        Returns:
            IQCapture: One row per channel in ``channels``.
        """

    # --- Optional: gain ---

    def set_gain(self, gain_db: float, *, direction: Direction = Direction.RX, channel: str = "0") -> None:
        """Set the overall gain.

        Override where the radio exposes a single gain figure.

        Args:
            gain_db (float): Gain in dB.
            direction (Direction): Signal path, ``Direction.RX`` or ``Direction.TX``.
            channel (str): Channel on that path, e.g. ``"0"``.

        Raises:
            NotImplementedError: If the driver does not support gain control.
        """
        raise NotImplementedError("Gain control has not been implemented for this driver")

    def get_gain(self, *, direction: Direction = Direction.RX, channel: str = "0") -> float:
        """Return the overall gain.

        Args:
            direction (Direction): Signal path, ``Direction.RX`` or ``Direction.TX``.
            channel (str): Channel on that path, e.g. ``"0"``.

        Returns:
            float: Gain in dB.

        Raises:
            NotImplementedError: If the driver does not support gain control.
        """
        raise NotImplementedError("Gain control has not been implemented for this driver")

    def set_gain_mode(self, automatic: bool, *, direction: Direction = Direction.RX, channel: str = "0") -> None:
        """Hand gain to the radio's automatic gain control, or take manual control.

        Args:
            automatic (bool): ``True`` for AGC, ``False`` for manual gain.
            direction (Direction): Signal path, ``Direction.RX`` or ``Direction.TX``.
            channel (str): Channel on that path, e.g. ``"0"``.

        Raises:
            NotImplementedError: If the driver does not support a gain mode.
        """
        raise NotImplementedError("Gain mode has not been implemented for this driver")

    def get_gain_mode(self, *, direction: Direction = Direction.RX, channel: str = "0") -> bool:
        """Report whether automatic gain control is in control.

        Many radios cannot read this back.

        Args:
            direction (Direction): Signal path, ``Direction.RX`` or ``Direction.TX``.
            channel (str): Channel on that path, e.g. ``"0"``.

        Returns:
            bool: ``True`` when AGC is in control.

        Raises:
            NotImplementedError: If the driver does not support gain mode readback.
        """
        raise NotImplementedError("Gain mode readback has not been implemented for this driver")

    def get_gain_range(self, *, direction: Direction = Direction.RX, channel: str = "0") -> tuple[float, float]:
        """Return the lowest and highest settable gain.

        Args:
            direction (Direction): Signal path, ``Direction.RX`` or ``Direction.TX``.
            channel (str): Channel on that path, e.g. ``"0"``.

        Returns:
            tuple[float, float]: ``(low, high)`` in dB.

        Raises:
            NotImplementedError: If the driver does not support reporting a gain range.
        """
        raise NotImplementedError("Gain range has not been implemented for this driver")

    # --- Optional: front end ---

    def set_bandwidth(self, bandwidth_hz: float, *, direction: Direction = Direction.RX, channel: str = "0") -> None:
        """Set the IF or filter bandwidth.

        Some radios tie this to the sample rate.

        Args:
            bandwidth_hz (float): Bandwidth in Hz.
            direction (Direction): Signal path, ``Direction.RX`` or ``Direction.TX``.
            channel (str): Channel on that path, e.g. ``"0"``.

        Raises:
            NotImplementedError: If the driver does not support bandwidth control.
        """
        raise NotImplementedError("Bandwidth control has not been implemented for this driver")

    def get_bandwidth(self, *, direction: Direction = Direction.RX, channel: str = "0") -> float:
        """Return the IF or filter bandwidth.

        Args:
            direction (Direction): Signal path, ``Direction.RX`` or ``Direction.TX``.
            channel (str): Channel on that path, e.g. ``"0"``.

        Returns:
            float: Bandwidth in Hz.

        Raises:
            NotImplementedError: If the driver does not support bandwidth control.
        """
        raise NotImplementedError("Bandwidth control has not been implemented for this driver")

    def set_freq_correction(self, ppm: float, *, direction: Direction = Direction.RX, channel: str = "0") -> None:
        """Correct the reference oscillator's frequency error.

        Args:
            ppm (float): Correction in parts per million.
            direction (Direction): Signal path, ``Direction.RX`` or ``Direction.TX``.
            channel (str): Channel on that path, e.g. ``"0"``.

        Raises:
            NotImplementedError: If the driver does not support frequency correction.
        """
        raise NotImplementedError("Frequency correction has not been implemented for this driver")

    def get_freq_correction(self, *, direction: Direction = Direction.RX, channel: str = "0") -> float:
        """Return the reference oscillator correction.

        Args:
            direction (Direction): Signal path, ``Direction.RX`` or ``Direction.TX``.
            channel (str): Channel on that path, e.g. ``"0"``.

        Returns:
            float: Correction in parts per million.

        Raises:
            NotImplementedError: If the driver does not support frequency correction.
        """
        raise NotImplementedError("Frequency correction has not been implemented for this driver")

    def list_antennas(self, *, direction: Direction = Direction.RX, channel: str = "0") -> list[str]:
        """List the selectable antenna ports.

        Args:
            direction (Direction): Signal path, ``Direction.RX`` or ``Direction.TX``.
            channel (str): Channel on that path, e.g. ``"0"``.

        Returns:
            list[str]: Antenna port names.

        Raises:
            NotImplementedError: If the driver does not support antenna selection.
        """
        raise NotImplementedError("Antenna selection has not been implemented for this driver")

    def set_antenna(self, antenna: str, *, direction: Direction = Direction.RX, channel: str = "0") -> None:
        """Select an antenna port.

        Args:
            antenna (str): Port name, one of `list_antennas`.
            direction (Direction): Signal path, ``Direction.RX`` or ``Direction.TX``.
            channel (str): Channel on that path, e.g. ``"0"``.

        Raises:
            NotImplementedError: If the driver does not support antenna selection.
        """
        raise NotImplementedError("Antenna selection has not been implemented for this driver")

    def get_antenna(self, *, direction: Direction = Direction.RX, channel: str = "0") -> str:
        """Return the selected antenna port.

        Args:
            direction (Direction): Signal path, ``Direction.RX`` or ``Direction.TX``.
            channel (str): Channel on that path, e.g. ``"0"``.

        Returns:
            str: Port name.

        Raises:
            NotImplementedError: If the driver does not support antenna selection.
        """
        raise NotImplementedError("Antenna selection has not been implemented for this driver")

    # --- Optional: streaming ---

    def start(self, *, direction: Direction = Direction.RX, channels: Sequence[str] = ("0",)) -> None:
        """Begin continuous acquisition.

        One stream covers one direction and its whole channel set, chosen here.

        Args:
            direction (Direction): Signal path, ``Direction.RX`` or ``Direction.TX``.
            channels (Sequence[str]): Channels the stream covers.

        Raises:
            NotImplementedError: If the driver does not support streaming.
        """
        raise NotImplementedError("Streaming has not been implemented for this driver")

    def stop(self, *, direction: Direction = Direction.RX) -> None:
        """End a direction's stream and release its buffers.

        Args:
            direction (Direction): Stream to end.

        Raises:
            NotImplementedError: If the driver does not support streaming.
        """
        raise NotImplementedError("Streaming has not been implemented for this driver")

    def fetch_iq(self, n_samples: int) -> IQCapture:
        """Return the next contiguous block from the receive stream, blocking until it is available.

        Args:
            n_samples (int): Samples to return per channel.

        Returns:
            IQCapture: One row per channel the stream covers.

        Raises:
            ValueError: If ``n_samples`` exceeds what the stream can buffer.
            NotImplementedError: If the driver does not support streaming.
        """
        raise NotImplementedError("Streaming has not been implemented for this driver")

    def get_backlog(self) -> int:
        """Return how many samples are waiting to be fetched from the receive stream.

        Returns:
            int: Samples per channel.

        Raises:
            NotImplementedError: If the driver does not support streaming.
        """
        raise NotImplementedError("Streaming has not been implemented for this driver")

    # --- Optional: capability discovery ---

    def get_num_channels(self, *, direction: Direction = Direction.RX) -> int:
        """Return the number of signal paths in a direction.

        Args:
            direction (Direction): Direction to count.

        Returns:
            int: Signal paths. ``0`` means the radio cannot use that direction at all.

        Raises:
            NotImplementedError: If the driver does not support reporting channel counts.
        """
        raise NotImplementedError("Channel counts have not been implemented for this driver")

    def get_frequency_range(self, *, direction: Direction = Direction.RX, channel: str = "0") -> tuple[float, float]:
        """Return the lowest and highest tunable center frequency.

        Args:
            direction (Direction): Signal path, ``Direction.RX`` or ``Direction.TX``.
            channel (str): Channel on that path, e.g. ``"0"``.

        Returns:
            tuple[float, float]: ``(low, high)`` in Hz.

        Raises:
            NotImplementedError: If the driver does not support reporting a frequency range.
        """
        raise NotImplementedError("Frequency range has not been implemented for this driver")

    def get_sample_rate_range(self, *, direction: Direction = Direction.RX, channel: str = "0") -> tuple[float, float]:
        """Return the lowest and highest settable sample rate.

        Args:
            direction (Direction): Signal path, ``Direction.RX`` or ``Direction.TX``.
            channel (str): Channel on that path, e.g. ``"0"``.

        Returns:
            tuple[float, float]: ``(low, high)`` in samples per second.

        Raises:
            NotImplementedError: If the driver does not support reporting a sample rate range.
        """
        raise NotImplementedError("Sample rate range has not been implemented for this driver")


class InstroSDR(Instrument):
    """Software defined radio instrument that publishes IQ blocks and spectrum features.

    Each IQ block publishes as one `~instro.lib.types.Measurement` with paired ``.i`` and ``.q``
    channels per signal path, timestamped from the sample period the device reports.
    """

    def __init__(self, name: str, driver: SDRDriverBase, **kwargs: Any):
        """Initialize an InstroSDR.

        Args:
            name (str): Channel-name prefix for published data.
            driver (SDRDriverBase): Concrete vendor driver.
            **kwargs: Passed to `~instro.lib.instrument.Instrument`: ``publishers`` and
                ``background_config``, ``dataset_rid`` to attach a ``NominalCorePublisher``, and any
                other keyword as a default tag.

        Example:
            ::

                from instro.unstable.sdr import InstroSDR

                sdr = InstroSDR(name="sdr", driver=MyVendorSDR(...))  # any concrete SDRDriverBase
                sdr.open()
                try:
                    sdr.set_center_freq(100e6)
                    sdr.set_sample_rate(2.4e6)
                    iq = sdr.measure_iq(n_samples=4096)
                    spectrum = sdr.measure_spectrum(n_samples=4096)
                finally:
                    sdr.close()
        """
        super().__init__(name, **kwargs)
        self._driver = driver
        self._resource_lock = threading.RLock()
        self._last_iq_timestamp: int | None = None
        self._sample_period_warning_issued = False
        self._streams: dict[Direction, tuple[str, ...]] = {}

    def open(self) -> None:
        """Open the underlying driver."""
        self._driver.open()

    def close(self) -> None:
        """Stop the background daemon and close the driver.

        The driver is closed even if the daemon does not shut down cleanly; that failure is logged.
        """
        try:
            super().close()
        except Exception:
            logger.warning("SDR '%s' did not shut down cleanly; releasing the driver anyway", self.name, exc_info=True)
        self._driver.close()

    @property
    def driver(self) -> SDRDriverBase:
        """The underlying hardware driver.

        Returns:
            SDRDriverBase: The driver passed at construction.
        """
        return self._driver

    def _iq_timestamps(self, capture: IQCapture, t_read_ns: int) -> list[int]:
        """Nanosecond timestamps for one capture, spaced at the sample period the device reported."""
        period_ns = capture.sample_period_ns
        if period_ns <= 0:
            raise ValueError(f"driver reported a non-positive sample period: {period_ns}")
        if period_ns < 1.0 and not self._sample_period_warning_issued:
            self._sample_period_warning_issued = True
            logger.warning(
                "Sample period %s ns is shorter than the integer nanosecond timestamps can resolve; "
                "samples in each block will share timestamps.",
                period_ns,
            )

        # Round per index, not a pre-rounded period: 417 ns for 416.667 is 800 ppm of drift.
        offsets = np.round(np.arange(capture.samples.shape[1]) * period_ns).astype(np.int64)

        if capture.t0_ns is not None:
            t0 = capture.t0_ns
        else:
            # Backstamp: the read returned after the samples were taken. Buffered samples
            # continue the previous timeline
            t0 = t_read_ns - int(offsets[-1])
            if self._last_iq_timestamp is not None:
                # Unconditional floor: a dropout keeps its true width even when the clock ran ahead.
                continued = self._last_iq_timestamp + round((capture.dropped_samples + 1) * period_ns)
                t0 = max(t0, continued)

        timestamps: list[int] = (t0 + offsets).tolist()
        self._last_iq_timestamp = timestamps[-1]
        return timestamps

    def _iq_channel_data(self, capture: IQCapture) -> dict[str, list[float]]:
        """Paired ``.i``/``.q`` channels for every row of a capture."""
        data: dict[str, list[float]] = {}
        for row, channel in enumerate(capture.channels):
            path = self._path(Direction.RX, channel)
            data[f"{self.name}.{path}.i"] = capture.samples[row].real.tolist()
            data[f"{self.name}.{path}.q"] = capture.samples[row].imag.tolist()
        return data

    def _report_stream_health(self, capture: IQCapture, backlog: int, timestamp: int) -> None:
        """Publish backlog and overflow for a block that came off a stream."""
        if capture.overflow:
            logger.warning("SDR '%s' dropped samples before this block", self.name)
        self._publish_stream_health(backlog, capture.overflow, self._path(Direction.RX, capture.channels[0]), timestamp)

    def _read_iq_block(self, n_samples: int, channels: Sequence[str] | None) -> tuple[IQCapture, list[int]] | None:
        """Acquire one capture and resolve its timestamps. Publishes stream health when routed."""
        if n_samples <= 0:
            raise ValueError(f"n_samples must be positive, got {n_samples}")

        backlog: int | None = None
        with self._resource_lock:
            if Direction.RX in self._streams:
                # A stream block is consumed whole, so narrowing it discards the rest. Default to
                # the stream's own channels rather than silently dropping the ones not asked for.
                wanted = tuple(channels) if channels is not None else self._streams[Direction.RX]
                # Reading the device directly here would race the stream's own reader, so the
                # block comes off the stream and stays contiguous with surrounding fetches.
                capture = self._driver.fetch_iq(n_samples).select(wanted)
                t_read_ns = time.time_ns()
                backlog = self._driver.get_backlog()
            else:
                wanted = tuple(channels) if channels is not None else ("0",)
                capture = self._driver.read_iq(n_samples, channels=wanted)
                t_read_ns = time.time_ns()
            if capture.samples.size == 0:
                return None
            timestamps = self._iq_timestamps(capture, t_read_ns)

        # A read that consumed from a stream owes the same health report a fetch gives.
        if backlog is not None:
            self._report_stream_health(capture, backlog, timestamps[-1])
        return capture, timestamps

    @publish_measurement
    def measure_iq(
        self, n_samples: int = 1024, *, channels: Sequence[str] | None = None, **kwargs: Any
    ) -> Measurement | None:
        """Acquire one time-aligned IQ block and publish it as paired ``.i`` and ``.q`` channels.

        While a receive stream runs, the block is taken from that stream rather than the device,
        so it stays contiguous with the surrounding fetches and also publishes ``backlog`` and
        ``overflow``.

        Args:
            n_samples (int): Samples per channel.
            channels (Sequence[str] | None): Receive channels to read. ``None`` means every channel the
                running stream covers, or ``("0",)`` with no stream running.
            **kwargs: Tags added to the published value, on top of the instrument's default tags.

        Returns:
            Measurement | None: The published block, or ``None`` if the read returned no samples.

        Raises:
            ValueError: If ``n_samples`` is not positive.
        """
        block = self._read_iq_block(n_samples, channels)
        if block is None:
            return None
        capture, timestamps = block

        return Measurement(
            channel_data=dict(self._iq_channel_data(capture)),
            timestamps=timestamps,
            tags={**self.default_tags, **kwargs},
        )

    def start(
        self,
        *,
        direction: Direction = Direction.RX,
        channels: Sequence[str] = ("0",),
        background: bool = False,
        n_samples: int = 1024,
        publish_spectrum: bool = False,
    ) -> None:
        """Begin continuous acquisition.

        With ``background=True`` the daemon runs `fetch_iq` in a loop, which the blocking fetch
        paces, so blocks publish without a loop of your own.

        Args:
            direction (Direction): Signal path, ``Direction.RX`` or ``Direction.TX``.
            channels (Sequence[str]): Channels the stream covers.
            background (bool): Hand the fetch loop to the background daemon.
            n_samples (int): Samples per channel per block, when ``background`` is set.
            publish_spectrum (bool): Also publish spectrum features for each block, when
                ``background`` is set.
        """
        with self._resource_lock:
            self._driver.start(direction=direction, channels=tuple(channels))
            self._streams[direction] = tuple(channels)
        if background:
            self._define_background_daemon(n_samples, publish_spectrum)
            super().start()

    def _define_background_daemon(self, n_samples: int, publish_spectrum: bool) -> None:
        """Register the blocking fetch that paces the daemon, as InstroDAQ does for hardware timing."""
        # fetch_iq blocks until the radio has the samples, so it times the loop itself; any
        # interval here would sit between blocks and leave gaps in an otherwise gapless stream.
        self._background_config.interval = 0
        self._background_methods = [entry for entry in self._background_methods if entry[0] != self.fetch_iq]
        self.add_background_daemon_function(self.fetch_iq, n_samples=n_samples, publish_spectrum=publish_spectrum)

    @property
    def background_interval(self) -> float:
        """Background daemon loop period.

        ``start(background=True)`` sets it to ``0``: `fetch_iq` blocks until the radio has the
        samples, so it paces the loop itself.

        Returns:
            float: Loop period in seconds.
        """
        return self._background_config.interval

    @background_interval.setter
    def background_interval(self, seconds: float) -> None:
        """Ignore the requested interval and log a warning.

        An interval would only add gaps between blocks of an otherwise gapless stream. Change
        ``n_samples`` in `start` instead.

        Args:
            seconds (float): Requested loop period in seconds. Ignored.
        """
        logger.warning(
            "Ignoring background_interval=%s on SDR '%s': fetch_iq blocks until the radio has the "
            "samples, so it paces the daemon itself. Change n_samples in start() instead.",
            seconds,
            self.name,
        )

    def stop(self, **kwargs: Any) -> None:
        """Stop the background daemon and every running hardware stream.

        Args:
            **kwargs: Ignored.
        """
        super().stop()
        for direction in tuple(self._streams):
            with self._resource_lock:
                try:
                    self._driver.stop(direction=direction)
                finally:
                    # Drop the record either way: the driver's own guard is authoritative about
                    # whether it is streaming, and a stale entry routes later reads to a dead stream.
                    del self._streams[direction]

    @publish_measurement
    def _publish_stream_health(self, backlog: int, overflow: bool, path: str, timestamp: int) -> Measurement:
        """Publish buffer depth and dropped-sample state for one fetch."""
        return Measurement(
            channel_data={
                f"{self.name}.{path}.backlog": [float(backlog)],
                f"{self.name}.{path}.overflow": [float(overflow)],
            },
            timestamps=[timestamp],
            tags={**self.default_tags},
        )

    @publish_measurement
    def fetch_iq(self, n_samples: int = 1024, *, publish_spectrum: bool = False, **kwargs: Any) -> Measurement | None:
        """Return the next contiguous block from the running stream and publish it.

        Publishes the block as paired ``.i`` and ``.q`` channels for every channel the stream
        covers, plus ``backlog`` and ``overflow`` stream-health channels.

        Args:
            n_samples (int): Samples per channel.
            publish_spectrum (bool): Also publish spectrum features computed from this block,
                without consuming another.
            **kwargs: Tags added to the published value, on top of the instrument's default tags.

        Returns:
            Measurement | None: The published block, or ``None`` if the fetch returned no samples.

        Raises:
            ValueError: If ``n_samples`` is not positive.
        """
        if n_samples <= 0:
            raise ValueError(f"n_samples must be positive, got {n_samples}")

        with self._resource_lock:
            capture = self._driver.fetch_iq(n_samples)
            t_read_ns = time.time_ns()
            if capture.samples.size == 0:
                return None
            timestamps = self._iq_timestamps(capture, t_read_ns)
            backlog = self._driver.get_backlog()

        self._report_stream_health(capture, backlog, timestamps[-1])
        if publish_spectrum:
            # Derived from this block, so the spectrum costs an FFT rather than another block.
            self._publish_spectrum_features(capture, timestamps[-1])

        return Measurement(
            channel_data=dict(self._iq_channel_data(capture)),
            timestamps=timestamps,
            tags={**self.default_tags, **kwargs},
        )

    def get_backlog(self) -> int:
        """Return how many samples are waiting on the receive stream.

        Returns:
            int: Samples per channel.

        Raises:
            NotImplementedError: If the driver does not support streaming.
        """
        with self._resource_lock:
            return self._driver.get_backlog()

    def is_streaming(self, *, direction: Direction = Direction.RX) -> bool:
        """Report whether a stream is running.

        Args:
            direction (Direction): Direction to check.

        Returns:
            bool: ``True`` while a stream started through this instrument is running.
        """
        return direction in self._streams

    @staticmethod
    def _psd_from_capture(capture: IQCapture, channel: str) -> tuple[np.ndarray, np.ndarray]:
        """Hann-windowed PSD for one channel of a capture: absolute frequencies and linear PSD."""
        samples = capture.samples_for(channel)
        sample_rate_hz = 1e9 / capture.sample_period_ns
        window = np.hanning(len(samples))
        spectrum = np.fft.fftshift(np.fft.fft(samples * window))
        psd = np.abs(spectrum) ** 2 / (sample_rate_hz * np.sum(window**2))
        freqs = np.fft.fftshift(np.fft.fftfreq(len(samples), d=1.0 / sample_rate_hz))
        return freqs + capture.center_freq_for(channel), psd

    @staticmethod
    def _occupied_bandwidth(freqs: np.ndarray, psd: np.ndarray, fraction: float = 0.99) -> float:
        """Width of the band holding ``fraction`` of total power, split evenly across both tails."""
        total = float(psd.sum())
        if total <= 0:
            return 0.0
        cumulative = np.cumsum(psd) / total
        tail = (1.0 - fraction) / 2.0
        low = int(np.searchsorted(cumulative, tail))
        high = min(int(np.searchsorted(cumulative, 1.0 - tail)), len(freqs) - 1)
        return float(freqs[high] - freqs[low])

    def compute_psd(self, n_samples: int = 1024, *, channel: str = "0") -> tuple[np.ndarray, np.ndarray] | None:
        """Acquire one block and return one channel's power spectral density. Publishes nothing.

        Uses a Hann-windowed FFT. Frequencies are absolute, offset by the tuned center frequency.

        Args:
            n_samples (int): Samples to acquire, which sets the frequency resolution.
            channel (str): Receive channel to analyze.

        Returns:
            tuple[numpy.ndarray, numpy.ndarray] | None: ``(frequencies_hz, power_db)``, frequencies in
            Hz and power in dB relative to one squared sample unit per Hz. ``None`` if the read
            returned no samples.

        Raises:
            ValueError: If ``n_samples`` is not positive.
        """
        block = self._read_iq_block(n_samples, (channel,))
        if block is None:
            return None
        freqs, psd = self._psd_from_capture(block[0], channel)
        return freqs, 10.0 * np.log10(np.maximum(psd, np.finfo(float).tiny))

    @publish_measurement
    def measure_spectrum(
        self, n_samples: int = 1024, *, channels: Sequence[str] | None = None, **kwargs: Any
    ) -> Measurement | None:
        """Acquire one block and publish four scalar spectrum features per channel.

        Publishes ``spectrum.peak_power_db``, ``spectrum.peak_freq_hz`` (Hz),
        ``spectrum.mean_power_db``, and ``spectrum.occupied_bw_hz`` (Hz, the band holding 99% of
        total power). The spectrum array itself is not published; use `compute_psd` for it.

        Args:
            n_samples (int): Samples per channel.
            channels (Sequence[str] | None): Receive channels to analyze. ``None`` means every channel
                the running stream covers, or ``("0",)`` with no stream running.
            **kwargs: Tags added to the published value, on top of the instrument's default tags.

        Returns:
            Measurement | None: The published features, or ``None`` if the read returned no samples.

        Raises:
            ValueError: If ``n_samples`` is not positive.
        """
        block = self._read_iq_block(n_samples, channels)
        if block is None:
            return None
        capture, timestamps = block

        return Measurement(
            channel_data=dict(self._spectrum_channel_data(capture)),
            timestamps=[timestamps[-1]],
            tags={**self.default_tags, **kwargs},
        )

    def _spectrum_channel_data(self, capture: IQCapture) -> dict[str, list[float]]:
        """The four scalar spectrum features, per channel of a capture."""
        floor = np.finfo(float).tiny
        data: dict[str, list[float]] = {}
        for channel in capture.channels:
            freqs, psd = self._psd_from_capture(capture, channel)
            peak = int(np.argmax(psd))
            path = f"{self.name}.{self._path(Direction.RX, channel)}.spectrum"
            data[f"{path}.peak_power_db"] = [float(10.0 * np.log10(max(psd[peak], floor)))]
            data[f"{path}.peak_freq_hz"] = [float(freqs[peak])]
            data[f"{path}.mean_power_db"] = [float(10.0 * np.log10(max(psd.mean(), floor)))]
            data[f"{path}.occupied_bw_hz"] = [self._occupied_bandwidth(freqs, psd)]
        return data

    @publish_measurement
    def _publish_spectrum_features(self, capture: IQCapture, timestamp: int) -> Measurement:
        """Publish spectrum features for a block that was already fetched, without consuming another."""
        return Measurement(
            channel_data=dict(self._spectrum_channel_data(capture)),
            timestamps=[timestamp],
            tags={**self.default_tags},
        )

    @staticmethod
    def _path(direction: Direction, channel: str) -> str:
        """Descriptor prefix naming one signal path, e.g. ``rx0``."""
        return f"{direction.value}{channel}"

    @publish_measurement
    def _execute_measurement(
        self,
        driver_method: Callable[..., Any],
        descriptor: str,
        direction: Direction,
        channel: str,
        **kwargs: Any,
    ) -> Measurement:
        """Execute a driver read and return a Measurement for the value."""
        with self._resource_lock:
            val = driver_method(direction=direction, channel=channel)
            timestamp = time.time_ns()
        val = val.value if isinstance(val, Enum) else val
        return self._package_measurement(f"{self._path(direction, channel)}.{descriptor}", val, timestamp, **kwargs)

    @publish_command
    def _execute_command(
        self,
        driver_method: Callable[..., None],
        value: float | bool | str,
        descriptor: str,
        direction: Direction,
        channel: str,
        **kwargs: Any,
    ) -> Command:
        """Execute a driver write and return a Command for the written value."""
        with self._resource_lock:
            driver_method(value, direction=direction, channel=channel)
            timestamp = time.time_ns()
        return self._package_command(f"{self._path(direction, channel)}.{descriptor}.cmd", value, timestamp, **kwargs)

    def set_center_freq(
        self, frequency_hz: float, *, direction: Direction = Direction.RX, channel: str = "0", **kwargs: Any
    ) -> Command:
        """Set the RF center frequency and publish it as ``center_freq.cmd``.

        Args:
            frequency_hz (float): Center frequency in Hz.
            direction (Direction): Signal path, ``Direction.RX`` or ``Direction.TX``.
            channel (str): Channel on that path, e.g. ``"0"``.
            **kwargs: Tags added to the published value, on top of the instrument's default tags.

        Returns:
            Command: The published command.
        """
        return self._execute_command(
            self._driver.set_center_freq, float(frequency_hz), "center_freq", direction, channel, **kwargs
        )

    def get_center_freq(self, *, direction: Direction = Direction.RX, channel: str = "0", **kwargs: Any) -> Measurement:
        """Query the RF center frequency and publish it as ``center_freq``.

        Args:
            direction (Direction): Signal path, ``Direction.RX`` or ``Direction.TX``.
            channel (str): Channel on that path, e.g. ``"0"``.
            **kwargs: Tags added to the published value, on top of the instrument's default tags.

        Returns:
            Measurement: The published reading, in Hz.
        """
        return self._execute_measurement(self._driver.get_center_freq, "center_freq", direction, channel, **kwargs)

    def set_sample_rate(
        self, sample_rate_hz: float, *, direction: Direction = Direction.RX, channel: str = "0", **kwargs: Any
    ) -> Command:
        """Set the IQ sample rate and publish it as ``sample_rate.cmd``.

        Args:
            sample_rate_hz (float): Sample rate in samples per second.
            direction (Direction): Signal path, ``Direction.RX`` or ``Direction.TX``.
            channel (str): Channel on that path, e.g. ``"0"``.
            **kwargs: Tags added to the published value, on top of the instrument's default tags.

        Returns:
            Command: The published command.
        """
        return self._execute_command(
            self._driver.set_sample_rate, float(sample_rate_hz), "sample_rate", direction, channel, **kwargs
        )

    def get_sample_rate(self, *, direction: Direction = Direction.RX, channel: str = "0", **kwargs: Any) -> Measurement:
        """Query the IQ sample rate and publish it as ``sample_rate``.

        Args:
            direction (Direction): Signal path, ``Direction.RX`` or ``Direction.TX``.
            channel (str): Channel on that path, e.g. ``"0"``.
            **kwargs: Tags added to the published value, on top of the instrument's default tags.

        Returns:
            Measurement: The published reading, in samples per second.
        """
        return self._execute_measurement(self._driver.get_sample_rate, "sample_rate", direction, channel, **kwargs)

    def set_gain(
        self, gain_db: float, *, direction: Direction = Direction.RX, channel: str = "0", **kwargs: Any
    ) -> Command:
        """Set the gain and publish it as ``gain.cmd``.

        Args:
            gain_db (float): Gain in dB.
            direction (Direction): Signal path, ``Direction.RX`` or ``Direction.TX``.
            channel (str): Channel on that path, e.g. ``"0"``.
            **kwargs: Tags added to the published value, on top of the instrument's default tags.

        Returns:
            Command: The published command.
        """
        return self._execute_command(self._driver.set_gain, float(gain_db), "gain", direction, channel, **kwargs)

    def get_gain(self, *, direction: Direction = Direction.RX, channel: str = "0", **kwargs: Any) -> Measurement:
        """Query the gain and publish it as ``gain``.

        Args:
            direction (Direction): Signal path, ``Direction.RX`` or ``Direction.TX``.
            channel (str): Channel on that path, e.g. ``"0"``.
            **kwargs: Tags added to the published value, on top of the instrument's default tags.

        Returns:
            Measurement: The published reading, in dB.
        """
        return self._execute_measurement(self._driver.get_gain, "gain", direction, channel, **kwargs)

    def set_gain_mode(
        self, automatic: bool, *, direction: Direction = Direction.RX, channel: str = "0", **kwargs: Any
    ) -> Command:
        """Hand gain to the AGC or take manual control, and publish it as ``gain_mode.cmd``.

        Args:
            automatic (bool): ``True`` for AGC, ``False`` for manual gain.
            direction (Direction): Signal path, ``Direction.RX`` or ``Direction.TX``.
            channel (str): Channel on that path, e.g. ``"0"``.
            **kwargs: Tags added to the published value, on top of the instrument's default tags.

        Returns:
            Command: The published command.
        """
        return self._execute_command(
            self._driver.set_gain_mode, bool(automatic), "gain_mode", direction, channel, **kwargs
        )

    def get_gain_mode(self, *, direction: Direction = Direction.RX, channel: str = "0", **kwargs: Any) -> Measurement:
        """Query the gain mode and publish it as ``gain_mode``.

        Args:
            direction (Direction): Signal path, ``Direction.RX`` or ``Direction.TX``.
            channel (str): Channel on that path, e.g. ``"0"``.
            **kwargs: Tags added to the published value, on top of the instrument's default tags.

        Returns:
            Measurement: The published reading, ``True`` when AGC is in control.
        """
        return self._execute_measurement(self._driver.get_gain_mode, "gain_mode", direction, channel, **kwargs)

    def set_bandwidth(
        self, bandwidth_hz: float, *, direction: Direction = Direction.RX, channel: str = "0", **kwargs: Any
    ) -> Command:
        """Set the IF or filter bandwidth and publish it as ``bandwidth.cmd``.

        Args:
            bandwidth_hz (float): Bandwidth in Hz.
            direction (Direction): Signal path, ``Direction.RX`` or ``Direction.TX``.
            channel (str): Channel on that path, e.g. ``"0"``.
            **kwargs: Tags added to the published value, on top of the instrument's default tags.

        Returns:
            Command: The published command.
        """
        return self._execute_command(
            self._driver.set_bandwidth, float(bandwidth_hz), "bandwidth", direction, channel, **kwargs
        )

    def get_bandwidth(self, *, direction: Direction = Direction.RX, channel: str = "0", **kwargs: Any) -> Measurement:
        """Query the IF or filter bandwidth and publish it as ``bandwidth``.

        Args:
            direction (Direction): Signal path, ``Direction.RX`` or ``Direction.TX``.
            channel (str): Channel on that path, e.g. ``"0"``.
            **kwargs: Tags added to the published value, on top of the instrument's default tags.

        Returns:
            Measurement: The published reading, in Hz.
        """
        return self._execute_measurement(self._driver.get_bandwidth, "bandwidth", direction, channel, **kwargs)

    def set_freq_correction(
        self, ppm: float, *, direction: Direction = Direction.RX, channel: str = "0", **kwargs: Any
    ) -> Command:
        """Set the reference oscillator correction and publish it as ``freq_correction.cmd``.

        Args:
            ppm (float): Correction in parts per million.
            direction (Direction): Signal path, ``Direction.RX`` or ``Direction.TX``.
            channel (str): Channel on that path, e.g. ``"0"``.
            **kwargs: Tags added to the published value, on top of the instrument's default tags.

        Returns:
            Command: The published command.
        """
        return self._execute_command(
            self._driver.set_freq_correction, float(ppm), "freq_correction", direction, channel, **kwargs
        )

    def get_freq_correction(
        self, *, direction: Direction = Direction.RX, channel: str = "0", **kwargs: Any
    ) -> Measurement:
        """Query the reference oscillator correction and publish it as ``freq_correction``.

        Args:
            direction (Direction): Signal path, ``Direction.RX`` or ``Direction.TX``.
            channel (str): Channel on that path, e.g. ``"0"``.
            **kwargs: Tags added to the published value, on top of the instrument's default tags.

        Returns:
            Measurement: The published reading, in parts per million.
        """
        return self._execute_measurement(
            self._driver.get_freq_correction, "freq_correction", direction, channel, **kwargs
        )

    def set_antenna(
        self, antenna: str, *, direction: Direction = Direction.RX, channel: str = "0", **kwargs: Any
    ) -> Command:
        """Set the antenna port and publish it as ``antenna.cmd``.

        Args:
            antenna (str): Port name, one of the driver's `~SDRDriverBase.list_antennas`.
            direction (Direction): Signal path, ``Direction.RX`` or ``Direction.TX``.
            channel (str): Channel on that path, e.g. ``"0"``.
            **kwargs: Tags added to the published value, on top of the instrument's default tags.

        Returns:
            Command: The published command.
        """
        return self._execute_command(self._driver.set_antenna, antenna, "antenna", direction, channel, **kwargs)

    def get_antenna(self, *, direction: Direction = Direction.RX, channel: str = "0", **kwargs: Any) -> Measurement:
        """Query the selected antenna port and publish it as ``antenna``.

        Args:
            direction (Direction): Signal path, ``Direction.RX`` or ``Direction.TX``.
            channel (str): Channel on that path, e.g. ``"0"``.
            **kwargs: Tags added to the published value, on top of the instrument's default tags.

        Returns:
            Measurement: The published reading, the port name.
        """
        return self._execute_measurement(self._driver.get_antenna, "antenna", direction, channel, **kwargs)
