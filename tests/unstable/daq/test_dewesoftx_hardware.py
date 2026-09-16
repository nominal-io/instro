"""Hardware integration test for the DewesoftX DCOM DAQ driver via InstroDAQ.

This test requires a running DewesoftX instance on the same Windows machine with
a setup loaded in Measure mode and its channels set to Used. Each test starts its
own storing session through the driver and stops it at the end: samples only flow
while DewesoftX is storing. The tests exercise the functionality the
DewesoftX driver exposes: attaching over DCOM, binding used channels as voltage,
current, and thermocouple inputs, hardware-timed reads (background and
non-background), software-timed reads, DewesoftX-owned sample-rate reporting,
and buffer-depth telemetry. All DewesoftX access goes through the driver;
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
    2. Ch. setup > Analog in: add two simulated channels, set both to Used.
    3. For the async test: Ch. setup > Math > Add math > Latch value math -> Select AI 1 as the Criteria Channel.
    A latch emits only on trigger events, so its output is an asynchronous channel.
    4. Switch to Measure mode. Each test starts and stops its own storing session.

  Then set SYNC_CHANNEL / SYNC_CHANNEL_2 below to those two channel names
  (the defaults match DewesoftX's "AI 1" and "AI 2"), and ASYNC_CHANNEL to the
  latch channel's name. ASYNC_CHANNEL and EXPECTED_SAMPLE_RATE_HZ are optional;
  None skips their checks.

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

from instro.daq import InstroDAQ  # noqa: E402
from instro.daq.scaling.thermocouple import TC_TYPE, TC_UNIT  # noqa: E402
from instro.daq.types import Logic  # noqa: E402
from instro.lib.types import Measurement  # noqa: E402
from instro.unstable.daq.drivers import DewesoftXDriver  # noqa: E402

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

# The sample rate shown in the DewesoftX setup; None skips the exact check in test_06.
EXPECTED_SAMPLE_RATE_HZ: float | None = None

# Deliberately not a DewesoftX rate: the driver must replace it with the DewesoftX sample rate.
REQUESTED_SAMPLE_RATE_HZ = 12345.0
SAMPLES_PER_CHANNEL = 100
SW_SAMPLE_RATE_HZ = 2.0

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
        daq = InstroDAQ(name=NAME, driver=DewesoftXDriver(dxd_name=dxd_name))
        daq.open()
        # Call the driver, not the HAL: InstroDAQ.start() skips driver.start() when software timing is configured.
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
            daq.configure_ai_hw_sample_rate(REQUESTED_SAMPLE_RATE_HZ, samples_per_channel=SAMPLES_PER_CHANNEL)
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
            daq.configure_ai_hw_sample_rate(REQUESTED_SAMPLE_RATE_HZ, samples_per_channel=SAMPLES_PER_CHANNEL)
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
            daq.configure_ai_hw_sample_rate(REQUESTED_SAMPLE_RATE_HZ, samples_per_channel=SAMPLES_PER_CHANNEL)
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
    # 5. SW-timed analog read with background daemon
    # =====================================================================
    def test_05_sw_timed_read_background(self):
        """Poll the driver at a software rate; each poll drains whatever the session produced since the last."""
        daq = self._create_daq()
        try:
            daq.configure_voltage_input(SYNC_CHANNEL, alias=SYNC_ALIAS)
            daq.configure_ai_sw_sample_rate(SW_SAMPLE_RATE_HZ)
            daq.start()
            try:
                ch = daq.get_channel(SYNC_ALIAS, 1, wait_for_new_samples=True, timeout=DATA_TIMEOUT_S)
                self.assertTrue(ch.values and math.isfinite(ch.latest), f"non-finite SW-timed read: {ch.values}")
                print(f"         {SYNC_ALIAS} (sw-timed) latest = {ch.latest}")
            finally:
                daq.stop()
        finally:
            self._release_daq(daq)

    # =====================================================================
    # 6. Actual sample rate comes from DewesoftX
    # =====================================================================
    def test_06_actual_sample_rate(self):
        """The driver reports the DewesoftX sample rate, not the requested one, as soon as timing is configured."""
        daq = self._create_daq()
        try:
            daq.configure_voltage_input(SYNC_CHANNEL, alias=SYNC_ALIAS)
            self.assertIsNone(daq.get_actual_sample_rate())
            daq.configure_ai_hw_sample_rate(REQUESTED_SAMPLE_RATE_HZ, samples_per_channel=SAMPLES_PER_CHANNEL)
            actual = daq.get_actual_sample_rate()
            print(f"         actual sample rate = {actual} Hz (requested {REQUESTED_SAMPLE_RATE_HZ} Hz)")
            self.assertIsNotNone(actual)
            self.assertGreater(actual, 0)
            self.assertNotEqual(actual, REQUESTED_SAMPLE_RATE_HZ)
            if EXPECTED_SAMPLE_RATE_HZ is not None:
                self.assertEqual(actual, EXPECTED_SAMPLE_RATE_HZ)
            timing = daq.ai_hw_timing_config
            self.assertEqual(timing.sample_rate, actual)
            self.assertEqual(timing.sample_period, round(1e9 / actual))
            self.assertEqual(timing.samples_per_channel, SAMPLES_PER_CHANNEL)
        finally:
            self._release_daq(daq)

    # =====================================================================
    # 7. Asynchronous channel (optional)
    # =====================================================================
    def test_07_async_channel(self):
        """Drain an asynchronous channel: per-sample timestamps arrive ordered and on wall-clock time."""
        if not ASYNC_CHANNEL:
            self.skipTest("ASYNC_CHANNEL is None; set it to a used asynchronous DewesoftX channel")
        daq = self._create_daq()
        try:
            daq.configure_voltage_input(ASYNC_CHANNEL, alias=ASYNC_ALIAS)
            bound_ns = time.time_ns()
            daq.configure_ai_hw_sample_rate(REQUESTED_SAMPLE_RATE_HZ, samples_per_channel=SAMPLES_PER_CHANNEL)
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
    # 8. Digital I/O unsupported
    # =====================================================================
    def test_08_digital_unsupported(self):
        """DewesoftX owns channel setup, so digital line configuration raises NotImplementedError."""
        daq = self._create_daq()
        try:
            with self.assertRaises(NotImplementedError):
                daq.configure_digital_input(SYNC_CHANNEL, logic=Logic.HIGH, alias="di")
            with self.assertRaises(NotImplementedError):
                daq.configure_digital_output(SYNC_CHANNEL, logic=Logic.HIGH, alias="do")
        finally:
            self._release_daq(daq)
