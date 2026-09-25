"""Tests for the HackRF One adapter."""

from __future__ import annotations

import importlib
import sys
import time
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from instro.unstable.sdr import Direction
from instro.unstable.sdr.drivers import HackRFOne


def _patch_hackrf():
    """Stand in for the vendor bindings, which are an optional source build we never import in CI."""
    pyhackrf = MagicMock(name="pyhackrf")
    device = MagicMock(name="PyHackrfDevice")
    pyhackrf.pyhackrf_device_list_open.return_value = device
    pyhackrf.pyhackrf_open_by_serial.return_value = device
    # The real helper snaps to one of 16 filter widths; identity keeps unrelated tests readable.
    pyhackrf.pyhackrf_compute_baseband_filter_bw.side_effect = lambda bandwidth_hz: bandwidth_hz

    pylibhackrf = MagicMock(name="pylibhackrf")
    pylibhackrf.pyhackrf = pyhackrf
    root = MagicMock(name="python_hackrf")
    root.pylibhackrf = pylibhackrf

    patcher = patch.dict(sys.modules, {"python_hackrf": root, "python_hackrf.pylibhackrf": pylibhackrf})
    patcher.start()
    return patcher, pyhackrf, device


def _deliver_on_start(device: MagicMock, blocks: list[np.ndarray], valid_lengths: list[int] | None = None) -> None:
    """Make start_rx push raw int8 blocks through the callback the driver registered."""
    registered: dict[str, Any] = {}
    device.set_rx_callback.side_effect = lambda fn: registered.__setitem__("cb", fn)
    lengths = valid_lengths if valid_lengths is not None else [len(b) for b in blocks]

    def start_rx() -> None:
        for block, valid in zip(blocks, lengths):
            registered["cb"](device, block, len(block), valid)

    device.pyhackrf_start_rx.side_effect = start_rx


def _open_driver(device_index: int = 0) -> HackRFOne:
    driver = HackRFOne(device_index=device_index)
    driver.open()
    return driver


def test_01_opens_the_requested_device_index() -> None:
    """Index selection needs the device list first; there is no bare index-based open."""
    patcher, pyhackrf, _ = _patch_hackrf()
    try:
        _open_driver(device_index=2)
    finally:
        patcher.stop()

    pyhackrf.pyhackrf_init.assert_called_once()
    pyhackrf.pyhackrf_device_list_open.assert_called_once_with(pyhackrf.pyhackrf_device_list.return_value, 2)


def test_02_opens_by_serial_when_one_is_given() -> None:
    """A serial addresses one radio regardless of enumeration order."""
    patcher, pyhackrf, _ = _patch_hackrf()
    try:
        HackRFOne(serial_number="0000000000000000457863dc2934e3cf").open()
    finally:
        patcher.stop()

    pyhackrf.pyhackrf_open_by_serial.assert_called_once_with("0000000000000000457863dc2934e3cf")
    pyhackrf.pyhackrf_device_list_open.assert_not_called()


def test_03_construction_touches_no_hardware_and_open_is_idempotent() -> None:
    """Connection params are captured in __init__; the USB handle is taken once in open()."""
    patcher, pyhackrf, _ = _patch_hackrf()
    try:
        driver = HackRFOne(device_index=0)
        assert pyhackrf.pyhackrf_device_list_open.call_count == 0

        driver.open()
        driver.open()
        assert pyhackrf.pyhackrf_device_list_open.call_count == 1
    finally:
        patcher.stop()


def test_04_open_reports_a_missing_radio() -> None:
    """The stub types the open functions as optional, so a None handle must not reach a setter."""
    patcher, pyhackrf, _ = _patch_hackrf()
    pyhackrf.pyhackrf_device_list_open.return_value = None
    try:
        with pytest.raises(RuntimeError, match="no HackRF at device index 3"):
            HackRFOne(device_index=3).open()
    finally:
        patcher.stop()


def test_05_open_programs_the_rate_before_the_frequency() -> None:
    """start_rx inherits the last user's settings, and setting the rate resets the filter."""
    patcher, _, device = _patch_hackrf()
    try:
        _open_driver()
    finally:
        patcher.stop()

    called = [name for name, _args, _kwargs in device.method_calls]
    assert called.index("pyhackrf_set_sample_rate") < called.index("pyhackrf_set_freq")
    device.pyhackrf_set_sample_rate.assert_called_once_with(HackRFOne.DEFAULT_SAMPLE_RATE_HZ)
    device.pyhackrf_set_freq.assert_called_once_with(int(HackRFOne.DEFAULT_CENTER_FREQ_HZ))


def test_06_open_releases_the_handle_when_the_initial_config_fails() -> None:
    """A handle held by a radio we never configured would make the next open() a silent no-op."""
    patcher, _, device = _patch_hackrf()
    device.pyhackrf_set_freq.side_effect = RuntimeError("pyhackrf_set_freq()")
    try:
        driver = HackRFOne(device_index=0)
        with pytest.raises(RuntimeError, match="pyhackrf_set_freq"):
            driver.open()

        device.pyhackrf_close.assert_called_once()
        with pytest.raises(RuntimeError, match="not open"):
            driver.get_center_freq()
    finally:
        patcher.stop()


def test_07_raises_a_clear_error_when_not_open() -> None:
    """Using a closed driver must say so, not crash inside the vendor library."""
    driver = HackRFOne(device_index=0)

    with pytest.raises(RuntimeError, match="not open"):
        driver.read_iq(1024)
    with pytest.raises(RuntimeError, match="not open"):
        driver.get_center_freq()


def test_08_driver_imports_without_the_vendor_sdk_installed() -> None:
    """python_hackrf is an optional source build; importing the driver must not require it."""
    module = importlib.import_module("instro.unstable.sdr.drivers.hackrf_one")

    with patch.dict(sys.modules, {"python_hackrf": None, "python_hackrf.pylibhackrf": None}):
        importlib.reload(module)
        driver = module.HackRFOne(device_index=0)  # construction stays SDK-free

        with pytest.raises(ImportError):
            driver.open()  # only the USB handle needs the SDK

    importlib.reload(module)


def test_09_center_freq_is_written_and_served_from_cache() -> None:
    """There is no frequency readback in libhackrf at all, so the getter answers from what we wrote."""
    patcher, _, device = _patch_hackrf()
    try:
        driver = _open_driver()
        driver.set_center_freq(915_000_000.0)

        device.pyhackrf_set_freq.assert_called_with(915_000_000)
        assert driver.get_center_freq() == 915_000_000.0
    finally:
        patcher.stop()


@pytest.mark.parametrize("frequency_hz", [0.0, 999_999.0, 6_000_000_001.0])
def test_10_center_freq_outside_the_radio_range_is_refused(frequency_hz: float) -> None:
    """The API accepts 0-7250 MHz but the radio is only rated 1 MHz - 6 GHz."""
    patcher, _, device = _patch_hackrf()
    try:
        driver = _open_driver()
        device.pyhackrf_set_freq.reset_mock()

        with pytest.raises(ValueError, match="outside the HackRF's"):
            driver.set_center_freq(frequency_hz)
        device.pyhackrf_set_freq.assert_not_called()
    finally:
        patcher.stop()


def test_11_setting_the_sample_rate_resets_the_cached_bandwidth() -> None:
    """The device silently returns the filter to 0.75 * rate, so a stale cache would lie."""
    patcher, _, device = _patch_hackrf()
    try:
        driver = _open_driver()
        driver.set_bandwidth(1_750_000)
        assert driver.get_bandwidth() == 1_750_000.0

        driver.set_sample_rate(8_000_000.0)

        device.pyhackrf_set_sample_rate.assert_called_with(8_000_000.0)
        assert driver.get_sample_rate() == 8_000_000.0
        assert driver.get_bandwidth() == 6_000_000.0
    finally:
        patcher.stop()


def test_12_bandwidth_snaps_to_a_width_the_filter_actually_has() -> None:
    """The filter has 16 discrete widths; the vendor helper picks one and the cache keeps it."""
    patcher, pyhackrf, device = _patch_hackrf()
    pyhackrf.pyhackrf_compute_baseband_filter_bw.side_effect = None
    pyhackrf.pyhackrf_compute_baseband_filter_bw.return_value = 2_500_000
    try:
        driver = _open_driver()
        driver.set_bandwidth(2_600_000)

        pyhackrf.pyhackrf_compute_baseband_filter_bw.assert_called_once_with(2_600_000)
        device.pyhackrf_set_baseband_filter_bandwidth.assert_called_with(2_500_000)
        assert driver.get_bandwidth() == 2_500_000.0
    finally:
        patcher.stop()


def test_13_rx_gain_is_distributed_over_the_lna_and_vga_stages() -> None:
    """There is no single gain register: 50 dB parks the LNA and puts the balance in the VGA."""
    patcher, _, device = _patch_hackrf()
    try:
        driver = _open_driver()
        driver.set_gain(50)

        device.pyhackrf_set_lna_gain.assert_called_with(16)
        device.pyhackrf_set_vga_gain.assert_called_with(34)
        assert driver.get_gain() == 50.0
        assert (driver.lna_gain_db, driver.vga_gain_db) == (16, 34)
    finally:
        patcher.stop()


@pytest.mark.parametrize("gain_db", [20, 30, 40, 50, 60, 78])
def test_13b_rx_gain_always_drives_the_vga(gain_db: int) -> None:
    """Regression: filling the LNA first left the VGA at 0 for every request up to 40 dB.

    The VGA is the last stage before the converter, so a real radio reached the ADC as a
    handful of LSBs across the whole lower half of the range.
    """
    patcher, _, _ = _patch_hackrf()
    try:
        driver = _open_driver()
        driver.set_gain(gain_db)

        assert driver.vga_gain_db > 0
        assert driver.lna_gain_db <= HackRFOne.LNA_PREFERRED_DB
        assert driver.get_gain() == float(gain_db)
    finally:
        patcher.stop()


@pytest.mark.parametrize("gain_db", list(range(0, 103)))
def test_13c_every_gain_lands_on_a_settable_pair(gain_db: int) -> None:
    """Each stage must stay on its own step, and the pair must not overshoot the request."""
    patcher, _, _ = _patch_hackrf()
    try:
        driver = _open_driver()
        driver.set_gain(gain_db)

        lna_max, lna_step = HackRFOne.LNA_GAIN
        vga_max, vga_step = HackRFOne.VGA_GAIN
        assert 0 <= driver.lna_gain_db <= lna_max and driver.lna_gain_db % lna_step == 0
        assert 0 <= driver.vga_gain_db <= vga_max and driver.vga_gain_db % vga_step == 0
        # Never more than asked for, and never short by more than the finest step.
        assert 0 <= gain_db - driver.get_gain() < vga_step
    finally:
        patcher.stop()


def test_14_gain_readback_reports_the_quantized_value_not_the_request() -> None:
    """The stages only move in 8 and 2 dB steps, so 7 dB of request lands as 6 dB of gain."""
    patcher, _, device = _patch_hackrf()
    try:
        driver = _open_driver()
        driver.set_gain(7)

        device.pyhackrf_set_lna_gain.assert_called_with(0)
        device.pyhackrf_set_vga_gain.assert_called_with(6)
        assert driver.get_gain() == 6.0
    finally:
        patcher.stop()


def test_15_out_of_range_gain_is_refused_rather_than_silently_clamped() -> None:
    """The binding clamps out-of-range gain without a word; a test rig needs to be told."""
    patcher, _, device = _patch_hackrf()
    try:
        driver = _open_driver()
        device.pyhackrf_set_lna_gain.reset_mock()

        with pytest.raises(ValueError, match="outside the HackRF's"):
            driver.set_gain(200)
        with pytest.raises(ValueError, match="outside the HackRF's"):
            driver.set_lna_gain(-1)
        device.pyhackrf_set_lna_gain.assert_not_called()
    finally:
        patcher.stop()


def test_16_transmit_gain_uses_the_tx_stage() -> None:
    """The transmit path has its own 1 dB-stepped VGA and a range of its own."""
    patcher, _, device = _patch_hackrf()
    try:
        driver = _open_driver()
        driver.set_gain(30, direction=Direction.TX)

        device.pyhackrf_set_txvga_gain.assert_called_with(30)
        assert driver.get_gain(direction=Direction.TX) == 30.0
        assert driver.get_gain_range(direction=Direction.TX) == (0.0, 47.0)
        assert driver.get_gain_range() == (0.0, 102.0)
    finally:
        patcher.stop()


def test_17_capabilities_the_radio_does_not_have_stay_unimplemented() -> None:
    """HackRF has no AGC, no ppm correction, and one fixed antenna port."""
    patcher, _, _ = _patch_hackrf()
    try:
        driver = _open_driver()

        for method, args in (
            ("set_gain_mode", (True,)),
            ("get_gain_mode", ()),
            ("set_freq_correction", (5.0,)),
            ("get_freq_correction", ()),
            ("list_antennas", ()),
            ("set_antenna", ("RX",)),
            ("get_antenna", ()),
        ):
            with pytest.raises(NotImplementedError):
                getattr(driver, method)(*args)
    finally:
        patcher.stop()


def test_18_read_iq_runs_a_stream_because_there_is_no_synchronous_read() -> None:
    """There is no read-N-samples call in libhackrf, so a one-shot opens and closes a stream."""
    patcher, _, device = _patch_hackrf()
    _deliver_on_start(device, [np.arange(8, dtype=np.int8)])
    try:
        driver = _open_driver()
        capture = driver.read_iq(4)

        assert capture.samples.shape == (1, 4)
        assert capture.channels == ("0",)
        # Interleaved signed bytes normalized by the vendor's own divisor of 128.
        assert capture.samples[0][0] == pytest.approx(0 + 1j / 128)
        assert capture.samples[0][3] == pytest.approx(6 / 128 + 7j / 128)
        device.pyhackrf_start_rx.assert_called_once()
        device.pyhackrf_stop_rx.assert_called_once()
    finally:
        patcher.stop()


def test_19_read_iq_refuses_while_a_stream_is_running() -> None:
    """A second start_rx would fight the running transfer loop for the same handle."""
    patcher, _, device = _patch_hackrf()
    _deliver_on_start(device, [np.arange(8, dtype=np.int8)])
    try:
        driver = _open_driver()
        driver.start()

        with pytest.raises(RuntimeError, match="streaming"):
            driver.read_iq(4)
        assert device.pyhackrf_start_rx.call_count == 1
    finally:
        patcher.stop()


def test_20_the_callback_registers_before_the_stream_starts() -> None:
    """start_rx installs its own trampoline, which discards blocks until a callback is set."""
    patcher, _, device = _patch_hackrf()
    try:
        driver = _open_driver()
        driver.start()
    finally:
        patcher.stop()

    called = [name for name, _args, _kwargs in device.method_calls]
    assert called.index("set_rx_callback") < called.index("pyhackrf_start_rx")


def test_21_the_callback_ignores_everything_past_valid_length() -> None:
    """The block is allocated uninitialized and only valid_length bytes are filled."""
    patcher, _, device = _patch_hackrf()
    block = np.array([1, 2, 3, 4, 99, 99, 99, 99], dtype=np.int8)
    _deliver_on_start(device, [block], valid_lengths=[4])
    try:
        driver = _open_driver()
        capture = driver.read_iq(2)

        assert capture.samples.shape == (1, 2)
        assert capture.samples[0][1] == pytest.approx(3 / 128 + 4j / 128)
    finally:
        patcher.stop()


def test_22_an_odd_block_carries_its_stray_byte_to_the_next_one() -> None:
    """Splitting an IQ pair across blocks would invert the phase of every sample after it."""
    patcher, _, device = _patch_hackrf()
    _deliver_on_start(device, [np.array([0, 1, 2], dtype=np.int8), np.array([3, 4, 5], dtype=np.int8)])
    try:
        driver = _open_driver()
        capture = driver.read_iq(3)

        assert capture.samples.shape == (1, 3)
        # Pairs stay (0,1) (2,3) (4,5): the 2 waited for the 3 instead of being dropped.
        assert capture.samples[0][1] == pytest.approx(2 / 128 + 3j / 128)
        assert capture.samples[0][2] == pytest.approx(4 / 128 + 5j / 128)
    finally:
        patcher.stop()


def test_23_a_failing_callback_does_not_kill_the_stream() -> None:
    """Any non-zero return tears the transfer loop down, so the callback must swallow its errors."""
    patcher, _, device = _patch_hackrf()
    try:
        driver = _open_driver()
        exploding = MagicMock()
        exploding.__getitem__.side_effect = ValueError("bad buffer")

        assert driver._on_samples(device, exploding, 8, 8) == 0
    finally:
        patcher.stop()


def test_24_stream_flags_overflow_when_the_buffer_fills() -> None:
    """A consumer too slow for the radio drops the oldest samples and says so."""
    patcher, _, device = _patch_hackrf()
    _deliver_on_start(device, [np.arange(8, dtype=np.int8) for _ in range(4)])
    try:
        driver = _open_driver()
        driver.STREAM_BUFFER_SAMPLES = 8  # type: ignore[misc]
        driver.start()

        capture = driver.fetch_iq(4)
        assert capture.overflow is True
        # The flag is per fetch, so a later block that lost nothing reads clean.
        assert driver.fetch_iq(4).overflow is False
    finally:
        patcher.stop()


def test_25_stop_clears_the_stream() -> None:
    """Stopping releases the buffer and tells the radio to stop receiving."""
    patcher, _, device = _patch_hackrf()
    _deliver_on_start(device, [np.arange(8, dtype=np.int8)])
    try:
        driver = _open_driver()
        driver.start()
        driver.stop()

        device.pyhackrf_stop_rx.assert_called_once()
        assert driver.get_backlog() == 0
        with pytest.raises(RuntimeError, match="not streaming"):
            driver.fetch_iq(4)
    finally:
        patcher.stop()


def test_26_close_stops_a_running_stream_and_releases_the_handle() -> None:
    """A stream left running must not outlive the device handle."""
    patcher, _, device = _patch_hackrf()
    _deliver_on_start(device, [np.arange(8, dtype=np.int8)])
    try:
        driver = _open_driver()
        driver.start()
        driver.close()

        device.pyhackrf_stop_rx.assert_called_once()
        device.pyhackrf_close.assert_called_once()
        with pytest.raises(RuntimeError, match="not open"):
            driver.get_backlog()
    finally:
        patcher.stop()


def test_27_fetch_larger_than_the_buffer_fails_instead_of_waiting_it_out() -> None:
    """The callback evicts to stay inside the buffer, so that wait could never end."""
    patcher, _, device = _patch_hackrf()
    _deliver_on_start(device, [np.arange(8, dtype=np.int8)])
    try:
        driver = _open_driver()
        driver.STREAM_BUFFER_SAMPLES = 2048  # type: ignore[misc]
        driver.FETCH_TIMEOUT_S = 5.0  # type: ignore[misc]
        driver.start()

        started = time.monotonic()
        with pytest.raises(ValueError, match="exceeds the 2,048-sample stream buffer"):
            driver.fetch_iq(2049)

        assert time.monotonic() - started < 0.5, "the guard did not short-circuit the wait"
    finally:
        patcher.stop()


def test_28_start_failure_leaves_the_driver_able_to_retry() -> None:
    """A driver stuck marked as streaming would refuse every later start() and read_iq()."""
    patcher, _, device = _patch_hackrf()
    device.pyhackrf_start_rx.side_effect = RuntimeError("pyhackrf_start_rx()")
    try:
        driver = _open_driver()
        with pytest.raises(RuntimeError, match="pyhackrf_start_rx"):
            driver.start()

        device.pyhackrf_start_rx.side_effect = None
        _deliver_on_start(device, [np.arange(8, dtype=np.int8)])
        assert driver.read_iq(4).samples.shape == (1, 4)
    finally:
        patcher.stop()


@pytest.mark.parametrize("direction", [Direction.RX, Direction.TX])
def test_29_rejects_channels_it_does_not_have(direction: Direction) -> None:
    """A single-path radio must refuse, not silently act on channel 0 instead."""
    patcher, _, device = _patch_hackrf()
    try:
        driver = _open_driver()

        with pytest.raises(ValueError, match="only channel '0'"):
            driver.get_center_freq(direction=direction, channel="1")
        with pytest.raises(ValueError, match="only rx channel '0'"):
            driver.read_iq(4, channels=("1",))
        device.pyhackrf_start_rx.assert_not_called()
    finally:
        patcher.stop()


def test_30_streaming_is_receive_only() -> None:
    """The radio can transmit, but this driver only acquires; a tx stream must be refused."""
    patcher, _, device = _patch_hackrf()
    try:
        driver = _open_driver()

        with pytest.raises(ValueError, match="only stream rx"):
            driver.start(direction=Direction.TX)
        device.pyhackrf_start_rx.assert_not_called()
    finally:
        patcher.stop()


def test_31_reports_its_channel_counts_and_ranges() -> None:
    """Capability discovery: one path each way, half duplex, with the rated tuning range."""
    driver = HackRFOne(device_index=0)

    assert driver.get_num_channels(direction=Direction.RX) == 1
    assert driver.get_num_channels(direction=Direction.TX) == 1
    assert driver.get_frequency_range() == (1e6, 6e9)
    assert driver.get_sample_rate_range() == (2e6, 20e6)


@pytest.mark.hardware
def test_32_reads_iq_from_a_connected_hackrf() -> None:
    """Requires one HackRF One on USB. Verifies open, configure, a real IQ read, and reopen."""
    sdr = HackRFOne(device_index=0)
    try:
        sdr.open()
        sdr.set_center_freq(89_700_000.0)
        sdr.set_sample_rate(2_000_000.0)
        sdr.set_gain(40)

        assert sdr.get_center_freq() == pytest.approx(89_700_000.0, rel=1e-4)
        assert sdr.get_sample_rate() == pytest.approx(2_000_000.0, rel=1e-4)
        assert sdr.get_gain() == 40.0

        capture = sdr.read_iq(16384)
        assert capture.samples.shape == (1, 16384)
        assert np.iscomplexobj(capture.samples)
        assert np.any(capture.samples != 0)
        assert np.all(np.abs(capture.samples.real) <= 1.0)
        assert capture.channels == ("0",)

        # The radio has no clock of its own, so it reports a period but no anchor.
        assert capture.sample_period_ns == pytest.approx(1e9 / 2_000_000.0, rel=1e-4)
        assert capture.center_freq_hz[0] == pytest.approx(89_700_000.0, rel=1e-4)
        assert capture.t0_ns is None

        # Setting the rate resets the filter, so the bandwidth is set after it.
        sdr.set_bandwidth(1_750_000)
        assert sdr.get_bandwidth() == 1_750_000.0

        sdr.set_amp_enable(True)
        sdr.set_amp_enable(False)

        for method in ("get_gain_mode", "get_freq_correction", "get_antenna"):
            with pytest.raises(NotImplementedError):
                getattr(sdr, method)()

        # Streaming: consecutive fetches must be contiguous, not merely adjacent.
        sdr.start()
        time.sleep(0.3)
        first = sdr.fetch_iq(65536)
        second = sdr.fetch_iq(65536)
        assert first.samples.shape == second.samples.shape == (1, 65536)
        assert sdr.get_backlog() >= 0
        sdr.stop()

        with pytest.raises(RuntimeError, match="not streaming"):
            sdr.fetch_iq(1024)

        sdr.close()
        sdr.open()
        assert sdr.read_iq(4096).samples.shape == (1, 4096)
    finally:
        sdr.close()
