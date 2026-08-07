"""Hardware integration test for an NI DAQ device (NI-DAQmx) via InstroDAQ.

This test requires a physical NI DAQ device (a PCIe/USB "DevN" or a cDAQ
chassis with installed modules) connected with the loopback wiring described
below. It exercises analog DAQ functionality exposed by the NI driver:
software-timed analog read (single-shot and background), hardware-timed analog
read (background and non-background), analog output, analog loopback
verification (each path alone and both paths together),
actual-sample-rate reporting, and buffer-depth telemetry. Each test step is
recorded as an event on a Nominal Core asset.

Digital I/O tests exercise single-line read/write via a DO_LINE -> DI_LINE
loopback. Relays are not supported by the NI driver, so that test is reported
as skipped.

============================================================================
NI LOOPBACK WIRING
============================================================================

  Device Specs:
      - Mod1: 9205 (AI)
      - Mod2: 9263 (AO)
      - Mod3: 9403 (DO)
      - Mod4: 9401 (DI)

  Analog loopback 1 (wire AO -> AI):
    AO_CHANNEL (<module>/ao0)  --->  AI_CHANNEL (<module>/ai0)
    AO_CHANNEL_2 (<module>/ao1)  --->  AI_CHANNEL_2 (<module>/ai1)

  Digital loopback (wire DO -> DI):
    DO_LINE (<module>/portM/lineP, output)  --->  DI_LINE (<module>/portM/lineP, input)

  Set ANALOG_LOOPBACK_WIRED = False and/or DIGITAL_LOOPBACK_WIRED = False to
  run structure-only checks (no value-match asserts) for the unwired path.

============================================================================
NOMINAL CORE CONFIGURATION
============================================================================

  Before running, configure:

    DEVICE_ID           — NI device name as shown in NI MAX (e.g. "Dev1" or
                          a cDAQ chassis like "cDAQ1")
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

    uv run pytest tests/daq/ni -m hardware -v -s

"""

import math
import time
import unittest
from datetime import timedelta

import pytest

pytest.importorskip("nidaqmx")

from nidaqmx.system import System as niSystem  # noqa: E402
from nominal.core import EventType, NominalClient  # noqa: E402

from instro.daq import InstroDAQ  # noqa: E402
from instro.daq.drivers.ni import NIDAQDriver  # noqa: E402
from instro.daq.types import Direction, Logic  # noqa: E402
from instro.lib.publishers import NominalCorePublisher  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration — edit before running
# ---------------------------------------------------------------------------
DEVICE_ID = "<NI DEVICE NAME>"  # NI device name as shown in NI MAX (e.g. "Dev1" or a cDAQ chassis like "cDAQ1")
NAME = "ni_validate"

# Set to a Nominal dataset RID to stream validation data via NominalCorePublisher;
# leave None to publish nowhere.
DATASET_RID = None

# Analog channel mapping — two AO -> AI loopback pairs (may live on different modules).
AI_CHANNEL, AI_ALIAS = f"{DEVICE_ID}Mod1/ai1", "ai1"
AO_CHANNEL, AO_ALIAS = f"{DEVICE_ID}Mod2/ao0", "ao0"
AI_CHANNEL_2, AI_ALIAS_2 = f"{DEVICE_ID}Mod1/ai2", "ai2"
AO_CHANNEL_2, AO_ALIAS_2 = f"{DEVICE_ID}Mod2/ao1", "ao1"

# Digital channel mapping — one DO line and one DI line (DevN/portM/lineP form).
DO_LINE, DO_ALIAS = f"{DEVICE_ID}Mod3/port0/line16", "do20"
DI_LINE, DI_ALIAS = f"{DEVICE_ID}Mod4/port0/line0", "di0"

# True when the corresponding path is physically looped back. Gates the strict
# value checks; structural checks always run.
ANALOG_LOOPBACK_WIRED = True
DIGITAL_LOOPBACK_WIRED = True

# AI/AO ranges and test points — adjust to the installed modules' capabilities.
AI_RANGE_MIN, AI_RANGE_MAX = -10.0, 10.0
AO_RANGE_MIN, AO_RANGE_MAX = -10.0, 10.0
ANALOG_TEST_VOLTAGES = [0.0, 0.5, 1.25, 2.5, 3.3, 4.5]
ANALOG_TOLERANCE_V = 0.05

SAMPLE_RATE_HZ = 1000.0
SAMPLES_PER_CHANNEL = 100
SW_SAMPLE_RATE_HZ = 1.0
HW_TIMED_DC_V = 2.0  # DC level held on the AO during hardware-timed reads.
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
            properties={"device_type": "NI DAQ", "purpose": "hardware-test"},
            name="NI DAQ",
            description="NI DAQ device under test",
            labels=["ni", "hardware-test"],
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
                labels=["ni-daq-test"],
            )


_recorder = _EventRecorder()


# ---------------------------------------------------------------------------
# Test suite
# ---------------------------------------------------------------------------
@pytest.mark.hardware
class TestNIDAQHardware(unittest.TestCase):
    """Hardware integration tests for an NI DAQ via InstroDAQ.

    Each test creates, opens, configures, and closes its own DAQ instance,
    making every test independent. A fresh open() also gives each test its
    own DAQmx tasks.
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
            driver=NIDAQDriver(device_id=DEVICE_ID),
        )
        if DATASET_RID:
            daq.add_publisher(NominalCorePublisher(dataset_rid=DATASET_RID))
        daq.open()
        return daq

    def _configure_ai(self, daq: InstroDAQ, physical: str = AI_CHANNEL, alias: str = AI_ALIAS):
        """Configure an AI input channel (defaults to the loopback-1 pair)."""
        daq.configure_analog_channel(
            direction=Direction.INPUT,
            physical_channel=physical,
            alias=alias,
            range_min=AI_RANGE_MIN,
            range_max=AI_RANGE_MAX,
        )

    def _configure_ao(self, daq: InstroDAQ, physical: str = AO_CHANNEL, alias: str = AO_ALIAS):
        """Configure an AO output channel (defaults to the loopback-1 pair)."""
        daq.configure_analog_channel(
            direction=Direction.OUTPUT,
            physical_channel=physical,
            alias=alias,
            range_min=AO_RANGE_MIN,
            range_max=AO_RANGE_MAX,
        )

    def _configure_digital_lines(self, daq: InstroDAQ):
        """Configure DO_LINE as output and DI_LINE as input (single lines)."""
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
    # 1. Device info
    # =====================================================================
    def test_01_device_info(self):
        """Verify the device is visible to NI-DAQmx and record its product type."""

        def step():
            daq = self._create_daq()
            try:
                dev = niSystem.local().devices[DEVICE_ID]
                print(f"         product_type={dev.product_type}  serial={dev.serial_num}")
            finally:
                daq.close()

        self._run_step(
            "Device info",
            "Verify open() finds the device in NI-DAQmx and record its product type and serial number.",
            step,
        )

    # =====================================================================
    # 2. Software-timed analog input
    # =====================================================================
    def test_02_sw_timed_analog_read(self):
        """Read the AI channel in software-timed mode (single-shot)."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_ai(daq)

                for _ in range(3):
                    measurement = daq.read(AI_ALIAS)[AI_ALIAS]
                    self.assertIsNotNone(measurement)
                    vals = measurement.values
                    self.assertTrue(vals and math.isfinite(vals[-1]), f"non-finite SW-timed read: {vals}")
                    print(f"         {AI_ALIAS} (sw-timed) = {vals[-1]:.4f} V")
                    time.sleep(0.25)
            finally:
                daq.close()

        self._run_step(
            "SW-timed analog read",
            "Configure the AI channel and perform 3 single-shot software-timed reads.",
            step,
        )

    # =====================================================================
    # 3. Analog output — write known voltages
    # =====================================================================
    def test_03_analog_output(self):
        """Write a series of voltages to the AO channel."""

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
            f"Configure the AO channel and write a sweep of voltages: {ANALOG_TEST_VOLTAGES} V.",
            step,
        )

    # =====================================================================
    # 4. Analog loopback — write AO, verify on AI (software-timed)
    # =====================================================================
    def test_04_analog_loopback_sw_timed(self):
        """Write known voltages to the AO and verify they appear on the AI (SW-timed)."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_ai(daq)
                self._configure_ao(daq)

                errs = []
                for v in ANALOG_TEST_VOLTAGES:
                    daq.write(AO_ALIAS, v)
                    time.sleep(0.05)  # let the output settle
                    measured = daq.read(AI_ALIAS)[AI_ALIAS].latest
                    err = measured - v
                    flag = (
                        ""
                        if (not ANALOG_LOOPBACK_WIRED or abs(err) <= ANALOG_TOLERANCE_V)
                        else "  <-- out of tolerance"
                    )
                    print(f"         {AO_ALIAS}={v:.3f} V | {AI_ALIAS}={measured:.4f} V | err={err:+.4f} V{flag}")
                    if not math.isfinite(measured):
                        errs.append(f"non-finite read at {v} V")
                    if ANALOG_LOOPBACK_WIRED and abs(err) > ANALOG_TOLERANCE_V:
                        errs.append(
                            f"{AO_ALIAS}={v} V -> {AI_ALIAS}={measured:.4f} V (err {err:+.4f} V > {ANALOG_TOLERANCE_V} V)"
                        )
                daq.write(AO_ALIAS, 0.0)
                self.assertFalse(errs, "; ".join(errs))
            finally:
                daq.write(AO_ALIAS, 0.0)
                daq.close()

        self._run_step(
            "Analog loopback (SW-timed)",
            "Write known voltages to the AO and read back on the AI via loopback wiring. "
            "Verifies the AO->AI signal path using software-timed single-shot reads.",
            step,
        )

    # =====================================================================
    # 5. Dual analog loopback — drive both AO->AI paths together
    # =====================================================================
    def test_05_dual_analog_loopback(self):
        """Drive both AOs at once and verify each AI tracks only its own source.

        Both AI channels share one DAQmx AI task, so a single read()
        samples them together.
        """

        def step():
            if not ANALOG_LOOPBACK_WIRED:
                self.skipTest("ANALOG_LOOPBACK_WIRED=False")
            daq = self._create_daq()
            try:
                self._configure_ai(daq)
                self._configure_ai(daq, AI_CHANNEL_2, AI_ALIAS_2)
                self._configure_ao(daq)
                self._configure_ao(daq, AO_CHANNEL_2, AO_ALIAS_2)

                errs = []
                for v1, v2 in [(1.0, 4.5), (4.5, 0.5), (2.5, 3.3), (0.0, 0.0)]:
                    daq.write([AO_ALIAS, AO_ALIAS_2], [v1, v2])
                    time.sleep(0.05)  # let both outputs settle

                    reads = daq.read([AI_ALIAS, AI_ALIAS_2])
                    for alias, target in [(AI_ALIAS, v1), (AI_ALIAS_2, v2)]:
                        measured = reads[alias].latest
                        err = measured - target
                        flag = "" if abs(err) <= ANALOG_TOLERANCE_V else "  <-- out of tolerance"
                        print(
                            f"         {AO_ALIAS}={v1:.3f} V | {AO_ALIAS_2}={v2:.3f} V | "
                            f"{alias}={measured:.4f} V | err={err:+.4f} V{flag}"
                        )
                        if not math.isfinite(measured):
                            errs.append(f"{alias}: non-finite read at target {target} V")
                        elif abs(err) > ANALOG_TOLERANCE_V:
                            errs.append(
                                f"{alias}={measured:.4f} V, target={target} V "
                                f"(err {err:+.4f} V > {ANALOG_TOLERANCE_V} V)"
                            )
                self.assertFalse(errs, "; ".join(errs))
            finally:
                daq.write([AO_ALIAS, AO_ALIAS_2], [0.0, 0.0])
                daq.close()

        self._run_step(
            "Dual analog loopback (SW-timed)",
            "Set both AOs to different voltages and read both AIs in one read(). "
            "Verifies each loopback path tracks its own source with no cross-talk.",
            step,
        )

    # =====================================================================
    # 6. Digital line write/read loopback
    # =====================================================================
    def test_06_digital_line_loopback(self):
        """Drive DO_LINE and verify the state on DI_LINE via single-line loopback."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_digital_lines(daq)

                errs = []
                for state in (0, 1, 0, 1, 0):
                    daq.write(DO_ALIAS, state)
                    time.sleep(0.05)
                    read = int(daq.read(DI_ALIAS)[DI_ALIAS].latest)
                    flag = "" if (not DIGITAL_LOOPBACK_WIRED or read == state) else "  <-- mismatch"
                    print(f"         {DO_ALIAS}<-{state} | {DI_ALIAS}={read}{flag}")
                    if DIGITAL_LOOPBACK_WIRED and read != state:
                        errs.append(f"drove {DO_ALIAS}={state}, read {DI_ALIAS}={read}")
                daq.write(DO_ALIAS, 0)
                self.assertFalse(errs, "; ".join(errs))
            finally:
                daq.write(DO_ALIAS, 0)
                daq.close()

        self._run_step(
            "Digital line loopback",
            "Drive DO_LINE through a 0/1 sequence and verify DI_LINE reads back the same state "
            "via single-line loopback wiring.",
            step,
        )

    # =====================================================================
    # 7. HW-timed analog read with background daemon
    # =====================================================================
    def test_07_hw_timed_analog_read_background(self):
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
                    print(f"         background buffer: {len(ch.values)} samples, mean {AI_ALIAS} = {mean:.4f} V")
                    if ANALOG_LOOPBACK_WIRED:
                        self.assertAlmostEqual(mean, HW_TIMED_DC_V, delta=HW_TIMED_TOLERANCE_V)
                finally:
                    daq.stop()
                    daq.write(AO_ALIAS, 0.0)
            finally:
                daq.close()

        self._run_step(
            "HW-timed analog read (background)",
            f"Start HW-timed acquisition at {SAMPLE_RATE_HZ} Hz with background daemon. "
            f"Hold the AO at {HW_TIMED_DC_V} V, verify AI reads match via get_channel().",
            step,
        )

    # =====================================================================
    # 8. HW-timed analog read without background daemon
    # =====================================================================
    def test_08_hw_timed_analog_read_no_background(self):
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
                    measurement = daq.read(AI_ALIAS)[AI_ALIAS]
                    self.assertIsNotNone(measurement)
                    vals = measurement.values
                    self.assertGreaterEqual(len(vals), 1)
                    self.assertTrue(all(math.isfinite(v) for v in vals), f"non-finite HW-timed fetch: n={len(vals)}")

                    mean = sum(vals) / len(vals)
                    print(
                        f"         fetched {len(vals)} samples, mean {AI_ALIAS} = {mean:.4f} V "
                        f"(AO held at {HW_TIMED_DC_V} V)"
                    )
                    if ANALOG_LOOPBACK_WIRED:
                        self.assertAlmostEqual(mean, HW_TIMED_DC_V, delta=HW_TIMED_TOLERANCE_V)
                finally:
                    daq.stop()
                    daq.write(AO_ALIAS, 0.0)
            finally:
                daq.close()

        self._run_step(
            "HW-timed analog read (no background)",
            f"Start HW-timed acquisition at {SAMPLE_RATE_HZ} Hz with background daemon disabled. "
            f"Hold the AO at {HW_TIMED_DC_V} V and read directly via read() (driver fetch_analog()).",
            step,
        )

    # =====================================================================
    # 9. SW-timed analog read with background daemon
    # =====================================================================
    def test_09_sw_timed_analog_read_background(self):
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
                    print(f"         background buffer: {len(ch.values)} samples, mean {AI_ALIAS} = {mean:.4f} V")
                    if ANALOG_LOOPBACK_WIRED:
                        self.assertAlmostEqual(mean, HW_TIMED_DC_V, delta=HW_TIMED_TOLERANCE_V)
                finally:
                    daq.stop()
                    daq.write(AO_ALIAS, 0.0)
            finally:
                daq.close()

        self._run_step(
            "SW-timed analog read (background)",
            f"Start SW-timed acquisition at {SW_SAMPLE_RATE_HZ} Hz with background daemon. "
            f"Hold the AO at {HW_TIMED_DC_V} V, verify AI reads match via get_channel().",
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
    # 11. Buffer-depth telemetry
    # =====================================================================
    def test_11_buffer_depth_telemetry(self):
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
    # 12. Clean shutdown — outputs to safe state
    # =====================================================================
    def test_12_clean_shutdown(self):
        """Set all outputs to safe state as a final step."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_ao(daq)
                self._configure_ao(daq, AO_CHANNEL_2, AO_ALIAS_2)
                self._configure_digital_lines(daq)

                daq.write([AO_ALIAS, AO_ALIAS_2, DO_ALIAS], [0.0, 0.0, 0])
            finally:
                daq.close()

        self._run_step(
            "Clean shutdown — safe state",
            "Set both AOs to 0 V and DO_LINE to 0 as a final safety step.",
            step,
        )

    # =====================================================================
    # 13. Methods not implemented on NI — reported as skipped
    # =====================================================================
    def test_13_relay_control_unsupported(self):
        """Relay control is not supported by the NI driver."""
        self.skipTest("DAQDriverBase relays unsupported by the NI driver")


if __name__ == "__main__":
    unittest.main()
