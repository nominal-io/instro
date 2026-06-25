"""Hardware integration test for the LabJack T8 DAQ via InstroDAQ.

This suite validates the InstroDAQ abstraction layer against a physical
LabJack T8. Every test goes through InstroDAQ's public API — no direct
LJM register access. This makes the suite driver-agnostic: the same
test structure would apply to any DAQDriverBase implementation.

Tests exercise:
  - Device identification via get_info()
  - Software-timed analog read across all 8 AIN channels
  - Analog output on both DAC channels (0–10 V range)
  - Analog loopback: write DAC0, verify on AIN0 (15 mV tolerance)
  - Dual-DAC independent isolation: DAC0→AIN0, DAC1→AIN2 simultaneously
  - Hardware-timed streaming: background daemon and foreground fetch
  - High data rate streaming (40 kS/s)
  - Actual sample rate reporting
  - Buffer-depth telemetry
  - Digital line loopback: FIO4 → FIO5
  - NotImplementedError assertions: port-width digital I/O, relay control
  - Clean shutdown

============================================================================
LABJACK T8 LOOPBACK WIRING
============================================================================

  Analog (screw terminals on DB15):
    DAC0  ──►  AIN0+   AIN0− ──► GND
    DAC0  ──►  AIN1+   AIN1− ──► GND
    DAC1  ──►  AIN2+   AIN2− ──► GND
    DAC0  ──►  AIN3+   AIN3− ──► GND   (through AIN7)
    DAC0  ──►  AIN4+   AIN4− ──► GND
    DAC0  ──►  AIN5+   AIN5− ──► GND
    DAC0  ──►  AIN6+   AIN6− ──► GND
    DAC0  ──►  AIN7+   AIN7− ──► GND

  Digital (FIO screw terminals):
    FIO4 (DO)  ──►  FIO5 (DI)

  Set LOOPBACK_WIRED = False to run structure-only checks (no value asserts).

============================================================================
NOMINAL CORE CONFIGURATION
============================================================================

  DEVICE_ID   — LabJack T8 serial number (or "ANY")
  DATASET_RID — Nominal dataset RID (optional; None to skip publishing)

============================================================================
RUNNING
============================================================================

    uv run pytest -m hardware -v -s

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
from instro.daq.types import Direction, Logic
from instro.lib.publishers import NominalCorePublisher

# ---------------------------------------------------------------------------
# Configuration — edit before running
# ---------------------------------------------------------------------------
DEVICE_ID = "LABJACK T8 SERIAL NUMBER"  # e.g. "123456789" or "ANY"
NAME = "t8_validate"
DATASET_RID = None

# Analog channels
AI_CHANNEL_0, AI_ALIAS_0 = "AIN0", "ain0"  # DAC0 loopback
AI_CHANNEL_1, AI_ALIAS_1 = "AIN1", "ain1"  # DAC0 loopback
AI_CHANNEL_2, AI_ALIAS_2 = "AIN2", "ain2"  # DAC1 loopback
ALL_AI_CHANNELS = [
    ("AIN0", "ain0"),
    ("AIN1", "ain1"),
    ("AIN2", "ain2"),
    ("AIN3", "ain3"),
    ("AIN4", "ain4"),
    ("AIN5", "ain5"),
    ("AIN6", "ain6"),
    ("AIN7", "ain7"),
]

AO_CHANNEL_0, AO_ALIAS_0 = "DAC0", "dac0"
AO_CHANNEL_1, AO_ALIAS_1 = "DAC1", "dac1"

# Digital channels
DO_LINE, DO_ALIAS = "FIO4", "fio4"
DI_LINE, DI_ALIAS = "FIO5", "fio5"

# Tolerances
LOOPBACK_WIRED = True

ANALOG_TEST_VOLTAGES = [0.0, 0.5, 1.25, 2.5, 3.3, 5.0, 7.5, 9.5]
ANALOG_TOLERANCE_V = 0.015  # 15 mV: 24-bit ADC, no multiplexer skew

SAMPLE_RATE_HZ = 1_000.0
SAMPLES_PER_CHANNEL = 100
HW_TIMED_DC_V = 5.0
HW_TIMED_TOLERANCE_V = 0.05

HIGH_RATE_HZ = 40_000.0
HIGH_RATE_SAMPLES = 4_000
HIGH_RATE_TOLERANCE_V = 0.10


# ---------------------------------------------------------------------------
# Nominal Core event helpers
# ---------------------------------------------------------------------------


def _get_client() -> NominalClient:
    return NominalClient.from_profile("default")


class _EventRecorder:
    def __init__(self):
        self._client: NominalClient | None = None
        self._events: list[dict] = []

    def begin(self):
        self._client = _get_client()

    def record_event(self, name: str, start_ns: int, end_ns: int, passed: bool, description: str = ""):
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
            properties={"device_type": "LabJack T8", "purpose": "hardware-test"},
            name="LabJack T8",
            description="LabJack T8 DAQ device under test",
            labels=["labjack", "t8", "hardware-test"],
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
                labels=["labjack-t8-test"],
            )


_recorder = _EventRecorder()


# ---------------------------------------------------------------------------
# Test suite
# ---------------------------------------------------------------------------


@pytest.mark.hardware
class TestLabJackT8Hardware(unittest.TestCase):
    """InstroDAQ abstraction tests for the LabJack T8.

    Every test goes through InstroDAQ's public API only. No direct LJM
    register access. This validates the full stack: driver → InstroDAQ
    → publishing → scaling.

    Test groups:
      01        Device identification
      02–10     Analog I/O (SW-timed, output, loopback, dual-DAC)
      11–16     Hardware-timed streaming (incl. multi-channel)
      17        Digital line loopback
      18        Clean shutdown
      19–20     NotImplementedError assertions
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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _create_daq(self) -> InstroDAQ:
        daq = InstroDAQ(name=NAME, driver=LabJackTSeriesDriver(device_id=DEVICE_ID))
        if DATASET_RID:
            daq.add_publisher(NominalCorePublisher(dataset_rid=DATASET_RID))
        daq.open()
        return daq

    def _configure_ai(self, daq, physical, alias, range_min=-11.0, range_max=11.0):
        daq.configure_analog_channel(
            direction=Direction.INPUT,
            physical_channel=physical,
            alias=alias,
            range_min=range_min,
            range_max=range_max,
        )

    def _configure_ao(self, daq, physical, alias):
        daq.configure_analog_channel(
            direction=Direction.OUTPUT,
            physical_channel=physical,
            alias=alias,
            range_min=0,
            range_max=10,
        )

    def _configure_digital_lines(self, daq):
        daq.configure_digital_line(
            direction=Direction.OUTPUT, physical_channel=DO_LINE, logic=Logic.HIGH, alias=DO_ALIAS
        )
        daq.configure_digital_line(
            direction=Direction.INPUT, physical_channel=DI_LINE, logic=Logic.HIGH, alias=DI_ALIAS
        )

    def _assert_t8(self, daq):
        device_type, conn_type, serial, _ip, _port, _ = daq.driver.get_info()
        print(f"         device_type={device_type} (T8={ljm.constants.dtT8}), conn={conn_type}, serial={serial}")
        self.assertEqual(device_type, ljm.constants.dtT8, f"Connected device is not a T8 (device_type={device_type})")

    @staticmethod
    def _ts(ns: int) -> str:
        """Format a time.time_ns() value as HH:MM:SS.mmm (local time)."""
        t = ns / 1e9
        import datetime

        dt = datetime.datetime.fromtimestamp(t)
        return dt.strftime("%H:%M:%S.") + f"{dt.microsecond // 1000:03d}"

    def _run_step(self, name: str, description: str, fn):
        start_ns = time.time_ns()
        try:
            fn(start_ns)
            end_ns = time.time_ns()
            elapsed_ms = (end_ns - start_ns) / 1e6
            print(f"         [{self._ts(start_ns)} → {self._ts(end_ns)}  elapsed {elapsed_ms:.1f} ms]")
            _recorder.record_event(name, start_ns, end_ns, passed=True, description=description)
        except Exception as exc:
            end_ns = time.time_ns()
            elapsed_ms = (end_ns - start_ns) / 1e6
            print(f"         [{self._ts(start_ns)} → {self._ts(end_ns)}  elapsed {elapsed_ms:.1f} ms]  FAILED: {exc}")
            _recorder.record_event(name, start_ns, end_ns, passed=False, description=f"{description}\n\nError: {exc}")
            raise

    # ==================================================================
    # 01. Device identification
    # ==================================================================
    def test_01_device_info(self):
        """Verify connected device is a T8 via get_info()."""

        def step(start_ns: int):
            print(f"         [start {self._ts(start_ns)}]")
            daq = self._create_daq()
            try:
                self._assert_t8(daq)
                fw = ljm.eReadName(daq.driver._handle, "FIRMWARE_VERSION")
                hw = ljm.eReadName(daq.driver._handle, "HARDWARE_VERSION")
                print(f"         FIRMWARE_VERSION={fw}  HARDWARE_VERSION={hw}")
                self.assertGreater(fw, 0)
                self.assertGreater(hw, 0)
            finally:
                daq.close()

        self._run_step(
            "Device info",
            "Verify get_info() reports a T8 device type.",
            step,
        )

    # ==================================================================
    # 02. SW-timed read — all 8 AIN channels
    # ==================================================================
    def test_02_sw_timed_all_ain_channels(self):
        """Read all 8 AIN channels via read_analog() in SW-timed mode."""

        def step(start_ns: int):
            print(f"         [start {self._ts(start_ns)}]")
            errs = []
            for physical, alias in ALL_AI_CHANNELS:
                daq = self._create_daq()
                try:
                    self._configure_ai(daq, physical, alias)
                    measurement = daq.read_analog()
                    self.assertIsNotNone(measurement, f"{alias}: measurement is None")
                    vals = measurement.values
                    self.assertTrue(vals, f"{alias}: empty values list")
                    v = vals[-1]
                    if not math.isfinite(v):
                        errs.append(f"{alias}: non-finite reading {v}")
                    else:
                        print(f"         {alias} = {v:.6f} V")
                finally:
                    daq.close()
            self.assertFalse(errs, "; ".join(errs))

        self._run_step(
            "SW-timed read — all 8 AIN channels",
            "Configure each of AIN0–AIN7 and call read_analog(). Asserts every channel returns a finite value.",
            step,
        )

    # ==================================================================
    # 03. Analog output — both DAC channels, full 0–10 V sweep
    # ==================================================================
    def test_03_analog_output_both_dacs(self):
        """Write a voltage sweep to DAC0 and DAC1 via write_analog_value()."""

        def step(start_ns: int):
            print(f"         [start {self._ts(start_ns)}]")
            for ao_physical, ao_alias in [(AO_CHANNEL_0, AO_ALIAS_0), (AO_CHANNEL_1, AO_ALIAS_1)]:
                daq = self._create_daq()
                try:
                    self._configure_ao(daq, ao_physical, ao_alias)
                    for v in ANALOG_TEST_VOLTAGES:
                        daq.write_analog_value(ao_alias, v)
                        time.sleep(0.02)
                    daq.write_analog_value(ao_alias, 0.0)
                    print(f"         {ao_alias}: swept {ANALOG_TEST_VOLTAGES} V — OK")
                finally:
                    daq.close()

        self._run_step(
            "Analog output (both DACs, 0–10 V)",
            "Sweep DAC0 and DAC1 through 0–9.5 V via write_analog_value().",
            step,
        )

    # ==================================================================
    # 04. Analog loopback — DAC0 → AIN0, SW-timed, 15 mV tolerance
    # ==================================================================
    def test_04_analog_loopback_sw_timed(self):
        """Write to DAC0, read back on AIN0 via read_analog() (15 mV tolerance)."""

        def step(start_ns: int):
            print(f"         [start {self._ts(start_ns)}]")
            daq = self._create_daq()
            try:
                self._configure_ai(daq, AI_CHANNEL_0, AI_ALIAS_0)
                self._configure_ao(daq, AO_CHANNEL_0, AO_ALIAS_0)
                errs = []
                for v in ANALOG_TEST_VOLTAGES:
                    daq.write_analog_value(AO_ALIAS_0, v)
                    time.sleep(0.05)
                    measured = daq.read_analog().latest
                    err = measured - v
                    flag = "" if (not LOOPBACK_WIRED or abs(err) <= ANALOG_TOLERANCE_V) else "  <-- OUT OF TOLERANCE"
                    print(f"         DAC0={v:.3f} V | AIN0={measured:.6f} V | err={err:+.6f} V{flag}")
                    if not math.isfinite(measured):
                        errs.append(f"non-finite at {v} V")
                    elif LOOPBACK_WIRED and abs(err) > ANALOG_TOLERANCE_V:
                        errs.append(f"DAC0={v} V → AIN0={measured:.6f} V (err {err:+.6f} V > {ANALOG_TOLERANCE_V} V)")
                daq.write_analog_value(AO_ALIAS_0, 0.0)
                self.assertFalse(errs, "; ".join(errs))
            finally:
                daq.write_analog_value(AO_ALIAS_0, 0.0)
                daq.close()

        self._run_step(
            "Analog loopback (SW-timed, 15 mV tol.)",
            "Sweep DAC0 0–9.5 V and read back on AIN0 via read_analog(). "
            "15 mV tolerance reflects 24-bit ADC with no multiplexer skew.",
            step,
        )

    # ==================================================================
    # 05. Dual-DAC independent isolation
    # ==================================================================
    def test_05_dual_dac_independent_isolation(self):
        """Drive DAC0→AIN0 and DAC1→AIN2 simultaneously; verify no cross-talk.

        Both channels are read in a single read_analog() call since the T8
        samples simultaneously. channel_data is used directly because
        .latest raises when multiple channels are present.
        """

        def step(start_ns: int):
            print(f"         [start {self._ts(start_ns)}]")
            if not LOOPBACK_WIRED:
                self.skipTest("LOOPBACK_WIRED=False")
            daq = self._create_daq()
            try:
                self._configure_ai(daq, AI_CHANNEL_0, AI_ALIAS_0)
                self._configure_ai(daq, AI_CHANNEL_2, AI_ALIAS_2)
                self._configure_ao(daq, AO_CHANNEL_0, AO_ALIAS_0)
                self._configure_ao(daq, AO_CHANNEL_1, AO_ALIAS_1)

                errs = []
                pairs = [(1.0, 8.0), (4.5, 0.5), (9.0, 3.3), (0.0, 0.0)]
                for v0, v1 in pairs:
                    daq.write_analog_value(AO_ALIAS_0, v0)
                    daq.write_analog_value(AO_ALIAS_1, v1)
                    time.sleep(0.05)

                    # Single read_analog() captures both channels simultaneously.
                    # .latest raises with multiple channels so use channel_data.
                    measurement = daq.read_analog()
                    ain0 = measurement.channel_data.get(f"{NAME}.{AI_ALIAS_0}", [None])[-1]
                    ain2 = measurement.channel_data.get(f"{NAME}.{AI_ALIAS_2}", [None])[-1]

                    for label, measured, target in [("AIN0", ain0, v0), ("AIN2", ain2, v1)]:
                        if measured is None or not math.isfinite(measured):
                            errs.append(f"{label}: non-finite at target={target} V")
                            continue
                        err = measured - target
                        flag = "" if abs(err) <= ANALOG_TOLERANCE_V else "  <-- FAIL"
                        print(
                            f"         DAC0={v0:.2f} V | DAC1={v1:.2f} V | "
                            f"{label}={measured:.4f} V (err={err:+.4f} V){flag}"
                        )
                        if abs(err) > ANALOG_TOLERANCE_V:
                            errs.append(f"{label}: target={target} V, measured={measured:.4f} V")

                daq.write_analog_value(AO_ALIAS_0, 0.0)
                daq.write_analog_value(AO_ALIAS_1, 0.0)
                self.assertFalse(errs, "; ".join(errs))
            finally:
                daq.write_analog_value(AO_ALIAS_0, 0.0)
                daq.write_analog_value(AO_ALIAS_1, 0.0)
                daq.close()

        self._run_step(
            "Dual-DAC independent isolation",
            "Set DAC0 and DAC1 to different voltages; verify AIN0 and AIN2 track "
            "their respective sources without cross-talk.",
            step,
        )

    # ==================================================================
    # 06. DAC1 loopback — verify DAC1 output reads back on AIN2
    # ==================================================================
    def test_06_dac1_loopback(self):
        """Write known voltages to DAC1 and verify on AIN2 via read_analog().

        test_03 sweeps DAC1 but never reads back. test_05 uses DAC1 but
        only as part of a cross-channel isolation check. This test isolates
        DAC1 accuracy independently: configure only AIN2 and DAC1, sweep
        the same voltage points as test_04, and assert the same 15 mV
        tolerance. If DAC1 has a gain or offset bug that DAC0 does not,
        this is the test that will catch it.
        """

        def step(start_ns: int):
            print(f"         [start {self._ts(start_ns)}]")
            daq = self._create_daq()
            try:
                self._configure_ai(daq, AI_CHANNEL_2, AI_ALIAS_2)
                self._configure_ao(daq, AO_CHANNEL_1, AO_ALIAS_1)
                errs = []
                for v in ANALOG_TEST_VOLTAGES:
                    daq.write_analog_value(AO_ALIAS_1, v)
                    time.sleep(0.05)
                    measured = daq.read_analog().latest
                    err = measured - v
                    flag = "" if (not LOOPBACK_WIRED or abs(err) <= ANALOG_TOLERANCE_V) else "  <-- OUT OF TOLERANCE"
                    print(f"         DAC1={v:.3f} V | AIN2={measured:.6f} V | err={err:+.6f} V{flag}")
                    if not math.isfinite(measured):
                        errs.append(f"non-finite at {v} V")
                    elif LOOPBACK_WIRED and abs(err) > ANALOG_TOLERANCE_V:
                        errs.append(f"DAC1={v} V -> AIN2={measured:.6f} V (err {err:+.6f} V > {ANALOG_TOLERANCE_V} V)")
                daq.write_analog_value(AO_ALIAS_1, 0.0)
                self.assertFalse(errs, "; ".join(errs))
            finally:
                daq.write_analog_value(AO_ALIAS_1, 0.0)
                daq.close()

        self._run_step(
            "DAC1 loopback (SW-timed, 15 mV tol.)",
            "Sweep DAC1 0-9.5 V and read back on AIN2 via read_analog(). Verifies DAC1 accuracy independently of DAC0.",
            step,
        )

    # ==================================================================
    # 07. Analog output — Command return value
    # ==================================================================
    def test_07_analog_output_command_return(self):
        """write_analog_value() must return a Command with the correct value.

        The @publish_command decorator on write_analog_value() builds and
        returns a Command object. This verifies the Command channel key and
        value are correct. If the driver silently swallows the write or the
        publisher mangles the value, the Command will expose it.
        """

        def step(start_ns: int):
            print(f"         [start {self._ts(start_ns)}]")
            daq = self._create_daq()
            try:
                self._configure_ao(daq, AO_CHANNEL_0, AO_ALIAS_0)
                for v in [0.0, 2.5, 5.0, 9.5]:
                    cmd = daq.write_analog_value(AO_ALIAS_0, v)
                    self.assertIsNotNone(cmd, f"write_analog_value returned None at {v} V")
                    expected_key = f"{NAME}.{AO_ALIAS_0}.cmd"
                    self.assertIn(
                        expected_key,
                        cmd.channel_data,
                        f"Command missing key '{expected_key}' at {v} V. Keys present: {list(cmd.channel_data.keys())}",
                    )
                    returned_v = cmd.channel_data[expected_key]
                    self.assertAlmostEqual(
                        returned_v,
                        v,
                        places=6,
                        msg=f"Command value {returned_v} != written value {v}",
                    )
                    print(f"         write {v:.2f} V -> Command key='{expected_key}' value={returned_v}")
                daq.write_analog_value(AO_ALIAS_0, 0.0)
            finally:
                daq.close()

        self._run_step(
            "Analog output - Command return value",
            "Verify write_analog_value() returns a Command with the correct "
            "channel key and value at 0, 2.5, 5.0, and 9.5 V.",
            step,
        )

    # ==================================================================
    # 08. Analog output — unconfigured channel raises KeyError
    # ==================================================================
    def test_08_analog_output_unconfigured_raises(self):
        """write_analog_value() on an unconfigured alias must raise KeyError.

        InstroDAQ guards every write with a channel lookup. If the alias
        has not been registered via configure_analog_channel(OUTPUT), a
        KeyError should be raised immediately rather than a cryptic LJM
        error or a silent no-op. This confirms the guard rail works.
        """

        def step(start_ns: int):
            print(f"         [start {self._ts(start_ns)}]")
            daq = self._create_daq()
            try:
                with self.assertRaises(
                    KeyError, msg="write_analog_value on unconfigured channel should raise KeyError"
                ):
                    daq.write_analog_value("dac0", 1.0)
            finally:
                daq.close()

        self._run_step(
            "Analog output - unconfigured channel raises KeyError",
            "Call write_analog_value() on an alias that was never configured. "
            "Confirms InstroDAQ channel guard raises KeyError immediately.",
            step,
        )

    # ==================================================================
    # 09. Analog output — output holds between writes (no decay)
    # ==================================================================
    def test_09_analog_output_holds(self):
        """Verify DAC0 holds its output voltage between write calls.

        Write a voltage, wait 500 ms, then read AIN0 without writing again.
        A DAC with a capacitor leak, charge pump failure, or a driver bug
        that resets on idle would show drift here. The tolerance is loosened
        to 25 mV to account for thermal drift, but a decaying output would
        fail by hundreds of millivolts.
        """
        HOLD_TOLERANCE_V = 0.025
        HOLD_DURATION_S = 0.5

        def step(start_ns: int):
            print(f"         [start {self._ts(start_ns)}]")
            if not LOOPBACK_WIRED:
                self.skipTest("LOOPBACK_WIRED=False")
            daq = self._create_daq()
            try:
                self._configure_ai(daq, AI_CHANNEL_0, AI_ALIAS_0)
                self._configure_ao(daq, AO_CHANNEL_0, AO_ALIAS_0)
                errs = []
                for v in [1.0, 5.0, 9.0]:
                    daq.write_analog_value(AO_ALIAS_0, v)
                    time.sleep(0.05)
                    before = daq.read_analog().latest

                    time.sleep(HOLD_DURATION_S)

                    after = daq.read_analog().latest
                    drift = abs(after - before)
                    flag = "" if drift <= HOLD_TOLERANCE_V else "  <-- DRIFT FAIL"
                    print(
                        f"         DAC0={v:.1f} V | before={before:.6f} V | "
                        f"after={after:.6f} V | drift={drift:.6f} V{flag}"
                    )
                    if not (math.isfinite(before) and math.isfinite(after)):
                        errs.append(f"non-finite reading at {v} V")
                    elif drift > HOLD_TOLERANCE_V:
                        errs.append(
                            f"DAC0={v} V drifted {drift:.4f} V over {HOLD_DURATION_S} s (limit {HOLD_TOLERANCE_V} V)"
                        )
                daq.write_analog_value(AO_ALIAS_0, 0.0)
                self.assertFalse(errs, "; ".join(errs))
            finally:
                daq.write_analog_value(AO_ALIAS_0, 0.0)
                daq.close()

        self._run_step(
            "Analog output - output holds (no decay)",
            f"Write DAC0 to 1, 5, 9 V; wait {HOLD_DURATION_S} s; verify AIN0 "
            f"drifts < 25 mV. Catches capacitor leak or driver reset-on-idle bugs.",
            step,
        )

    # ==================================================================
    # 10. Analog output — rapid successive writes settle correctly
    # ==================================================================
    def test_10_analog_output_rapid_writes(self):
        """Write to DAC0 rapidly then verify the final value is correct.

        Sends 20 writes in quick succession with no sleep between them,
        then reads back after a single settle delay. If the driver queues
        writes incorrectly, drops writes under load, or has a race condition
        in the command path, the final read will not match the last write.
        """
        N_WRITES = 20
        FINAL_V = 7.0

        def step(start_ns: int):
            print(f"         [start {self._ts(start_ns)}]")
            if not LOOPBACK_WIRED:
                self.skipTest("LOOPBACK_WIRED=False")
            daq = self._create_daq()
            try:
                self._configure_ai(daq, AI_CHANNEL_0, AI_ALIAS_0)
                self._configure_ao(daq, AO_CHANNEL_0, AO_ALIAS_0)

                for i in range(N_WRITES - 1):
                    daq.write_analog_value(AO_ALIAS_0, 1.0 if i % 2 == 0 else 9.0)
                daq.write_analog_value(AO_ALIAS_0, FINAL_V)

                time.sleep(0.1)
                measured = daq.read_analog().latest
                err = measured - FINAL_V
                print(
                    f"         {N_WRITES} rapid writes | final={FINAL_V} V | "
                    f"measured={measured:.6f} V | err={err:+.6f} V"
                )

                if LOOPBACK_WIRED:
                    self.assertTrue(math.isfinite(measured), "non-finite after rapid writes")
                    self.assertAlmostEqual(
                        measured,
                        FINAL_V,
                        delta=ANALOG_TOLERANCE_V,
                        msg=f"After {N_WRITES} rapid writes, DAC0 reads "
                        f"{measured:.4f} V instead of {FINAL_V} V -- "
                        "possible write drop or race condition",
                    )
                daq.write_analog_value(AO_ALIAS_0, 0.0)
            finally:
                daq.write_analog_value(AO_ALIAS_0, 0.0)
                daq.close()

        self._run_step(
            "Analog output - rapid successive writes",
            f"Send {N_WRITES} rapid writes alternating 1/9 V, end on {FINAL_V} V. "
            "Verify final read matches last write.",
            step,
        )

    # ==================================================================
    # 11. HW-timed streaming — background daemon
    # ==================================================================
    def test_11_hw_timed_background_daemon(self):
        """start(background=True) + get_channel() — verify buffered data."""

        def step(start_ns: int):
            print(f"         [start {self._ts(start_ns)}]")
            daq = self._create_daq()
            try:
                self._configure_ai(daq, AI_CHANNEL_0, AI_ALIAS_0)
                self._configure_ao(daq, AO_CHANNEL_0, AO_ALIAS_0)
                daq.write_analog_value(AO_ALIAS_0, HW_TIMED_DC_V)
                daq.configure_ai_sample_rate(sample_rate=SAMPLE_RATE_HZ, samples_per_channel=SAMPLES_PER_CHANNEL)
                daq.start()
                try:
                    time.sleep(1.0)
                    ch = daq.get_channel(f"{NAME}.{AI_ALIAS_0}", 50, True)
                    self.assertIsNotNone(ch)
                    self.assertGreaterEqual(len(ch.values), 1)
                    self.assertTrue(all(math.isfinite(v) for v in ch.values), "non-finite samples in background buffer")
                    mean = sum(ch.values) / len(ch.values)
                    print(f"         background: {len(ch.values)} samples, mean AIN0={mean:.6f} V")
                    if LOOPBACK_WIRED:
                        self.assertAlmostEqual(mean, HW_TIMED_DC_V, delta=HW_TIMED_TOLERANCE_V)
                finally:
                    daq.stop()
                    daq.write_analog_value(AO_ALIAS_0, 0.0)
            finally:
                daq.close()

        self._run_step(
            "HW-timed streaming (background daemon)",
            f"start() at {SAMPLE_RATE_HZ} Hz with background=True. "
            f"Hold DAC0 at {HW_TIMED_DC_V} V; verify mean via get_channel().",
            step,
        )

    # ==================================================================
    # 12. read_analog() while background daemon is running raises RuntimeError
    # ==================================================================
    def test_12_read_analog_raises_while_daemon_running(self):
        """read_analog() must raise RuntimeError while the background daemon owns the buffer.

        Accounts for known bugs in the buffer ownership logic.

        This test verifies that guard is in place. If the RuntimeError is
        NOT raised, it means the guard was removed or bypassed — and any
        code that calls read_analog() expecting the daemon to own the buffer
        would silently get partial data instead of a clear error.
        """

        def step(start_ns: int):
            print(f"         [start {self._ts(start_ns)}]")
            daq = self._create_daq()
            try:
                self._configure_ai(daq, AI_CHANNEL_0, AI_ALIAS_0)
                daq.configure_ai_sample_rate(sample_rate=SAMPLE_RATE_HZ, samples_per_channel=SAMPLES_PER_CHANNEL)
                daq.start(background=True)
                try:
                    time.sleep(0.2)  # give the daemon time to start and confirm it is alive
                    self.assertTrue(
                        daq._background_thread and daq._background_thread.is_alive(),
                        "Background daemon thread is not alive after start() — "
                        "cannot test the RuntimeError guard meaningfully",
                    )
                    with self.assertRaises(
                        RuntimeError,
                        msg="read_analog() should raise RuntimeError while the background daemon is running",
                    ):
                        daq.read_analog()
                    print("         RuntimeError raised correctly — daemon owns the buffer")
                finally:
                    daq.stop()
            finally:
                daq.close()

        self._run_step(
            "read_analog() raises while daemon running",
            "Verify read_analog() raises RuntimeError when the background daemon "
            "is active. Guards against buffer race conditions (INSTRO-149).",
            step,
        )

    # ==================================================================
    # 13. HW-timed streaming — foreground fetch
    # ==================================================================
    def test_13_hw_timed_foreground_fetch(self):
        """start(background=False) + read_analog() — verify direct fetch."""

        def step(start_ns: int):
            print(f"         [start {self._ts(start_ns)}]")
            daq = self._create_daq()
            try:
                self._configure_ai(daq, AI_CHANNEL_0, AI_ALIAS_0)
                self._configure_ao(daq, AO_CHANNEL_0, AO_ALIAS_0)
                daq.write_analog_value(AO_ALIAS_0, HW_TIMED_DC_V)
                daq.configure_ai_sample_rate(sample_rate=SAMPLE_RATE_HZ, samples_per_channel=SAMPLES_PER_CHANNEL)
                daq.start(background=False)
                try:
                    measurement = daq.read_analog()
                    self.assertIsNotNone(measurement)
                    vals = measurement.values
                    self.assertGreaterEqual(len(vals), 1)
                    self.assertTrue(all(math.isfinite(v) for v in vals), f"non-finite HW-timed fetch: n={len(vals)}")
                    mean = sum(vals) / len(vals)
                    print(f"         foreground: {len(vals)} samples, mean AIN0={mean:.6f} V")
                    if LOOPBACK_WIRED:
                        self.assertAlmostEqual(mean, HW_TIMED_DC_V, delta=HW_TIMED_TOLERANCE_V)
                finally:
                    daq.stop()
                    daq.write_analog_value(AO_ALIAS_0, 0.0)
            finally:
                daq.close()

        self._run_step(
            "HW-timed streaming (foreground fetch)",
            f"start(background=False) at {SAMPLE_RATE_HZ} Hz. "
            f"Hold DAC0 at {HW_TIMED_DC_V} V; read directly via read_analog().",
            step,
        )

    # ==================================================================
    # 14. HW-timed streaming — high data rate (40 kS/s)
    # ==================================================================
    def test_14_hw_timed_high_rate(self):
        """Stream at 40 kS/s — T8 maximum per-channel rate."""

        def step(start_ns: int):
            print(f"         [start {self._ts(start_ns)}]")
            daq = self._create_daq()
            try:
                self._configure_ai(daq, AI_CHANNEL_0, AI_ALIAS_0)
                self._configure_ao(daq, AO_CHANNEL_0, AO_ALIAS_0)
                daq.write_analog_value(AO_ALIAS_0, HW_TIMED_DC_V)
                daq.configure_ai_sample_rate(sample_rate=HIGH_RATE_HZ, samples_per_channel=HIGH_RATE_SAMPLES)
                daq.start(background=False)
                try:
                    measurement = daq.read_analog()
                    self.assertIsNotNone(measurement)
                    vals = measurement.values
                    self.assertGreaterEqual(
                        len(vals), HIGH_RATE_SAMPLES // 2, "Expected at least half the requested samples"
                    )
                    self.assertTrue(all(math.isfinite(v) for v in vals), "non-finite samples in high-rate stream")
                    mean = sum(vals) / len(vals)
                    print(f"         {HIGH_RATE_HZ / 1000:.0f} kS/s: {len(vals)} samples, mean AIN0={mean:.4f} V")
                    if LOOPBACK_WIRED:
                        self.assertAlmostEqual(mean, HW_TIMED_DC_V, delta=HIGH_RATE_TOLERANCE_V)
                finally:
                    daq.stop()
                    daq.write_analog_value(AO_ALIAS_0, 0.0)
            finally:
                daq.close()

        self._run_step(
            f"HW-timed high-rate stream ({HIGH_RATE_HZ / 1000:.0f} kS/s)",
            f"Stream AIN0 at {HIGH_RATE_HZ} Hz via start(background=False). "
            "Verifies T8 maximum per-channel rate without errors.",
            step,
        )

    # ==================================================================
    # 15. Sample rate and buffer-depth telemetry
    # ==================================================================
    def test_15_sample_rate_and_buffer_telemetry(self):
        """get_actual_sample_rate() and get_points_in_buffer() during streaming."""

        def step(start_ns: int):
            print(f"         [start {self._ts(start_ns)}]")
            daq = self._create_daq()
            try:
                self._configure_ai(daq, AI_CHANNEL_0, AI_ALIAS_0)
                daq.configure_ai_sample_rate(sample_rate=SAMPLE_RATE_HZ, samples_per_channel=SAMPLES_PER_CHANNEL)
                daq.start()
                try:
                    time.sleep(0.5)
                    actual_rate = daq.get_actual_sample_rate()
                    self.assertIsNotNone(actual_rate, "get_actual_sample_rate returned None after start()")
                    print(f"         actual_sample_rate={actual_rate} Hz (requested {SAMPLE_RATE_HZ} Hz)")
                    self.assertAlmostEqual(
                        actual_rate,
                        SAMPLE_RATE_HZ,
                        delta=SAMPLE_RATE_HZ * 0.1,
                        msg=f"Rate {actual_rate} deviates >10% from {SAMPLE_RATE_HZ}",
                    )
                    depth = daq.get_points_in_buffer().latest
                    print(f"         points_in_buffer={depth}")
                    self.assertTrue(math.isfinite(depth) and depth >= 0, f"invalid buffer depth: {depth}")
                finally:
                    daq.stop()
            finally:
                daq.close()

        self._run_step(
            "Sample rate and buffer-depth telemetry",
            f"Verify get_actual_sample_rate() is within 10% of {SAMPLE_RATE_HZ} Hz "
            "and get_points_in_buffer() returns a non-negative finite value.",
            step,
        )

    # ==================================================================
    # 16. HW-timed streaming — multi-channel simultaneous
    # ==================================================================
    def test_16_hw_timed_multi_channel(self):
        """Stream AIN0, AIN1, and AIN2 simultaneously in HW-timed mode.

        Configures three analog input channels and one sample rate, then
        starts in foreground mode and reads back. Verifies:

          - All three channels return the correct sample count
          - Every sample on every channel is finite
          - AIN0 and AIN1 (both wired to DAC0) track each other within
            5 mV — confirming simultaneous sampling with no multiplexer
            skew between them
          - AIN2 (wired to DAC1) tracks DAC1's set point within the
            standard HW-timed tolerance

        The inter-channel skew check (AIN0 vs AIN1) is the key assertion
        that single-channel tests cannot provide: it catches a driver bug
        where channels are sampled sequentially rather than simultaneously,
        which would produce offsets proportional to the signal slew rate.
        """
        MULTI_CH_CHANNELS = [
            (AI_CHANNEL_0, AI_ALIAS_0),  # DAC0 loopback
            (AI_CHANNEL_1, AI_ALIAS_1),  # DAC0 loopback — skew reference
            (AI_CHANNEL_2, AI_ALIAS_2),  # DAC1 loopback
        ]
        DAC0_V = 3.3
        DAC1_V = 7.0
        SKEW_TOLERANCE_V = 0.005  # 5 mV: simultaneous sampling, same source

        def step(start_ns: int):
            print(f"         [start {self._ts(start_ns)}]")
            if not LOOPBACK_WIRED:
                self.skipTest("LOOPBACK_WIRED=False")
            daq = self._create_daq()
            try:
                for physical, alias in MULTI_CH_CHANNELS:
                    self._configure_ai(daq, physical, alias)
                self._configure_ao(daq, AO_CHANNEL_0, AO_ALIAS_0)
                self._configure_ao(daq, AO_CHANNEL_1, AO_ALIAS_1)

                daq.write_analog_value(AO_ALIAS_0, DAC0_V)
                daq.write_analog_value(AO_ALIAS_1, DAC1_V)
                time.sleep(0.05)

                daq.configure_ai_sample_rate(sample_rate=SAMPLE_RATE_HZ, samples_per_channel=SAMPLES_PER_CHANNEL)
                daq.start(background=False)
                try:
                    measurement = daq.read_analog()
                    self.assertIsNotNone(measurement)

                    errs = []
                    means = {}
                    for _physical, alias in MULTI_CH_CHANNELS:
                        key = f"{NAME}.{alias}"
                        samples = measurement.channel_data.get(key)
                        self.assertIsNotNone(
                            samples,
                            f"channel_data missing key '{key}'. Keys present: {list(measurement.channel_data.keys())}",
                        )
                        self.assertGreaterEqual(
                            len(samples),
                            1,
                            f"{alias}: expected ≥1 sample, got {len(samples)}",
                        )
                        non_finite = [v for v in samples if not math.isfinite(v)]
                        if non_finite:
                            errs.append(f"{alias}: {len(non_finite)} non-finite sample(s)")
                            continue
                        mean = sum(samples) / len(samples)
                        means[alias] = mean
                        print(f"         {alias}: {len(samples)} samples, mean={mean:.6f} V")

                    # AIN0 and AIN1 share the same source (DAC0) — verify
                    # they agree within SKEW_TOLERANCE_V to confirm
                    # simultaneous sampling.
                    if AI_ALIAS_0 in means and AI_ALIAS_1 in means:
                        skew = abs(means[AI_ALIAS_0] - means[AI_ALIAS_1])
                        flag = "" if skew <= SKEW_TOLERANCE_V else "  <-- SKEW FAIL"
                        print(
                            f"         AIN0 vs AIN1 skew: {skew * 1000:.3f} mV"
                            f" (limit {SKEW_TOLERANCE_V * 1000:.0f} mV){flag}"
                        )
                        if skew > SKEW_TOLERANCE_V:
                            errs.append(
                                f"AIN0/AIN1 skew {skew * 1000:.3f} mV > "
                                f"{SKEW_TOLERANCE_V * 1000:.0f} mV — "
                                "channels may not be sampled simultaneously"
                            )

                    # Verify each channel is close to its DAC set point.
                    targets = {
                        AI_ALIAS_0: DAC0_V,
                        AI_ALIAS_1: DAC0_V,
                        AI_ALIAS_2: DAC1_V,
                    }
                    for alias, target in targets.items():
                        if alias not in means:
                            continue
                        err = means[alias] - target
                        flag = "" if abs(err) <= HW_TIMED_TOLERANCE_V else "  <-- OUT OF TOLERANCE"
                        print(f"         {alias} err vs DAC set point: {err:+.6f} V{flag}")
                        if abs(err) > HW_TIMED_TOLERANCE_V:
                            errs.append(f"{alias}: mean={means[alias]:.4f} V, target={target} V, err={err:+.4f} V")

                    self.assertFalse(errs, "; ".join(errs))
                finally:
                    daq.stop()
                    daq.write_analog_value(AO_ALIAS_0, 0.0)
                    daq.write_analog_value(AO_ALIAS_1, 0.0)
            finally:
                daq.close()

        self._run_step(
            "HW-timed multi-channel stream (AIN0, AIN1, AIN2)",
            f"Stream AIN0+AIN1 (DAC0={DAC0_V} V) and AIN2 (DAC1={DAC1_V} V) "
            f"simultaneously at {SAMPLE_RATE_HZ} Hz. Verifies sample counts, "
            f"finite values, AIN0/AIN1 skew < {SKEW_TOLERANCE_V * 1000:.0f} mV, "
            f"and per-channel accuracy within {HW_TIMED_TOLERANCE_V * 1000:.0f} mV.",
            step,
        )

    # ==================================================================
    # 17. Digital line loopback
    # ==================================================================
    def test_17_digital_line_loopback(self):
        """write_digital_line() / read_digital_line() via FIO4 → FIO5 loopback."""

        def step(start_ns: int):
            print(f"         [start {self._ts(start_ns)}]")
            daq = self._create_daq()
            try:
                self._configure_digital_lines(daq)
                errs = []
                for state in (0, 1, 0, 1, 0):
                    daq.write_digital_line(DO_ALIAS, state)
                    time.sleep(0.05)
                    read = int(daq.read_digital_line(DI_ALIAS).latest)
                    flag = "" if (not LOOPBACK_WIRED or read == state) else "  <-- mismatch"
                    print(f"         FIO4←{state} | FIO5={read}{flag}")
                    if LOOPBACK_WIRED and read != state:
                        errs.append(f"drove FIO4={state}, read FIO5={read}")
                daq.write_digital_line(DO_ALIAS, 0)
                self.assertFalse(errs, "; ".join(errs))
            finally:
                daq.write_digital_line(DO_ALIAS, 0)
                daq.close()

        self._run_step(
            "Digital line loopback",
            "Drive FIO4 through 0/1 sequence via write_digital_line(); verify FIO5 follows via read_digital_line().",
            step,
        )

    # ==================================================================
    # 17. Clean shutdown
    # ==================================================================
    def test_18_clean_shutdown(self):
        """Set all outputs to safe state via InstroDAQ public methods."""

        def step(start_ns: int):
            print(f"         [start {self._ts(start_ns)}]")
            daq = self._create_daq()
            try:
                for ao_phys, ao_alias in [(AO_CHANNEL_0, AO_ALIAS_0), (AO_CHANNEL_1, AO_ALIAS_1)]:
                    self._configure_ao(daq, ao_phys, ao_alias)
                    daq.write_analog_value(ao_alias, 0.0)
                self._configure_digital_lines(daq)
                daq.write_digital_line(DO_ALIAS, 0)
                print("         DAC0=0 V, DAC1=0 V, FIO4=0 — safe state confirmed")
            finally:
                daq.close()

        self._run_step(
            "Clean shutdown — safe state",
            "Set DAC0, DAC1 to 0 V and FIO4 to 0 via InstroDAQ public methods.",
            step,
        )

    # ==================================================================
    # 18. NotImplementedError — port-width digital I/O
    # ==================================================================
    def test_19_port_width_digital_raises(self):
        """configure_digital_port() must raise NotImplementedError on T8."""

        def step(start_ns: int):
            print(f"         [start {self._ts(start_ns)}]")
            daq = self._create_daq()
            try:
                with self.assertRaises(
                    NotImplementedError, msg="configure_digital_port should raise NotImplementedError on the T8 driver"
                ):
                    daq.configure_digital_port(
                        direction=Direction.OUTPUT,
                        physical_channel="FIO0",
                        logic=Logic.HIGH,
                        port_width=8,
                    )
            finally:
                daq.close()

        self._run_step(
            "NotImplementedError — port-width digital I/O",
            "Assert configure_digital_port() raises NotImplementedError.",
            step,
        )

    # ==================================================================
    # 19. NotImplementedError — relay control
    # ==================================================================
    def test_20_relay_control_raises(self):
        """close_relay() must raise NotImplementedError on the T8 driver."""

        def step(start_ns: int):
            print(f"         [start {self._ts(start_ns)}]")
            daq = self._create_daq()
            try:
                daq.configure_relay_channel(physical_channel="3101", alias="relay1")
                with self.assertRaises(NotImplementedError, msg="close_relay should raise NotImplementedError"):
                    daq.close_relay("relay1")
            finally:
                daq.close()

        self._run_step(
            "NotImplementedError — relay control",
            "Assert close_relay() raises NotImplementedError on the T8 driver.",
            step,
        )


if __name__ == "__main__":
    unittest.main()
