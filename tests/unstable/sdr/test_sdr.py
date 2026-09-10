"""Tests for the generic SDR contract and the RTL-SDR adapter."""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest

from instro.unstable.sdr import InstroSDR, SDRDriverBase


class _MinimalSDRDriver(SDRDriverBase):
    def __init__(self) -> None:
        self._center_freq_hz = 100_000_000.0
        self._sample_rate_hz = 2_400_000.0
        self._gain_db = 20.0
        self._bandwidth_hz = 1_000_000.0

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def set_center_freq(self, frequency_hz: float) -> None:
        self._center_freq_hz = float(frequency_hz)

    def get_center_freq(self) -> float:
        return self._center_freq_hz

    def set_sample_rate(self, sample_rate_hz: float) -> None:
        self._sample_rate_hz = float(sample_rate_hz)

    def get_sample_rate(self) -> float:
        return self._sample_rate_hz

    def set_gain(self, gain_db: float) -> None:
        self._gain_db = float(gain_db)

    def get_gain(self) -> float:
        return self._gain_db

    def set_bandwidth(self, bandwidth_hz: float) -> None:
        self._bandwidth_hz = float(bandwidth_hz)

    def get_bandwidth(self) -> float:
        return self._bandwidth_hz

    def read_iq(self, n_samples: int) -> np.ndarray:
        base = np.linspace(0, 1, n_samples, dtype=float)
        return base.astype(np.complex128) + 1j * (base * 2.0)


def test_01_sdr_driver_base_requires_implementation() -> None:
    with pytest.raises(TypeError):
        SDRDriverBase()  # type: ignore[abstract]

    class _Incomplete(SDRDriverBase):
        def open(self) -> None:
            pass

        def close(self) -> None:
            pass

    with pytest.raises(TypeError):
        _Incomplete()  # type: ignore[abstract]

    assert isinstance(_MinimalSDRDriver(), SDRDriverBase)


def test_02_instro_sdr_measure_iq_packages_a_buffer_as_measurement() -> None:
    driver = _MinimalSDRDriver()
    sdr = InstroSDR(name="rtl", driver=driver)

    measurement = sdr.measure_iq(n_samples=8)

    assert measurement.channel_data["rtl.i"]
    assert measurement.channel_data["rtl.q"]
    assert len(measurement.timestamps) == 8
    assert measurement.channel_data["rtl.i"][0] == pytest.approx(0.0)
    assert measurement.channel_data["rtl.q"][-1] == pytest.approx(2.0)


def test_03_instro_sdr_measure_spectrum_returns_power_summary() -> None:
    driver = _MinimalSDRDriver()
    sdr = InstroSDR(name="rtl", driver=driver)

    measurement = sdr.measure_spectrum(n_samples=8)

    assert "rtl.spectrum" in measurement.channel_data
    assert len(measurement.channel_data["rtl.spectrum"]) == 5
    assert all(value >= 0.0 for value in measurement.channel_data["rtl.spectrum"])


def test_04_instro_sdr_safely_wraps_driver_methods() -> None:
    driver = MagicMock(spec=_MinimalSDRDriver)
    driver.get_sample_rate.return_value = 2_400_000.0
    driver.read_iq.return_value = np.array([1 + 2j, 3 + 4j], dtype=np.complex128)
    sdr = InstroSDR(name="rtl", driver=driver)

    measurement = sdr.measure_iq(n_samples=2)

    assert "rtl.i" in measurement.channel_data
    assert measurement.channel_data["rtl.i"][0] == pytest.approx(1.0)
    assert measurement.channel_data["rtl.q"][1] == pytest.approx(4.0)


@pytest.mark.parametrize(
    ("getter_name", "descriptor", "initial_value"),
    [
        ("get_center_freq", "center_freq", 100_000_000.0),
        ("get_sample_rate", "sample_rate", 2_400_000.0),
        ("get_gain", "gain", 20.0),
        ("get_bandwidth", "bandwidth", 1_000_000.0),
    ],
)
def test_05_instro_sdr_getters_publish_as_measurement(getter_name: str, descriptor: str, initial_value: float) -> None:
    """A read publishes as Measurement, not Command -- same categorical convention as every other category."""
    driver = _MinimalSDRDriver()
    sdr = InstroSDR(name="rtl", driver=driver)

    measurement = getattr(sdr, getter_name)()

    assert measurement.channel_data == {f"rtl.{descriptor}": [initial_value]}


def test_06_instro_sdr_getters_publish_to_attached_publishers() -> None:
    published = []
    publisher = MagicMock()
    publisher.publish.side_effect = lambda data, **kwargs: published.append(data)
    driver = _MinimalSDRDriver()
    sdr = InstroSDR(name="rtl", driver=driver, publishers=[publisher])

    sdr.get_gain()

    assert len(published) == 1
    assert published[0].channel_data == {"rtl.gain": [20.0]}
