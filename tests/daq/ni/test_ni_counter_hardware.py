"""Hardware integration test for NI-DAQmx counter input and output via InstroDAQ.

This test requires two NI devices with two counter terminals each, wired
terminal to terminal. Every test runs two iterations. In the first, device A
drives both terminals and device B measures both. In the second, the roles
swap. So every wire carries a train in both directions, and every counter is
used as an output and as an input.

There is one test per measurement and train type. Pulse count has no
continuous test, because a continuous train has no pulse total to compare
against.

============================================================================
COUNTER LOOPBACK WIRING
============================================================================

  A terminal 0  <--->  B terminal 0
  A terminal 1  <--->  B terminal 1
  A ground      <--->  B ground

  On a 9401 each counter has its own CTR OUT pin: ctr0 is PFI3, and ctr1 is
  PFI7. If a 9401 raises -201133 while it drives, put its terminals on those
  pins.

============================================================================
RUNNING
============================================================================

    uv run pytest tests/daq/ni/test_ni_counter_hardware.py -m hardware -v -s

"""

import time
import unittest
from typing import NamedTuple

import pytest

pytest.importorskip("nidaqmx")

from instro.daq import InstroDAQ  # noqa: E402
from instro.daq.drivers.ni import NIDAQDriver  # noqa: E402
from instro.daq.types import FrequencyPulseConfig  # noqa: E402
from instro.lib.publishers import NominalCorePublisher  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration — edit before running
# ---------------------------------------------------------------------------
# The counters live on the device, and the PFI terminals live on a module in it, so
# each side needs both names. On a desktop board the module suffix is empty.
DEVICE_A = "cDAQ3"  # NI device name as shown in NI MAX (e.g. "cDAQ1")
MODULE_A = "Mod1"  # Module suffix carrying the PFI lines (e.g. "Mod1")
DEVICE_B = "Dev1"
MODULE_B = ""

# Two counter/terminal pairs per device. Terminal 0 on A is wired to terminal 0 on B,
# and terminal 1 on A to terminal 1 on B.
A_COUNTER_0, A_TERMINAL_0 = f"{DEVICE_A}/ctr0", f"/{DEVICE_A}{MODULE_A}/PFI0"
A_COUNTER_1, A_TERMINAL_1 = f"{DEVICE_A}/ctr1", f"/{DEVICE_A}{MODULE_A}/PFI4"
B_COUNTER_0, B_TERMINAL_0 = f"{DEVICE_B}/ctr0", f"/{DEVICE_B}{MODULE_B}/PFI0"
B_COUNTER_1, B_TERMINAL_1 = f"{DEVICE_B}/ctr1", f"/{DEVICE_B}{MODULE_B}/PFI1"

# Set to a Nominal dataset RID to stream validation data; leave None to publish nowhere.
DATASET_RID = None

# The train the driving device generates: 500 Hz at 50% duty, so a finite run of 500 pulses lasts one second.
TEST_FREQUENCY_HZ = 500.0
TEST_DUTY_CYCLE = 0.5
FINITE_PULSES = 500
PULSE_CONFIG = FrequencyPulseConfig(frequency=TEST_FREQUENCY_HZ, duty_cycle=TEST_DUTY_CYCLE)

# What each measurement should report for that train, and how far off it may be.
EXPECTED_FREQUENCY_HZ = 500.0
EXPECTED_PERIOD_S = 0.002
EXPECTED_PULSE_WIDTH_S = 0.001
FREQUENCY_TOLERANCE_HZ = 25.0
PERIOD_TOLERANCE_S = 0.0001
PULSE_WIDTH_TOLERANCE_S = 0.00005

# Ranges handed to the counter inputs: an order of magnitude either side of the train.
FREQUENCY_RANGE_MIN_HZ = 50.0
FREQUENCY_RANGE_MAX_HZ = 5000.0
TIME_RANGE_MIN_S = 0.0002
TIME_RANGE_MAX_S = 0.02

# How long to let the trains run before reading them.
SETTLE_S = 0.1

# Aliases for the two trains on the driving device and the two counters on the measuring device.
OUT_0, OUT_1 = "out_0", "out_1"
IN_0, IN_1 = "in_0", "in_1"


class _Device(NamedTuple):
    """One device and its two counter/terminal pairs."""

    name: str
    device_id: str
    counter_0: str
    terminal_0: str
    counter_1: str
    terminal_1: str


SIDE_A = _Device("A", DEVICE_A, A_COUNTER_0, A_TERMINAL_0, A_COUNTER_1, A_TERMINAL_1)
SIDE_B = _Device("B", DEVICE_B, B_COUNTER_0, B_TERMINAL_0, B_COUNTER_1, B_TERMINAL_1)

# The two iterations of every test, as (device driving both terminals, device measuring both).
DIRECTIONS = [(SIDE_A, SIDE_B), (SIDE_B, SIDE_A)]


# ---------------------------------------------------------------------------
# Test suite
# ---------------------------------------------------------------------------
@pytest.mark.hardware
class TestNICounterHardware(unittest.TestCase):
    """Two counter loopbacks between two NI devices, driven one way and then the other."""

    def _create_daq(self, device: _Device) -> InstroDAQ:
        """Build a DAQ for one device. The ``with`` block in each test opens and closes it."""
        daq = InstroDAQ(name=f"counter_{device.name.lower()}", driver=NIDAQDriver(device_id=device.device_id))
        if DATASET_RID:
            daq.add_publisher(NominalCorePublisher(dataset_rid=DATASET_RID))
        return daq

    # =====================================================================
    # 1. Frequency
    # =====================================================================
    def test_frequency_continuous(self):
        """Both inputs read 500 Hz from continuous trains, with each device taking a turn as the driver."""
        for driver, measurer in DIRECTIONS:
            out_daq = self._create_daq(driver)
            in_daq = self._create_daq(measurer)
            with self.subTest(direction=f"{driver.name} -> {measurer.name}"), out_daq, in_daq:
                # Configure both terminals on the measuring device as frequency inputs.
                # Configure only reserves each task; the first read starts it.
                in_daq.configure_frequency_counter_input(
                    physical_channel=measurer.terminal_0,
                    counter_source=measurer.counter_0,
                    range_min=FREQUENCY_RANGE_MIN_HZ,
                    range_max=FREQUENCY_RANGE_MAX_HZ,
                    alias=IN_0,
                )
                in_daq.configure_frequency_counter_input(
                    physical_channel=measurer.terminal_1,
                    counter_source=measurer.counter_1,
                    range_min=FREQUENCY_RANGE_MIN_HZ,
                    range_max=FREQUENCY_RANGE_MAX_HZ,
                    alias=IN_1,
                )

                # Configure both terminals on the driving device as continuous trains.
                out_daq.configure_continuous_counter_output(
                    physical_channel=driver.terminal_0,
                    counter_source=driver.counter_0,
                    pulse_config=PULSE_CONFIG,
                    alias=OUT_0,
                )
                out_daq.configure_continuous_counter_output(
                    physical_channel=driver.terminal_1,
                    counter_source=driver.counter_1,
                    pulse_config=PULSE_CONFIG,
                    alias=OUT_1,
                )

                # Run both trains at the same time.
                out_daq.start_counter_output(OUT_0)
                out_daq.start_counter_output(OUT_1)
                time.sleep(SETTLE_S)

                # Each input reads the train on the wire it shares with the driving device.
                reading_0 = in_daq.read_counter(IN_0).latest
                reading_1 = in_daq.read_counter(IN_1).latest
                print(f"         {driver.name} -> {measurer.name}: {reading_0:.2f} Hz, {reading_1:.2f} Hz")
                self.assertAlmostEqual(reading_0, EXPECTED_FREQUENCY_HZ, delta=FREQUENCY_TOLERANCE_HZ)
                self.assertAlmostEqual(reading_1, EXPECTED_FREQUENCY_HZ, delta=FREQUENCY_TOLERANCE_HZ)

                out_daq.stop_counter_output(OUT_0)
                out_daq.stop_counter_output(OUT_1)

    def test_frequency_finite(self):
        """Both inputs read 500 Hz from finite trains, with each device taking a turn as the driver."""
        for driver, measurer in DIRECTIONS:
            out_daq = self._create_daq(driver)
            in_daq = self._create_daq(measurer)
            with self.subTest(direction=f"{driver.name} -> {measurer.name}"), out_daq, in_daq:
                in_daq.configure_frequency_counter_input(
                    physical_channel=measurer.terminal_0,
                    counter_source=measurer.counter_0,
                    range_min=FREQUENCY_RANGE_MIN_HZ,
                    range_max=FREQUENCY_RANGE_MAX_HZ,
                    alias=IN_0,
                )
                in_daq.configure_frequency_counter_input(
                    physical_channel=measurer.terminal_1,
                    counter_source=measurer.counter_1,
                    range_min=FREQUENCY_RANGE_MIN_HZ,
                    range_max=FREQUENCY_RANGE_MAX_HZ,
                    alias=IN_1,
                )

                # A finite train of 500 pulses at 500 Hz runs for one second, so the reads below
                # land while it is still pulsing.
                out_daq.configure_finite_counter_output(
                    physical_channel=driver.terminal_0,
                    counter_source=driver.counter_0,
                    pulse_config=PULSE_CONFIG,
                    n_pulses=FINITE_PULSES,
                    alias=OUT_0,
                )
                out_daq.configure_finite_counter_output(
                    physical_channel=driver.terminal_1,
                    counter_source=driver.counter_1,
                    pulse_config=PULSE_CONFIG,
                    n_pulses=FINITE_PULSES,
                    alias=OUT_1,
                )

                out_daq.start_counter_output(OUT_0)
                out_daq.start_counter_output(OUT_1)
                time.sleep(SETTLE_S)

                reading_0 = in_daq.read_counter(IN_0).latest
                reading_1 = in_daq.read_counter(IN_1).latest
                print(f"         {driver.name} -> {measurer.name}: {reading_0:.2f} Hz, {reading_1:.2f} Hz")
                self.assertAlmostEqual(reading_0, EXPECTED_FREQUENCY_HZ, delta=FREQUENCY_TOLERANCE_HZ)
                self.assertAlmostEqual(reading_1, EXPECTED_FREQUENCY_HZ, delta=FREQUENCY_TOLERANCE_HZ)

                # Let both trains finish, which checks the wait returns rather than timing out.
                out_daq.wait_for_counter_output(OUT_0)
                out_daq.wait_for_counter_output(OUT_1)
                out_daq.stop_counter_output(OUT_0)
                out_daq.stop_counter_output(OUT_1)

    # =====================================================================
    # 2. Period
    # =====================================================================
    def test_period_continuous(self):
        """Both inputs read a 2 ms period from continuous trains, with each device taking a turn as the driver."""
        for driver, measurer in DIRECTIONS:
            out_daq = self._create_daq(driver)
            in_daq = self._create_daq(measurer)
            with self.subTest(direction=f"{driver.name} -> {measurer.name}"), out_daq, in_daq:
                in_daq.configure_period_counter_input(
                    physical_channel=measurer.terminal_0,
                    counter_source=measurer.counter_0,
                    range_min=TIME_RANGE_MIN_S,
                    range_max=TIME_RANGE_MAX_S,
                    alias=IN_0,
                )
                in_daq.configure_period_counter_input(
                    physical_channel=measurer.terminal_1,
                    counter_source=measurer.counter_1,
                    range_min=TIME_RANGE_MIN_S,
                    range_max=TIME_RANGE_MAX_S,
                    alias=IN_1,
                )

                out_daq.configure_continuous_counter_output(
                    physical_channel=driver.terminal_0,
                    counter_source=driver.counter_0,
                    pulse_config=PULSE_CONFIG,
                    alias=OUT_0,
                )
                out_daq.configure_continuous_counter_output(
                    physical_channel=driver.terminal_1,
                    counter_source=driver.counter_1,
                    pulse_config=PULSE_CONFIG,
                    alias=OUT_1,
                )

                out_daq.start_counter_output(OUT_0)
                out_daq.start_counter_output(OUT_1)
                time.sleep(SETTLE_S)

                reading_0 = in_daq.read_counter(IN_0).latest
                reading_1 = in_daq.read_counter(IN_1).latest
                print(f"         {driver.name} -> {measurer.name}: {reading_0:.6f} s, {reading_1:.6f} s")
                self.assertAlmostEqual(reading_0, EXPECTED_PERIOD_S, delta=PERIOD_TOLERANCE_S)
                self.assertAlmostEqual(reading_1, EXPECTED_PERIOD_S, delta=PERIOD_TOLERANCE_S)

                out_daq.stop_counter_output(OUT_0)
                out_daq.stop_counter_output(OUT_1)

    def test_period_finite(self):
        """Both inputs read a 2 ms period from finite trains, with each device taking a turn as the driver."""
        for driver, measurer in DIRECTIONS:
            out_daq = self._create_daq(driver)
            in_daq = self._create_daq(measurer)
            with self.subTest(direction=f"{driver.name} -> {measurer.name}"), out_daq, in_daq:
                in_daq.configure_period_counter_input(
                    physical_channel=measurer.terminal_0,
                    counter_source=measurer.counter_0,
                    range_min=TIME_RANGE_MIN_S,
                    range_max=TIME_RANGE_MAX_S,
                    alias=IN_0,
                )
                in_daq.configure_period_counter_input(
                    physical_channel=measurer.terminal_1,
                    counter_source=measurer.counter_1,
                    range_min=TIME_RANGE_MIN_S,
                    range_max=TIME_RANGE_MAX_S,
                    alias=IN_1,
                )

                out_daq.configure_finite_counter_output(
                    physical_channel=driver.terminal_0,
                    counter_source=driver.counter_0,
                    pulse_config=PULSE_CONFIG,
                    n_pulses=FINITE_PULSES,
                    alias=OUT_0,
                )
                out_daq.configure_finite_counter_output(
                    physical_channel=driver.terminal_1,
                    counter_source=driver.counter_1,
                    pulse_config=PULSE_CONFIG,
                    n_pulses=FINITE_PULSES,
                    alias=OUT_1,
                )

                out_daq.start_counter_output(OUT_0)
                out_daq.start_counter_output(OUT_1)
                time.sleep(SETTLE_S)

                reading_0 = in_daq.read_counter(IN_0).latest
                reading_1 = in_daq.read_counter(IN_1).latest
                print(f"         {driver.name} -> {measurer.name}: {reading_0:.6f} s, {reading_1:.6f} s")
                self.assertAlmostEqual(reading_0, EXPECTED_PERIOD_S, delta=PERIOD_TOLERANCE_S)
                self.assertAlmostEqual(reading_1, EXPECTED_PERIOD_S, delta=PERIOD_TOLERANCE_S)

                out_daq.wait_for_counter_output(OUT_0)
                out_daq.wait_for_counter_output(OUT_1)
                out_daq.stop_counter_output(OUT_0)
                out_daq.stop_counter_output(OUT_1)

    # =====================================================================
    # 3. Pulse width
    # =====================================================================
    def test_pulse_width_continuous(self):
        """Both inputs read a 1 ms high time from continuous trains, with each device taking a turn as the driver."""
        for driver, measurer in DIRECTIONS:
            out_daq = self._create_daq(driver)
            in_daq = self._create_daq(measurer)
            with self.subTest(direction=f"{driver.name} -> {measurer.name}"), out_daq, in_daq:
                in_daq.configure_pulse_width_counter_input(
                    physical_channel=measurer.terminal_0,
                    counter_source=measurer.counter_0,
                    range_min=TIME_RANGE_MIN_S,
                    range_max=TIME_RANGE_MAX_S,
                    alias=IN_0,
                )
                in_daq.configure_pulse_width_counter_input(
                    physical_channel=measurer.terminal_1,
                    counter_source=measurer.counter_1,
                    range_min=TIME_RANGE_MIN_S,
                    range_max=TIME_RANGE_MAX_S,
                    alias=IN_1,
                )

                out_daq.configure_continuous_counter_output(
                    physical_channel=driver.terminal_0,
                    counter_source=driver.counter_0,
                    pulse_config=PULSE_CONFIG,
                    alias=OUT_0,
                )
                out_daq.configure_continuous_counter_output(
                    physical_channel=driver.terminal_1,
                    counter_source=driver.counter_1,
                    pulse_config=PULSE_CONFIG,
                    alias=OUT_1,
                )

                out_daq.start_counter_output(OUT_0)
                out_daq.start_counter_output(OUT_1)
                time.sleep(SETTLE_S)

                reading_0 = in_daq.read_counter(IN_0).latest
                reading_1 = in_daq.read_counter(IN_1).latest
                print(f"         {driver.name} -> {measurer.name}: {reading_0:.6f} s, {reading_1:.6f} s")
                self.assertAlmostEqual(reading_0, EXPECTED_PULSE_WIDTH_S, delta=PULSE_WIDTH_TOLERANCE_S)
                self.assertAlmostEqual(reading_1, EXPECTED_PULSE_WIDTH_S, delta=PULSE_WIDTH_TOLERANCE_S)

                out_daq.stop_counter_output(OUT_0)
                out_daq.stop_counter_output(OUT_1)

    def test_pulse_width_finite(self):
        """Both inputs read a 1 ms high time from finite trains, with each device taking a turn as the driver."""
        for driver, measurer in DIRECTIONS:
            out_daq = self._create_daq(driver)
            in_daq = self._create_daq(measurer)
            with self.subTest(direction=f"{driver.name} -> {measurer.name}"), out_daq, in_daq:
                in_daq.configure_pulse_width_counter_input(
                    physical_channel=measurer.terminal_0,
                    counter_source=measurer.counter_0,
                    range_min=TIME_RANGE_MIN_S,
                    range_max=TIME_RANGE_MAX_S,
                    alias=IN_0,
                )
                in_daq.configure_pulse_width_counter_input(
                    physical_channel=measurer.terminal_1,
                    counter_source=measurer.counter_1,
                    range_min=TIME_RANGE_MIN_S,
                    range_max=TIME_RANGE_MAX_S,
                    alias=IN_1,
                )

                out_daq.configure_finite_counter_output(
                    physical_channel=driver.terminal_0,
                    counter_source=driver.counter_0,
                    pulse_config=PULSE_CONFIG,
                    n_pulses=FINITE_PULSES,
                    alias=OUT_0,
                )
                out_daq.configure_finite_counter_output(
                    physical_channel=driver.terminal_1,
                    counter_source=driver.counter_1,
                    pulse_config=PULSE_CONFIG,
                    n_pulses=FINITE_PULSES,
                    alias=OUT_1,
                )

                out_daq.start_counter_output(OUT_0)
                out_daq.start_counter_output(OUT_1)
                time.sleep(SETTLE_S)

                reading_0 = in_daq.read_counter(IN_0).latest
                reading_1 = in_daq.read_counter(IN_1).latest
                print(f"         {driver.name} -> {measurer.name}: {reading_0:.6f} s, {reading_1:.6f} s")
                self.assertAlmostEqual(reading_0, EXPECTED_PULSE_WIDTH_S, delta=PULSE_WIDTH_TOLERANCE_S)
                self.assertAlmostEqual(reading_1, EXPECTED_PULSE_WIDTH_S, delta=PULSE_WIDTH_TOLERANCE_S)

                out_daq.wait_for_counter_output(OUT_0)
                out_daq.wait_for_counter_output(OUT_1)
                out_daq.stop_counter_output(OUT_0)
                out_daq.stop_counter_output(OUT_1)

    # =====================================================================
    # 4. Pulse count
    # =====================================================================
    def test_pulse_count_finite(self):
        """Both inputs count exactly 500 pulses from finite trains, with each device taking a turn as the driver."""
        for driver, measurer in DIRECTIONS:
            out_daq = self._create_daq(driver)
            in_daq = self._create_daq(measurer)
            with self.subTest(direction=f"{driver.name} -> {measurer.name}"), out_daq, in_daq:
                # Edge counting takes no range.
                in_daq.configure_pulse_count_counter_input(
                    physical_channel=measurer.terminal_0,
                    counter_source=measurer.counter_0,
                    alias=IN_0,
                )
                in_daq.configure_pulse_count_counter_input(
                    physical_channel=measurer.terminal_1,
                    counter_source=measurer.counter_1,
                    alias=IN_1,
                )

                out_daq.configure_finite_counter_output(
                    physical_channel=driver.terminal_0,
                    counter_source=driver.counter_0,
                    pulse_config=PULSE_CONFIG,
                    n_pulses=FINITE_PULSES,
                    alias=OUT_0,
                )
                out_daq.configure_finite_counter_output(
                    physical_channel=driver.terminal_1,
                    counter_source=driver.counter_1,
                    pulse_config=PULSE_CONFIG,
                    n_pulses=FINITE_PULSES,
                    alias=OUT_1,
                )

                # Read each counter once before the trains start. The first read starts the
                # counter task, so this arms both counters and gives the count they start from.
                start_0 = in_daq.read_counter(IN_0).latest
                start_1 = in_daq.read_counter(IN_1).latest

                # Run both trains to completion, then count what arrived.
                out_daq.start_counter_output(OUT_0)
                out_daq.start_counter_output(OUT_1)
                out_daq.wait_for_counter_output(OUT_0)
                out_daq.wait_for_counter_output(OUT_1)

                counted_0 = in_daq.read_counter(IN_0).latest - start_0
                counted_1 = in_daq.read_counter(IN_1).latest - start_1
                print(f"         {driver.name} -> {measurer.name}: {counted_0:.0f} edges, {counted_1:.0f} edges")
                self.assertEqual(counted_0, FINITE_PULSES)
                self.assertEqual(counted_1, FINITE_PULSES)

                out_daq.stop_counter_output(OUT_0)
                out_daq.stop_counter_output(OUT_1)


if __name__ == "__main__":
    unittest.main()
