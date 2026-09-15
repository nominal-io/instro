"""RTL-SDR driver wrapper around ``rtlsdr``.

This is intentionally a thin adapter: it keeps the vendor SDK dependency at the
driver boundary and exposes the minimal SDR semantics needed by the higher-level
instro interface. The driver does not own measurement publishing; the ``InstroSDR``
wrapper does that using the repo's shared ``Measurement`` objects.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Sequence
from typing import Any, ClassVar

import numpy as np

from instro.unstable.sdr.sdr import IQCapture, SDRDriverBase
from instro.unstable.sdr.types import Direction

logger = logging.getLogger(__name__)


class RTLSDR(SDRDriverBase):
    """RTL-SDR dongle. Connection params captured in ``__init__``; USB opens on ``open()``."""

    # librtlsdr moves whole 512-byte USB blocks; a short read makes pyrtlsdr close the device.
    READ_GRANULARITY: ClassVar[int] = 256
    STREAM_CHUNK_SAMPLES: ClassVar[int] = 131072
    # Roughly a second at 2.4 MSa/s. Past this the oldest samples go and overflow is flagged.
    STREAM_BUFFER_SAMPLES: ClassVar[int] = 2_621_440
    FETCH_TIMEOUT_S: ClassVar[float] = 10.0
    STREAM_STOP_TIMEOUT_S: ClassVar[float] = 5.0

    def __init__(self, device_index: int = 0, **kwargs: Any):
        """``device_index`` picks among connected dongles; ``kwargs`` pass through to ``RtlSdr``."""
        self._device_index = device_index
        self._kwargs = kwargs
        self._device: Any = None
        self._stream_lock = threading.Condition()
        self._stream_thread: threading.Thread | None = None
        self._chunks: deque[np.ndarray] = deque()
        self._buffered = 0
        self._dropped = 0
        self._streaming = False

    def open(self) -> None:
        from rtlsdr import RtlSdr  # type: ignore[import-untyped]

        if self._device is None:
            self._device = RtlSdr(device_index=self._device_index, **self._kwargs)

    def close(self) -> None:
        if self._stream_thread is not None:
            try:
                self.stop()
            except Exception:
                logger.warning("could not stop the RTLSDR stream cleanly", exc_info=True)
        if self._device is not None:
            self._device.close()
            self._device = None
        self._stream_thread = None

    def set_center_freq(self, frequency_hz: float, *, direction: Direction = Direction.RX, channel: str = "0") -> None:
        self._require_rx(direction, channel).center_freq = float(frequency_hz)

    def get_center_freq(self, *, direction: Direction = Direction.RX, channel: str = "0") -> float:
        return float(self._require_rx(direction, channel).center_freq)

    def set_sample_rate(
        self, sample_rate_hz: float, *, direction: Direction = Direction.RX, channel: str = "0"
    ) -> None:
        self._require_rx(direction, channel).sample_rate = float(sample_rate_hz)

    def get_sample_rate(self, *, direction: Direction = Direction.RX, channel: str = "0") -> float:
        return float(self._require_rx(direction, channel).sample_rate)

    def set_gain(self, gain_db: float, *, direction: Direction = Direction.RX, channel: str = "0") -> None:
        self._require_rx(direction, channel).gain = float(gain_db)

    def get_gain(self, *, direction: Direction = Direction.RX, channel: str = "0") -> float:
        return float(self._require_rx(direction, channel).gain)

    def set_gain_mode(self, automatic: bool, *, direction: Direction = Direction.RX, channel: str = "0") -> None:
        self._require_rx(direction, channel).set_manual_gain_enabled(not automatic)

    def get_gain_range(self, *, direction: Direction = Direction.RX, channel: str = "0") -> tuple[float, float]:
        gains = self._require_rx(direction, channel).valid_gains_db
        return float(min(gains)), float(max(gains))

    def set_bandwidth(self, bandwidth_hz: float, *, direction: Direction = Direction.RX, channel: str = "0") -> None:
        self._require_rx(direction, channel).bandwidth = float(bandwidth_hz)

    def get_bandwidth(self, *, direction: Direction = Direction.RX, channel: str = "0") -> float:
        return float(self._require_rx(direction, channel).bandwidth)

    def set_freq_correction(self, ppm: float, *, direction: Direction = Direction.RX, channel: str = "0") -> None:
        self._require_rx(direction, channel).freq_correction = int(ppm)

    def get_freq_correction(self, *, direction: Direction = Direction.RX, channel: str = "0") -> float:
        return float(self._require_rx(direction, channel).freq_correction)

    def get_num_channels(self, *, direction: Direction = Direction.RX) -> int:
        return 1 if direction is Direction.RX else 0

    def read_iq(self, n_samples: int, *, channels: Sequence[str] = ("0",)) -> IQCapture:
        """Read ``n_samples`` complex IQ pairs from the device."""
        if n_samples <= 0 or n_samples % self.READ_GRANULARITY:
            raise ValueError(f"n_samples must be a positive multiple of {self.READ_GRANULARITY}, got {n_samples}")
        device = self._require_channels(Direction.RX, channels)
        if self._stream_thread is not None:
            # librtlsdr cannot serve a sync read while its async reader owns the handle.
            raise RuntimeError("RTLSDR is streaming; use fetch_iq() or stop() first")
        samples = np.asarray(device.read_samples(n_samples), dtype=np.complex128)
        # No sample clock of its own, so t0 stays unset and the host anchors the block.
        return self._capture(device, samples)

    def start(self, *, direction: Direction = Direction.RX, channels: Sequence[str] = ("0",)) -> None:
        device = self._require_channels(direction, channels)
        with self._stream_lock:
            if self._stream_thread is not None and self._stream_thread.is_alive():
                return
            self._chunks.clear()
            self._buffered = 0
            self._dropped = 0
            self._streaming = True

        self._stream_thread = threading.Thread(target=self._stream_worker, args=(device,), daemon=True)
        self._stream_thread.start()

    def stop(self, *, direction: Direction = Direction.RX) -> None:
        self._check_path(direction, ("0",))
        thread = self._stream_thread
        if thread is None:
            return

        with self._stream_lock:
            self._streaming = False
            self._stream_lock.notify_all()

        # The handle may already be gone, but the stream still has to come down.
        if self._device is not None:
            try:
                # cancel_read_async unblocks read_samples_async inside the worker.
                self._device.cancel_read_async()
            except Exception:
                logger.warning("RTLSDR async reader could not be cancelled", exc_info=True)

        thread.join(timeout=self.STREAM_STOP_TIMEOUT_S)
        with self._stream_lock:
            self._chunks.clear()
            self._buffered = 0
        if thread.is_alive():
            # Keep the thread recorded so start() refuses to open a second reader on one handle.
            logger.error("RTLSDR stream worker still running after %ss", self.STREAM_STOP_TIMEOUT_S)
            return
        self._stream_thread = None

    def fetch_iq(self, n_samples: int) -> IQCapture:
        device = self._require_channels(Direction.RX, ("0",))
        if self._stream_thread is None:
            raise RuntimeError("RTLSDR is not streaming; call start() first")
        if n_samples <= 0:
            raise ValueError(f"n_samples must be positive, got {n_samples}")
        if n_samples > self.STREAM_BUFFER_SAMPLES:
            # The reader evicts to stay inside the buffer, so the wait below could never end.
            raise ValueError(
                f"n_samples {n_samples:,} exceeds the {self.STREAM_BUFFER_SAMPLES:,}-sample stream buffer, "
                "so the wait can never finish; fetch less or raise STREAM_BUFFER_SAMPLES"
            )

        deadline = time.monotonic() + self.FETCH_TIMEOUT_S
        with self._stream_lock:
            while self._buffered < n_samples:
                if not self._streaming:
                    raise RuntimeError("RTLSDR stream stopped while waiting for samples")
                if not self._stream_lock.wait(timeout=max(0.0, deadline - time.monotonic())):
                    raise TimeoutError(f"timed out waiting for {n_samples} samples from the stream")
            samples = self._take_locked(n_samples)
            dropped, self._dropped = self._dropped, 0

        return self._capture(device, samples, dropped_samples=dropped)

    def get_backlog(self) -> int:
        self._require_channels(Direction.RX, ("0",))
        with self._stream_lock:
            return self._buffered

    def _stream_worker(self, device: Any) -> None:
        """Run librtlsdr's async reader, handing each chunk to the buffer."""
        try:
            device.read_samples_async(self._on_samples, self.STREAM_CHUNK_SAMPLES)
        except Exception:
            logger.exception("RTLSDR stream worker stopped")
        finally:
            with self._stream_lock:
                self._streaming = False
                self._stream_lock.notify_all()

    def _on_samples(self, samples: Any, _context: Any) -> None:
        """Buffer one chunk from librtlsdr, dropping the oldest rather than blocking the USB reader."""
        chunk = np.asarray(samples, dtype=np.complex128)
        with self._stream_lock:
            while self._buffered + len(chunk) > self.STREAM_BUFFER_SAMPLES and self._chunks:
                lost = len(self._chunks.popleft())
                self._buffered -= lost
                self._dropped += lost
            self._chunks.append(chunk)
            self._buffered += len(chunk)
            self._stream_lock.notify_all()

    def _take_locked(self, n_samples: int) -> np.ndarray:
        """Remove and return ``n_samples`` from the front of the buffer. Caller holds the lock."""
        taken: list[np.ndarray] = []
        remaining = n_samples
        while remaining:
            chunk = self._chunks[0]
            if len(chunk) <= remaining:
                taken.append(self._chunks.popleft())
                remaining -= len(chunk)
            else:
                taken.append(chunk[:remaining])
                self._chunks[0] = chunk[remaining:]
                remaining = 0
        self._buffered -= n_samples
        return np.concatenate(taken)

    def _capture(self, device: Any, samples: np.ndarray, *, dropped_samples: int = 0) -> IQCapture:
        """Wrap one channel's samples as a capture, tagged with the state they were taken under."""
        return IQCapture(
            samples=samples.reshape(1, -1),
            sample_period_ns=1e9 / float(device.sample_rate),
            channels=("0",),
            center_freq_hz=(float(device.center_freq),),
            dropped_samples=dropped_samples,
        )

    def _check_path(self, direction: Direction, channels: Sequence[str]) -> None:
        """An RTL-SDR is receive-only with a single signal path."""
        if direction is not Direction.RX or tuple(channels) != ("0",):
            raise ValueError(f"RTLSDR has only rx channel '0', got {direction.value} channels {tuple(channels)!r}")

    def _require_channels(self, direction: Direction, channels: Sequence[str]) -> Any:
        self._check_path(direction, channels)
        return self._require_device()

    def _require_rx(self, direction: Direction, channel: str) -> Any:
        """Config methods address a single channel."""
        return self._require_channels(direction, (channel,))

    def _require_device(self) -> Any:
        # pyrtlsdr closes on a transport error; reading through the stale handle segfaults.
        if self._device is not None and not getattr(self._device, "device_opened", True):
            self._device = None
        if self._device is None:
            raise RuntimeError("RTLSDR driver is not open; call open() first")
        return self._device
