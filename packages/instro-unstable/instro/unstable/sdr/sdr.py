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
    """One time-aligned block of IQ samples across one or more channels of a signal path."""

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
        """Whether any samples were dropped before this block."""
        return self.dropped_samples > 0

    def samples_for(self, channel: str) -> np.ndarray:
        """Row of samples belonging to ``channel``."""
        return self.samples[self.channels.index(channel)]

    def center_freq_for(self, channel: str) -> float:
        """Frequency ``channel`` was tuned to when these samples were taken."""
        return self.center_freq_hz[self.channels.index(channel)]

    def select(self, channels: Sequence[str]) -> "IQCapture":
        """Narrow this capture to ``channels``, keeping the shared timebase."""
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
    """Vendor SDR driver contract."""

    # --- Required ---

    @abc.abstractmethod
    def open(self) -> None:
        """Open the underlying transport or SDR handle."""

    @abc.abstractmethod
    def close(self) -> None:
        """Close the underlying transport or SDR handle."""

    @abc.abstractmethod
    def set_center_freq(self, frequency_hz: float, *, direction: Direction = Direction.RX, channel: str = "0") -> None:
        """Set the RF center frequency in Hz."""

    @abc.abstractmethod
    def get_center_freq(self, *, direction: Direction = Direction.RX, channel: str = "0") -> float:
        """Get the RF center frequency in Hz."""

    @abc.abstractmethod
    def set_sample_rate(
        self, sample_rate_hz: float, *, direction: Direction = Direction.RX, channel: str = "0"
    ) -> None:
        """Set the sample rate in samples per second."""

    @abc.abstractmethod
    def get_sample_rate(self, *, direction: Direction = Direction.RX, channel: str = "0") -> float:
        """Get the sample rate in samples per second."""

    @abc.abstractmethod
    def read_iq(self, n_samples: int, *, channels: Sequence[str] = ("0",)) -> IQCapture:
        """Read one time-aligned receive block across ``channels``, with the timebase it was taken on."""

    # --- Optional: gain ---

    def set_gain(self, gain_db: float, *, direction: Direction = Direction.RX, channel: str = "0") -> None:
        """Set the overall gain in dB. Override if the radio exposes a single gain figure."""
        raise NotImplementedError("Gain control has not been implemented for this driver")

    def get_gain(self, *, direction: Direction = Direction.RX, channel: str = "0") -> float:
        """Get the overall gain in dB."""
        raise NotImplementedError("Gain control has not been implemented for this driver")

    def set_gain_mode(self, automatic: bool, *, direction: Direction = Direction.RX, channel: str = "0") -> None:
        """Hand gain to the radio's AGC (``True``) or take manual control (``False``)."""
        raise NotImplementedError("Gain mode has not been implemented for this driver")

    def get_gain_mode(self, *, direction: Direction = Direction.RX, channel: str = "0") -> bool:
        """Report whether the AGC is in control. Many radios cannot read this back."""
        raise NotImplementedError("Gain mode readback has not been implemented for this driver")

    def get_gain_range(self, *, direction: Direction = Direction.RX, channel: str = "0") -> tuple[float, float]:
        """Lowest and highest settable gain in dB."""
        raise NotImplementedError("Gain range has not been implemented for this driver")

    # --- Optional: front end ---

    def set_bandwidth(self, bandwidth_hz: float, *, direction: Direction = Direction.RX, channel: str = "0") -> None:
        """Set the IF or filter bandwidth in Hz. Some radios tie this to the sample rate."""
        raise NotImplementedError("Bandwidth control has not been implemented for this driver")

    def get_bandwidth(self, *, direction: Direction = Direction.RX, channel: str = "0") -> float:
        """Get the IF or filter bandwidth in Hz."""
        raise NotImplementedError("Bandwidth control has not been implemented for this driver")

    def set_freq_correction(self, ppm: float, *, direction: Direction = Direction.RX, channel: str = "0") -> None:
        """Correct the reference oscillator error in parts per million."""
        raise NotImplementedError("Frequency correction has not been implemented for this driver")

    def get_freq_correction(self, *, direction: Direction = Direction.RX, channel: str = "0") -> float:
        """Get the reference oscillator correction in parts per million."""
        raise NotImplementedError("Frequency correction has not been implemented for this driver")

    def list_antennas(self, *, direction: Direction = Direction.RX, channel: str = "0") -> list[str]:
        """Names of the selectable antenna ports."""
        raise NotImplementedError("Antenna selection has not been implemented for this driver")

    def set_antenna(self, antenna: str, *, direction: Direction = Direction.RX, channel: str = "0") -> None:
        """Select an antenna port by name."""
        raise NotImplementedError("Antenna selection has not been implemented for this driver")

    def get_antenna(self, *, direction: Direction = Direction.RX, channel: str = "0") -> str:
        """Name of the selected antenna port."""
        raise NotImplementedError("Antenna selection has not been implemented for this driver")

    # --- Optional: streaming ---

    def start(self, *, direction: Direction = Direction.RX, channels: Sequence[str] = ("0",)) -> None:
        """Begin continuous acquisition; one stream covers one direction and the whole channel set."""
        raise NotImplementedError("Streaming has not been implemented for this driver")

    def stop(self, *, direction: Direction = Direction.RX) -> None:
        """End this direction's stream and release its buffers."""
        raise NotImplementedError("Streaming has not been implemented for this driver")

    def fetch_iq(self, n_samples: int) -> IQCapture:
        """Block until ``n_samples`` are available; raise ``ValueError`` if more than the buffer holds."""
        raise NotImplementedError("Streaming has not been implemented for this driver")

    def get_backlog(self) -> int:
        """Samples per channel already captured and waiting to be fetched from the receive stream."""
        raise NotImplementedError("Streaming has not been implemented for this driver")

    # --- Optional: capability discovery ---

    def get_num_channels(self, *, direction: Direction = Direction.RX) -> int:
        """Number of signal paths in ``direction``. Zero means the radio cannot do it at all."""
        raise NotImplementedError("Channel counts have not been implemented for this driver")

    def get_frequency_range(self, *, direction: Direction = Direction.RX, channel: str = "0") -> tuple[float, float]:
        """Lowest and highest tunable center frequency in Hz."""
        raise NotImplementedError("Frequency range has not been implemented for this driver")

    def get_sample_rate_range(self, *, direction: Direction = Direction.RX, channel: str = "0") -> tuple[float, float]:
        """Lowest and highest settable sample rate in samples per second."""
        raise NotImplementedError("Sample rate range has not been implemented for this driver")


class InstroSDR(Instrument):
    """Higher-level SDR wrapper that publishes buffered IQ measurements."""

    def __init__(self, name: str, driver: SDRDriverBase, **kwargs: Any):
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
        """Close the underlying driver and stop the daemon; the driver is released regardless."""
        try:
            super().close()
        except Exception:
            logger.warning("SDR '%s' did not shut down cleanly; releasing the driver anyway", self.name, exc_info=True)
        self._driver.close()

    @property
    def driver(self) -> SDRDriverBase:
        """Return the underlying hardware driver."""
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
        """One time-aligned IQ block as paired ``.i``/``.q``, defaulting to the stream's channels."""
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
        """Begin continuous acquisition; ``background`` hands the ``n_samples`` fetch loop to the daemon."""
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
        """Daemon loop period (s). Always 0 while streaming: the blocking fetch sets the pace."""
        return self._background_config.interval

    @background_interval.setter
    def background_interval(self, seconds: float) -> None:
        """Ignored -- fetch_iq blocks, so an interval would only add gaps between blocks."""
        logger.warning(
            "Ignoring background_interval=%s on SDR '%s': fetch_iq blocks until the radio has the "
            "samples, so it paces the daemon itself. Change n_samples in start() instead.",
            seconds,
            self.name,
        )

    def stop(self, **kwargs: Any) -> None:
        """Stop the background daemon and every running hardware stream."""
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
        """Return the next contiguous block from the running stream, across every channel it covers."""
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
        """Samples per channel waiting on the receive stream."""
        with self._resource_lock:
            return self._driver.get_backlog()

    def is_streaming(self, *, direction: Direction = Direction.RX) -> bool:
        """Whether a stream is running on ``direction``."""
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
        """Acquire a block and return one channel's ``(frequencies_hz, power_db)``. Publishes nothing."""
        block = self._read_iq_block(n_samples, (channel,))
        if block is None:
            return None
        freqs, psd = self._psd_from_capture(block[0], channel)
        return freqs, 10.0 * np.log10(np.maximum(psd, np.finfo(float).tiny))

    @publish_measurement
    def measure_spectrum(
        self, n_samples: int = 1024, *, channels: Sequence[str] | None = None, **kwargs: Any
    ) -> Measurement | None:
        """Publish scalar spectrum features per channel, defaulting to the stream's channels."""
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
        """Set RF center frequency and publish the command."""
        return self._execute_command(
            self._driver.set_center_freq, float(frequency_hz), "center_freq", direction, channel, **kwargs
        )

    def get_center_freq(self, *, direction: Direction = Direction.RX, channel: str = "0", **kwargs: Any) -> Measurement:
        """Query the current RF center frequency in Hz."""
        return self._execute_measurement(self._driver.get_center_freq, "center_freq", direction, channel, **kwargs)

    def set_sample_rate(
        self, sample_rate_hz: float, *, direction: Direction = Direction.RX, channel: str = "0", **kwargs: Any
    ) -> Command:
        """Set the sample rate and publish the command."""
        return self._execute_command(
            self._driver.set_sample_rate, float(sample_rate_hz), "sample_rate", direction, channel, **kwargs
        )

    def get_sample_rate(self, *, direction: Direction = Direction.RX, channel: str = "0", **kwargs: Any) -> Measurement:
        """Query the current sample rate in samples per second."""
        return self._execute_measurement(self._driver.get_sample_rate, "sample_rate", direction, channel, **kwargs)

    def set_gain(
        self, gain_db: float, *, direction: Direction = Direction.RX, channel: str = "0", **kwargs: Any
    ) -> Command:
        """Set the gain in dB and publish the command."""
        return self._execute_command(self._driver.set_gain, float(gain_db), "gain", direction, channel, **kwargs)

    def get_gain(self, *, direction: Direction = Direction.RX, channel: str = "0", **kwargs: Any) -> Measurement:
        """Query the current gain in dB."""
        return self._execute_measurement(self._driver.get_gain, "gain", direction, channel, **kwargs)

    def set_gain_mode(
        self, automatic: bool, *, direction: Direction = Direction.RX, channel: str = "0", **kwargs: Any
    ) -> Command:
        """Hand gain to the radio's AGC or take manual control, and publish the command."""
        return self._execute_command(
            self._driver.set_gain_mode, bool(automatic), "gain_mode", direction, channel, **kwargs
        )

    def get_gain_mode(self, *, direction: Direction = Direction.RX, channel: str = "0", **kwargs: Any) -> Measurement:
        """Query whether the AGC is in control."""
        return self._execute_measurement(self._driver.get_gain_mode, "gain_mode", direction, channel, **kwargs)

    def set_bandwidth(
        self, bandwidth_hz: float, *, direction: Direction = Direction.RX, channel: str = "0", **kwargs: Any
    ) -> Command:
        """Set the bandwidth in Hz and publish the command."""
        return self._execute_command(
            self._driver.set_bandwidth, float(bandwidth_hz), "bandwidth", direction, channel, **kwargs
        )

    def get_bandwidth(self, *, direction: Direction = Direction.RX, channel: str = "0", **kwargs: Any) -> Measurement:
        """Query the current IF or filter bandwidth in Hz."""
        return self._execute_measurement(self._driver.get_bandwidth, "bandwidth", direction, channel, **kwargs)

    def set_freq_correction(
        self, ppm: float, *, direction: Direction = Direction.RX, channel: str = "0", **kwargs: Any
    ) -> Command:
        """Set the reference oscillator correction in ppm and publish the command."""
        return self._execute_command(
            self._driver.set_freq_correction, float(ppm), "freq_correction", direction, channel, **kwargs
        )

    def get_freq_correction(
        self, *, direction: Direction = Direction.RX, channel: str = "0", **kwargs: Any
    ) -> Measurement:
        """Query the reference oscillator correction in ppm."""
        return self._execute_measurement(
            self._driver.get_freq_correction, "freq_correction", direction, channel, **kwargs
        )

    def set_antenna(
        self, antenna: str, *, direction: Direction = Direction.RX, channel: str = "0", **kwargs: Any
    ) -> Command:
        """Select an antenna port by name and publish the command."""
        return self._execute_command(self._driver.set_antenna, antenna, "antenna", direction, channel, **kwargs)

    def get_antenna(self, *, direction: Direction = Direction.RX, channel: str = "0", **kwargs: Any) -> Measurement:
        """Query the selected antenna port."""
        return self._execute_measurement(self._driver.get_antenna, "antenna", direction, channel, **kwargs)
