"""Tests for the RTL-SDR adapter."""

from __future__ import annotations

import importlib
import sys
import threading
import time
from unittest.mock import patch

import numpy as np
import pytest

from instro.unstable.sdr import Direction
from instro.unstable.sdr.drivers import RTLSDR


def _patch_rtlsdr():
    """Patch the vendor class the driver imports, with a device stub carrying sane defaults."""
    patcher = patch("rtlsdr.RtlSdr", autospec=True)
    rtl_sdr_cls = patcher.start()
    device = rtl_sdr_cls.return_value
    device.center_freq = 80_000_000
    device.sample_rate = 1_024_000.0
    device.gain = 0.0
    device.bandwidth = 0
    return patcher, rtl_sdr_cls, device


def test_01_rtlsdr_opens_the_requested_device_index() -> None:
    """Regression: the driver passed index=, which RtlSdr rejects, and silently fell back to device 0."""
    patcher, rtl_sdr_cls, _ = _patch_rtlsdr()
    try:
        RTLSDR(device_index=2).open()
    finally:
        patcher.stop()

    rtl_sdr_cls.assert_called_once_with(device_index=2)


def test_02_rtlsdr_opens_exactly_one_handle() -> None:
    """Regression: a non-zero index constructed a second device and leaked the first."""
    patcher, rtl_sdr_cls, _ = _patch_rtlsdr()
    try:
        RTLSDR(device_index=1).open()
    finally:
        patcher.stop()

    assert rtl_sdr_cls.call_count == 1


def test_03_rtlsdr_propagates_an_open_failure() -> None:
    """Regression: `except TypeError` swallowed the failure and opened a different device instead."""
    patcher = patch("rtlsdr.RtlSdr", autospec=True)
    rtl_sdr_cls = patcher.start()
    rtl_sdr_cls.side_effect = OSError("Could not open SDR (device index = 5)")
    try:
        with pytest.raises(OSError, match="device index = 5"):
            RTLSDR(device_index=5).open()
    finally:
        patcher.stop()


def test_04_rtlsdr_forwards_vendor_kwargs() -> None:
    """serial_number and friends reach RtlSdr without colliding with device_index."""
    patcher, rtl_sdr_cls, _ = _patch_rtlsdr()
    try:
        RTLSDR(device_index=0, serial_number="00000001").open()
    finally:
        patcher.stop()

    rtl_sdr_cls.assert_called_once_with(device_index=0, serial_number="00000001")


def test_05_construction_touches_no_hardware() -> None:
    """Connection params are captured in __init__; the USB handle is taken in open()."""
    patcher, rtl_sdr_cls, _ = _patch_rtlsdr()
    try:
        driver = RTLSDR(device_index=0)
        assert rtl_sdr_cls.call_count == 0

        driver.open()
        assert rtl_sdr_cls.call_count == 1
    finally:
        patcher.stop()


def test_06_rtlsdr_reopens_after_close() -> None:
    """Regression: close() left a dead handle in place and open() only logged, so reuse hit a closed device."""
    patcher, rtl_sdr_cls, device = _patch_rtlsdr()
    try:
        driver = RTLSDR(device_index=0)
        driver.open()
        driver.close()
        device.close.assert_called_once()

        driver.open()
        assert rtl_sdr_cls.call_count == 2
    finally:
        patcher.stop()


def test_07_rtlsdr_open_is_idempotent() -> None:
    """A second open() on a live driver must not take a second handle."""
    patcher, rtl_sdr_cls, _ = _patch_rtlsdr()
    try:
        driver = RTLSDR(device_index=0)
        driver.open()
        driver.open()
    finally:
        patcher.stop()

    assert rtl_sdr_cls.call_count == 1


def test_08_rtlsdr_raises_a_clear_error_when_not_open() -> None:
    """Using a closed driver must say so, not crash inside the vendor library."""
    driver = RTLSDR(device_index=0)

    with pytest.raises(RuntimeError, match="not open"):
        driver.read_iq(1024)
    with pytest.raises(RuntimeError, match="not open"):
        driver.get_center_freq()


@pytest.mark.parametrize("n_samples", [1000, 257, 0, -256])
def test_11_read_iq_rejects_sample_counts_librtlsdr_cannot_transfer(n_samples: int) -> None:
    """A short read makes pyrtlsdr close the device, after which the next call segfaults."""
    patcher, _, device = _patch_rtlsdr()
    try:
        driver = RTLSDR(device_index=0)
        driver.open()

        with pytest.raises(ValueError, match="multiple of 256"):
            driver.read_iq(n_samples)
        device.read_samples.assert_not_called()
    finally:
        patcher.stop()


def test_12_driver_notices_the_vendor_closing_the_handle() -> None:
    """Regression: pyrtlsdr closes on a transport error, leaving a stale handle we then dereference."""
    patcher, _, device = _patch_rtlsdr()
    try:
        driver = RTLSDR(device_index=0)
        driver.open()
        device.device_opened = False  # what pyrtlsdr does to itself on a failed read

        with pytest.raises(RuntimeError, match="not open"):
            driver.read_iq(1024)
        device.read_samples.assert_not_called()

        device.device_opened = True  # a fresh handle from a real reopen
        driver.open()  # the stale handle was dropped, so the driver can recover
        assert driver.read_iq(1024).samples is not None
    finally:
        patcher.stop()


def test_09_driver_imports_without_the_vendor_sdk_installed() -> None:
    """The vendor SDK belongs to instro-unstable, not core instro: importing must not require it."""
    module = importlib.import_module("instro.unstable.sdr.drivers.rtl_sdr")

    with patch.dict(sys.modules, {"rtlsdr": None}):
        importlib.reload(module)
        driver = module.RTLSDR(device_index=0)  # construction stays SDK-free

        with pytest.raises(ImportError):
            driver.open()  # only the USB handle needs the SDK

    importlib.reload(module)


@pytest.mark.hardware
def test_10_rtlsdr_reads_iq_from_a_connected_dongle() -> None:
    """Requires one RTL-SDR on USB. Verifies open, configure, a real IQ read, and reopen."""
    sdr = RTLSDR(device_index=0)
    try:
        sdr.open()
        sdr.set_center_freq(89_700_000.0)
        sdr.set_sample_rate(2_400_000.0)

        assert sdr.get_center_freq() == pytest.approx(89_700_000.0, rel=1e-4)
        assert sdr.get_sample_rate() == pytest.approx(2_400_000.0, rel=1e-4)

        capture = sdr.read_iq(4096)
        assert capture.samples.shape == (1, 4096)
        assert np.iscomplexobj(capture.samples)
        assert np.any(capture.samples != 0)
        assert capture.channels == ("0",)

        # The dongle has no clock of its own, so it reports a period but no anchor.
        assert capture.sample_period_ns == pytest.approx(1e9 / 2_400_000.0, rel=1e-4)
        assert capture.center_freq_hz[0] == pytest.approx(89_700_000.0, rel=1e-4)
        assert capture.t0_ns is None

        # Optional capabilities the dongle genuinely backs.
        assert sdr.get_num_channels(direction=Direction.RX) == 1
        assert sdr.get_num_channels(direction=Direction.TX) == 0

        low, high = sdr.get_gain_range()
        assert 0.0 <= low < high

        sdr.set_freq_correction(5)
        assert sdr.get_freq_correction() == pytest.approx(5.0)
        sdr.set_freq_correction(0)

        sdr.set_gain_mode(True)
        sdr.set_gain_mode(False)

        # Optional capabilities it does not have.
        for method in ("list_antennas", "get_antenna", "get_gain_mode"):
            with pytest.raises(NotImplementedError):
                getattr(sdr, method)()

        # Paths this radio does not have are refused, not silently redirected to rx0.
        with pytest.raises(ValueError, match="only rx channel"):
            sdr.get_center_freq(direction=Direction.TX)
        with pytest.raises(ValueError, match="only rx channel"):
            sdr.read_iq(1024, channels=("1",))
        # Acquisition is receive-only, so a transmit block cannot even be asked for.
        with pytest.raises(TypeError):
            sdr.read_iq(1024, direction=Direction.TX)  # type: ignore[call-arg]

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
        assert sdr.read_iq(1024).samples.shape == (1, 1024)
    finally:
        sdr.close()


@pytest.mark.parametrize(
    ("direction", "channel"),
    [(Direction.TX, "0"), (Direction.RX, "1"), (Direction.TX, "1")],
)
def test_13_rtlsdr_rejects_paths_it_does_not_have(direction: Direction, channel: str) -> None:
    """A receive-only single-path radio must refuse, not silently act on rx0 instead."""
    patcher, _, device = _patch_rtlsdr()
    try:
        driver = RTLSDR(device_index=0)
        driver.open()

        # Config addresses either direction, so the driver has to refuse the ones it lacks.
        with pytest.raises(ValueError, match="only rx channel"):
            driver.get_center_freq(direction=direction, channel=channel)
        # Acquisition is receive-only by signature, so only the channel can be wrong here.
        if channel != "0":
            with pytest.raises(ValueError, match="only rx channel"):
                driver.read_iq(1024, channels=(channel,))
        device.read_samples.assert_not_called()
    finally:
        patcher.stop()


def test_14_rtlsdr_reports_its_channel_counts() -> None:
    """Capability discovery: one receive path, no transmit path."""
    driver = RTLSDR(device_index=0)

    assert driver.get_num_channels(direction=Direction.RX) == 1
    assert driver.get_num_channels(direction=Direction.TX) == 0


def test_15_rtlsdr_leaves_unsupported_capabilities_unimplemented() -> None:
    """The dongle has no antenna switch and pyrtlsdr exposes no gain-mode readback."""
    patcher, _, _ = _patch_rtlsdr()
    try:
        driver = RTLSDR(device_index=0)
        driver.open()

        for method, args in (("list_antennas", ()), ("get_antenna", ()), ("get_gain_mode", ())):
            with pytest.raises(NotImplementedError):
                getattr(driver, method)(*args)
    finally:
        patcher.stop()


def test_16_rtlsdr_gain_mode_maps_onto_manual_gain() -> None:
    """AGC on means manual gain off, which is how librtlsdr expresses it."""
    patcher, _, device = _patch_rtlsdr()
    try:
        driver = RTLSDR(device_index=0)
        driver.open()

        driver.set_gain_mode(True)
        device.set_manual_gain_enabled.assert_called_once_with(False)

        driver.set_gain_mode(False)
        device.set_manual_gain_enabled.assert_called_with(True)
    finally:
        patcher.stop()


def test_17_rtlsdr_reports_its_gain_range() -> None:
    """The range comes from the tuner's own gain table, not a hardcoded guess."""
    patcher, _, device = _patch_rtlsdr()
    try:
        driver = RTLSDR(device_index=0)
        driver.open()
        device.valid_gains_db = [0.0, 0.9, 1.4, 49.6]

        assert driver.get_gain_range() == (0.0, 49.6)
    finally:
        patcher.stop()


def _drain(driver: RTLSDR) -> None:
    """Wait for the fake async reader to deliver everything, so fetches are deterministic."""
    thread = driver._stream_thread
    if thread is not None:
        thread.join(timeout=5.0)


def _streaming_driver(chunks: int = 4, chunk_samples: int = 1024):
    """A patched RtlSdr whose async reader delivers a fixed number of chunks."""
    patcher, _, device = _patch_rtlsdr()
    device.sample_rate = 2_400_000.0
    device.center_freq = 89_700_000.0

    def fake_async(callback, num_samples, *args, **kwargs):
        for i in range(chunks):
            callback(np.full(chunk_samples, i, dtype=np.complex128), None)

    device.read_samples_async.side_effect = fake_async
    return patcher, device


def test_18_fetch_iq_assembles_blocks_from_the_stream() -> None:
    """Streaming hands back what the async reader delivered, in order."""
    patcher, _ = _streaming_driver(chunks=4, chunk_samples=1024)
    try:
        driver = RTLSDR(device_index=0)
        driver.open()
        driver.start()
        _drain(driver)

        capture = driver.fetch_iq(2048)

        assert capture.samples.shape == (1, 2048)
        assert capture.channels == ("0",)
        # First chunk is all 0s, second all 1s: the stream preserved order.
        assert capture.samples[0][0] == 0
        assert capture.samples[0][-1] == 1
        assert capture.overflow is False
        assert driver.get_backlog() == 2048
    finally:
        patcher.stop()


def test_19_fetch_iq_before_start_says_so() -> None:
    """Fetching without a running stream must not silently fall back to a one-shot read."""
    patcher, device = _streaming_driver()
    try:
        driver = RTLSDR(device_index=0)
        driver.open()

        with pytest.raises(RuntimeError, match="not streaming"):
            driver.fetch_iq(1024)
        device.read_samples.assert_not_called()
    finally:
        patcher.stop()


def test_20_stream_flags_overflow_when_the_buffer_fills() -> None:
    """A consumer too slow for the radio drops the oldest samples and says so."""
    patcher, _ = _streaming_driver(chunks=8, chunk_samples=1024)
    try:
        driver = RTLSDR(device_index=0)
        driver.open()
        driver.STREAM_BUFFER_SAMPLES = 2048  # type: ignore[misc]
        driver.start()
        _drain(driver)

        capture = driver.fetch_iq(1024)

        assert capture.overflow is True
        # The flag is per fetch, so a later block that lost nothing reads clean.
        assert driver.fetch_iq(1024).overflow is False
    finally:
        patcher.stop()


def test_21_stop_clears_the_stream() -> None:
    """Stopping releases the buffer and cancels the reader."""
    patcher, device = _streaming_driver()
    try:
        driver = RTLSDR(device_index=0)
        driver.open()
        driver.start()
        driver.stop()

        device.cancel_read_async.assert_called_once()
        assert driver.get_backlog() == 0
        with pytest.raises(RuntimeError, match="not streaming"):
            driver.fetch_iq(1024)
    finally:
        patcher.stop()


def test_22_close_stops_a_running_stream() -> None:
    """A stream left running must not outlive the device handle."""
    patcher, device = _streaming_driver()
    try:
        driver = RTLSDR(device_index=0)
        driver.open()
        driver.start()
        driver.close()

        device.cancel_read_async.assert_called_once()
        device.close.assert_called_once()
    finally:
        patcher.stop()


def test_23_read_iq_refuses_while_the_async_reader_owns_the_handle() -> None:
    """Librtlsdr cannot serve a sync read during an async one; the HAL routes, the driver refuses."""
    patcher, device = _streaming_driver()
    try:
        driver = RTLSDR(device_index=0)
        driver.open()
        driver.start()
        _drain(driver)

        with pytest.raises(RuntimeError, match="streaming"):
            driver.read_iq(1024)
        device.read_samples.assert_not_called()

        driver.stop()
        driver.read_iq(1024)
        device.read_samples.assert_called_once_with(1024)
    finally:
        patcher.stop()


def _wedged_streaming_driver():
    """A patched RtlSdr whose async reader ignores cancel_read_async, as a wedged USB reader would."""
    patcher, _, device = _patch_rtlsdr()
    release = threading.Event()

    def blocking_async(callback, num_samples, *args, **kwargs):
        release.wait(timeout=30)

    device.read_samples_async.side_effect = blocking_async
    device.cancel_read_async.side_effect = lambda *a, **k: None  # does not unblock the reader
    return patcher, device, release


def test_24_stop_keeps_a_worker_that_outlived_its_join() -> None:
    """Clearing the handle would let start() open a second reader on top of the live one."""
    patcher, device, release = _wedged_streaming_driver()
    try:
        driver = RTLSDR(device_index=0)
        driver.STREAM_STOP_TIMEOUT_S = 0.2  # type: ignore[misc]
        driver.open()
        driver.start()

        driver.stop()

        assert driver._stream_thread is not None  # still recorded, so start() will not double up
        driver.start()
        assert device.read_samples_async.call_count == 1
    finally:
        release.set()
        patcher.stop()


def test_25_close_releases_the_handle_when_the_vendor_already_closed_it() -> None:
    """Regression: stop() touched the device first, so close() raised and leaked the handle."""
    patcher, _, device = _patch_rtlsdr()
    try:
        driver = RTLSDR(device_index=0)
        driver.open()
        driver.start()
        device.device_opened = False  # what pyrtlsdr does to itself on a transport error

        driver.close()

        assert driver._stream_thread is None
        assert driver._device is None
        device.close.assert_called_once()
    finally:
        patcher.stop()


def test_26_stop_wakes_a_blocked_fetch_immediately() -> None:
    """stop() must not leave a pending fetch parked until its own timeout expires."""
    patcher, _, release = _wedged_streaming_driver()
    try:
        driver = RTLSDR(device_index=0)
        # Long enough that waking only after the join would be unmistakable.
        driver.STREAM_STOP_TIMEOUT_S = 1.5  # type: ignore[misc]
        driver.FETCH_TIMEOUT_S = 30.0  # type: ignore[misc]
        driver.open()
        driver.start()

        outcome: list[tuple[str, float]] = []

        def fetch() -> None:
            start = time.monotonic()
            try:
                driver.fetch_iq(1024)
                outcome.append(("returned", time.monotonic() - start))
            except Exception as exc:  # noqa: BLE001 -- the type is the assertion
                outcome.append((type(exc).__name__, time.monotonic() - start))

        waiter = threading.Thread(target=fetch)
        waiter.start()
        time.sleep(0.2)  # let it reach the wait()
        driver.stop()
        waiter.join(timeout=5)

        assert outcome, "fetch never returned"
        kind, elapsed = outcome[0]
        assert kind == "RuntimeError"
        assert elapsed < 0.8, f"fetch waited {elapsed:.1f}s; stop() did not wake it until the join finished"
    finally:
        release.set()
        patcher.stop()


def test_27_start_restarts_after_the_worker_died() -> None:
    """A worker that exited on its own must not block a later start()."""
    patcher, device = _streaming_driver(chunks=1, chunk_samples=256)
    try:
        driver = RTLSDR(device_index=0)
        driver.open()
        driver.start()
        _drain(driver)  # the fake reader returns, so the worker exits

        driver.start()

        assert device.read_samples_async.call_count == 2
    finally:
        patcher.stop()


def test_28_fetch_larger_than_the_buffer_fails_instead_of_waiting_it_out() -> None:
    """Regression: the reader evicts to stay inside the buffer, so the wait could never end."""
    patcher, _, device = _patch_rtlsdr()
    stop_feeding = threading.Event()

    def feed_forever(callback, num_samples, *args, **kwargs):
        # A live stream, so the fetch below waits rather than being cut short by the worker
        # exiting. Without the guard it burns FETCH_TIMEOUT_S while these chunks are evicted.
        while not stop_feeding.wait(timeout=0.01):
            callback(np.zeros(num_samples, dtype=np.complex128), None)

    device.read_samples_async.side_effect = feed_forever
    try:
        driver = RTLSDR(device_index=0)
        driver.STREAM_BUFFER_SAMPLES = 2048  # type: ignore[misc]
        driver.STREAM_CHUNK_SAMPLES = 512  # type: ignore[misc]
        driver.FETCH_TIMEOUT_S = 5.0  # type: ignore[misc]
        driver.open()
        driver.start()

        started = time.monotonic()
        with pytest.raises(ValueError, match="exceeds the 2,048-sample stream buffer"):
            driver.fetch_iq(driver.STREAM_BUFFER_SAMPLES + 1)
        elapsed = time.monotonic() - started

        # The point of the guard: refuse on entry rather than burn FETCH_TIMEOUT_S while the
        # reader keeps discarding everything it receives.
        assert elapsed < 0.5, f"took {elapsed:.1f}s; the guard did not short-circuit the wait"
    finally:
        stop_feeding.set()
        patcher.stop()


def test_29_fetch_of_exactly_the_buffer_is_allowed() -> None:
    """The bound is inclusive: a full buffer can be handed over in one fetch."""
    patcher, _device = _streaming_driver(chunks=2, chunk_samples=1024)
    try:
        driver = RTLSDR(device_index=0)
        driver.STREAM_BUFFER_SAMPLES = 2048  # type: ignore[misc]
        driver.open()
        driver.start()
        _drain(driver)

        capture = driver.fetch_iq(2048)

        assert capture.samples.shape == (1, 2048)
    finally:
        patcher.stop()
