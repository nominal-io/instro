"""RTL-SDR driver wrapper around ``rtlsdr``.

This is intentionally a thin adapter: it keeps the vendor SDK dependency at the
driver boundary and exposes the minimal SDR semantics needed by the higher-level
instro interface. The driver does not own measurement publishing; the ``InstroSDR``
wrapper does that using the repo's shared ``Measurement`` objects.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import numpy as np

from instro.unstable.sdr.sdr import SDRDriverBase

logger = logging.getLogger(__name__)

from rtlsdr import RtlSdr  # type: ignore[import-not-found]


class RTLSDR(SDRDriverBase):
    """Thin wrapper around ``rtlsdr.RtlSdr``."""

    def __init__(self, index: int = 0, *args: Any, **kwargs: Any):
        if RtlSdr is None:
            raise ModuleNotFoundError("rtlsdr is required for the RTLSDR driver")

        self._device = RtlSdr(*args, **kwargs)
        if index != 0:
            try:
                self._device = RtlSdr(index=index, *args, **kwargs)
            except TypeError:
                self._device = RtlSdr(*args, **kwargs)
        self._center_freq_hz = float(self._device.center_freq)
        self._sample_rate_hz = float(self._device.sample_rate)
        self._gain_db = float(self._device.gain)
        self._bandwidth_hz = float(getattr(self._device, "bandwidth", self._sample_rate_hz))

    def open(self) -> None:
        """Open the underlying device if it has not already been initialized."""
        if self._device is not None:
            logger.info("RTLSDR device already initialized")

    def close(self) -> None:
        """Close the device handle if supported by the vendor library."""
        if hasattr(self._device, "close"):
            self._device.close()

    def set_center_freq(self, frequency_hz: float) -> None:
        self._center_freq_hz = float(frequency_hz)
        self._device.center_freq = self._center_freq_hz

    def get_center_freq(self) -> float:
        return float(self._device.center_freq)

    def set_sample_rate(self, sample_rate_hz: float) -> None:
        self._sample_rate_hz = float(sample_rate_hz)
        self._device.sample_rate = self._sample_rate_hz

    def get_sample_rate(self) -> float:
        return float(self._device.sample_rate)

    def set_gain(self, gain_db: float) -> None:
        self._gain_db = float(gain_db)
        self._device.gain = self._gain_db

    def get_gain(self) -> float:
        return float(self._device.gain)

    def set_bandwidth(self, bandwidth_hz: float) -> None:
        self._bandwidth_hz = float(bandwidth_hz)
        if hasattr(self._device, "bandwidth"):
            self._device.bandwidth = self._bandwidth_hz

    def get_bandwidth(self) -> float:
        if hasattr(self._device, "bandwidth"):
            return float(self._device.bandwidth)
        return self._bandwidth_hz

    def read_iq(self, n_samples: int) -> np.ndarray:
        """Read ``n_samples`` complex IQ pairs from the device."""
        data = self._device.read_samples(n_samples)
        return np.asarray(data, dtype=np.complex128)
