"""Hardware integration test for the LabJack T4 DAQ via InstroDAQ.

This test requires a physical LabJack T4 connected with the loopback wiring
described below. It exercises analog DAQ functionality exposed by the T-series
driver: software-timed analog read, hardware-timed analog read (background and
non-background), analog output, analog loopback verification, actual-sample-rate
reporting, and buffer-depth telemetry. Each test step is recorded as an event on
a Nominal Core asset.

Digital I/O tests exercise single-line read/write via FIO4 (output) and FIO5
(input) with a 1-line loopback. The T4 does not support port-width digital I/O
(write_digital_port / read_digital_port) or relays through this driver, so those
are reported as skipped.

============================================================================
LABJACK T4 LOOPBACK WIRING
============================================================================

  Device specs:
    - Analog inputs AIN0-AIN3, +/-10 V (T4 high-voltage lines)
    - 2 analog outputs DAC0/DAC1, 0-5 V
    - Flexible I/O FIO4-FIO7 usable as digital lines

  Analog loopback (wire DAC0 -> AIN0):
    DAC0 (AO, 0-5 V)  --->  AIN0  (AI, +/-10 V line)

  Digital loopback (wire FIO4 -> FIO5):
    FIO4 (driven as output)  --->  FIO5 (read as input)

  Channel configuration summary:
    AI ch "AIN0"  — alias "ain0", RSE, +/-10 V (loopback from DAC0)
    AO ch "DAC0"  — alias "dac0", 0-5 V
    DO line "FIO4" — alias "fio4", Logic.HIGH
    DI line "FIO5" — alias "fio5", Logic.HIGH

  Set LOOPBACK_WIRED = False to run structure-only checks (no value-match
  asserts).

============================================================================
NOMINAL CORE CONFIGURATION
============================================================================

  Before running, configure:

    DEVICE_ID           — LabJack T4 serial number (or "ANY" for the first
                          device found)
    DATASET_RID         — dataset RID for the NominalCorePublisher (optional;
                          leave None to publish nowhere)
    NOMINAL_API_TOKEN   — Nominal API token (optional if authenticated via
                          `nominal auth set-token`, which stores a default
                          profile in ~/.nominal/config)

  A Nominal Core asset is found or created for the device. Each test method
  creates an event on that asset with the test name, status (SUCCESS/ERROR),
  and duration. Data is streamed to the dataset via NominalCorePublisher.

============================================================================
RUNNING
============================================================================

    pytest -m hardware -v -s

"""

import dataclasses
import math
import time
import unittest
from datetime import timedelta

import pytest
from labjack import ljm
from nominal.core import EventType, NominalClient

from instro.daq import InstroDAQ
from instro.daq.drivers.labjack import LabJackTSeriesDriver
from instro.daq.types import Direction, Logic
from instro.lib.publishers import NominalCorePublisher

# ---------------------------------------------------------------------------
# Configuration — edit before running
# ---------------------------------------------------------------------------
DEVICE_ID = "<LABJACK T4 SERIAL NUMBER>"  # LabJack T4 serial number (or "ANY" for the first device found)
NAME = "t4_validate"

# Set to a Nominal dataset RID to stream validation data via NominalCorePublisher;
# leave None to publish nowhere.
DATASET_RID = None

# Analog channel mapping
AI_CHANNEL, AI_ALIAS = "AIN0", "ain0"
AO_CHANNEL, AO_ALIAS = "DAC0", "dac0"

# Digital channel mapping
DO_LINE, DO_ALIAS = "FIO4", "fio4"
DI_LINE, DI_ALIAS = "FIO5", "fio5"

# True when DAC0->AIN0 and FIO4->FIO5 are physically looped back. Gates the
# strict value checks; structural checks always run.
LOOPBACK_WIRED = True

# DAC0 spans 0-5 V, so every analog test point stays inside that range.
ANALOG_TEST_VOLTAGES = [0.0, 0.5, 1.25, 2.5, 3.3, 4.5]
ANALOG_TOLERANCE_V = 0.05  # DAC ~10 mV + AIN noise/offset; 50 mV is comfortable.

SAMPLE_RATE_HZ = 1000.0
SAMPLES_PER_CHANNEL = 100
SW_SAMPLE_RATE_HZ = 1.0
HW_TIMED_DC_V = 2.0  # DC level held on DAC0 during hardware-timed reads.
HW_TIMED_TOLERANCE_V = 0.1


# ---------------------------------------------------------------------------
# Nominal Core event helpers
# ---------------------------------------------------------------------------


def _get_client() -> NominalClient:
    """Create a Nominal client."""
    return NominalClient.from_profile("default")


class _EventRecorder:
    """Collects test events during execution, then creates them on a Nominal asset."""

    def __init__(self):
        self._client: NominalClient | None = None
        self._events: list[dict] = []

    def begin(self):
        self._client = _get_client()

    def record_event(
        self,
        name: str,
        start_ns: int,
        end_ns: int,
        passed: bool,
        description: str = "",
    ):
        self._events.append(
            {
                "name": name,
                "start_ns": start_ns,
                "end_ns": end_ns,
                "passed": passed,
                "description": description,
            }
        )

    def finish(self):
        asset = self._client.get_or_create_asset_by_properties(
            properties={"device_type": "LabJack T4", "purpose": "hardware-test"},
            name="LabJack T4",
            description="LabJack T4 DAQ device under test",
            labels=["labjack", "t4", "hardware-test"],
        )
        for evt in self._events:
            duration_ns = evt["end_ns"] - evt["start_ns"]
            self._client.create_event(
                name=evt["name"],
                type=EventType.SUCCESS if evt["passed"] else EventType.ERROR,
                start=evt["start_ns"],
                duration=timedelta(microseconds=duration_ns / 1_000),
                description=evt["description"],
                assets=[asset],
                properties={"status": "PASS" if evt["passed"] else "FAIL"},
                labels=["labjack-t4-test"],
            )


_recorder = _EventRecorder()


# ---------------------------------------------------------------------------
# Test suite
# ---------------------------------------------------------------------------
@pytest.mark.hardware
class TestLabJackT4Hardware(unittest.TestCase):
    """Hardware integration tests for the LabJack T4 via InstroDAQ.

    Each test creates, opens, configures, and closes its own DAQ instance,
    making every test independent. A fresh open() also resets the LabJack
    stream engine between hardware-timed acquisitions.
    """

    @classmethod
    def setUpClass(cls):
        _recorder.begin()

    @classmethod
    def tearDownClass(cls):
        try:
            _recorder.finish()
        except Exception as exc:
            print(f"\n*** Failed to create Nominal events: {exc} ***")
            raise

    # -- helpers ----------------------------------------------------------

    def _create_daq(self) -> InstroDAQ:
        """Create, optionally attach publisher, and open a fresh DAQ instance."""
        daq = InstroDAQ(
            name=NAME,
            driver=LabJackTSeriesDriver(device_id=DEVICE_ID),
        )
        if DATASET_RID:
            daq.add_publisher(NominalCorePublisher(dataset_rid=DATASET_RID))
        daq.open()
        return daq

    def _configure_ai(self, daq: InstroDAQ, range_min: float = -10, range_max: float = 10):
        """Configure the standard AIN0 input channel (RSE)."""
        daq.configure_analog_channel(
            direction=Direction.INPUT,
            physical_channel=AI_CHANNEL,
            alias=AI_ALIAS,
            range_min=range_min,
            range_max=range_max,
        )

    def _configure_ao(self, daq: InstroDAQ):
        """Configure the standard DAC0 output channel (0-5 V)."""
        daq.configure_analog_channel(
            direction=Direction.OUTPUT,
            physical_channel=AO_CHANNEL,
            alias=AO_ALIAS,
            range_min=0,
            range_max=5,
        )

    def _configure_digital_lines(self, daq: InstroDAQ):
        """Configure FIO4 as output and FIO5 as input (single lines)."""
        daq.configure_digital_line(
            direction=Direction.OUTPUT,
            physical_channel=DO_LINE,
            logic=Logic.HIGH,
            alias=DO_ALIAS,
        )
        daq.configure_digital_line(
            direction=Direction.INPUT,
            physical_channel=DI_LINE,
            logic=Logic.HIGH,
            alias=DI_ALIAS,
        )

    def _assert_t4(self, daq: InstroDAQ):
        """Verify the connected device is a T4 before running value checks."""
        device_type, conn_type, serial, _ip, _port, _ = daq.driver.get_info()
        print(f"         device_type={device_type} (T4={ljm.constants.dtT4}), conn={conn_type}, serial={serial}")
        self.assertEqual(
            device_type,
            ljm.constants.dtT4,
            f"Connected device is not a T4 (device_type={device_type})",
        )

    def _run_step(self, name: str, description: str, fn):
        """Execute *fn*, record a Nominal event with description, and re-raise on failure."""
        start_ns = time.time_ns()
        try:
            fn()
            _recorder.record_event(name, start_ns, time.time_ns(), passed=True, description=description)
        except Exception as exc:
            _recorder.record_event(
                name, start_ns, time.time_ns(), passed=False, description=f"{description}\n\nError: {exc}"
            )
            raise

    # =====================================================================
    # 1. Device info and firmware
    # =====================================================================
    def test_01_device_info_and_firmware(self):
        """Verify the connected device is a T4 and record firmware/hardware version."""

        def step():
            daq = self._create_daq()
            try:
                self._assert_t4(daq)
                fw = ljm.eReadName(daq.driver._handle, "FIRMWARE_VERSION")
                hw = ljm.eReadName(daq.driver._handle, "HARDWARE_VERSION")
                print(f"         FIRMWARE_VERSION={fw}  HARDWARE_VERSION={hw}")
            finally:
                daq.close()

        self._run_step(
            "Device info / firmware",
            "Verify get_info() reports a T4 device type and record FIRMWARE_VERSION / HARDWARE_VERSION.",
            step,
        )

    # =====================================================================
    # 2. Software-timed analog input
    # =====================================================================
    def test_02_sw_timed_analog_read(self):
        """Read AIN0 in software-timed mode (single-shot)."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_ai(daq)

                for _ in range(3):
                    measurement = daq.read(AI_ALIAS)
                    self.assertIsNotNone(measurement)
                    vals = measurement.values
                    self.assertTrue(vals and math.isfinite(vals[-1]), f"non-finite SW-timed read: {vals}")
                    print(f"         AIN0 (sw-timed) = {vals[-1]:.4f} V")
                    time.sleep(0.25)
            finally:
                daq.close()

        self._run_step(
            "SW-timed analog read",
            "Configure AIN0 (RSE, +/-10 V) and perform 3 single-shot software-timed reads.",
            step,
        )

    # =====================================================================
    # 3. Analog output — write known voltages
    # =====================================================================
    def test_03_analog_output(self):
        """Write a series of voltages to DAC0."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_ao(daq)

                for v in ANALOG_TEST_VOLTAGES:
                    daq.write(AO_ALIAS, v)
                    time.sleep(0.02)
                daq.write(AO_ALIAS, 0.0)
            finally:
                daq.close()

        self._run_step(
            "Analog output write",
            "Configure DAC0 (0-5 V) and write a sweep of voltages: 0, 0.5, 1.25, 2.5, 3.3, 4.5 V.",
            step,
        )

    # =====================================================================
    # 4. Analog loopback — write DAC0, verify on AIN0 (software-timed)
    # =====================================================================
    def test_04_analog_loopback_sw_timed(self):
        """Write known voltages to DAC0 and verify they appear on AIN0 (SW-timed)."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_ai(daq)
                self._configure_ao(daq)

                errs = []
                for v in ANALOG_TEST_VOLTAGES:
                    daq.write(AO_ALIAS, v)
                    time.sleep(0.05)  # let the DAC settle
                    measured = daq.read(AI_ALIAS).latest
                    err = measured - v
                    flag = "" if (not LOOPBACK_WIRED or abs(err) <= ANALOG_TOLERANCE_V) else "  <-- out of tolerance"
                    print(f"         DAC0={v:.3f} V | AIN0={measured:.4f} V | err={err:+.4f} V{flag}")
                    if not math.isfinite(measured):
                        errs.append(f"non-finite read at {v} V")
                    if LOOPBACK_WIRED and abs(err) > ANALOG_TOLERANCE_V:
                        errs.append(f"DAC0={v} V -> AIN0={measured:.4f} V (err {err:+.4f} V > {ANALOG_TOLERANCE_V} V)")
                daq.write(AO_ALIAS, 0.0)
                self.assertFalse(errs, "; ".join(errs))
            finally:
                daq.write(AO_ALIAS, 0.0)
                daq.close()

        self._run_step(
            "Analog loopback (SW-timed)",
            "Write known voltages to DAC0 and read back on AIN0 via loopback wiring. "
            "Verifies DAC0->AIN0 signal path using software-timed single-shot reads.",
            step,
        )

    # =====================================================================
    # 5. Digital line write/read loopback
    # =====================================================================
    def test_05_digital_line_loopback(self):
        """Drive FIO4 and verify the state on FIO5 via single-line loopback."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_digital_lines(daq)

                errs = []
                for state in (0, 1, 0, 1, 0):
                    daq.write(DO_ALIAS, state)
                    time.sleep(0.05)
                    read = int(daq.read(DI_ALIAS).latest)
                    flag = "" if (not LOOPBACK_WIRED or read == state) else "  <-- mismatch"
                    print(f"         FIO4<-{state} | FIO5={read}{flag}")
                    if LOOPBACK_WIRED and read != state:
                        errs.append(f"drove FIO4={state}, read FIO5={read}")
                daq.write(DO_ALIAS, 0)
                self.assertFalse(errs, "; ".join(errs))
            finally:
                daq.write(DO_ALIAS, 0)
                daq.close()

        self._run_step(
            "Digital line loopback",
            "Drive FIO4 through a 0/1 sequence and verify FIO5 reads back the same state "
            "via single-line loopback wiring.",
            step,
        )

    # =====================================================================
    # 6. HW-timed analog read with background daemon
    # =====================================================================
    def test_06_hw_timed_analog_read_background(self):
        """Start HW-timed acquisition with background daemon and read buffered data."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_ai(daq)
                self._configure_ao(daq)
                daq.write(AO_ALIAS, HW_TIMED_DC_V)  # hold a DC level before streaming
                daq.configure_ai_hw_sample_rate(
                    sample_rate=SAMPLE_RATE_HZ,
                    samples_per_channel=SAMPLES_PER_CHANNEL,
                )
                daq.start()

                try:
                    time.sleep(1.0)  # let background daemon collect samples

                    ch = daq.get_channel(f"{NAME}.{AI_ALIAS}", 50, True)
                    self.assertIsNotNone(ch)
                    self.assertGreaterEqual(len(ch.values), 1)
                    self.assertTrue(all(math.isfinite(v) for v in ch.values), "non-finite samples in background buffer")

                    mean = sum(ch.values) / len(ch.values)
                    print(f"         background buffer: {len(ch.values)} samples, mean AIN0 = {mean:.4f} V")
                    if LOOPBACK_WIRED:
                        self.assertAlmostEqual(mean, HW_TIMED_DC_V, delta=HW_TIMED_TOLERANCE_V)
                finally:
                    daq.stop()
                    daq.write(AO_ALIAS, 0.0)
            finally:
                daq.close()

        self._run_step(
            "HW-timed analog read (background)",
            f"Start HW-timed acquisition at {SAMPLE_RATE_HZ} Hz with background daemon. "
            f"Hold DAC0 at {HW_TIMED_DC_V} V, verify AIN0 reads match via get_channel().",
            step,
        )

    # =====================================================================
    # 7. HW-timed analog read without background daemon
    # =====================================================================
    def test_07_hw_timed_analog_read_no_background(self):
        """Start HW-timed acquisition without background daemon and read directly."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_ai(daq)
                self._configure_ao(daq)
                daq.write(AO_ALIAS, HW_TIMED_DC_V)
                daq.configure_ai_hw_sample_rate(
                    sample_rate=SAMPLE_RATE_HZ,
                    samples_per_channel=SAMPLES_PER_CHANNEL,
                )
                daq.start(background=False)

                try:
                    # No background daemon: read() dispatches to the driver's fetch_analog().
                    measurement = daq.read(AI_ALIAS)
                    self.assertIsNotNone(measurement)
                    vals = measurement.values
                    self.assertGreaterEqual(len(vals), 1)
                    self.assertTrue(all(math.isfinite(v) for v in vals), f"non-finite HW-timed fetch: n={len(vals)}")

                    mean = sum(vals) / len(vals)
                    print(
                        f"         fetched {len(vals)} samples, mean AIN0 = {mean:.4f} V (DAC0 held at {HW_TIMED_DC_V} V)"
                    )
                    if LOOPBACK_WIRED:
                        self.assertAlmostEqual(mean, HW_TIMED_DC_V, delta=HW_TIMED_TOLERANCE_V)
                finally:
                    daq.stop()
                    daq.write(AO_ALIAS, 0.0)
            finally:
                daq.close()

        self._run_step(
            "HW-timed analog read (no background)",
            f"Start HW-timed acquisition at {SAMPLE_RATE_HZ} Hz with background daemon disabled. "
            f"Hold DAC0 at {HW_TIMED_DC_V} V and read directly via read() (driver fetch_analog()).",
            step,
        )

    # =====================================================================
    # 8. SW-timed analog read with background daemon
    # =====================================================================
    def test_08_sw_timed_analog_read_background(self):
        """Start SW-timed acquisition with background daemon and read buffered data."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_ai(daq)
                self._configure_ao(daq)
                daq.write(AO_ALIAS, HW_TIMED_DC_V)  # hold a DC level before streaming
                daq.configure_ai_sw_sample_rate(sample_rate=SW_SAMPLE_RATE_HZ)
                daq.start()

                try:
                    time.sleep(1.0)  # let background daemon collect samples

                    ch = daq.get_channel(f"{NAME}.{AI_ALIAS}", 9, True)
                    self.assertIsNotNone(ch)
                    self.assertGreaterEqual(len(ch.values), 1)
                    self.assertTrue(all(math.isfinite(v) for v in ch.values), "non-finite samples in background buffer")

                    mean = sum(ch.values) / len(ch.values)
                    print(f"         background buffer: {len(ch.values)} samples, mean AIN0 = {mean:.4f} V")
                    if LOOPBACK_WIRED:
                        self.assertAlmostEqual(mean, HW_TIMED_DC_V, delta=HW_TIMED_TOLERANCE_V)
                finally:
                    daq.stop()
                    daq.write(AO_ALIAS, 0.0)
            finally:
                daq.close()

        self._run_step(
            "SW-timed analog read (background)",
            f"Start SW-timed acquisition at {SW_SAMPLE_RATE_HZ} Hz with background daemon. "
            f"Hold DAC0 at {HW_TIMED_DC_V} V, verify AIN0 reads match via get_channel().",
            step,
        )

    # =====================================================================
    # 9. Actual sample rate reporting
    # =====================================================================
    def test_09_actual_sample_rate(self):
        """Verify get_actual_sample_rate returns a reasonable value after start."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_ai(daq)
                daq.configure_ai_hw_sample_rate(
                    sample_rate=SAMPLE_RATE_HZ,
                    samples_per_channel=SAMPLES_PER_CHANNEL,
                )
                daq.start(background=False)

                try:
                    actual_rate = daq.get_actual_sample_rate()
                    self.assertIsNotNone(actual_rate, "get_actual_sample_rate returned None after start()")
                    print(f"         actual sample rate = {actual_rate} Hz (requested {SAMPLE_RATE_HZ} Hz)")
                    self.assertAlmostEqual(
                        actual_rate,
                        SAMPLE_RATE_HZ,
                        delta=SAMPLE_RATE_HZ * 0.1,
                        msg=f"Actual rate {actual_rate} deviates >10% from requested {SAMPLE_RATE_HZ}",
                    )
                finally:
                    daq.stop()
            finally:
                daq.close()

        self._run_step(
            "Actual sample rate",
            f"Verify get_actual_sample_rate() returns a value within 10% of the requested {SAMPLE_RATE_HZ} Hz.",
            step,
        )

    # =====================================================================
    # 10. Buffer-depth telemetry
    # =====================================================================
    def test_10_buffer_depth_telemetry(self):
        """Verify get_points_in_buffer reports a valid depth during background acquisition."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_ai(daq)
                daq.configure_ai_hw_sample_rate(
                    sample_rate=SAMPLE_RATE_HZ,
                    samples_per_channel=SAMPLES_PER_CHANNEL,
                )
                daq.start()

                try:
                    time.sleep(0.5)  # let the buffer accumulate
                    depth = daq.get_points_in_buffer().latest
                    print(f"         points_in_buffer telemetry = {depth}")
                    self.assertTrue(math.isfinite(depth) and depth >= 0, f"invalid buffer depth: {depth}")
                finally:
                    daq.stop()
            finally:
                daq.close()

        self._run_step(
            "Buffer-depth telemetry",
            "Run background HW-timed acquisition and verify get_points_in_buffer() reports a "
            "finite, non-negative buffer depth.",
            step,
        )

    # =====================================================================
    # 11. Clean shutdown — outputs to safe state
    # =====================================================================
    def test_11_clean_shutdown(self):
        """Set all outputs to safe state as a final step."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_ao(daq)
                self._configure_digital_lines(daq)

                daq.write(AO_ALIAS, 0.0)
                daq.write(DO_ALIAS, 0)
            finally:
                daq.close()

        self._run_step(
            "Clean shutdown — safe state",
            "Set DAC0 to 0 V and FIO4 to 0 as a final safety step.",
            step,
        )

    # =====================================================================
    # 12. Unified write — single, batch, invalid-alias batch
    # =====================================================================
    def test_12_write(self):
        """Exercise write() single-channel, write_batch() multi-channel, and write_batch() with an unknown alias."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_ao(daq)
                self._configure_digital_lines(daq)

                # Normal commanding of a single channel.
                cmd = daq.write(AO_ALIAS, 1.0)
                self.assertIsNotNone(cmd)
                print(f"         single write: {AO_ALIAS} <- 1.0 V")

                # Normal commanding of multiple channels to multiple values.
                commands = daq.write_batch([AO_ALIAS, DO_ALIAS], [2.5, 1])
                self.assertEqual(len(commands), 2)
                print("         batch write: 2 channels -> 2 commands")

                # Edge case: batch containing an unconfigured alias raises KeyError, nothing written.
                with self.assertRaises(KeyError) as ctx:
                    daq.write_batch([AO_ALIAS, "not_a_channel"], [0.0, 0.0])
                self.assertIn("not_a_channel", str(ctx.exception))
                print(f"         invalid batch: {ctx.exception}")

                # Edge case: AO value outside the configured range raises ValueError, nothing written.
                with self.assertRaises(ValueError):
                    daq.write_batch([AO_ALIAS], [6.0])

                # Edge case: non-finite analog value raises ValueError, nothing written.
                with self.assertRaises(ValueError):
                    daq.write_batch([AO_ALIAS], [math.nan])

                # Edge case: digital line value other than 0/1 raises ValueError, nothing written.
                with self.assertRaises(ValueError):
                    daq.write_batch([DO_ALIAS], [2])
                print("         ValueError raised for out-of-range, non-finite, and non-binary values")

                daq.write_batch([AO_ALIAS, DO_ALIAS], [0.0, 0])
            finally:
                daq.close()

        self._run_step(
            "Unified write",
            "Write one channel with write(), several channels with write_batch(), and verify unknown aliases, "
            "out-of-range AO values, and invalid analog/digital values each raise with nothing written.",
            step,
        )

    # =====================================================================
    # 13. Unified read — single, batch, invalid aliases
    # =====================================================================
    def test_13_read(self):
        """Exercise read() single-channel, read_batch() multi-channel, and both invalid-alias paths."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_ai(daq)
                self._configure_digital_lines(daq)

                # Reading of a single channel.
                measurement = daq.read(AI_ALIAS)
                self.assertTrue(math.isfinite(measurement.latest))
                print(f"         single read: {AI_ALIAS} = {measurement.latest:.4f} V")

                # Reading of multiple channels.
                aliases = [AI_ALIAS, DI_ALIAS]
                reads = daq.read_batch(aliases)
                self.assertEqual(list(reads), aliases)
                self.assertTrue(all(math.isfinite(reads[a].latest) for a in aliases))
                print("         batch read: " + ", ".join(f"{a}={reads[a].latest:.4f}" for a in aliases))

                # Edge case: unknown alias raises KeyError for both single and batch reads.
                with self.assertRaises(KeyError) as single_ctx:
                    daq.read("not_a_channel")
                self.assertIn("not_a_channel", str(single_ctx.exception))
                with self.assertRaises(KeyError) as batch_ctx:
                    daq.read_batch([AI_ALIAS, "not_a_channel"])
                self.assertIn("not_a_channel", str(batch_ctx.exception))
                print("         KeyError raised for invalid single and batch reads")

                # Edge case: reads while the background daemon is streaming serve analog from its buffer.
                daq.configure_ai_sw_sample_rate(sample_rate=SW_SAMPLE_RATE_HZ)
                daq.start()
                try:
                    # read()'s buffered path blocks until the daemon's first sample arrives.
                    streaming = daq.read(AI_ALIAS)
                    self.assertTrue(math.isfinite(streaming.latest))
                    streaming_batch = daq.read_batch(aliases)
                    self.assertTrue(all(math.isfinite(streaming_batch[a].latest) for a in aliases))
                    print(f"         streaming read: {AI_ALIAS} = {streaming.latest:.4f} V (daemon running)")
                finally:
                    daq.stop()
            finally:
                daq.close()

        self._run_step(
            "Unified read",
            "Read one channel with read(), several channels with read_batch(), verify unknown aliases raise "
            "KeyError, and confirm reads serve from the daemon buffer while background streaming runs.",
            step,
        )

    # =====================================================================
    # 14. write_batch failure handling — continue_on_failed_write
    # =====================================================================
    def test_14_write_batch_continue_on_failed_write(self):
        """Verify write_batch stops on a failed write by default and skips it with continue_on_failed_write=True."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_ao(daq)
                daq.configure_analog_channel(
                    direction=Direction.OUTPUT, physical_channel="DAC1", alias="dac1", range_min=0, range_max=5
                )
                self._configure_digital_lines(daq)
                # Drive the DO low first: an unwritten FIO line tri-states and its pull-up reads back as 1.
                daq.write(DO_ALIAS, 0)

                # Point the second AO at an invalid LJM register so its write raises a real LJM error mid-batch.
                broken = dataclasses.replace(daq._driver._ao_channels["dac1"], physical_channel="NOT_A_REGISTER")
                daq._driver._ao_channels["dac1"] = broken
                batch_channels = [AO_ALIAS, "dac1", DO_ALIAS]

                with self.assertRaises(RuntimeError) as ctx:
                    daq.write_batch(batch_channels, [1.0, 1.0, 1])
                self.assertIn("dac1", str(ctx.exception))
                print(f"         default: {ctx.exception}")
                if LOOPBACK_WIRED:
                    time.sleep(0.05)
                    self.assertEqual(int(daq.read(DI_ALIAS).latest), 0, "batch continued past the failed write")

                commands = daq.write_batch(batch_channels, [1.0, 1.0, 1], continue_on_failed_write=True)
                self.assertEqual(len(commands), 2, f"expected 2 successful commands, got {len(commands)}")
                print(f"         continue_on_failed_write=True: {len(commands)}/{len(batch_channels)} succeeded")
                if LOOPBACK_WIRED:
                    time.sleep(0.05)
                    self.assertEqual(int(daq.read(DI_ALIAS).latest), 1, "write after the failed channel was skipped")

                daq.write_batch([AO_ALIAS, DO_ALIAS], [0.0, 0])
            finally:
                daq.close()

        self._run_step(
            "write_batch failure handling",
            "Break one AO channel mid-batch. Verify write_batch raises and stops at the failed channel by "
            "default, and skips it and continues with continue_on_failed_write=True.",
            step,
        )

    # =====================================================================
    # 15-16. Methods not implemented on the T4 — reported as skipped
    # =====================================================================
    def test_15_port_width_digital_unsupported(self):
        """write_digital_port / read_digital_port are not implemented for the T4."""
        self.skipTest("driver raises NotImplementedError for LabJack port-width digital I/O")

    def test_16_relay_control_unsupported(self):
        """Relay control is not supported by the LabJack driver."""
        self.skipTest("DAQDriverBase relays unsupported by LabJack")

    # =====================================================================
    # 17. stop() halts the stream engine, and a restart works in one session
    # =====================================================================
    def test_17_stream_stop_and_restart(self):
        """stop() stops the device stream and retires the LJM callback, so a second start() succeeds."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_ai(daq)
                daq.configure_ai_hw_sample_rate(
                    sample_rate=SAMPLE_RATE_HZ,
                    samples_per_channel=SAMPLES_PER_CHANNEL,
                )
                driver = daq.driver

                daq.start()
                time.sleep(0.5)
                first = daq.get_channel(f"{NAME}.{AI_ALIAS}", 50, True)
                self.assertGreaterEqual(len(first.values), 1, "stream delivered no samples before stop()")
                daq.stop()

                # The device stream engine is off, so open() has no leftover stream to clear.
                enabled = ljm.eReadName(driver._handle, "STREAM_ENABLE")
                self.assertEqual(enabled, 0, f"stop() left the stream running (STREAM_ENABLE={enabled})")

                # The callback is retired, so nothing refills the queue stop() drained.
                self.assertTrue(driver._data_queue.empty(), "stop() left scans queued")
                time.sleep(0.3)
                self.assertTrue(driver._data_queue.empty(), "the callback queued scans after stop()")

                # Fetching after stop() reports no active scan instead of stalling for the 5 s timeout.
                with self.assertRaises(RuntimeError):
                    daq.read_analog()
                print("         stop(): STREAM_ENABLE=0, queue empty, read_analog() raises RuntimeError")

                # A second start() in one session used to fail on an already-active stream.
                daq.start()
                try:
                    time.sleep(0.5)
                    second = daq.get_channel(f"{NAME}.{AI_ALIAS}", 50, True)
                    self.assertGreaterEqual(len(second.values), 1, "restarted stream delivered no samples")
                    self.assertTrue(all(math.isfinite(v) for v in second.values), "non-finite samples after restart")
                    print(f"         restart delivered {len(second.values)} samples on {AI_ALIAS}")
                finally:
                    daq.stop()
            finally:
                daq.close()

        self._run_step(
            "Stream stop and restart",
            "Start HW-timed acquisition and stop it. Verify STREAM_ENABLE reads 0, the LJM callback queues "
            "nothing more, and a second start() in the same session streams real samples.",
            step,
        )


if __name__ == "__main__":
    unittest.main()
