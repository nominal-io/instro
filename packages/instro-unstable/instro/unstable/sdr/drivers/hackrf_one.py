"""HackRF One driver wrapper around ``python_hackrf``.

A thin adapter in the same spirit as the RTL-SDR driver: the vendor SDK stays at the
driver boundary and the ``InstroSDR`` wrapper owns measurement publishing. Two traits of
libhackrf shape everything here. It exposes no getters at all, so every value the driver
writes is cached to serve the category's ``get_*`` contract; and it has no synchronous
read, so even a one-shot ``read_iq`` is served by starting the stream, collecting
callback buffers, and stopping again.
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


class HackRFOne(SDRDriverBase):
    """HackRF One radio. Connection params captured in ``__init__``; USB opens on ``open()``."""

    STREAM_BUFFER_SAMPLES: ClassVar[int] = 2_621_440
    FETCH_TIMEOUT_S: ClassVar[float] = 10.0

    FREQ_RANGE_HZ: ClassVar[tuple[float, float]] = (1e6, 6e9)
    SAMPLE_RATE_RANGE_HZ: ClassVar[tuple[float, float]] = (2e6, 20e6)

    # Gain stages, as (maximum dB, step dB). The hardware quantizes to these steps.
    LNA_GAIN: ClassVar[tuple[int, int]] = (40, 8)
    VGA_GAIN: ClassVar[tuple[int, int]] = (62, 2)
    TXVGA_GAIN: ClassVar[tuple[int, int]] = (47, 1)

    # start_rx leaves freq/rate/gain at whatever the last user left behind, so open()
    # programs a known state rather than inheriting an unknown one.
    DEFAULT_CENTER_FREQ_HZ: ClassVar[float] = 100e6
    # The bottom of the supported range: a Python RX callback is least likely to drop here.
    DEFAULT_SAMPLE_RATE_HZ: ClassVar[float] = 2e6
    DEFAULT_LNA_GAIN_DB: ClassVar[int] = 16
    DEFAULT_VGA_GAIN_DB: ClassVar[int] = 16
    DEFAULT_TXVGA_GAIN_DB: ClassVar[int] = 0

    def __init__(self, device_index: int = 0, *, serial_number: str | None = None):
        """``device_index`` picks among connected radios; ``serial_number`` addresses one directly."""
        self._device_index = device_index
        self._serial_number = serial_number
        self._device: Any = None
        self._pyhackrf: Any = None

        self._center_freq_hz = self.DEFAULT_CENTER_FREQ_HZ
        self._sample_rate_hz = self.DEFAULT_SAMPLE_RATE_HZ
        self._bandwidth_hz = 0.75 * self.DEFAULT_SAMPLE_RATE_HZ
        self._lna_gain_db = self.DEFAULT_LNA_GAIN_DB
        self._vga_gain_db = self.DEFAULT_VGA_GAIN_DB
        self._txvga_gain_db = self.DEFAULT_TXVGA_GAIN_DB
        self._amp_enabled = False
        self._bias_tee_enabled = False

        self._stream_lock = threading.Condition()
        self._chunks: deque[np.ndarray] = deque()
        self._buffered = 0
        self._dropped = 0
        self._streaming = False
        # An odd byte count would split an IQ pair across two callbacks and invert the
        # phase of every sample after it, so the stray byte waits for its partner.
        self._carry: np.ndarray | None = None

    def open(self) -> None:
        from python_hackrf.pylibhackrf import pyhackrf  # type: ignore[import-not-found]

        if self._device is not None:
            return

        pyhackrf.pyhackrf_init()
        if self._serial_number is not None:
            device = pyhackrf.pyhackrf_open_by_serial(self._serial_number)
        else:
            device = pyhackrf.pyhackrf_device_list_open(pyhackrf.pyhackrf_device_list(), self._device_index)
        if device is None:
            raise RuntimeError(f"no HackRF at device index {self._device_index}")

        self._device = device
        self._pyhackrf = pyhackrf
        try:
            self._apply_config()
        except Exception:
            # Holding a handle whose state never got programmed would make open() look
            # idempotent while leaving the radio unconfigured.
            self.close()
            raise

    def close(self) -> None:
        if self._streaming:
            try:
                self.stop()
            except Exception:
                logger.warning("could not stop the HackRF stream cleanly", exc_info=True)
        if self._device is not None:
            # Idempotent in the binding, and it drops the registered callbacks with it.
            self._device.pyhackrf_close()
            self._device = None
        self._pyhackrf = None
        with self._stream_lock:
            self._chunks.clear()
            self._buffered = 0
            self._carry = None
        # pyhackrf_exit() is library-global and refuses while any radio is open, so a second
        # HackRF in the same process would lose its library out from under it.

    def _apply_config(self) -> None:
        """Program the cached state onto a freshly opened radio."""
        # Rate first: libhackrf resets the baseband filter to 0.75 * rate whenever it changes.
        self.set_sample_rate(self._sample_rate_hz)
        self.set_center_freq(self._center_freq_hz)
        self.set_lna_gain(self._lna_gain_db)
        self.set_vga_gain(self._vga_gain_db)
        self.set_txvga_gain(self._txvga_gain_db)
        self.set_amp_enable(self._amp_enabled)
        self.set_bias_tee(self._bias_tee_enabled)

    # --- Tuning and rate. One PLL and one ADC serve both directions, so these are device-wide. ---

    def set_center_freq(self, frequency_hz: float, *, direction: Direction = Direction.RX, channel: str = "0") -> None:
        device = self._require_path(direction, channel)
        low, high = self.FREQ_RANGE_HZ
        if not low <= frequency_hz <= high:
            raise ValueError(
                f"center frequency {frequency_hz:,.0f} Hz is outside the HackRF's {low:,.0f}-{high:,.0f} Hz range"
            )
        device.pyhackrf_set_freq(int(frequency_hz))
        # ~50 Hz tuning resolution that libhackrf cannot report back, so this is the request.
        self._center_freq_hz = float(int(frequency_hz))

    def get_center_freq(self, *, direction: Direction = Direction.RX, channel: str = "0") -> float:
        self._require_path(direction, channel)
        return self._center_freq_hz

    def set_sample_rate(
        self, sample_rate_hz: float, *, direction: Direction = Direction.RX, channel: str = "0"
    ) -> None:
        device = self._require_path(direction, channel)
        low, high = self.SAMPLE_RATE_RANGE_HZ
        if not low <= sample_rate_hz <= high:
            raise ValueError(
                f"sample rate {sample_rate_hz:,.0f} Hz is outside the HackRF's {low:,.0f}-{high:,.0f} Hz range"
            )
        device.pyhackrf_set_sample_rate(float(sample_rate_hz))
        self._sample_rate_hz = float(sample_rate_hz)
        # The device silently returns the filter to its default here, so the cache follows it
        # rather than keeping a bandwidth the hardware has already discarded.
        self._bandwidth_hz = 0.75 * self._sample_rate_hz

    def get_sample_rate(self, *, direction: Direction = Direction.RX, channel: str = "0") -> float:
        self._require_path(direction, channel)
        return self._sample_rate_hz

    def set_bandwidth(self, bandwidth_hz: float, *, direction: Direction = Direction.RX, channel: str = "0") -> None:
        """Set the baseband filter, snapped to the nearest width the hardware actually has."""
        device = self._require_path(direction, channel)
        if bandwidth_hz <= 0:
            raise ValueError(f"bandwidth must be positive, got {bandwidth_hz}")
        # The filter has 16 discrete widths; the vendor helper picks the nearest one.
        snapped = int(self._pyhackrf.pyhackrf_compute_baseband_filter_bw(int(bandwidth_hz)))
        device.pyhackrf_set_baseband_filter_bandwidth(snapped)
        self._bandwidth_hz = float(snapped)

    def get_bandwidth(self, *, direction: Direction = Direction.RX, channel: str = "0") -> float:
        self._require_path(direction, channel)
        return self._bandwidth_hz

    # --- Gain. No single hardware gain figure exists, so rx spreads one over two stages. ---

    def set_gain(self, gain_db: float, *, direction: Direction = Direction.RX, channel: str = "0") -> None:
        """Distribute gain over the LNA (8 dB steps) then the VGA (2 dB steps); tx sets the TX VGA."""
        self._require_path(direction, channel)
        if direction is Direction.TX:
            self.set_txvga_gain(gain_db)
            return

        low, high = self.get_gain_range(direction=direction, channel=channel)
        if not low <= gain_db <= high:
            raise ValueError(f"rx gain {gain_db} dB is outside the HackRF's {low:g}-{high:g} dB range")
        lna_max, lna_step = self.LNA_GAIN
        lna = min(lna_max, int(gain_db // lna_step) * lna_step)
        self.set_lna_gain(lna)
        self.set_vga_gain(min(self.VGA_GAIN[0], gain_db - lna))

    def get_gain(self, *, direction: Direction = Direction.RX, channel: str = "0") -> float:
        """Sum of the quantized stage gains, excluding the separate on/off RF amp."""
        self._require_path(direction, channel)
        if direction is Direction.TX:
            return float(self._txvga_gain_db)
        return float(self._lna_gain_db + self._vga_gain_db)

    def get_gain_range(self, *, direction: Direction = Direction.RX, channel: str = "0") -> tuple[float, float]:
        self._require_path(direction, channel)
        if direction is Direction.TX:
            return 0.0, float(self.TXVGA_GAIN[0])
        return 0.0, float(self.LNA_GAIN[0] + self.VGA_GAIN[0])

    def set_lna_gain(self, gain_db: float) -> None:
        """Set the RX LNA (IF) gain, quantized down to a step the hardware has."""
        device = self._require_device()
        quantized = self._quantize(gain_db, *self.LNA_GAIN, "rx LNA gain")
        device.pyhackrf_set_lna_gain(quantized)
        self._lna_gain_db = quantized

    def set_vga_gain(self, gain_db: float) -> None:
        """Set the RX VGA (baseband) gain, quantized down to a step the hardware has."""
        device = self._require_device()
        quantized = self._quantize(gain_db, *self.VGA_GAIN, "rx VGA gain")
        device.pyhackrf_set_vga_gain(quantized)
        self._vga_gain_db = quantized

    def set_txvga_gain(self, gain_db: float) -> None:
        """Set the TX VGA (IF) gain."""
        device = self._require_device()
        quantized = self._quantize(gain_db, *self.TXVGA_GAIN, "tx VGA gain")
        device.pyhackrf_set_txvga_gain(quantized)
        self._txvga_gain_db = quantized

    def set_amp_enable(self, enabled: bool) -> None:
        """Switch the ~11 dB RF amplifier, shared by both directions."""
        device = self._require_device()
        device.pyhackrf_set_amp_enable(bool(enabled))
        self._amp_enabled = bool(enabled)

    def set_bias_tee(self, enabled: bool) -> None:
        """Switch the 3.3V antenna-port bias tee. The firmware drops it on return to idle."""
        device = self._require_device()
        device.pyhackrf_set_antenna_enable(bool(enabled))
        self._bias_tee_enabled = bool(enabled)

    @property
    def lna_gain_db(self) -> int:
        """Quantized RX LNA gain last written."""
        return self._lna_gain_db

    @property
    def vga_gain_db(self) -> int:
        """Quantized RX VGA gain last written."""
        return self._vga_gain_db

    @property
    def txvga_gain_db(self) -> int:
        """TX VGA gain last written."""
        return self._txvga_gain_db

    @property
    def amp_enabled(self) -> bool:
        """Whether the RF amplifier was last switched on."""
        return self._amp_enabled

    @property
    def bias_tee_enabled(self) -> bool:
        """Whether the bias tee was last switched on. The firmware may since have dropped it."""
        return self._bias_tee_enabled

    @staticmethod
    def _quantize(gain_db: float, maximum: int, step: int, label: str) -> int:
        """Floor a gain onto the hardware's step. The binding clamps silently; we refuse instead."""
        if not 0 <= gain_db <= maximum:
            raise ValueError(f"{label} {gain_db} dB is outside the HackRF's 0-{maximum} dB range")
        return int(gain_db // step) * step

    # --- Capability discovery ---

    def get_num_channels(self, *, direction: Direction = Direction.RX) -> int:
        """One path each way. The radio is half duplex, so only one of them runs at a time."""
        return 1

    def get_frequency_range(self, *, direction: Direction = Direction.RX, channel: str = "0") -> tuple[float, float]:
        self._check_path(direction, (channel,))
        return self.FREQ_RANGE_HZ

    def get_sample_rate_range(self, *, direction: Direction = Direction.RX, channel: str = "0") -> tuple[float, float]:
        self._check_path(direction, (channel,))
        return self.SAMPLE_RATE_RANGE_HZ

    # --- Acquisition ---

    def read_iq(self, n_samples: int, *, channels: Sequence[str] = ("0",)) -> IQCapture:
        """Read ``n_samples`` by running the stream for exactly as long as they take."""
        self._require_rx_stream(channels)
        if n_samples <= 0:
            raise ValueError(f"n_samples must be positive, got {n_samples}")
        if self._streaming:
            # A second start_rx on a streaming radio would fight the running transfer loop.
            raise RuntimeError("HackRFOne is streaming; use fetch_iq() or stop() first")

        # libhackrf has no synchronous read, so a one-shot is a stream held open just long enough.
        self.start(channels=channels)
        try:
            return self.fetch_iq(n_samples)
        finally:
            self.stop()

    def start(self, *, direction: Direction = Direction.RX, channels: Sequence[str] = ("0",)) -> None:
        device = self._require_rx_stream(channels)
        if direction is not Direction.RX:
            raise ValueError(f"HackRFOne can only stream rx, got {direction.value}")
        with self._stream_lock:
            if self._streaming:
                return
            self._chunks.clear()
            self._buffered = 0
            self._dropped = 0
            self._carry = None
            self._streaming = True

        # The callback has to be registered first: start_rx installs the binding's own
        # trampoline, which discards every block until it has somewhere to send them.
        try:
            device.set_rx_callback(self._on_samples)
            device.pyhackrf_start_rx()
        except Exception:
            with self._stream_lock:
                self._streaming = False
                self._stream_lock.notify_all()
            raise

    def stop(self, *, direction: Direction = Direction.RX) -> None:
        self._check_path(direction, ("0",))
        if not self._streaming:
            return

        with self._stream_lock:
            self._streaming = False
            # Wake a parked fetch now rather than leaving it to time out.
            self._stream_lock.notify_all()

        try:
            if self._device is not None:
                self._device.pyhackrf_stop_rx()
        finally:
            with self._stream_lock:
                self._chunks.clear()
                self._buffered = 0
                self._carry = None

    def fetch_iq(self, n_samples: int) -> IQCapture:
        self._require_rx_stream(("0",))
        if not self._streaming:
            raise RuntimeError("HackRFOne is not streaming; call start() first")
        if n_samples <= 0:
            raise ValueError(f"n_samples must be positive, got {n_samples}")
        if n_samples > self.STREAM_BUFFER_SAMPLES:
            # The callback evicts to stay inside the buffer, so the wait below could never end.
            raise ValueError(
                f"n_samples {n_samples:,} exceeds the {self.STREAM_BUFFER_SAMPLES:,}-sample stream buffer, "
                "so the wait can never finish; fetch less or raise STREAM_BUFFER_SAMPLES"
            )

        deadline = time.monotonic() + self.FETCH_TIMEOUT_S
        with self._stream_lock:
            while self._buffered < n_samples:
                if not self._streaming:
                    raise RuntimeError("HackRF stream stopped while waiting for samples")
                if not self._stream_lock.wait(timeout=max(0.0, deadline - time.monotonic())):
                    raise TimeoutError(f"timed out waiting for {n_samples} samples from the stream")
            samples = self._take_locked(n_samples)
            dropped, self._dropped = self._dropped, 0

        return self._capture(samples, dropped_samples=dropped)

    def get_backlog(self) -> int:
        self._require_rx_stream(("0",))
        with self._stream_lock:
            return self._buffered

    def _on_samples(self, _device: Any, buffer: Any, _buffer_length: int, valid_length: int) -> int:
        """Buffer one block from libhackrf's USB thread. Returning non-zero would end the stream."""
        try:
            chunk = self._to_complex(buffer, valid_length)
            if chunk.size:
                with self._stream_lock:
                    while self._buffered + len(chunk) > self.STREAM_BUFFER_SAMPLES and self._chunks:
                        lost = len(self._chunks.popleft())
                        self._buffered -= lost
                        self._dropped += lost
                    self._chunks.append(chunk)
                    self._buffered += len(chunk)
                    self._stream_lock.notify_all()
        except Exception:
            # An exception escaping here reaches C as a non-zero result and kills the stream.
            logger.exception("HackRF receive callback failed")
        return 0

    def _to_complex(self, buffer: Any, valid_length: int) -> np.ndarray:
        """Interleaved signed bytes to normalized complex samples. Caller is the USB thread."""
        # Past valid_length the block is uninitialized memory, so the slice is not optional.
        raw = np.asarray(buffer[:valid_length], dtype=np.int8)
        if self._carry is not None:
            raw = np.concatenate((self._carry, raw))
        pairs = raw.size // 2
        self._carry = raw[2 * pairs :].copy() if raw.size % 2 else None
        if not pairs:
            return np.empty(0, dtype=np.complex128)
        # 128 is the vendor's own divisor, giving samples in [-1.0, 1.0).
        return (raw[: 2 * pairs : 2] / 128.0 + 1j * (raw[1 : 2 * pairs : 2] / 128.0)).astype(np.complex128)

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

    def _capture(self, samples: np.ndarray, *, dropped_samples: int = 0) -> IQCapture:
        """Wrap one channel's samples as a capture, tagged with the state they were taken under."""
        return IQCapture(
            samples=samples.reshape(1, -1),
            sample_period_ns=1e9 / self._sample_rate_hz,
            channels=("0",),
            center_freq_hz=(self._center_freq_hz,),
            dropped_samples=dropped_samples,
        )

    def _check_path(self, direction: Direction, channels: Sequence[str]) -> None:
        """One receive and one transmit path, addressed as channel '0'."""
        if tuple(channels) != ("0",):
            raise ValueError(f"HackRFOne has only channel '0', got {direction.value} channels {tuple(channels)!r}")

    def _require_path(self, direction: Direction, channel: str) -> Any:
        self._check_path(direction, (channel,))
        return self._require_device()

    def _require_rx_stream(self, channels: Sequence[str]) -> Any:
        """Acquisition is receive-only, whatever the radio can do on transmit."""
        if tuple(channels) != ("0",):
            raise ValueError(f"HackRFOne has only rx channel '0', got channels {tuple(channels)!r}")
        return self._require_device()

    def _require_device(self) -> Any:
        if self._device is None:
            raise RuntimeError("HackRFOne driver is not open; call open() first")
        return self._device
