"""Hardware integration test for NI-DAQmx counter input and output via InstroDAQ.

This test requires two NI devices wired with two loopbacks, one in each
direction. Both loopbacks run at the same time, so every test holds two
counter outputs and two counter inputs open together and each device acts as
driver and measurer at once.

There is one test per measurement and train type. Pulse count has no
continuous test, because a continuous train has no pulse total to compare
against.

============================================================================
COUNTER LOOPBACK WIRING
============================================================================

  Loopback 1:  Device A counter out  --->  Device B counter in
  Loopback 2:  Device B counter out  --->  Device A counter in
               Device A ground       <-->  Device B ground

  On a 9401 a counter drives its own CTR OUT pin: ctr0 leaves by PFI3, and
  ctr1 by PFI7. Routing a counter to any other PFI needs a route, and a
  second route cannot take the module-wide lock it needs (DAQmx -201133).
  The counter input may use any PFI line.

============================================================================
RUNNING
============================================================================

    uv run pytest tests/daq/ni/test_ni_counter_hardware.py -m hardware -v -s

"""

import time
import unittest

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
# each side needs both names. On a desktop board the two are the same name.
DEVICE_A = "cDAQ3"  # NI device name as shown in NI MAX (e.g. "cDAQ1")
MODULE_A = "Mod1"  # Module carrying the PFI lines (e.g. "cDAQ1Mod1")
DEVICE_B = "Dev1"
MODULE_B = ""

# Per side: the counter that generates the train and the terminal it leaves by,
# then the counter that measures and the terminal the signal arrives on.
A_OUT_COUNTER, A_OUT_TERMINAL = f"{DEVICE_A}/ctr0", f"/{DEVICE_A}{MODULE_A}/PFI0"
A_IN_COUNTER, A_IN_TERMINAL = f"{DEVICE_A}/ctr1", f"/{DEVICE_A}{MODULE_A}/PFI4"
B_OUT_COUNTER, B_OUT_TERMINAL = f"{DEVICE_B}/ctr0", f"/{DEVICE_B}{MODULE_B}/PFI0"
B_IN_COUNTER, B_IN_TERMINAL = f"{DEVICE_B}/ctr1", f"/{DEVICE_B}{MODULE_B}/PFI1"

# Set to a Nominal dataset RID to stream validation data; leave None to publish nowhere.
DATASET_RID = None

# The train both devices generate: 500 Hz at 50% duty, so a finite run of 500 pulses lasts one second.
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

OUT_ALIAS = "train"
IN_ALIAS = "counter"


# ---------------------------------------------------------------------------
# Test suite
# ---------------------------------------------------------------------------
@pytest.mark.hardware
class TestNICounterHardware(unittest.TestCase):
    """Two counter loopbacks between two NI devices, driven and measured at the same time."""

    def setUp(self):
        """Open one DAQ per device. Every test configures its own channels on them."""
        self.daq_a = InstroDAQ(name="counter_a", driver=NIDAQDriver(device_id=DEVICE_A))
        self.daq_b = InstroDAQ(name="counter_b", driver=NIDAQDriver(device_id=DEVICE_B))
        if DATASET_RID:
            self.daq_a.add_publisher(NominalCorePublisher(dataset_rid=DATASET_RID))
            self.daq_b.add_publisher(NominalCorePublisher(dataset_rid=DATASET_RID))
        self.daq_a.open()
        self.daq_b.open()

    def tearDown(self):
        """Close both DAQs, which closes every counter task they own."""
        self.daq_a.close()
        self.daq_b.close()

    # =====================================================================
    # 1. Frequency
    # =====================================================================
    def test_frequency_continuous(self):
        """Each device reads 500 Hz from the continuous train the other device sends."""
        # Configure a frequency counter input on each device. A counter input task starts
        # on configure, so both are measuring before either train begins.
        self.daq_a.configure_frequency_counter_input(
            physical_channel=A_IN_TERMINAL,
            counter_source=A_IN_COUNTER,
            range_min=FREQUENCY_RANGE_MIN_HZ,
            range_max=FREQUENCY_RANGE_MAX_HZ,
            alias=IN_ALIAS,
        )
        self.daq_b.configure_frequency_counter_input(
            physical_channel=B_IN_TERMINAL,
            counter_source=B_IN_COUNTER,
            range_min=FREQUENCY_RANGE_MIN_HZ,
            range_max=FREQUENCY_RANGE_MAX_HZ,
            alias=IN_ALIAS,
        )

        # Configure a continuous train on each device.
        self.daq_a.configure_continuous_counter_output(
            physical_channel=A_OUT_TERMINAL,
            counter_source=A_OUT_COUNTER,
            pulse_config=PULSE_CONFIG,
            alias=OUT_ALIAS,
        )
        self.daq_b.configure_continuous_counter_output(
            physical_channel=B_OUT_TERMINAL,
            counter_source=B_OUT_COUNTER,
            pulse_config=PULSE_CONFIG,
            alias=OUT_ALIAS,
        )

        # Run both trains at the same time.
        self.daq_a.start_counter_output(OUT_ALIAS)
        self.daq_b.start_counter_output(OUT_ALIAS)
        time.sleep(SETTLE_S)

        # Device B measures device A's train, and device A measures device B's.
        a_to_b = self.daq_b.read_counter(IN_ALIAS).latest
        b_to_a = self.daq_a.read_counter(IN_ALIAS).latest
        print(f"         A->B {a_to_b:.2f} Hz, B->A {b_to_a:.2f} Hz, expected {EXPECTED_FREQUENCY_HZ} Hz")
        self.assertAlmostEqual(a_to_b, EXPECTED_FREQUENCY_HZ, delta=FREQUENCY_TOLERANCE_HZ)
        self.assertAlmostEqual(b_to_a, EXPECTED_FREQUENCY_HZ, delta=FREQUENCY_TOLERANCE_HZ)

        self.daq_a.stop_counter_output(OUT_ALIAS)
        self.daq_b.stop_counter_output(OUT_ALIAS)

    def test_frequency_finite(self):
        """Each device reads 500 Hz from the finite train the other device sends."""
        self.daq_a.configure_frequency_counter_input(
            physical_channel=A_IN_TERMINAL,
            counter_source=A_IN_COUNTER,
            range_min=FREQUENCY_RANGE_MIN_HZ,
            range_max=FREQUENCY_RANGE_MAX_HZ,
            alias=IN_ALIAS,
        )
        self.daq_b.configure_frequency_counter_input(
            physical_channel=B_IN_TERMINAL,
            counter_source=B_IN_COUNTER,
            range_min=FREQUENCY_RANGE_MIN_HZ,
            range_max=FREQUENCY_RANGE_MAX_HZ,
            alias=IN_ALIAS,
        )

        # A finite train of 500 pulses at 500 Hz runs for one second, so the reads below
        # land while it is still pulsing.
        self.daq_a.configure_finite_counter_output(
            physical_channel=A_OUT_TERMINAL,
            counter_source=A_OUT_COUNTER,
            pulse_config=PULSE_CONFIG,
            n_pulses=FINITE_PULSES,
            alias=OUT_ALIAS,
        )
        self.daq_b.configure_finite_counter_output(
            physical_channel=B_OUT_TERMINAL,
            counter_source=B_OUT_COUNTER,
            pulse_config=PULSE_CONFIG,
            n_pulses=FINITE_PULSES,
            alias=OUT_ALIAS,
        )

        self.daq_a.start_counter_output(OUT_ALIAS)
        self.daq_b.start_counter_output(OUT_ALIAS)
        time.sleep(SETTLE_S)

        a_to_b = self.daq_b.read_counter(IN_ALIAS).latest
        b_to_a = self.daq_a.read_counter(IN_ALIAS).latest
        print(f"         A->B {a_to_b:.2f} Hz, B->A {b_to_a:.2f} Hz, expected {EXPECTED_FREQUENCY_HZ} Hz")
        self.assertAlmostEqual(a_to_b, EXPECTED_FREQUENCY_HZ, delta=FREQUENCY_TOLERANCE_HZ)
        self.assertAlmostEqual(b_to_a, EXPECTED_FREQUENCY_HZ, delta=FREQUENCY_TOLERANCE_HZ)

        # Let both trains finish, then confirm the wait returns rather than timing out.
        self.daq_a.wait_for_counter_output(OUT_ALIAS)
        self.daq_b.wait_for_counter_output(OUT_ALIAS)
        self.daq_a.stop_counter_output(OUT_ALIAS)
        self.daq_b.stop_counter_output(OUT_ALIAS)

    # =====================================================================
    # 2. Period
    # =====================================================================
    def test_period_continuous(self):
        """Each device reads a 2 ms period from the continuous train the other device sends."""
        self.daq_a.configure_period_counter_input(
            physical_channel=A_IN_TERMINAL,
            counter_source=A_IN_COUNTER,
            range_min=TIME_RANGE_MIN_S,
            range_max=TIME_RANGE_MAX_S,
            alias=IN_ALIAS,
        )
        self.daq_b.configure_period_counter_input(
            physical_channel=B_IN_TERMINAL,
            counter_source=B_IN_COUNTER,
            range_min=TIME_RANGE_MIN_S,
            range_max=TIME_RANGE_MAX_S,
            alias=IN_ALIAS,
        )

        self.daq_a.configure_continuous_counter_output(
            physical_channel=A_OUT_TERMINAL,
            counter_source=A_OUT_COUNTER,
            pulse_config=PULSE_CONFIG,
            alias=OUT_ALIAS,
        )
        self.daq_b.configure_continuous_counter_output(
            physical_channel=B_OUT_TERMINAL,
            counter_source=B_OUT_COUNTER,
            pulse_config=PULSE_CONFIG,
            alias=OUT_ALIAS,
        )

        self.daq_a.start_counter_output(OUT_ALIAS)
        self.daq_b.start_counter_output(OUT_ALIAS)
        time.sleep(SETTLE_S)

        a_to_b = self.daq_b.read_counter(IN_ALIAS).latest
        b_to_a = self.daq_a.read_counter(IN_ALIAS).latest
        print(f"         A->B {a_to_b:.6f} s, B->A {b_to_a:.6f} s, expected {EXPECTED_PERIOD_S} s")
        self.assertAlmostEqual(a_to_b, EXPECTED_PERIOD_S, delta=PERIOD_TOLERANCE_S)
        self.assertAlmostEqual(b_to_a, EXPECTED_PERIOD_S, delta=PERIOD_TOLERANCE_S)

        self.daq_a.stop_counter_output(OUT_ALIAS)
        self.daq_b.stop_counter_output(OUT_ALIAS)

    def test_period_finite(self):
        """Each device reads a 2 ms period from the finite train the other device sends."""
        self.daq_a.configure_period_counter_input(
            physical_channel=A_IN_TERMINAL,
            counter_source=A_IN_COUNTER,
            range_min=TIME_RANGE_MIN_S,
            range_max=TIME_RANGE_MAX_S,
            alias=IN_ALIAS,
        )
        self.daq_b.configure_period_counter_input(
            physical_channel=B_IN_TERMINAL,
            counter_source=B_IN_COUNTER,
            range_min=TIME_RANGE_MIN_S,
            range_max=TIME_RANGE_MAX_S,
            alias=IN_ALIAS,
        )

        self.daq_a.configure_finite_counter_output(
            physical_channel=A_OUT_TERMINAL,
            counter_source=A_OUT_COUNTER,
            pulse_config=PULSE_CONFIG,
            n_pulses=FINITE_PULSES,
            alias=OUT_ALIAS,
        )
        self.daq_b.configure_finite_counter_output(
            physical_channel=B_OUT_TERMINAL,
            counter_source=B_OUT_COUNTER,
            pulse_config=PULSE_CONFIG,
            n_pulses=FINITE_PULSES,
            alias=OUT_ALIAS,
        )

        self.daq_a.start_counter_output(OUT_ALIAS)
        self.daq_b.start_counter_output(OUT_ALIAS)
        time.sleep(SETTLE_S)

        a_to_b = self.daq_b.read_counter(IN_ALIAS).latest
        b_to_a = self.daq_a.read_counter(IN_ALIAS).latest
        print(f"         A->B {a_to_b:.6f} s, B->A {b_to_a:.6f} s, expected {EXPECTED_PERIOD_S} s")
        self.assertAlmostEqual(a_to_b, EXPECTED_PERIOD_S, delta=PERIOD_TOLERANCE_S)
        self.assertAlmostEqual(b_to_a, EXPECTED_PERIOD_S, delta=PERIOD_TOLERANCE_S)

        self.daq_a.wait_for_counter_output(OUT_ALIAS)
        self.daq_b.wait_for_counter_output(OUT_ALIAS)
        self.daq_a.stop_counter_output(OUT_ALIAS)
        self.daq_b.stop_counter_output(OUT_ALIAS)

    # =====================================================================
    # 3. Pulse width
    # =====================================================================
    def test_pulse_width_continuous(self):
        """Each device reads a 1 ms high time from the continuous train the other device sends."""
        self.daq_a.configure_pulse_width_counter_input(
            physical_channel=A_IN_TERMINAL,
            counter_source=A_IN_COUNTER,
            range_min=TIME_RANGE_MIN_S,
            range_max=TIME_RANGE_MAX_S,
            alias=IN_ALIAS,
        )
        self.daq_b.configure_pulse_width_counter_input(
            physical_channel=B_IN_TERMINAL,
            counter_source=B_IN_COUNTER,
            range_min=TIME_RANGE_MIN_S,
            range_max=TIME_RANGE_MAX_S,
            alias=IN_ALIAS,
        )

        self.daq_a.configure_continuous_counter_output(
            physical_channel=A_OUT_TERMINAL,
            counter_source=A_OUT_COUNTER,
            pulse_config=PULSE_CONFIG,
            alias=OUT_ALIAS,
        )
        self.daq_b.configure_continuous_counter_output(
            physical_channel=B_OUT_TERMINAL,
            counter_source=B_OUT_COUNTER,
            pulse_config=PULSE_CONFIG,
            alias=OUT_ALIAS,
        )

        self.daq_a.start_counter_output(OUT_ALIAS)
        self.daq_b.start_counter_output(OUT_ALIAS)
        time.sleep(SETTLE_S)

        a_to_b = self.daq_b.read_counter(IN_ALIAS).latest
        b_to_a = self.daq_a.read_counter(IN_ALIAS).latest
        print(f"         A->B {a_to_b:.6f} s, B->A {b_to_a:.6f} s, expected {EXPECTED_PULSE_WIDTH_S} s")
        self.assertAlmostEqual(a_to_b, EXPECTED_PULSE_WIDTH_S, delta=PULSE_WIDTH_TOLERANCE_S)
        self.assertAlmostEqual(b_to_a, EXPECTED_PULSE_WIDTH_S, delta=PULSE_WIDTH_TOLERANCE_S)

        self.daq_a.stop_counter_output(OUT_ALIAS)
        self.daq_b.stop_counter_output(OUT_ALIAS)

    def test_pulse_width_finite(self):
        """Each device reads a 1 ms high time from the finite train the other device sends."""
        self.daq_a.configure_pulse_width_counter_input(
            physical_channel=A_IN_TERMINAL,
            counter_source=A_IN_COUNTER,
            range_min=TIME_RANGE_MIN_S,
            range_max=TIME_RANGE_MAX_S,
            alias=IN_ALIAS,
        )
        self.daq_b.configure_pulse_width_counter_input(
            physical_channel=B_IN_TERMINAL,
            counter_source=B_IN_COUNTER,
            range_min=TIME_RANGE_MIN_S,
            range_max=TIME_RANGE_MAX_S,
            alias=IN_ALIAS,
        )

        self.daq_a.configure_finite_counter_output(
            physical_channel=A_OUT_TERMINAL,
            counter_source=A_OUT_COUNTER,
            pulse_config=PULSE_CONFIG,
            n_pulses=FINITE_PULSES,
            alias=OUT_ALIAS,
        )
        self.daq_b.configure_finite_counter_output(
            physical_channel=B_OUT_TERMINAL,
            counter_source=B_OUT_COUNTER,
            pulse_config=PULSE_CONFIG,
            n_pulses=FINITE_PULSES,
            alias=OUT_ALIAS,
        )

        self.daq_a.start_counter_output(OUT_ALIAS)
        self.daq_b.start_counter_output(OUT_ALIAS)
        time.sleep(SETTLE_S)

        a_to_b = self.daq_b.read_counter(IN_ALIAS).latest
        b_to_a = self.daq_a.read_counter(IN_ALIAS).latest
        print(f"         A->B {a_to_b:.6f} s, B->A {b_to_a:.6f} s, expected {EXPECTED_PULSE_WIDTH_S} s")
        self.assertAlmostEqual(a_to_b, EXPECTED_PULSE_WIDTH_S, delta=PULSE_WIDTH_TOLERANCE_S)
        self.assertAlmostEqual(b_to_a, EXPECTED_PULSE_WIDTH_S, delta=PULSE_WIDTH_TOLERANCE_S)

        self.daq_a.wait_for_counter_output(OUT_ALIAS)
        self.daq_b.wait_for_counter_output(OUT_ALIAS)
        self.daq_a.stop_counter_output(OUT_ALIAS)
        self.daq_b.stop_counter_output(OUT_ALIAS)

    # =====================================================================
    # 4. Pulse count
    # =====================================================================
    def test_pulse_count_finite(self):
        """Each device counts exactly the 500 pulses the other device's finite train emits."""
        # Edge counting takes no range. The input task starts on configure, so both counters
        # are running before either train begins and no pulse is missed.
        self.daq_a.configure_pulse_count_counter_input(
            physical_channel=A_IN_TERMINAL,
            counter_source=A_IN_COUNTER,
            alias=IN_ALIAS,
        )
        self.daq_b.configure_pulse_count_counter_input(
            physical_channel=B_IN_TERMINAL,
            counter_source=B_IN_COUNTER,
            alias=IN_ALIAS,
        )

        self.daq_a.configure_finite_counter_output(
            physical_channel=A_OUT_TERMINAL,
            counter_source=A_OUT_COUNTER,
            pulse_config=PULSE_CONFIG,
            n_pulses=FINITE_PULSES,
            alias=OUT_ALIAS,
        )
        self.daq_b.configure_finite_counter_output(
            physical_channel=B_OUT_TERMINAL,
            counter_source=B_OUT_COUNTER,
            pulse_config=PULSE_CONFIG,
            n_pulses=FINITE_PULSES,
            alias=OUT_ALIAS,
        )

        # Run both trains to completion, then count what arrived.
        self.daq_a.start_counter_output(OUT_ALIAS)
        self.daq_b.start_counter_output(OUT_ALIAS)
        self.daq_a.wait_for_counter_output(OUT_ALIAS)
        self.daq_b.wait_for_counter_output(OUT_ALIAS)

        a_to_b = self.daq_b.read_counter(IN_ALIAS).latest
        b_to_a = self.daq_a.read_counter(IN_ALIAS).latest
        print(f"         A->B {a_to_b:.0f} edges, B->A {b_to_a:.0f} edges, expected {FINITE_PULSES}")
        self.assertEqual(a_to_b, FINITE_PULSES)
        self.assertEqual(b_to_a, FINITE_PULSES)

        self.daq_a.stop_counter_output(OUT_ALIAS)
        self.daq_b.stop_counter_output(OUT_ALIAS)


if __name__ == "__main__":
    unittest.main()
