"""Hardware integration test for the DewesoftX DCOM DAQ driver via InstroDAQ.

This test requires a running DewesoftX instance on the same Windows machine with
a setup loaded in Measure mode and its channels set to Used. Each test starts its
own storing session through the driver and stops it at the end: samples only flow
while DewesoftX is storing. The tests exercise the functionality the
DewesoftX driver exposes: attaching over DCOM, binding used channels as voltage,
current, and thermocouple inputs, hardware-timed reads (background and
non-background), DewesoftX-owned sample-rate reporting, buffer-depth
telemetry, and reading again after a close and reopen. The driver does not support software timing, so nothing here calls
configure_ai_sw_sample_rate(). All DewesoftX access goes through the driver;
nothing in this file talks DCOM directly.

DewesoftX owns all channel setup, scaling, and the sample clock, so there is no
loopback wiring: the tests check structure and time-axis correctness, not values.
Digital I/O and relays are not supported by this driver, so digital configuration
is checked to raise NotImplementedError.

============================================================================
DEWESOFTX SETUP
============================================================================

  No hardware needed. In DewesoftX:
    1. Settings > Hardware setup > Devices: set the device to Offline.
    2. Ch. setup > Analog in: add two simulated channels, set both to Used (named AI 1 and AI 2 by default).
    3. Set Dynamic acquisition rate to 5000
    4. For the async test: Ch. setup > Math > Add math > Latch value math -> Select AI 1 as the Criteria Channel.
    A latch emits only on trigger events, so its output is an asynchronous channel.

  Then set SYNC_CHANNEL / SYNC_CHANNEL_2 below to those two channel names
  (the defaults match DewesoftX's "AI 1" and "AI 2"), and ASYNC_CHANNEL to the
  latch channel's name, and SAMPLE_RATE_HZ to the rate the setup runs at.
  ASYNC_CHANNEL is optional; None skips the async test.

============================================================================
RUNNING
============================================================================

    uv run pytest tests/unstable/daq -m hardware -v -s

"""

import math
import time
import unittest

import pytest

pytest.importorskip("win32com")

from instro.daq import HWTimingException, InstroDAQ  # noqa: E402
from instro.daq.scaling.thermocouple import TC_TYPE, TC_UNIT  # noqa: E402
from instro.daq.types import Logic  # noqa: E402
from instro.lib.types import Measurement  # noqa: E402
from instro.unstable.daq.drivers import DewesoftX  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration — edit before running
# ---------------------------------------------------------------------------
NAME = "dewesoftx_validate"

# Synchronous channels — DewesoftX Name or LongName of two channels set to "Used".
SYNC_CHANNEL, SYNC_ALIAS = "AI 1", "sync_1"
SYNC_CHANNEL_2, SYNC_ALIAS_2 = "AI 2", "sync_2"

# Asynchronous channel (per-sample timestamps, e.g. a CAN signal's LongName); None skips the async test.
ASYNC_CHANNEL: str | None = "AI 1/Latch"
ASYNC_ALIAS = "async_1"

# Must equal the sample rate shown in the DewesoftX setup; the driver rejects any other value.
SAMPLE_RATE_HZ = 5000.0
SAMPLES_PER_CHANNEL = 100

# How long to wait for samples to arrive.
DATA_TIMEOUT_S = 10.0

# How far the driver's absolute time axis may sit from this PC's clock. It is anchored at the
# store-start time DewesoftX reports and advanced by sample count, so a small offset is expected.
WALL_CLOCK_TOLERANCE_NS = int(5.0 * 1e9)


def _sample_period_ns(timestamps: list[int]) -> int:
    """Return the constant spacing of a sync batch; fail if any gap deviates by more than the 1 ns rounding."""
    diffs = [b - a for a, b in zip(timestamps, timestamps[1:])]
    dt = diffs[0]
    assert all(abs(d - dt) <= 1 for d in diffs), f"non-uniform sync timestamps: gaps span {min(diffs)}..{max(diffs)} ns"
    return dt


@pytest.mark.hardware
class TestDewesoftXHardware(unittest.TestCase):
    """Hardware integration tests for the DewesoftX driver via InstroDAQ.

    Each test creates, opens, and configures its own DAQ instance, runs its own
    storing session, and closes both, making every test independent.
    """

    # -- helpers ----------------------------------------------------------

    def _create_daq(self) -> InstroDAQ:
        """Create and open a DAQ, then start its own storing session; reads stay empty until DewesoftX stores."""
        # Timestamped: DewesoftX opens a modal overwrite prompt when the data file exists, which blocks every DCOM call.
        dxd_name = f"instro_{self._testMethodName}_{time.strftime('%Y%m%d_%H%M%S')}.dxd"
        daq = InstroDAQ(name=NAME, driver=DewesoftX(dxd_name=dxd_name))
        daq.open()
        # Start the session through the driver, before any channel binds: cursors seed on the live session's
        # anchor, and the tests that never call the HAL's start() still need samples flowing.
        daq.driver.start(start_storing_session=True)
        return daq

    def _release_daq(self, daq: InstroDAQ) -> None:
        """Stop this test's storing session, then close the DAQ."""
        daq.driver.stop(stop_storing_session=True)
        daq.close()

    def _wait_for_batch(self, daq: InstroDAQ, alias: str) -> Measurement:
        """Fetch until the driver returns samples for ``alias``; async channels only deliver when their source does."""
        deadline = time.monotonic() + DATA_TIMEOUT_S
        while time.monotonic() < deadline:
            batch = daq.read_analog()
            for measurement in batch if isinstance(batch, list) else [batch]:
                if f"{NAME}.{alias}" in measurement.channel_data:
                    return measurement
        self.fail(f"no samples for '{alias}' within {DATA_TIMEOUT_S}s; is that channel Used and producing data?")

    # =====================================================================
    # 1. Attach and bind the configured channels
    # =====================================================================
    def test_01_attach_and_bind_channels(self):
        """open() attaches to DewesoftX and every configured channel resolves to a used channel of the running setup."""
        daq = self._create_daq()
        try:
            channels = {SYNC_CHANNEL: SYNC_ALIAS, SYNC_CHANNEL_2: SYNC_ALIAS_2}
            if ASYNC_CHANNEL:
                channels[ASYNC_CHANNEL] = ASYNC_ALIAS
            # Binding raises ValueError when DewesoftX has no used channel by that name; that is the config check.
            for physical, alias in channels.items():
                daq.configure_voltage_input(physical, alias=alias)
            self.assertEqual(set(daq.ai_channels), set(channels.values()))
            daq.configure_ai_hw_sample_rate(SAMPLE_RATE_HZ, samples_per_channel=SAMPLES_PER_CHANNEL)
            print(
                f"         DewesoftX sample rate = {daq.get_actual_sample_rate()} Hz, bound {len(channels)} channel(s)"
            )
        finally:
            self._release_daq(daq)

    # =====================================================================
    # 2. Analog input configuration
    # =====================================================================
    def test_02_configure_inputs(self):
        """Bind one DewesoftX channel as voltage, current, and thermocouple inputs; reject unknown channels."""
        daq = self._create_daq()
        try:
            daq.configure_voltage_input(SYNC_CHANNEL, alias="as_voltage")
            daq.configure_current_input(SYNC_CHANNEL, alias="as_current")
            daq.configure_thermocouple_input(SYNC_CHANNEL, TC_TYPE.K, unit=TC_UNIT.CELSIUS, alias="as_thermocouple")
            self.assertEqual(set(daq.ai_channels), {"as_voltage", "as_current", "as_thermocouple"})
            for channel in daq.ai_channels.values():
                self.assertEqual(channel.physical_channel, SYNC_CHANNEL)

            with self.assertRaises(ValueError):
                daq.configure_voltage_input("<no such dewesoft channel>", alias="missing")
            self.assertNotIn("missing", daq.ai_channels)
        finally:
            self._release_daq(daq)

    # =====================================================================
    # 3. HW-timed analog read without background daemon
    # =====================================================================
    def test_03_hw_timed_read_no_background(self):
        """Fetch two batches directly and check the sync time axis is uniform, continuous, and on wall-clock time."""
        daq = self._create_daq()
        try:
            # Binding seeds the read cursor at "now": every sample fetched below dates from after this point.
            daq.configure_voltage_input(SYNC_CHANNEL, alias=SYNC_ALIAS)
            bound_ns = time.time_ns()
            daq.configure_ai_hw_sample_rate(SAMPLE_RATE_HZ, samples_per_channel=SAMPLES_PER_CHANNEL)
            daq.start(background=False)
            try:
                rate = daq.get_actual_sample_rate()
                first = daq.read(SYNC_ALIAS)
                second = daq.read(SYNC_ALIAS)
                now_ns = time.time_ns()
                for batch in (first, second):
                    self.assertGreaterEqual(len(batch.values), SAMPLES_PER_CHANNEL)
                    self.assertTrue(all(math.isfinite(v) for v in batch.values), "non-finite samples in fetch")
                dt = _sample_period_ns(first.timestamps)
                print(f"         fetched {len(first.values)} + {len(second.values)} samples, dt={dt} ns at {rate} Hz")

                # Sync channels store no timestamps: the driver derives them from the DewesoftX rate and the
                # channel's sample-rate divider, so dt must be a whole multiple of the master period.
                divider = dt * rate / 1e9
                self.assertAlmostEqual(
                    divider, round(divider), delta=1e-3, msg=f"dt {dt} ns is not a multiple of 1/{rate} s"
                )
                self.assertGreaterEqual(round(divider), 1)
                # Consecutive fetches continue the same sample index with no gap or overlap.
                self.assertAlmostEqual(second.timestamps[0] - first.timestamps[-1], dt, delta=1)
                # The time axis is absolute: the first sample dates from the bind and the last from just now.
                self.assertGreaterEqual(first.timestamps[0], bound_ns - WALL_CLOCK_TOLERANCE_NS)
                self.assertGreaterEqual(second.timestamps[-1], now_ns - WALL_CLOCK_TOLERANCE_NS)
                self.assertLessEqual(second.timestamps[-1], now_ns + WALL_CLOCK_TOLERANCE_NS)
            finally:
                daq.stop()
        finally:
            self._release_daq(daq)

    # =====================================================================
    # 4. HW-timed analog read with background daemon (two channels)
    # =====================================================================
    def test_04_hw_timed_read_background(self):
        """Stream two sync channels through the background daemon, which re-attaches COM on its own thread."""
        daq = self._create_daq()
        try:
            daq.configure_voltage_input(SYNC_CHANNEL, alias=SYNC_ALIAS)
            daq.configure_voltage_input(SYNC_CHANNEL_2, alias=SYNC_ALIAS_2)
            daq.configure_ai_hw_sample_rate(SAMPLE_RATE_HZ, samples_per_channel=SAMPLES_PER_CHANNEL)
            daq.start()
            try:
                for alias in (SYNC_ALIAS, SYNC_ALIAS_2):
                    ch = daq.get_channel(alias, SAMPLES_PER_CHANNEL, wait_for_new_samples=True, timeout=DATA_TIMEOUT_S)
                    self.assertGreaterEqual(len(ch.values), SAMPLES_PER_CHANNEL)
                    self.assertTrue(all(math.isfinite(v) for v in ch.values), f"non-finite samples on {alias}")
                    _sample_period_ns(ch.timestamps)
                    print(f"         {alias}: {len(ch.values)} buffered samples, latest = {ch.latest}")
                depth = daq.get_points_in_buffer().latest
                print(f"         points_in_buffer telemetry = {depth}")
                self.assertTrue(math.isfinite(depth) and depth >= 0, f"invalid buffer depth: {depth}")
            finally:
                daq.stop()
        finally:
            self._release_daq(daq)

    # =====================================================================
    # 5. The requested sample rate has to be DewesoftX's own
    # =====================================================================
    def test_05_sample_rate_must_match_dewesoftx(self):
        """A rate DewesoftX is not running is rejected; the matching rate is accepted and reported back."""
        daq = self._create_daq()
        try:
            daq.configure_voltage_input(SYNC_CHANNEL, alias=SYNC_ALIAS)
            self.assertIsNone(daq.get_actual_sample_rate())

            # InstroDAQ derives the batch size and the channel buffer from the requested rate, so a
            # mismatch has to raise rather than be silently corrected.
            with self.assertRaises(HWTimingException) as caught:
                daq.configure_ai_hw_sample_rate(SAMPLE_RATE_HZ + 1, samples_per_channel=SAMPLES_PER_CHANNEL)
            print(f"         rejected mismatch: {caught.exception}")
            self.assertIsNone(daq.get_actual_sample_rate())

            daq.configure_ai_hw_sample_rate(SAMPLE_RATE_HZ, samples_per_channel=SAMPLES_PER_CHANNEL)
            timing = daq.ai_hw_timing_config
            self.assertEqual(daq.get_actual_sample_rate(), SAMPLE_RATE_HZ)
            self.assertEqual(timing.sample_rate, SAMPLE_RATE_HZ)
            self.assertEqual(timing.sample_period, round(1e9 / SAMPLE_RATE_HZ))
            self.assertEqual(timing.samples_per_channel, SAMPLES_PER_CHANNEL)
        finally:
            self._release_daq(daq)

    # =====================================================================
    # 6. Asynchronous channel (optional)
    # =====================================================================
    def test_06_async_channel(self):
        """Drain an asynchronous channel: per-sample timestamps arrive ordered and on wall-clock time."""
        if not ASYNC_CHANNEL:
            self.skipTest("ASYNC_CHANNEL is None; set it to a used asynchronous DewesoftX channel")
        daq = self._create_daq()
        try:
            daq.configure_voltage_input(ASYNC_CHANNEL, alias=ASYNC_ALIAS)
            bound_ns = time.time_ns()
            daq.configure_ai_hw_sample_rate(SAMPLE_RATE_HZ, samples_per_channel=SAMPLES_PER_CHANNEL)
            daq.start(background=False)
            try:
                batch = self._wait_for_batch(daq, ASYNC_ALIAS)
                now_ns = time.time_ns()
                print(f"         {ASYNC_ALIAS}: {len(batch.values)} async samples, latest = {batch.latest}")
                self.assertTrue(all(math.isfinite(v) for v in batch.values), "non-finite async samples")
                self.assertEqual(batch.timestamps, sorted(batch.timestamps), "async timestamps out of order")
                self.assertGreaterEqual(batch.timestamps[0], bound_ns - WALL_CLOCK_TOLERANCE_NS)
                self.assertLessEqual(batch.timestamps[-1], now_ns + WALL_CLOCK_TOLERANCE_NS)
            finally:
                daq.stop()
        finally:
            self._release_daq(daq)

    # =====================================================================
    # 7. Digital I/O unsupported
    # =====================================================================
    def test_07_digital_unsupported(self):
        """DewesoftX owns channel setup, so digital line configuration raises NotImplementedError."""
        daq = self._create_daq()
        try:
            with self.assertRaises(NotImplementedError):
                daq.configure_digital_input(SYNC_CHANNEL, logic=Logic.HIGH, alias="di")
            with self.assertRaises(NotImplementedError):
                daq.configure_digital_output(SYNC_CHANNEL, logic=Logic.HIGH, alias="do")
        finally:
            self._release_daq(daq)

    # =====================================================================
    # 8. Close and reopen
    # =====================================================================
    def test_08_close_and_reopen(self):
        """A reopened driver reads without reconfiguring, and a surviving alias is still rejected as a duplicate."""
        daq = self._create_daq()
        try:
            daq.configure_voltage_input(SYNC_CHANNEL, alias=SYNC_ALIAS)
            daq.configure_ai_hw_sample_rate(SAMPLE_RATE_HZ, samples_per_channel=SAMPLES_PER_CHANNEL)
            daq.start(background=False)
            before = daq.read(SYNC_ALIAS)
            daq.stop()

            # close() drops the COM references and the read cursors, but keeps the channels and the timing config
            daq.close()
            daq.open()
            self.assertEqual(set(daq.ai_channels), {SYNC_ALIAS})
            self.assertTrue(daq.is_hw_timing_configured)

            # Rebinding a surviving alias stays a duplicate, the same as on every other driver
            with self.assertRaises(ValueError):
                daq.configure_voltage_input(SYNC_CHANNEL, alias=SYNC_ALIAS)

            # open() reseeded the cursor, so the channel reads again with no reconfiguration
            daq.start(background=False)
            after = daq.read(SYNC_ALIAS)
            daq.stop()
            print(f"         read {len(after.values)} samples after reopen, with no reconfiguration")
            self.assertGreaterEqual(len(after.values), SAMPLES_PER_CHANNEL)
            self.assertTrue(all(math.isfinite(v) for v in after.values), "non-finite samples after reopen")
            self.assertGreater(after.timestamps[0], before.timestamps[-1])
        finally:
            self._release_daq(daq)
