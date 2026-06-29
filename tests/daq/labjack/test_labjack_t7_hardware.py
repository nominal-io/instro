"""Hardware integration test for the LabJack T7 DAQ via InstroDAQ.

This test requires a physical LabJack T7 connected with the loopback wiring
described below. It exercises analog DAQ functionality exposed by the T-series
driver: software-timed analog read, hardware-timed analog read (background and
non-background), analog output, analog loopback verification, actual-sample-rate
reporting, and buffer-depth telemetry. Each test step is recorded as an event on
a Nominal Core asset.

Digital I/O tests exercise single-line read/write via FIO0 (output) and FIO1
(input) with a 1-line loopback. The T7 does not support port-width digital I/O
(write_digital_port / read_digital_port) or relays through this driver, so those
are reported as skipped.

============================================================================
LABJACK T7 LOOPBACK WIRING
============================================================================

  Device specs:
    - Analog inputs AIN0-AIN3, +/-10 V (default), +/-1 V, +/-0.1 V, +/-0.01 V (configurable gain)
    - 2 analog outputs DAC0/DAC1, 0-5 V
    - Flexible I/O FIO0-FIO3 usable as digital lines

  Analog loopback (wire DAC0 -> AIN0):
    DAC0 (AO, 0-5 V)  --->  AIN0  (AI)
    DAC1 (AO, 0-5 V)  --->  AIN1  (AI)

  Digital loopback (wire FIO0 -> FIO1):
    FIO0 (driven as output)  --->  FIO1 (read as input)

  Channel configuration summary:
    AI ch "AIN0"  — alias "ain0", RSE (loopback from DAC0)
    AI ch "AIN1"  — alias "ain1", RSE (loopback from DAC1)
    AO ch "DAC0"  — alias "dac0", 0-5 V
    AO ch "DAC1"  — alias "dac1", 0-5 V
    DO line "FIO0" — alias "fio0", Logic.HIGH
    DI line "FIO1" — alias "fio1", Logic.HIGH

  Set LOOPBACK_WIRED = False to run structure-only checks (no value-match
  asserts).

============================================================================
NOMINAL CORE CONFIGURATION
============================================================================

  Before running, configure:

    DEVICE_ID           — LabJack T7 serial number (or "ANY" for the first
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

import math
import time
import unittest
from datetime import timedelta

import pytest
from labjack import ljm
from nominal.core import EventType, NominalClient

from instro.daq import InstroDAQ
from instro.daq.drivers.labjack import LabJackTSeriesDriver
from instro.daq.types import Direction, Logic, TerminalConfig
from instro.lib import InstrumentNotOpenError
from instro.lib.publishers import NominalCorePublisher

# ---------------------------------------------------------------------------
# Configuration — edit before running
# ---------------------------------------------------------------------------
DEVICE_ID = "<LABJACK T7 SERIAL NUMBER>"  # LabJack T7 serial number (or "ANY" for the first device found)
NAME = "t7_validate"

# Set to a Nominal dataset RID to stream validation data via NominalCorePublisher;
# leave None to publish nowhere.
DATASET_RID = None

# Analog channel mapping
AI0_CHANNEL, AI0_ALIAS = "AIN0", "ain0"
AI1_CHANNEL, AI1_ALIAS = "AIN1", "ain1"
AO0_CHANNEL, AO0_ALIAS = "DAC0", "dac0"
AO1_CHANNEL, AO1_ALIAS = "DAC1", "dac1"

# Digital channel mapping
DO_LINE, DO_ALIAS = "FIO0", "fio0"
DI_LINE, DI_ALIAS = "FIO1", "fio1"

# T7 analog input ranges, each with an in-range and over-range DAC0 test voltage.
# DAC0 maxes at 5 V, so the +/-10 V range has no over-range case.

AIN_VOLTAGE_RANGES = [
    (-10, 10, 4.5, None),
    (-1, 1, 0.5, 2.5),
    (-0.1, 0.1, 0.05, 1.0),
    (-0.01, 0.01, 0.005, 0.1),
]

# True when DAC0->AIN0 and FIO0->FIO1 are physically looped back. Gates the
# strict value checks; structural checks always run.
LOOPBACK_WIRED = True

# DAC0 spans 0-5 V, so every analog test point stays inside that range.
ANALOG_TEST_VOLTAGES = [0.0, 0.5, 1.25, 2.5, 3.3, 4.5]
ANALOG_TOLERANCE_V = 0.05  # DAC ~10 mV + AIN noise/offset; 50 mV is comfortable.

SAMPLE_RATE_HZ = 1000.0
SAMPLES_PER_CHANNEL = 100
# Distinct DC levels held on DAC0/DAC1 (looped to AIN0/AIN1) during hardware-timed reads.
HW_TIMED_DC_V0 = 2.0
HW_TIMED_DC_V1 = 3.5
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
            properties={"device_type": "LabJack T7", "purpose": "hardware-test"},
            name="LabJack T7",
            description="LabJack T7 DAQ device under test",
            labels=["labjack", "t7", "hardware-test"],
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
                labels=["labjack-t7-test"],
            )


_recorder = _EventRecorder()


# ---------------------------------------------------------------------------
# Test suite
# ---------------------------------------------------------------------------
@pytest.mark.hardware
class TestLabJackT7Hardware(unittest.TestCase):
    """Hardware integration tests for the LabJack T7 via InstroDAQ.

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

    def _ain_tolerance(self, value: float, range_max: float) -> float:
        """Per-range tolerance for an AIN reading vs the DAC0 source voltage.

        Models the three error sources that scale differently:
        - gain error  (DAC gain + AIN gain)      -> fraction of reading
        - offset/noise (scales with the AIN range) -> fraction of full scale
        - DAC offset + quantization (fixed)        -> absolute floor
        """
        REL_FRAC = 0.01  # 1% of reading: covers DAC + AIN gain error
        RANGE_FRAC = 0.002  # 0.2% of full scale: AIN offset/noise floor per range
        FLOOR_V = 0.003  # 3 mV: fixed DAC offset + ADC quantization
        return REL_FRAC * abs(value) + RANGE_FRAC * range_max + FLOOR_V

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
        """Configure the AIN0 and AIN1 input channels (RSE)."""
        for channel, alias in ((AI0_CHANNEL, AI0_ALIAS), (AI1_CHANNEL, AI1_ALIAS)):
            daq.configure_analog_channel(
                direction=Direction.INPUT,
                physical_channel=channel,
                alias=alias,
                range_min=range_min,
                range_max=range_max,
            )

    def _configure_ao(self, daq: InstroDAQ):
        """Configure the DAC0 and DAC1 output channels (0-5 V)."""
        for channel, alias in ((AO0_CHANNEL, AO0_ALIAS), (AO1_CHANNEL, AO1_ALIAS)):
            daq.configure_analog_channel(
                direction=Direction.OUTPUT,
                physical_channel=channel,
                alias=alias,
                range_min=0,
                range_max=5,
            )

    def _configure_digital_lines(self, daq: InstroDAQ):
        """Configure FIO0 as output and FIO1 as input (single lines)."""
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

    def _assert_t7(self, daq: InstroDAQ):
        """Verify the connected device is a T7 before running value checks."""
        device_type, conn_type, serial, _ip, _port, _ = daq.driver.get_info()
        print(f"         device_type={device_type} (T7={ljm.constants.dtT7}), conn={conn_type}, serial={serial}")
        self.assertEqual(
            device_type,
            ljm.constants.dtT7,
            f"Connected device is not a T7 (device_type={device_type})",
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
        """Verify the connected device is a T7 and record firmware/hardware version."""

        def step():
            daq = self._create_daq()
            try:
                self._assert_t7(daq)
                fw = ljm.eReadName(daq.driver._handle, "FIRMWARE_VERSION")
                hw = ljm.eReadName(daq.driver._handle, "HARDWARE_VERSION")
                print(f"         FIRMWARE_VERSION={fw}  HARDWARE_VERSION={hw}")
            finally:
                daq.close()

        self._run_step(
            "Device info / firmware",
            "Verify get_info() reports a T7 device type and record FIRMWARE_VERSION / HARDWARE_VERSION.",
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
                    measurement = daq.read_analog()
                    self.assertIsNotNone(measurement)
                    vals = measurement.channel_data.get(f"{NAME}.{AI0_ALIAS}", [])
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
                    daq.write_analog_value(AO0_ALIAS, v)
                    readback = ljm.eReadNames(daq.driver._handle, 1, [AO0_CHANNEL])[0]
                    print(f"         DAC0<-{v:.3f} V | register readback={readback:.4f} V")
                    self.assertAlmostEqual(
                        readback,
                        v,
                        delta=ANALOG_TOLERANCE_V,
                        msg=f"DAC0 register readback {readback:.4f} V != written {v} V",
                    )
                    time.sleep(0.02)
                daq.write_analog_value(AO0_ALIAS, 0.0)
            finally:
                daq.close()

        self._run_step(
            "Analog output write",
            "Configure DAC0 (0-5 V) and write a sweep of voltages: 0, 0.5, 1.25, 2.5, 3.3, 4.5 V.",
            step,
        )

    # =====================================================================
    # 4. Software-timed differential analog inputs
    # =====================================================================
    def test_04_differential_pair(self):
        """Read AIN0/AIN1 single-ended, then verify a differential read equals their difference."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_ai(daq)
                self._configure_ao(daq)
                daq.write_analog_value(AO0_ALIAS, 3.0)
                daq.write_analog_value(AO1_ALIAS, 1.0)
                time.sleep(0.05)

                m = daq.read_analog()
                ain0 = m.channel_data.get(f"{NAME}.{AI0_ALIAS}", [None])[-1]
                ain1 = m.channel_data.get(f"{NAME}.{AI1_ALIAS}", [None])[-1]
                self.assertTrue(
                    ain0 is not None and ain1 is not None and math.isfinite(ain0) and math.isfinite(ain1),
                    f"non-finite single-ended reads: AIN0={ain0}, AIN1={ain1}",
                )
                single_ended_diff = ain0 - ain1
                print(f"         single-ended: AIN0={ain0:.4f} V, AIN1={ain1:.4f} V, diff={single_ended_diff:+.4f} V")

                # Reconfigure AIN0 as the positive leg of a differential pair (AIN1 = negative).
                daq.configure_analog_channel(
                    direction=Direction.INPUT,
                    physical_channel=AI0_CHANNEL,
                    alias=AI0_ALIAS,
                    terminal_config=TerminalConfig.DIFF,
                )
                time.sleep(0.05)
                diff_reading = daq.read_analog().channel_data.get(f"{NAME}.{AI0_ALIAS}", [None])[-1]
                self.assertTrue(
                    diff_reading is not None and math.isfinite(diff_reading),
                    f"non-finite differential read: {diff_reading}",
                )
                err = diff_reading - single_ended_diff
                flag = "" if (not LOOPBACK_WIRED or abs(err) <= ANALOG_TOLERANCE_V) else "  <-- mismatch"
                print(
                    f"         differential AIN0-AIN1 = {diff_reading:.4f} V (err vs single-ended {err:+.4f} V){flag}"
                )
                if LOOPBACK_WIRED:
                    self.assertAlmostEqual(
                        diff_reading,
                        single_ended_diff,
                        delta=ANALOG_TOLERANCE_V,
                        msg=f"differential read {diff_reading:.4f} V != single-ended diff {single_ended_diff:.4f} V",
                    )
                daq.write_analog_value(AO0_ALIAS, 0.0)
                daq.write_analog_value(AO1_ALIAS, 0.0)
            finally:
                daq.write_analog_value(AO0_ALIAS, 0.0)
                daq.write_analog_value(AO1_ALIAS, 0.0)
                daq.close()

        self._run_step(
            "Differential analog inputs",
            "Read AIN0 and AIN1 single-ended and compute their difference, then reconfigure "
            "AIN0/AIN1 as a differential pair and verify the differential read matches that difference.",
            step,
        )

    # =====================================================================
    # 5. Analog loopback — write DAC0, verify on AIN0 (software-timed)
    # =====================================================================
    def test_05_analog_loopback_sw_timed(self):
        """Write known voltages to DAC0 and verify they appear on AIN0 (SW-timed)."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_ai(daq)
                self._configure_ao(daq)

                errs = []
                for v in ANALOG_TEST_VOLTAGES:
                    daq.write_analog_value(AO0_ALIAS, v)
                    time.sleep(0.05)  # let the DAC settle
                    measured = daq.read_analog().channel_data.get(f"{NAME}.{AI0_ALIAS}", [None])[-1]
                    err = measured - v
                    flag = "" if (not LOOPBACK_WIRED or abs(err) <= ANALOG_TOLERANCE_V) else "  <-- out of tolerance"
                    print(f"         DAC0={v:.3f} V | AIN0={measured:.4f} V | err={err:+.4f} V{flag}")
                    if not math.isfinite(measured):
                        errs.append(f"non-finite read at {v} V")
                    if LOOPBACK_WIRED and abs(err) > ANALOG_TOLERANCE_V:
                        errs.append(f"DAC0={v} V -> AIN0={measured:.4f} V (err {err:+.4f} V > {ANALOG_TOLERANCE_V} V)")
                daq.write_analog_value(AO0_ALIAS, 0.0)
                self.assertFalse(errs, "; ".join(errs))
            finally:
                daq.write_analog_value(AO0_ALIAS, 0.0)
                daq.close()

        self._run_step(
            "Analog loopback (SW-timed)",
            "Write known voltages to DAC0 and read back on AIN0 via loopback wiring. "
            "Verifies DAC0->AIN0 signal path using software-timed single-shot reads.",
            step,
        )

    # =====================================================================
    # 6. Software-timed analog input ranges
    # =====================================================================
    def test_06_ain_voltage_ranges(self):
        """Configure AIN0 to each input range; verify in-range voltages track and over-range voltages clamp."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_ao(daq)

                def read_ain0():
                    return daq.read_analog().channel_data.get(f"{NAME}.{AI0_ALIAS}", [None])[-1]

                errs = []
                for range_min, range_max, in_v, over_v in AIN_VOLTAGE_RANGES:
                    self._configure_ai(daq, range_min=range_min, range_max=range_max)

                    # --- in-range: AIN0 should track DAC0 within the per-range tolerance ---
                    daq.write_analog_value(AO0_ALIAS, in_v)
                    time.sleep(0.05)
                    measured = read_ain0()
                    if measured is None or not math.isfinite(measured):
                        errs.append(f"non-finite in-range read at +/-{range_max} V")
                    else:
                        tol = self._ain_tolerance(in_v, range_max)
                        ok = not LOOPBACK_WIRED or abs(measured - in_v) <= tol
                        flag = "" if ok else "  <-- FAIL"
                        print(
                            f"         +/-{range_max} V | in   DAC0={in_v:.4f} V | AIN0={measured:.4f} V "
                            f"(err {measured - in_v:+.4f} V, tol {tol * 1e3:.1f} mV){flag}"
                        )
                        if not ok:
                            errs.append(
                                f"in-range +/-{range_max} V: DAC0={in_v} V -> AIN0={measured:.4f} V "
                                f"(err {measured - in_v:+.4f} V > {tol:.4f} V)"
                            )

                    if over_v is None:
                        continue

                    # --- over-range: AIN0 should clamp near full scale ---
                    daq.write_analog_value(AO0_ALIAS, over_v)
                    time.sleep(0.05)
                    measured = read_ain0()
                    if measured is None or not math.isfinite(measured):
                        errs.append(f"non-finite over-range read at +/-{range_max} V")
                    else:
                        tol = self._ain_tolerance(range_max, range_max)
                        clamped = measured <= range_max + tol and measured < over_v - tol
                        ok = not LOOPBACK_WIRED or clamped
                        flag = "" if ok else "  <-- FAIL"
                        print(
                            f"         +/-{range_max} V | over DAC0={over_v:.4f} V | AIN0={measured:.4f} V "
                            f"(clamp~{range_max} V, tol {tol * 1e3:.1f} mV){flag}"
                        )
                        if not ok:
                            errs.append(
                                f"over-range +/-{range_max} V: DAC0={over_v} V -> AIN0={measured:.4f} V "
                                f"(expected clamp ~{range_max} V)"
                            )

                daq.write_analog_value(AO0_ALIAS, 0.0)
                self.assertFalse(errs, "; ".join(errs))
            finally:
                daq.write_analog_value(AO0_ALIAS, 0.0)
                daq.close()

        self._run_step(
            "AIN voltage ranges",
            "Configure AIN0 to each T7 input range (+/-10, +/-1, +/-0.1, +/-0.01 V). For each, verify an "
            "in-range DAC0 voltage tracks within a per-range tolerance and an over-range voltage clamps near "
            "full scale (no over-range case for +/-10 V since DAC0 maxes below 5 V).",
            step,
        )

    # =====================================================================
    # 7. Digital line write/read loopback
    # =====================================================================
    def test_07_digital_line_loopback(self):
        """Drive FIO0 and verify the state on FIO1 via single-line loopback."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_digital_lines(daq)

                errs = []
                for state in (0, 1, 0, 1, 0):
                    daq.write_digital_line(DO_ALIAS, state)
                    time.sleep(0.05)
                    read = int(daq.read_digital_line(DI_ALIAS).latest)
                    flag = "" if (not LOOPBACK_WIRED or read == state) else "  <-- mismatch"
                    print(f"         FIO0<-{state} | FIO1={read}{flag}")
                    if LOOPBACK_WIRED and read != state:
                        errs.append(f"drove FIO0={state}, read FIO1={read}")
                daq.write_digital_line(DO_ALIAS, 0)
                self.assertFalse(errs, "; ".join(errs))
            finally:
                daq.write_digital_line(DO_ALIAS, 0)
                daq.close()

        self._run_step(
            "Digital line loopback",
            "Drive FIO0 through a 0/1 sequence and verify FIO1 reads back the same state "
            "via single-line loopback wiring.",
            step,
        )

    # =====================================================================
    # 8. Multichannel HW-timed analog read with background daemon
    # =====================================================================
    def test_08_hw_timed_analog_read_background(self):
        """Start multichannel HW-timed acquisition with background daemon and verify each channel's buffer."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_ai(daq)
                self._configure_ao(daq)
                daq.write_analog_value(AO0_ALIAS, HW_TIMED_DC_V0)  # hold distinct DC levels before streaming
                daq.write_analog_value(AO1_ALIAS, HW_TIMED_DC_V1)
                daq.configure_ai_sample_rate(
                    sample_rate=SAMPLE_RATE_HZ,
                    samples_per_channel=SAMPLES_PER_CHANNEL,
                )
                daq.start()

                try:
                    time.sleep(1.0)  # let background daemon collect samples

                    for ai_alias, level in ((AI0_ALIAS, HW_TIMED_DC_V0), (AI1_ALIAS, HW_TIMED_DC_V1)):
                        ch = daq.get_channel(f"{NAME}.{ai_alias}", 50, True)
                        self.assertIsNotNone(ch)
                        self.assertGreaterEqual(len(ch.values), 1)
                        self.assertTrue(
                            all(math.isfinite(v) for v in ch.values), f"non-finite samples in {ai_alias} buffer"
                        )
                        mean = sum(ch.values) / len(ch.values)
                        print(
                            f"         background {ai_alias}: {len(ch.values)} samples, mean = {mean:.4f} V (expected {level} V)"
                        )
                        if LOOPBACK_WIRED:
                            self.assertAlmostEqual(
                                mean,
                                level,
                                delta=HW_TIMED_TOLERANCE_V,
                                msg=f"{ai_alias} mean {mean:.4f} V != expected {level} V",
                            )
                finally:
                    daq.stop()
                    daq.write_analog_value(AO0_ALIAS, 0.0)
                    daq.write_analog_value(AO1_ALIAS, 0.0)
            finally:
                daq.close()

        self._run_step(
            "HW-timed analog read (background, multichannel)",
            f"Start multichannel HW-timed acquisition at {SAMPLE_RATE_HZ} Hz with background daemon. "
            f"Hold DAC0 at {HW_TIMED_DC_V0} V and DAC1 at {HW_TIMED_DC_V1} V; verify AIN0 and AIN1 buffers "
            "each track their own source via get_channel().",
            step,
        )

    # =====================================================================
    # 9. Multichannel HW-timed analog read without background daemon
    # =====================================================================
    def test_09_hw_timed_analog_read_no_background(self):
        """Start multichannel HW-timed acquisition without background daemon and read both channels directly."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_ai(daq)
                self._configure_ao(daq)
                daq.write_analog_value(AO0_ALIAS, HW_TIMED_DC_V0)
                daq.write_analog_value(AO1_ALIAS, HW_TIMED_DC_V1)
                daq.configure_ai_sample_rate(
                    sample_rate=SAMPLE_RATE_HZ,
                    samples_per_channel=SAMPLES_PER_CHANNEL,
                )
                daq.start(background=False)

                try:
                    # No background daemon: read_analog() dispatches to the driver's fetch_analog().
                    measurement = daq.read_analog()
                    self.assertIsNotNone(measurement)

                    for ai_alias, level in ((AI0_ALIAS, HW_TIMED_DC_V0), (AI1_ALIAS, HW_TIMED_DC_V1)):
                        vals = measurement.channel_data.get(f"{NAME}.{ai_alias}", [])
                        self.assertGreaterEqual(len(vals), 1, f"no samples fetched for {ai_alias}")
                        self.assertTrue(
                            all(math.isfinite(v) for v in vals),
                            f"non-finite HW-timed fetch for {ai_alias}: n={len(vals)}",
                        )
                        mean = sum(vals) / len(vals)
                        print(
                            f"         fetched {ai_alias}: {len(vals)} samples, mean = {mean:.4f} V (expected {level} V)"
                        )
                        if LOOPBACK_WIRED:
                            self.assertAlmostEqual(
                                mean,
                                level,
                                delta=HW_TIMED_TOLERANCE_V,
                                msg=f"{ai_alias} mean {mean:.4f} V != expected {level} V",
                            )
                finally:
                    daq.stop()
                    daq.write_analog_value(AO0_ALIAS, 0.0)
                    daq.write_analog_value(AO1_ALIAS, 0.0)
            finally:
                daq.close()

        self._run_step(
            "HW-timed analog read (no background, multichannel)",
            f"Start multichannel HW-timed acquisition at {SAMPLE_RATE_HZ} Hz with background daemon disabled. "
            f"Hold DAC0 at {HW_TIMED_DC_V0} V and DAC1 at {HW_TIMED_DC_V1} V and read both directly via "
            "read_analog() (driver fetch_analog()); verify each channel tracks its own source.",
            step,
        )

    # =====================================================================
    # 10. Actual sample rate reporting
    # =====================================================================
    def test_10_actual_sample_rate(self):
        """Verify get_actual_sample_rate returns a reasonable value after start."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_ai(daq)
                daq.configure_ai_sample_rate(
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
    # 11. Buffer-depth telemetry
    # =====================================================================
    def test_11_buffer_depth_telemetry(self):
        """Verify get_points_in_buffer reports a valid depth during background acquisition."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_ai(daq)
                daq.configure_ai_sample_rate(
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
    # 12. Clean shutdown — outputs to safe state
    # =====================================================================
    def test_12_clean_shutdown(self):
        """Set all outputs to safe state as a final step."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_ao(daq)
                self._configure_digital_lines(daq)

                daq.write_analog_value(AO0_ALIAS, 0.0)
                daq.write_digital_line(DO_ALIAS, 0)
            finally:
                daq.close()

        self._run_step(
            "Clean shutdown — safe state",
            "Set DAC0 to 0 V and FIO0 to 0 as a final safety step.",
            step,
        )

    # =====================================================================
    # 13. Channel registry introspection + immutability
    # =====================================================================
    def test_13_channel_registry(self):
        """Configured channels appear in the driver's frozen snapshots, keyed by alias, and those snapshots are read-only (MappingProxyType)."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_ai(daq)
                self._configure_ao(daq)
                self._configure_digital_lines(daq)

                # configure_* must land each channel in the matching frozen snapshot.
                self.assertEqual(set(daq.ai_channels), {AI0_ALIAS, AI1_ALIAS})
                self.assertEqual(set(daq.ao_channels), {AO0_ALIAS, AO1_ALIAS})
                self.assertEqual(set(daq.di_channels), {DI_ALIAS})
                self.assertEqual(set(daq.do_channels), {DO_ALIAS})

                # range_min/max and physical_channel round-trip onto the AnalogChannel.
                ai0 = daq.ai_channels[AI0_ALIAS]
                print(f"         {AI0_ALIAS}: phys={ai0.physical_channel} range=[{ai0.range_min},{ai0.range_max}]")
                self.assertEqual(ai0.physical_channel, AI0_CHANNEL)
                self.assertEqual((ai0.range_min, ai0.range_max), (-10, 10))

                # Snapshot is a read-only MappingProxyType; mutation must raise.
                with self.assertRaises(TypeError):
                    daq.ai_channels["bogus"] = ai0
                print("         ai/ao/di/do snapshots correct; snapshot mutation rejected")
            finally:
                daq.close()

        self._run_step(
            "Channel registry introspection",
            "Verify configure_* lands channels in the driver's frozen ai/ao/di/do snapshots keyed by alias, "
            "that physical_channel and range bounds round-trip onto the AnalogChannel, and that the snapshots "
            "are immutable (MappingProxyType).",
            step,
        )

    # =====================================================================
    # 14. Error contract — acting on unconfigured channels
    # =====================================================================
    def test_14_unconfigured_channel_errors(self):
        """Writing/reading an unconfigured alias raises KeyError (per the driver contract)."""

        def step():
            daq = self._create_daq()
            try:
                # Nothing configured yet -- every alias lookup should miss and raise.
                with self.assertRaises(KeyError):
                    daq.write_analog_value("nonexistent_dac", 1.0)
                with self.assertRaises(KeyError):
                    daq.write_digital_line("nonexistent_line", 1)
                with self.assertRaises(KeyError):
                    daq.read_digital_line("nonexistent_line")
                print("         KeyError raised for all three unconfigured-channel operations")
            finally:
                daq.close()

        self._run_step(
            "Unconfigured-channel error contract",
            "Verify write_analog_value / write_digital_line / read_digital_line each raise KeyError when the "
            "target alias was never configured, matching the driver's documented contract.",
            step,
        )

    # =====================================================================
    # 15. Lifecycle guard — operations before open()
    # =====================================================================
    def test_15_requires_open(self):
        """Device operations before open() raise InstrumentNotOpenError."""

        def step():
            # Construct but deliberately do NOT open the DAQ.
            daq = InstroDAQ(name=NAME, driver=LabJackTSeriesDriver(device_id=DEVICE_ID))
            with self.assertRaises(InstrumentNotOpenError):
                daq.configure_analog_channel(
                    direction=Direction.INPUT,
                    physical_channel=AI0_CHANNEL,
                    alias=AI0_ALIAS,
                )
            with self.assertRaises(InstrumentNotOpenError):
                daq.read_analog()
            print("         InstrumentNotOpenError raised for config + read before open()")

        self._run_step(
            "Lifecycle guard (not open)",
            "Verify configure_analog_channel() and read_analog() raise InstrumentNotOpenError when called "
            "before open(), confirming the _require_open() gate.",
            step,
        )

    # =====================================================================
    # 16. Methods not implemented on the T7 — reported as skipped
    # =====================================================================
    def test_16_port_width_digital_unsupported(self):
        """write_digital_port / read_digital_port are not implemented for the T7."""
        self.skipTest("driver raises NotImplementedError for LabJack port-width digital I/O")

    def test_17_relay_control_unsupported(self):
        """Relay control is not supported by the LabJack driver."""
        self.skipTest("DAQDriverBase relays unsupported by LabJack")


if __name__ == "__main__":
    unittest.main()
