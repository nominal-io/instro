"""Hardware integration test for background-daemon loop timing on NI.

Mirrors ``examples/daq/daq_hw_and_sw_timed_ni.py`` — one hardware-timed and one
software-timed InstroDAQ running side by side — but the only thing asserted is each
daemon's measured loop period. The software-timed daemon paces itself at
``1 / sample_rate``, so it should land on that period. The hardware-timed daemon adds no
wait: ``fetch_analog()`` returns as soon as a full ``samples_per_channel`` batch is
buffered, so its period should be at or under ``samples_per_channel / sample_rate``.

Every daemon publishes ``{name}.loop_time`` (measured iteration period, seconds) on every
cycle; the assertions read that channel straight out of the instrument's channel buffer.

============================================================================
cDAQ WIRING / SETUP
============================================================================

  Required:
    - One NI CompactDAQ chassis reachable in NI MAX (set DEVICE)
    - Two analog input modules (set HW_MODULE / SW_MODULE), each with at least
      CHANNELS_PER_TASK channels. The two instances must not share channels.
    - The SW_MODULE must support on-demand (single-point) reads.

  No signal source or loopback wiring is needed: the assertions are about daemon
  timing, not sample values. Inputs may float.

============================================================================
RUNNING
============================================================================

    uv run --extra nidaq pytest tests/daq/ni -m hardware -v

"""

import math
import statistics
import unittest
import warnings

import pytest

pytest.importorskip("nidaqmx")

from instro.daq import InstroDAQ  # noqa: E402
from instro.daq.drivers.ni import NIDAQDriver  # noqa: E402
from instro.daq.types import Direction  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration — edit before running
# ---------------------------------------------------------------------------
DEVICE = "cDAQ"
CHANNELS_PER_TASK = 3
HW_MODULE = f"{DEVICE}Mod1"
SW_MODULE = f"{DEVICE}Mod2"

HW_SAMPLE_RATE = 50000  # Hz, driven by the device's sample clock
SW_SAMPLE_RATE = 1  # Hz, driven by the background daemon loop

# InstroDAQ defaults samples_per_channel to 10 % of the sample rate, and one fetch blocks
# for a full batch, so that batch is what paces the hardware-timed daemon.
HW_SAMPLES_PER_CHANNEL = max(1, int(HW_SAMPLE_RATE // 10))
EXPECTED_HW_PERIOD_S = HW_SAMPLES_PER_CHANNEL / HW_SAMPLE_RATE
EXPECTED_SW_PERIOD_S = 1 / SW_SAMPLE_RATE

# The daemon sleeps with Event.wait(), so a period can overshoot on OS timer granularity
# and publisher overhead but cannot undershoot the configured interval by more than jitter.
PERIOD_TOLERANCE_S = 0.05
LOOP_SAMPLES = 10  # iterations averaged per assertion
LOOP_TIMEOUT_S = 30.0


@pytest.mark.hardware
class TestNIDAQHWAndSWTimedHardware(unittest.TestCase):
    """Loop-period assertions for the hardware-timed and software-timed background daemons."""

    # -- helpers ----------------------------------------------------------

    def _assert_publishing(self, daq: InstroDAQ, alias: str):
        """The daemon must publish real samples on ``alias``, not just its own loop timing."""
        latest = daq.get_channel(alias, wait_for_new_samples=True, timeout=LOOP_TIMEOUT_S).latest
        self.assertTrue(math.isfinite(latest), f"{daq.name} published a non-finite sample on {alias}: {latest}")

    def _loop_periods(self, daq: InstroDAQ) -> list[float]:
        """Measured loop periods (s) over the daemon's next LOOP_SAMPLES iterations."""
        loop_times = daq.get_channel(
            "loop_time", length=LOOP_SAMPLES, wait_for_new_samples=True, timeout=LOOP_TIMEOUT_S
        ).values
        self.assertEqual(len(loop_times), LOOP_SAMPLES, f"only {len(loop_times)} loop_time samples arrived")
        return loop_times

    # =====================================================================
    # Background daemon loop periods
    # =====================================================================
    def test_background_daemon_loop_periods(self):
        """The SW daemon runs at its configured period; the HW daemon at or under its batch period."""
        daq_hw = InstroDAQ(name="daqHw", driver=NIDAQDriver(device_id=DEVICE))
        daq_sw = InstroDAQ(name="daqSw", driver=NIDAQDriver(device_id=DEVICE))

        with daq_hw, daq_sw:
            # A physical channel belongs to exactly one instance; allocate without overlap.
            for i in range(CHANNELS_PER_TASK):
                daq_hw.configure_analog_channel(
                    direction=Direction.INPUT,
                    physical_channel=f"{HW_MODULE}/ai{i}",
                    alias=f"hw_channel{i}",
                    range_min=0,
                    range_max=5,
                )
                daq_sw.configure_analog_channel(
                    direction=Direction.INPUT,
                    physical_channel=f"{SW_MODULE}/ai{i}",
                    alias=f"sw_channel{i}",
                    range_min=0,
                    range_max=5,
                )

            # One instance is hardware-timed, the other software-timed.
            daq_hw.configure_ai_hw_sample_rate(sample_rate=HW_SAMPLE_RATE)
            daq_sw.configure_ai_sw_sample_rate(sample_rate=SW_SAMPLE_RATE)

            # Each start() launches that instance's own background daemon.
            daq_hw.start()
            daq_sw.start()
            try:
                # Daemon reads that raise are only logged, and loop_time publishes either way,
                # so require real samples first or a broken read reads as perfect pacing.
                self._assert_publishing(daq_sw, "sw_channel0")
                self._assert_publishing(daq_hw, "hw_channel0")

                sw_periods = self._loop_periods(daq_sw)
                hw_period = statistics.mean(self._loop_periods(daq_hw))
            finally:
                daq_sw.stop()
                daq_hw.stop()

        sw_period = statistics.mean(sw_periods)

        self.assertAlmostEqual(
            sw_period,
            EXPECTED_SW_PERIOD_S,
            delta=PERIOD_TOLERANCE_S,
            msg=f"SW daemon ran at {sw_period:.4f} s/cycle, expected {EXPECTED_SW_PERIOD_S:.4f} s",
        )
        self.assertLessEqual(
            hw_period,
            EXPECTED_HW_PERIOD_S + PERIOD_TOLERANCE_S,
            msg=f"HW daemon ran at {hw_period:.4f} s/cycle, expected at most {EXPECTED_HW_PERIOD_S:.4f} s",
        )

    # =====================================================================
    # Unsustainable software-timed rate warns
    # =====================================================================
    def test_sw_timed_rate_warns_when_read_outlasts_the_period(self):
        """A read slower than the requested period must warn, not silently run slow."""
        daq_sw = InstroDAQ(name="daqSw", driver=NIDAQDriver(device_id=DEVICE))

        with daq_sw:
            for i in range(CHANNELS_PER_TASK):
                daq_sw.configure_analog_channel(
                    direction=Direction.INPUT,
                    physical_channel=f"{SW_MODULE}/ai{i}",
                    alias=f"sw_channel{i}",
                    range_min=0,
                    range_max=0.078,
                )

            # Request sample rate that sw timed daemon cannot support
            daq_sw.configure_ai_sw_sample_rate(sample_rate=10)

            # The daemon thread raises the warning; catch_warnings patches module-global state,
            # so it lands here. "always" defeats the dedup on the message text.
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                daq_sw.start()
                try:
                    self._assert_publishing(daq_sw, "sw_channel0")
                    self._loop_periods(daq_sw)
                finally:
                    daq_sw.stop()

        messages = [str(warning.message) for warning in caught]
        self.assertTrue(
            any("maximum read rate" in message for message in messages),
            f"a {SW_SAMPLE_RATE} Hz request the module cannot sustain raised no warning; caught: {messages}",
        )
