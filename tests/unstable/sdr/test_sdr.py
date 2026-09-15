"""Tests for the generic SDR contract and the RTL-SDR adapter."""

from __future__ import annotations

import copy
import logging
import time
from unittest.mock import MagicMock

import numpy as np
import pytest

import instro.unstable.sdr.sdr as sdr_module
from instro.lib import Command
from instro.unstable.sdr import Direction, InstroSDR, IQCapture, SDRDriverBase


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

    def set_center_freq(self, frequency_hz: float, **kwargs) -> None:
        self._center_freq_hz = float(frequency_hz)

    def get_center_freq(self, **kwargs) -> float:
        return self._center_freq_hz

    def set_sample_rate(self, sample_rate_hz: float, **kwargs) -> None:
        self._sample_rate_hz = float(sample_rate_hz)

    def get_sample_rate(self, **kwargs) -> float:
        return self._sample_rate_hz

    def set_gain(self, gain_db: float, **kwargs) -> None:
        self._gain_db = float(gain_db)

    def get_gain(self, **kwargs) -> float:
        return self._gain_db

    def set_bandwidth(self, bandwidth_hz: float, **kwargs) -> None:
        self._bandwidth_hz = float(bandwidth_hz)

    def get_bandwidth(self, **kwargs) -> float:
        return self._bandwidth_hz

    def read_iq(self, n_samples: int, **kwargs) -> IQCapture:
        base = np.linspace(0, 1, n_samples, dtype=float)
        samples = base.astype(np.complex128) + 1j * (base * 2.0)
        return IQCapture(
            samples=samples.reshape(1, -1),
            sample_period_ns=1e9 / self._sample_rate_hz,
            channels=("0",),
            center_freq_hz=(self._center_freq_hz,),
        )


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

    assert measurement.channel_data["rtl.rx0.i"]
    assert measurement.channel_data["rtl.rx0.q"]
    assert len(measurement.timestamps) == 8
    assert measurement.channel_data["rtl.rx0.i"][0] == pytest.approx(0.0)
    assert measurement.channel_data["rtl.rx0.q"][-1] == pytest.approx(2.0)


def test_03_instro_sdr_measure_spectrum_publishes_one_scalar_per_channel() -> None:
    """Regression: the summary emitted 5 values against 1 timestamp, which no publisher can zip."""
    published = []
    publisher = MagicMock()
    publisher.publish.side_effect = lambda data, **kwargs: published.append(data)
    sdr = InstroSDR(name="rtl", driver=_MinimalSDRDriver(), publishers=[publisher])

    measurement = sdr.measure_spectrum(n_samples=1024)

    assert published == [measurement]
    assert set(measurement.channel_data) == {
        "rtl.rx0.spectrum.peak_power_db",
        "rtl.rx0.spectrum.peak_freq_hz",
        "rtl.rx0.spectrum.mean_power_db",
        "rtl.rx0.spectrum.occupied_bw_hz",
    }
    assert len(measurement.timestamps) == 1
    assert all(len(values) == 1 for values in measurement.channel_data.values())


def test_04_instro_sdr_safely_wraps_driver_methods() -> None:
    driver = MagicMock(spec=_MinimalSDRDriver)
    driver.get_sample_rate.return_value = 2_400_000.0
    driver.read_iq.return_value = IQCapture(
        samples=np.array([[1 + 2j, 3 + 4j]], dtype=np.complex128),
        sample_period_ns=1e9 / 2_400_000.0,
        channels=("0",),
        center_freq_hz=(100_000_000.0,),
    )
    sdr = InstroSDR(name="rtl", driver=driver)

    measurement = sdr.measure_iq(n_samples=2)

    assert "rtl.rx0.i" in measurement.channel_data
    assert measurement.channel_data["rtl.rx0.i"][0] == pytest.approx(1.0)
    assert measurement.channel_data["rtl.rx0.q"][1] == pytest.approx(4.0)


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

    assert measurement.channel_data == {f"rtl.rx0.{descriptor}": [initial_value]}


def test_06_instro_sdr_getters_publish_to_attached_publishers() -> None:
    published = []
    publisher = MagicMock()
    publisher.publish.side_effect = lambda data, **kwargs: published.append(data)
    driver = _MinimalSDRDriver()
    sdr = InstroSDR(name="rtl", driver=driver, publishers=[publisher])

    sdr.get_gain()

    assert len(published) == 1
    assert published[0].channel_data == {"rtl.rx0.gain": [20.0]}


@pytest.mark.parametrize(
    ("setter_name", "value", "descriptor"),
    [
        ("set_center_freq", 89_700_000.0, "center_freq"),
        ("set_sample_rate", 2_400_000.0, "sample_rate"),
        ("set_gain", 30.0, "gain"),
        ("set_bandwidth", 1_500_000.0, "bandwidth"),
    ],
)
def test_07_instro_sdr_setters_publish_a_command(setter_name: str, value: float, descriptor: str) -> None:
    """Regression: setters packaged a Command but never published it -- every set_* was dropped."""
    published = []
    publisher = MagicMock()
    publisher.publish.side_effect = lambda data, **kwargs: published.append(data)
    sdr = InstroSDR(name="rtl", driver=_MinimalSDRDriver(), publishers=[publisher])

    command = getattr(sdr, setter_name)(value)

    assert isinstance(command, Command)
    assert published == [command]
    assert command.channel_data == {f"rtl.rx0.{descriptor}.cmd": value}


def test_08_measure_iq_spaces_timestamps_at_the_device_sample_period() -> None:
    """Regression: spacing was hardcoded to 1 ms (1 kSa/s) regardless of the real sample rate."""
    driver = _MinimalSDRDriver()
    driver.set_sample_rate(2_400_000.0)
    sdr = InstroSDR(name="rtl", driver=driver)

    measurement = sdr.measure_iq(n_samples=64)

    # 416.667 ns does not fit in whole ns, so spacing dithers between the two neighbours.
    period_ns = 1e9 / 2_400_000.0
    spacings = {b - a for a, b in zip(measurement.timestamps, measurement.timestamps[1:])}
    assert spacings <= {416, 417}
    assert 1_000_000 not in spacings
    assert measurement.timestamps[-1] - measurement.timestamps[0] == pytest.approx(63 * period_ns, abs=1)

    # The read returns after the samples were taken, so the block ends at "now", not starts there.
    assert measurement.timestamps[-1] <= time.time_ns()


def test_09_measure_iq_blocks_never_overlap_in_time() -> None:
    """Regression: every block re-anchored to the wall clock, so rapid blocks overlapped."""
    driver = _MinimalSDRDriver()
    driver.set_sample_rate(2_400_000.0)
    sdr = InstroSDR(name="rtl", driver=driver)

    first = sdr.measure_iq(n_samples=4096)
    second = sdr.measure_iq(n_samples=4096)

    assert second.timestamps[0] > first.timestamps[-1]


def test_10_measure_iq_re_anchors_after_a_real_gap() -> None:
    """A dropout longer than one block stays visible as a gap instead of being papered over."""
    driver = _MinimalSDRDriver()
    driver.set_sample_rate(2_400_000.0)
    sdr = InstroSDR(name="rtl", driver=driver)

    first = sdr.measure_iq(n_samples=8)
    dt = round(1e9 / 2_400_000.0)
    time.sleep(0.01)
    second = sdr.measure_iq(n_samples=8)

    assert second.timestamps[0] - first.timestamps[-1] > dt


def test_11_measure_iq_rejects_a_non_positive_sample_period() -> None:
    """A driver that cannot report its timebase must fail loudly rather than fabricate one."""
    driver = MagicMock(spec=_MinimalSDRDriver)
    driver.read_iq.return_value = IQCapture(
        samples=np.array([[1 + 2j]], dtype=np.complex128),
        sample_period_ns=0.0,
        channels=("0",),
        center_freq_hz=(1e8,),
    )
    sdr = InstroSDR(name="rtl", driver=driver)

    with pytest.raises(ValueError, match="non-positive sample period"):
        sdr.measure_iq(n_samples=1)


def test_12_measure_iq_timestamps_track_the_exact_sample_period() -> None:
    """Regression: a period rounded to whole ns drifts 800 ppm at RTL-SDR rates."""
    rate, n_samples = 2_400_000.0, 100_000
    driver = _MinimalSDRDriver()
    driver.set_sample_rate(rate)
    sdr = InstroSDR(name="rtl", driver=driver)

    measurement = sdr.measure_iq(n_samples=n_samples)

    period_ns = 1e9 / rate
    t0 = measurement.timestamps[0]
    worst_ns = max(abs((t - t0) - i * period_ns) for i, t in enumerate(measurement.timestamps))
    assert worst_ns <= 1.0  # rounding a 417 ns period instead would drift ~33 us over this block


def test_13_measure_iq_warns_once_on_a_sub_nanosecond_sample_period(caplog) -> None:
    """A sub-nanosecond period collapses samples onto duplicate timestamps; warn, don't fail."""
    driver = MagicMock(spec=_MinimalSDRDriver)
    driver.read_iq.return_value = IQCapture(
        samples=np.zeros((1, 8), dtype=np.complex128),
        sample_period_ns=0.5,
        channels=("0",),
        center_freq_hz=(1e8,),
    )
    sdr = InstroSDR(name="rtl", driver=driver)

    with caplog.at_level(logging.WARNING, logger="instro.unstable.sdr.sdr"):
        measurement = sdr.measure_iq(n_samples=8)
        sdr.measure_iq(n_samples=8)

    assert len(measurement.channel_data["rtl.rx0.i"]) == 8
    assert len([r for r in caplog.records if "shorter than the integer nanosecond" in r.getMessage()]) == 1


class _ToneSDRDriver(_MinimalSDRDriver):
    """Emits a single complex tone offset from the centre frequency."""

    def __init__(self, offset_hz: float) -> None:
        super().__init__()
        self._offset_hz = offset_hz

    def read_iq(self, n_samples: int, **kwargs) -> IQCapture:
        n = np.arange(n_samples)
        samples = np.exp(2j * np.pi * self._offset_hz * n / self._sample_rate_hz)
        return IQCapture(
            samples=samples.reshape(1, -1),
            sample_period_ns=1e9 / self._sample_rate_hz,
            channels=("0",),
            center_freq_hz=(self._center_freq_hz,),
        )


def test_14_measure_spectrum_locates_a_tone_at_its_true_frequency() -> None:
    """Regression: the old summary was |z|^2 resampled to 5 points -- no FFT, no frequency axis."""
    offset_hz = 300_000.0
    sdr = InstroSDR(name="rtl", driver=_ToneSDRDriver(offset_hz))

    measurement = sdr.measure_spectrum(n_samples=1024)

    bin_width_hz = 2_400_000.0 / 1024
    assert measurement.channel_data["rtl.rx0.spectrum.peak_freq_hz"][0] == pytest.approx(
        100_000_000.0 + offset_hz, abs=bin_width_hz
    )
    peak_db = measurement.channel_data["rtl.rx0.spectrum.peak_power_db"][0]
    assert peak_db > measurement.channel_data["rtl.rx0.spectrum.mean_power_db"][0] + 20
    assert measurement.channel_data["rtl.rx0.spectrum.occupied_bw_hz"][0] < 10 * bin_width_hz


def test_15_compute_psd_returns_the_array_without_publishing() -> None:
    """The full spectrum is caller-facing only; it must not reach publishers."""
    published = []
    publisher = MagicMock()
    publisher.publish.side_effect = lambda data, **kwargs: published.append(data)
    sdr = InstroSDR(name="rtl", driver=_ToneSDRDriver(300_000.0), publishers=[publisher])

    freqs, power_db = sdr.compute_psd(n_samples=1024)

    assert published == []
    assert freqs.shape == power_db.shape == (1024,)
    assert np.all(np.diff(freqs) > 0)
    assert freqs[int(np.argmax(power_db))] == pytest.approx(100_300_000.0, abs=2_400_000.0 / 1024)


def test_16_attribute_lookup_before_driver_is_set_does_not_recurse() -> None:
    """Regression: __getattr__ read self._driver unguarded, so a lookup before it was set recursed."""
    half_built = InstroSDR.__new__(InstroSDR)  # _driver not assigned yet

    with pytest.raises(AttributeError):
        half_built.anything  # noqa: B018 -- the attribute access itself is under test

    # copy.copy consults dunders that InstroSDR lacks, which is what tripped the recursion.
    assert copy.copy(InstroSDR(name="rtl", driver=_MinimalSDRDriver())) is not None


def test_17_instro_sdr_does_not_delegate_to_the_driver() -> None:
    """Driver methods stay behind the HAL: delegation bypassed the lock and the publishing path."""
    driver = _MinimalSDRDriver()
    sdr = InstroSDR(name="rtl", driver=driver)

    with pytest.raises(AttributeError, match="InstroSDR"):
        sdr.read_iq  # noqa: B018 -- the attribute access itself is under test

    assert sdr.driver is driver
    assert sdr.driver.read_iq(4).samples.shape == (1, 4)


def test_18_measure_iq_values_match_per_element_conversion_exactly() -> None:
    """The vectorised conversion must be bit-identical to the per-element form it replaced."""
    rng = np.random.default_rng(0)
    samples = (rng.standard_normal(2048) + 1j * rng.standard_normal(2048)).astype(np.complex128)
    driver = MagicMock(spec=_MinimalSDRDriver)
    driver.read_iq.return_value = IQCapture(
        samples=samples.reshape(1, -1),
        sample_period_ns=1e9 / 2_400_000.0,
        channels=("0",),
        center_freq_hz=(100_000_000.0,),
    )
    sdr = InstroSDR(name="rtl", driver=driver)

    measurement = sdr.measure_iq(n_samples=2048)

    assert measurement.channel_data["rtl.rx0.i"] == [float(np.real(v)) for v in samples]
    assert measurement.channel_data["rtl.rx0.q"] == [float(np.imag(v)) for v in samples]
    # Publishers require plain Python scalars, not numpy types.
    assert type(measurement.channel_data["rtl.rx0.i"][0]) is float
    assert type(measurement.timestamps[0]) is int


def test_19_a_hardware_timestamp_anchors_the_block() -> None:
    """A driver whose device timed the samples supplies t0; the host clock must not override it."""
    hardware_t0 = 1_700_000_000_000_000_000
    period_ns = 1e9 / 2_400_000.0
    driver = MagicMock(spec=_MinimalSDRDriver)
    driver.read_iq.return_value = IQCapture(
        samples=np.zeros((1, 64), dtype=np.complex128),
        sample_period_ns=period_ns,
        channels=("0",),
        center_freq_hz=(100_000_000.0,),
        t0_ns=hardware_t0,
    )
    sdr = InstroSDR(name="rtl", driver=driver)

    measurement = sdr.measure_iq(n_samples=64)

    assert measurement.timestamps[0] == hardware_t0
    assert measurement.timestamps[-1] == hardware_t0 + round(63 * period_ns)


def test_20_hardware_timestamps_bypass_the_wall_clock_continuity_guard() -> None:
    """With a device clock, blocks land where the device says, even if that repeats a timeline."""
    period_ns = 1e9 / 2_400_000.0
    driver = MagicMock(spec=_MinimalSDRDriver)
    sdr = InstroSDR(name="rtl", driver=driver)

    driver.read_iq.return_value = IQCapture(
        samples=np.zeros((1, 8), dtype=np.complex128),
        sample_period_ns=period_ns,
        channels=("0",),
        center_freq_hz=(1e8,),
        t0_ns=5_000,
    )
    first = sdr.measure_iq(n_samples=8)
    second = sdr.measure_iq(n_samples=8)

    assert first.timestamps == second.timestamps == [5_000 + round(i * period_ns) for i in range(8)]


def test_21_measure_iq_takes_the_timebase_from_the_capture_not_a_second_query() -> None:
    """Regression risk: re-querying the rate can disagree with the block that was just read."""
    driver = MagicMock(spec=_MinimalSDRDriver)
    driver.read_iq.return_value = IQCapture(
        samples=np.zeros((1, 16), dtype=np.complex128),
        sample_period_ns=1e9 / 2_400_000.0,
        channels=("0",),
        center_freq_hz=(100_000_000.0,),
    )
    driver.get_sample_rate.return_value = 999.0  # a stale value the HAL must ignore
    sdr = InstroSDR(name="rtl", driver=driver)

    measurement = sdr.measure_iq(n_samples=16)

    driver.get_sample_rate.assert_not_called()
    assert set(np.diff(measurement.timestamps).tolist()) <= {416, 417}


class _RequiredOnlyDriver(SDRDriverBase):
    """Implements the required tier and nothing else."""

    def open(self) -> None: ...

    def close(self) -> None: ...

    def set_center_freq(self, frequency_hz: float, **kwargs) -> None: ...

    def get_center_freq(self, **kwargs) -> float:
        return 1e8

    def set_sample_rate(self, sample_rate_hz: float, **kwargs) -> None: ...

    def get_sample_rate(self, **kwargs) -> float:
        return 2.4e6

    def read_iq(self, n_samples: int, **kwargs) -> IQCapture:
        return IQCapture(
            samples=np.zeros((1, n_samples), dtype=np.complex128),
            sample_period_ns=1e9 / 2.4e6,
            channels=("0",),
            center_freq_hz=(1e8,),
        )


def test_22_a_driver_implementing_only_the_required_tier_is_concrete() -> None:
    """Optional capabilities must not force stubs onto drivers whose hardware lacks them."""
    driver = _RequiredOnlyDriver()

    assert isinstance(driver, SDRDriverBase)
    assert InstroSDR(name="rtl", driver=driver).measure_iq(n_samples=8) is not None


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("set_gain", (10.0,)),
        ("get_gain", ()),
        ("set_gain_mode", (True,)),
        ("get_gain_mode", ()),
        ("get_gain_range", ()),
        ("set_bandwidth", (1e6,)),
        ("get_bandwidth", ()),
        ("set_freq_correction", (10.0,)),
        ("get_freq_correction", ()),
        ("list_antennas", ()),
        ("set_antenna", ("RX2",)),
        ("get_antenna", ()),
        ("get_num_channels", ()),
        ("get_frequency_range", ()),
        ("get_sample_rate_range", ()),
    ],
)
def test_23_unimplemented_optional_methods_say_so(method: str, args: tuple) -> None:
    """An unsupported capability raises NotImplementedError, not AttributeError or silence."""
    driver = _RequiredOnlyDriver()

    with pytest.raises(NotImplementedError):
        getattr(driver, method)(*args)


def test_24_direction_and_channel_reach_the_driver() -> None:
    """The signal path a caller names must be the one the driver is asked about."""
    driver = MagicMock(spec=_MinimalSDRDriver)
    driver.get_center_freq.return_value = 1e8
    sdr = InstroSDR(name="usrp", driver=driver)

    measurement = sdr.get_center_freq(direction=Direction.TX, channel="1")

    driver.get_center_freq.assert_called_once_with(direction=Direction.TX, channel="1")
    assert measurement.channel_data == {"usrp.tx1.center_freq": [1e8]}


def test_25_published_channels_name_the_signal_path() -> None:
    """Multi-path radios need every channel to say which path it came from."""
    sdr = InstroSDR(name="rtl", driver=_MinimalSDRDriver())

    iq = sdr.measure_iq(n_samples=8)
    command = sdr.set_center_freq(1e8)

    assert set(iq.channel_data) == {"rtl.rx0.i", "rtl.rx0.q"}
    assert set(command.channel_data) == {"rtl.rx0.center_freq.cmd"}


class _FrozenClock:
    """Stands in for the ``time`` module so a backstamp does not depend on scheduling."""

    def __init__(self, start: int = 1_700_000_000_000_000_000) -> None:
        self.now = start

    def time_ns(self) -> int:
        return self.now


def _capture(samples: int = 8, dropped: int = 0, n_channels: int = 1) -> IQCapture:
    return IQCapture(
        samples=np.zeros((n_channels, samples), dtype=np.complex128),
        sample_period_ns=1e9 / 2_400_000.0,
        channels=tuple(str(i) for i in range(n_channels)),
        center_freq_hz=(1e8,) * n_channels,
        dropped_samples=dropped,
    )


def test_26_fetch_iq_publishes_iq_and_stream_health() -> None:
    """A fetch reports buffer depth alongside the samples, as InstroDAQ does."""
    published = []
    publisher = MagicMock()
    publisher.publish.side_effect = lambda data, **kwargs: published.append(data)
    driver = MagicMock(spec=_MinimalSDRDriver)
    driver.fetch_iq.return_value = _capture()
    driver.get_backlog.return_value = 4096
    sdr = InstroSDR(name="rtl", driver=driver, publishers=[publisher])

    measurement = sdr.fetch_iq(n_samples=8)

    assert set(measurement.channel_data) == {"rtl.rx0.i", "rtl.rx0.q"}
    health = next(d for d in published if "rtl.rx0.backlog" in d.channel_data)
    assert health.channel_data["rtl.rx0.backlog"] == [4096.0]
    assert health.channel_data["rtl.rx0.overflow"] == [0.0]
    assert len(health.timestamps) == 1


def test_27_fetch_iq_reports_a_dropped_block(caplog) -> None:
    """An overflow is published and logged, never swallowed."""
    published = []
    publisher = MagicMock()
    publisher.publish.side_effect = lambda data, **kwargs: published.append(data)
    driver = MagicMock(spec=_MinimalSDRDriver)
    driver.fetch_iq.return_value = _capture(dropped=1024)
    driver.get_backlog.return_value = 0
    sdr = InstroSDR(name="rtl", driver=driver, publishers=[publisher])

    with caplog.at_level(logging.WARNING, logger="instro.unstable.sdr.sdr"):
        sdr.fetch_iq(n_samples=8)

    health = next(d for d in published if "rtl.rx0.overflow" in d.channel_data)
    assert health.channel_data["rtl.rx0.overflow"] == [1.0]
    assert any("dropped samples" in r.getMessage() for r in caplog.records)


def test_28_fetch_iq_blocks_are_contiguous(monkeypatch) -> None:
    """Consecutive fetches continue one timeline: streaming loses nothing between calls."""
    monkeypatch.setattr(sdr_module, "time", _FrozenClock())
    driver = MagicMock(spec=_MinimalSDRDriver)
    driver.fetch_iq.return_value = _capture(samples=1024)
    driver.get_backlog.return_value = 0
    sdr = InstroSDR(name="rtl", driver=driver)

    first = sdr.fetch_iq(n_samples=1024)
    second = sdr.fetch_iq(n_samples=1024)

    assert second.timestamps[0] - first.timestamps[-1] == round(1e9 / 2_400_000.0)


def test_29_start_and_stop_reach_the_driver() -> None:
    """The HAL owns the daemon; the driver owns the hardware stream."""
    driver = MagicMock(spec=_MinimalSDRDriver)
    sdr = InstroSDR(name="rtl", driver=driver)

    sdr.start(channels=("0",))
    driver.start.assert_called_once_with(direction=Direction.RX, channels=("0",))

    sdr.stop()
    driver.stop.assert_called_once_with(direction=Direction.RX)


def test_30_stop_without_start_leaves_the_driver_alone() -> None:
    """close() routes through stop(), so a never-started stream must not be stopped."""
    driver = MagicMock(spec=_MinimalSDRDriver)
    sdr = InstroSDR(name="rtl", driver=driver)

    sdr.stop()

    driver.stop.assert_not_called()


def test_31_streaming_is_optional_for_a_driver() -> None:
    """A driver without a stream says so rather than failing obscurely."""
    sdr = InstroSDR(name="rtl", driver=_RequiredOnlyDriver())

    with pytest.raises(NotImplementedError):
        sdr.start()
    with pytest.raises(NotImplementedError):
        sdr.fetch_iq(n_samples=8)


def test_32_iq_capture_rejects_a_mismatched_shape() -> None:
    """A driver that mislabels its rows must fail at the boundary, not downstream."""
    with pytest.raises(ValueError, match="must be 2-D"):
        IQCapture(
            samples=np.zeros(8, dtype=np.complex128),
            sample_period_ns=416.0,
            channels=("0",),
            center_freq_hz=(1e8,),
        )
    with pytest.raises(ValueError, match="2 sample rows but 1 channels"):
        IQCapture(
            samples=np.zeros((2, 8), dtype=np.complex128),
            sample_period_ns=416.0,
            channels=("0",),
            center_freq_hz=(1e8,),
        )


def test_33_measure_iq_publishes_every_channel_on_one_timebase() -> None:
    """MIMO capture exists to be time-aligned, so all rows share one timestamp vector."""
    driver = MagicMock(spec=_MinimalSDRDriver)
    driver.read_iq.return_value = IQCapture(
        samples=np.array([[1 + 1j, 2 + 2j], [3 + 3j, 4 + 4j]], dtype=np.complex128),
        sample_period_ns=1e9 / 2_400_000.0,
        channels=("0", "1"),
        center_freq_hz=(1e8, 2e8),
    )
    sdr = InstroSDR(name="usrp", driver=driver)

    measurement = sdr.measure_iq(n_samples=2, channels=("0", "1"))

    assert set(measurement.channel_data) == {"usrp.rx0.i", "usrp.rx0.q", "usrp.rx1.i", "usrp.rx1.q"}
    assert measurement.channel_data["usrp.rx0.i"] == [1.0, 2.0]
    assert measurement.channel_data["usrp.rx1.i"] == [3.0, 4.0]
    assert len(measurement.timestamps) == 2  # one vector shared by every channel
    driver.read_iq.assert_called_once_with(2, channels=("0", "1"))


def test_34_spectrum_uses_each_channel_own_centre_frequency() -> None:
    """Channels of one stream can be tuned independently, as on a USRP or Pluto."""
    n = 1024
    rate = 2_400_000.0
    tone = np.exp(2j * np.pi * 300_000.0 * np.arange(n) / rate)
    driver = MagicMock(spec=_MinimalSDRDriver)
    driver.read_iq.return_value = IQCapture(
        samples=np.stack([tone, tone]),
        sample_period_ns=1e9 / rate,
        channels=("0", "1"),
        center_freq_hz=(100_000_000.0, 200_000_000.0),
    )
    sdr = InstroSDR(name="usrp", driver=driver)

    measurement = sdr.measure_spectrum(n_samples=n, channels=("0", "1"))

    bin_hz = rate / n
    assert measurement.channel_data["usrp.rx0.spectrum.peak_freq_hz"][0] == pytest.approx(100_300_000.0, abs=bin_hz)
    assert measurement.channel_data["usrp.rx1.spectrum.peak_freq_hz"][0] == pytest.approx(200_300_000.0, abs=bin_hz)
    assert len(measurement.timestamps) == 1


def test_35_a_stream_covers_a_channel_set_not_a_single_channel() -> None:
    """SoapySDR setupStream and UHD stream args both take a channel list, not one channel."""
    driver = MagicMock(spec=_MinimalSDRDriver)
    driver.fetch_iq.return_value = _capture(samples=4, n_channels=2)
    driver.get_backlog.return_value = 0
    sdr = InstroSDR(name="usrp", driver=driver)

    sdr.start(channels=("0", "1"))
    measurement = sdr.fetch_iq(n_samples=4)

    driver.start.assert_called_once_with(direction=Direction.RX, channels=("0", "1"))
    # fetch and stop address the stream, which already knows its channels.
    driver.fetch_iq.assert_called_once_with(4)
    assert set(measurement.channel_data) == {"usrp.rx0.i", "usrp.rx0.q", "usrp.rx1.i", "usrp.rx1.q"}

    sdr.stop()
    driver.stop.assert_called_once_with(direction=Direction.RX)


def test_36_receive_and_transmit_are_separate_streams() -> None:
    """Starting a receive stream must never key a transmitter."""
    driver = MagicMock(spec=_MinimalSDRDriver)
    sdr = InstroSDR(name="usrp", driver=driver)

    sdr.start(direction=Direction.RX)

    assert driver.start.call_args.kwargs["direction"] is Direction.RX
    assert driver.start.call_count == 1

    sdr.start(direction=Direction.TX)
    sdr.stop()

    stopped = {call.kwargs["direction"] for call in driver.stop.call_args_list}
    assert stopped == {Direction.RX, Direction.TX}


def test_37_measure_iq_during_a_stream_comes_off_the_stream() -> None:
    """A direct device read here would race the stream's own reader on one handle."""
    driver = MagicMock(spec=_MinimalSDRDriver)
    driver.fetch_iq.return_value = _capture(samples=8)
    driver.get_backlog.return_value = 0
    sdr = InstroSDR(name="rtl", driver=driver)

    sdr.start()
    sdr.measure_iq(n_samples=8)

    driver.fetch_iq.assert_called_once_with(8)
    driver.read_iq.assert_not_called()


def test_38_measure_iq_without_a_stream_reads_the_device() -> None:
    """Routing applies only while a stream is running on that direction."""
    driver = MagicMock(spec=_MinimalSDRDriver)
    driver.read_iq.return_value = _capture(samples=8)
    sdr = InstroSDR(name="rtl", driver=driver)

    sdr.measure_iq(n_samples=8)

    driver.read_iq.assert_called_once_with(8, channels=("0",))
    driver.fetch_iq.assert_not_called()


def test_40_a_routed_read_narrows_a_multi_channel_stream() -> None:
    """compute_psd asks for one channel; a two-channel stream must not hand back both."""
    driver = MagicMock(spec=_MinimalSDRDriver)
    driver.fetch_iq.return_value = _capture(samples=1024, n_channels=2)
    driver.get_backlog.return_value = 0
    sdr = InstroSDR(name="usrp", driver=driver)

    sdr.start(channels=("0", "1"))
    measurement = sdr.measure_iq(n_samples=1024, channels=("1",))

    assert set(measurement.channel_data) == {"usrp.rx1.i", "usrp.rx1.q"}


def test_41_a_routed_read_rejects_a_channel_the_stream_lacks() -> None:
    """Asking for a path outside the stream is a caller error, not silently the wrong data."""
    driver = MagicMock(spec=_MinimalSDRDriver)
    driver.fetch_iq.return_value = _capture(samples=8, n_channels=1)
    driver.get_backlog.return_value = 0
    sdr = InstroSDR(name="usrp", driver=driver)

    sdr.start(channels=("0",))

    with pytest.raises(ValueError, match="asked for"):
        sdr.measure_iq(n_samples=8, channels=("1",))


def test_42_routed_reads_stay_contiguous_with_fetches(monkeypatch) -> None:
    """Mixing the two calls must leave one unbroken timeline, since both drain one buffer."""
    monkeypatch.setattr(sdr_module, "time", _FrozenClock())
    driver = MagicMock(spec=_MinimalSDRDriver)
    driver.fetch_iq.return_value = _capture(samples=1024)
    driver.get_backlog.return_value = 0
    sdr = InstroSDR(name="rtl", driver=driver)
    sdr.start()

    first = sdr.fetch_iq(n_samples=1024)
    middle = sdr.measure_iq(n_samples=1024)
    last = sdr.fetch_iq(n_samples=1024)

    period = round(1e9 / 2_400_000.0)
    assert middle.timestamps[0] - first.timestamps[-1] == period
    assert last.timestamps[0] - middle.timestamps[-1] == period


def test_43_is_streaming_reports_per_direction() -> None:
    driver = MagicMock(spec=_MinimalSDRDriver)
    sdr = InstroSDR(name="usrp", driver=driver)

    assert sdr.is_streaming() is False
    sdr.start(direction=Direction.RX)
    assert sdr.is_streaming(direction=Direction.RX) is True
    assert sdr.is_streaming(direction=Direction.TX) is False
    sdr.stop()
    assert sdr.is_streaming(direction=Direction.RX) is False


def test_44_a_routed_read_reports_stream_health() -> None:
    """A read that consumes from a stream owes the same overflow report a fetch gives."""
    published = []
    publisher = MagicMock()
    publisher.publish.side_effect = lambda data, **kwargs: published.append(data)
    driver = MagicMock(spec=_MinimalSDRDriver)
    driver.fetch_iq.return_value = _capture(samples=8, dropped=512)
    driver.get_backlog.return_value = 77
    sdr = InstroSDR(name="rtl", driver=driver, publishers=[publisher])
    sdr.start()

    sdr.measure_iq(n_samples=8)

    health = next(d for d in published if "rtl.rx0.overflow" in d.channel_data)
    assert health.channel_data["rtl.rx0.overflow"] == [1.0]
    assert health.channel_data["rtl.rx0.backlog"] == [77.0]


def _dropout_gap(monkeypatch, elapsed_ns: int) -> int:
    """Gap a 4096-sample dropout opens when ``elapsed_ns`` of wall clock passed between fetches."""
    clock = _FrozenClock()
    monkeypatch.setattr(sdr_module, "time", clock)
    driver = MagicMock(spec=_MinimalSDRDriver)
    driver.get_backlog.return_value = 0
    sdr = InstroSDR(name="rtl", driver=driver)
    sdr.start()

    driver.fetch_iq.return_value = _capture(samples=1024)
    clean = sdr.fetch_iq(n_samples=1024)
    clock.now += elapsed_ns
    driver.fetch_iq.return_value = _capture(samples=1024, dropped=4096)
    after = sdr.fetch_iq(n_samples=1024)

    return after.timestamps[0] - clean.timestamps[-1]


def test_45_a_dropout_opens_a_gap_of_its_true_width(monkeypatch) -> None:
    """Timestamps must not claim contiguity across samples the stream lost."""
    period = 1e9 / 2_400_000.0

    gap = _dropout_gap(monkeypatch, elapsed_ns=0)

    assert gap == round(4097 * period)  # the 4096 lost samples plus the usual one-sample step


def test_45b_a_dropout_keeps_its_width_when_the_clock_ran_ahead(monkeypatch) -> None:
    """Regression: the floor was skipped once the backstamp passed the previous timeline."""
    period = 1e9 / 2_400_000.0
    block_ns = round(1023 * period)
    dropout_ns = round(4097 * period)

    # More wall clock than one block covers, but less than the dropout: the backstamp lands
    # ahead of the previous timeline, which used to discard the dropped-sample advance entirely.
    gap = _dropout_gap(monkeypatch, elapsed_ns=1_250_000)

    assert block_ns < 1_250_000 < dropout_ns
    assert gap == dropout_ns


def test_46_overflow_is_derived_from_the_dropped_count() -> None:
    """One source of truth: a block with no losses is not an overflow."""
    assert _capture(dropped=0).overflow is False
    assert _capture(dropped=1).overflow is True
    assert _capture(dropped=4096).select(("0",)).dropped_samples == 4096


class _LockAssertingDict(dict):
    """Records every mutation's lock state so a write outside the lock is caught deterministically."""

    def __init__(self, lock) -> None:
        super().__init__()
        self._lock = lock
        self.unlocked_writes: list[str] = []

    def __setitem__(self, key, value) -> None:
        if not self._lock._is_owned():
            self.unlocked_writes.append(f"set {key}")
        super().__setitem__(key, value)

    def __delitem__(self, key) -> None:
        if not self._lock._is_owned():
            self.unlocked_writes.append(f"del {key}")
        super().__delitem__(key)


def test_47_stream_bookkeeping_happens_under_the_resource_lock() -> None:
    """A window where _streams and the driver disagree routes a read to the wrong path."""
    driver = MagicMock(spec=_MinimalSDRDriver)
    sdr = InstroSDR(name="rtl", driver=driver)
    tracked = _LockAssertingDict(sdr._resource_lock)
    sdr._streams = tracked

    sdr.start(direction=Direction.RX, channels=("0",))
    sdr.stop()

    assert tracked.unlocked_writes == []


def test_48_close_releases_the_driver_when_the_stream_will_not_stop() -> None:
    """close() routes through stop(); a raise there must not strand the hardware handle."""
    driver = MagicMock(spec=_MinimalSDRDriver)
    driver.stop.side_effect = RuntimeError("device vanished")
    sdr = InstroSDR(name="rtl", driver=driver)
    sdr.start()

    sdr.close()

    driver.close.assert_called_once()
    assert sdr.is_streaming() is False


def test_49_a_failed_stop_still_clears_the_stream_record() -> None:
    """A stale record would route later reads to fetch_iq on a stream that is not running."""
    driver = MagicMock(spec=_MinimalSDRDriver)
    driver.stop.side_effect = RuntimeError("device vanished")
    sdr = InstroSDR(name="rtl", driver=driver)
    sdr.start()

    with pytest.raises(RuntimeError, match="device vanished"):
        sdr.stop()

    assert sdr.is_streaming() is False
    assert sdr._streams == {}


def test_50_one_directions_failure_does_not_strand_the_other() -> None:
    """Every stream gets a stop attempt, whatever the first one did."""
    driver = MagicMock(spec=_MinimalSDRDriver)
    sdr = InstroSDR(name="rtl", driver=driver)
    sdr.start(direction=Direction.RX)
    sdr.start(direction=Direction.TX)
    driver.stop.side_effect = [RuntimeError("rx failed"), None]

    with pytest.raises(RuntimeError):
        sdr.stop()
    sdr.stop()  # the remaining stream is still tracked, so a second call reaches it

    stopped = {call.kwargs["direction"] for call in driver.stop.call_args_list}
    assert stopped == {Direction.RX, Direction.TX}
    assert sdr._streams == {}


def test_51_background_start_registers_the_blocking_fetch() -> None:
    """Regression: background=True spun a daemon with an empty work list, so nothing published."""
    driver = MagicMock(spec=_MinimalSDRDriver)
    driver.fetch_iq.return_value = _capture(samples=8)
    driver.get_backlog.return_value = 0
    sdr = InstroSDR(name="rtl", driver=driver)

    try:
        sdr.start(background=True, n_samples=8)

        registered = [(method, kwargs) for method, _, kwargs in sdr._background_methods]
        assert [m for m, _ in registered] == [sdr.fetch_iq]
        assert registered[0][1]["n_samples"] == 8
    finally:
        sdr.stop()


def test_52_the_blocking_fetch_paces_the_daemon_not_an_interval() -> None:
    """fetch_iq waits on the radio, so any interval would sit between blocks and open gaps."""
    driver = MagicMock(spec=_MinimalSDRDriver)
    driver.fetch_iq.return_value = _capture(samples=8)
    driver.get_backlog.return_value = 0
    sdr = InstroSDR(name="rtl", driver=driver)
    assert sdr.background_interval == 1.0  # the Instrument default

    try:
        sdr.start(background=True, n_samples=8)
        assert sdr.background_interval == 0
    finally:
        sdr.stop()


def test_53_setting_the_interval_is_refused_with_a_warning(caplog) -> None:
    """Silently honouring it would reintroduce the gaps the zeroed interval exists to avoid."""
    sdr = InstroSDR(name="rtl", driver=MagicMock(spec=_MinimalSDRDriver))

    with caplog.at_level(logging.WARNING, logger="instro.unstable.sdr.sdr"):
        sdr.background_interval = 2.5

    assert sdr.background_interval == 1.0  # unchanged
    assert any("Ignoring background_interval" in r.getMessage() for r in caplog.records)


def test_54_restarting_does_not_register_the_fetch_twice() -> None:
    """A second start() would otherwise double every published block."""
    driver = MagicMock(spec=_MinimalSDRDriver)
    driver.fetch_iq.return_value = _capture(samples=8)
    driver.get_backlog.return_value = 0
    sdr = InstroSDR(name="rtl", driver=driver)

    try:
        sdr.start(background=True, n_samples=8)
        sdr.start(background=True, n_samples=16)

        assert len(sdr._background_methods) == 1
        assert sdr._background_methods[0][2]["n_samples"] == 16  # the newer block size wins
    finally:
        sdr.stop()


def test_55_fetch_iq_can_publish_the_spectrum_from_the_same_block() -> None:
    """Regression: measure_spectrum consumed a second block, so half the IQ never published."""
    published = []
    publisher = MagicMock()
    publisher.publish.side_effect = lambda data, **kwargs: published.append(data)
    driver = MagicMock(spec=_MinimalSDRDriver)
    driver.fetch_iq.return_value = _capture(samples=1024)
    driver.get_backlog.return_value = 0
    sdr = InstroSDR(name="rtl", driver=driver, publishers=[publisher])
    sdr.start()

    measurement = sdr.fetch_iq(n_samples=1024, publish_spectrum=True)

    assert driver.fetch_iq.call_count == 1  # one block covered both
    assert set(measurement.channel_data) == {"rtl.rx0.i", "rtl.rx0.q"}
    spectrum = next(d for d in published if "rtl.rx0.spectrum.peak_freq_hz" in d.channel_data)
    assert spectrum.timestamps == [measurement.timestamps[-1]]


def test_56_fetch_iq_publishes_no_spectrum_by_default() -> None:
    driver = MagicMock(spec=_MinimalSDRDriver)
    driver.fetch_iq.return_value = _capture(samples=1024)
    driver.get_backlog.return_value = 0
    published = []
    publisher = MagicMock()
    publisher.publish.side_effect = lambda data, **kwargs: published.append(data)
    sdr = InstroSDR(name="rtl", driver=driver, publishers=[publisher])
    sdr.start()

    sdr.fetch_iq(n_samples=1024)

    assert not any("spectrum" in c for d in published for c in d.channel_data)


@pytest.mark.parametrize(("method", "kwargs"), [("compute_psd", {"n_samples": 1024}), ("get_backlog", {})])
def test_39_acquisition_cannot_name_a_direction(method: str, kwargs: dict) -> None:
    """Sampling is receive-only in SoapySDR, UHD, pyadi-iio and NI, so the verb carries it."""
    driver = MagicMock(spec=_MinimalSDRDriver)
    driver.read_iq.return_value = _capture(samples=1024)
    driver.get_backlog.return_value = 0
    sdr = InstroSDR(name="usrp", driver=driver)

    with pytest.raises(TypeError, match="direction"):
        getattr(sdr, method)(direction=Direction.TX, **kwargs)


def test_57_configuration_still_addresses_both_directions() -> None:
    """A transmitter's tuned frequency is a real reading, unlike a transmit-side IQ block."""
    driver = MagicMock(spec=_MinimalSDRDriver)
    driver.get_center_freq.return_value = 2.4e9
    sdr = InstroSDR(name="usrp", driver=driver)

    measurement = sdr.get_center_freq(direction=Direction.TX, channel="1")

    driver.get_center_freq.assert_called_once_with(direction=Direction.TX, channel="1")
    assert measurement.channel_data == {"usrp.tx1.center_freq": [2.4e9]}


def test_58_published_acquisition_channels_stay_on_the_receive_path() -> None:
    """Dropping the parameter must not change how channels are named."""
    sdr = InstroSDR(name="rtl", driver=_MinimalSDRDriver())

    iq = sdr.measure_iq(n_samples=8)
    spectrum = sdr.measure_spectrum(n_samples=1024)

    assert set(iq.channel_data) == {"rtl.rx0.i", "rtl.rx0.q"}
    assert all(c.startswith("rtl.rx0.spectrum.") for c in spectrum.channel_data)


def test_59_measure_iq_defaults_to_every_channel_the_stream_covers() -> None:
    """Regression: the default narrowed to rx0, so a two-channel block lost rx1 on the way out."""
    driver = MagicMock(spec=_MinimalSDRDriver)
    driver.fetch_iq.return_value = _capture(samples=8, n_channels=2)
    driver.get_backlog.return_value = 0
    sdr = InstroSDR(name="usrp", driver=driver)
    sdr.start(channels=("0", "1"))

    measurement = sdr.measure_iq(n_samples=8)

    assert set(measurement.channel_data) == {"usrp.rx0.i", "usrp.rx0.q", "usrp.rx1.i", "usrp.rx1.q"}


def test_60_measure_spectrum_defaults_to_every_channel_the_stream_covers() -> None:
    driver = MagicMock(spec=_MinimalSDRDriver)
    driver.fetch_iq.return_value = _capture(samples=1024, n_channels=2)
    driver.get_backlog.return_value = 0
    sdr = InstroSDR(name="usrp", driver=driver)
    sdr.start(channels=("0", "1"))

    measurement = sdr.measure_spectrum(n_samples=1024)

    assert {c.split(".")[1] for c in measurement.channel_data} == {"rx0", "rx1"}


def test_61_an_explicit_channel_still_narrows_a_stream_block() -> None:
    """Narrowing stays available; a stream block is consumed whole, so the rest is the caller's call."""
    driver = MagicMock(spec=_MinimalSDRDriver)
    driver.fetch_iq.return_value = _capture(samples=8, n_channels=2)
    driver.get_backlog.return_value = 0
    sdr = InstroSDR(name="usrp", driver=driver)
    sdr.start(channels=("0", "1"))

    measurement = sdr.measure_iq(n_samples=8, channels=("1",))

    assert set(measurement.channel_data) == {"usrp.rx1.i", "usrp.rx1.q"}


def test_62_without_a_stream_the_default_is_the_first_receive_path() -> None:
    driver = MagicMock(spec=_MinimalSDRDriver)
    driver.read_iq.return_value = _capture(samples=8)
    sdr = InstroSDR(name="rtl", driver=driver)

    sdr.measure_iq(n_samples=8)

    driver.read_iq.assert_called_once_with(8, channels=("0",))
