"""Pure in-memory simulated VNA driver for higher-level wrapper tests."""

from __future__ import annotations

import numpy as np

from instro.unstable.vna.vna import VNADriverBase

DEFAULT_FREQ_START_HZ = 1_000_000_000.0
DEFAULT_FREQ_STOP_HZ = 2_000_000_000.0
DEFAULT_NPOINTS = 5
DEFAULT_NPORTS = 2


class SimulatedVNA(VNADriverBase):
    """Deterministic sweep generator returning the expected VNA data."""

    def __init__(
        self,
        *,
        start_hz: float = DEFAULT_FREQ_START_HZ,
        stop_hz: float = DEFAULT_FREQ_STOP_HZ,
        npoints: int = DEFAULT_NPOINTS,
        nports: int = DEFAULT_NPORTS,
    ) -> None:
        self._start_hz = float(start_hz)
        self._stop_hz = float(stop_hz)
        self._npoints = int(npoints)
        self._nports = int(nports)

    def open(self) -> None:
        return None

    def close(self) -> None:
        return None

    def get_freq_start(self, ch: int | None = None) -> float:
        return self._start_hz

    def set_freq_start(self, freq: float, ch: int | None = None) -> float:
        self._start_hz = float(freq)
        return self._start_hz

    def get_freq_stop(self, ch: int | None = None) -> float:
        return self._stop_hz

    def set_freq_stop(self, freq: float, ch: int | None = None) -> float:
        self._stop_hz = float(freq)
        return self._stop_hz

    def get_freq_npoints(self, ch: int | None = None) -> int:
        return self._npoints

    def set_freq_npoints(self, npoints: int, ch: int | None = None) -> int:
        self._npoints = int(npoints)
        return self._npoints

    def get_nports(self, ch: int | None = None) -> int:
        return self._nports

    def get_smat(self, m: int, n: int, ch: int | None = None) -> np.ndarray:
        rng = np.random.default_rng(seed=(self._npoints + self._nports + m * 10 + n * 7 + (ch or 0)))
        phase = rng.uniform(0.0, 2.0 * np.pi, size=self._npoints)
        amplitude = rng.uniform(0.05, 0.9, size=self._npoints)
        return amplitude * np.exp(1j * phase)
