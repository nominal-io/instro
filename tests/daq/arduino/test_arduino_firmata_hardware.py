"""Hardware integration test for the Arduino Firmata DAQ driver via InstroDAQ.

This test requires a physical Arduino connected via USB running StandardFirmata.
It exercises the ArduinoFirmata driver: analog input via background daemon,
analog output (PWM), digital line read/write, sampling-rate change, and
buffer-depth telemetry. Each test step is optionally recorded as an event on
a Nominal Core asset.

============================================================================
WIRING
============================================================================

  No loopback is required for the basic tests. For loopback tests, wire:

    Analog loopback  (D10 PWM out → A0 in):
      D10 (PWM output, 0-5 V)  --->  A0 (analog input, 0-5 V)
      Note: PWM is a square wave; the read value depends on sampling timing.
      The loopback test only checks that A0 reads a finite value in [0, 5] V
      — it does not assert a tight voltage match.

    Digital loopback (D2 out → D3 in):
      D2 (digital output)  --->  D3 (digital input)

  Set LOOPBACK_WIRED = True once the wires are in place.

============================================================================
NOMINAL CORE CONFIGURATION
============================================================================

  Before running, configure:

    PORT            — serial port of the Arduino (e.g. /dev/tty.usbmodem1101)
    DATASET_RID     — dataset RID for the NominalCorePublisher (optional;
                      leave None to publish nowhere)

  Nominal event recording is skipped automatically when no auth profile is
  found on disk (so the test suite still runs without Nominal credentials).

============================================================================
RUNNING
============================================================================

    pytest tests/daq/arduino/test_arduino_firmata_hardware.py -m hardware -v -s

"""

import math
import time
import unittest
from datetime import timedelta

import pytest
from nominal.core import EventType, NominalClient

from instro.daq import InstroDAQ
from instro.daq.drivers.arduino_firmata import ArduinoFirmata
from instro.daq.types import Direction, Logic
from instro.lib.publishers import NominalCorePublisher

# ---------------------------------------------------------------------------
# Configuration — edit before running
# ---------------------------------------------------------------------------
PORT = "/dev/tty.usbmodem1101"
NAME = "arduino_validate"

# Set to a Nominal dataset RID to stream validation data via NominalCorePublisher;
# leave None to publish nowhere.
DATASET_RID = None

# Analog channel mapping
AI_CHANNEL_0, AI_ALIAS_0 = "A0", "voltage"
AI_CHANNEL_1, AI_ALIAS_1 = "A1", "voltage2"
AO_CHANNEL, AO_ALIAS = "D10", "pwm_out"

# Digital channel mapping
DO_LINE, DO_ALIAS = "D2", "do_line"
DI_LINE, DI_ALIAS = "D3", "di_line"

# Set True once D10→A0 and D2→D3 are physically looped back.
LOOPBACK_WIRED = True

SAMPLE_RATE_HZ = 100.0
PWM_TEST_VOLTAGES = [0.0, 1.0, 2.5, 4.0, 5.0]


# ---------------------------------------------------------------------------
# Nominal Core event helpers
# ---------------------------------------------------------------------------


class _EventRecorder:
    """Collects test events and uploads them to a Nominal asset on finish.

    Gracefully disabled when no Nominal auth profile is found on disk.
    """

    def __init__(self) -> None:
        self._client: NominalClient | None = None
        self._events: list[dict] = []
        self._enabled = False

    def begin(self) -> None:
        try:
            self._client = NominalClient.from_profile("default")
            self._enabled = True
        except Exception as exc:
            print(f"\nNominal Core unavailable; event recording disabled: {exc}")

    def record_event(
        self,
        name: str,
        start_ns: int,
        end_ns: int,
        passed: bool,
        description: str = "",
    ) -> None:
        if not self._enabled:
            return
        self._events.append(
            {
                "name": name,
                "start_ns": start_ns,
                "end_ns": end_ns,
                "passed": passed,
                "description": description,
            }
        )

    def finish(self) -> None:
        if not self._enabled or not self._events:
            return
        assert self._client is not None
        asset = self._client.get_or_create_asset_by_properties(
            properties={"device_type": "Arduino", "purpose": "hardware-test"},
            name="Arduino",
            description="Arduino DAQ device under test",
            labels=["arduino", "firmata", "hardware-test"],
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
                labels=["arduino-firmata-test"],
            )


_recorder = _EventRecorder()


# ---------------------------------------------------------------------------
# Test suite
# ---------------------------------------------------------------------------
@pytest.mark.hardware
class TestArduinoFirmataHardware(unittest.TestCase):
    """Hardware integration tests for the Arduino Firmata driver via InstroDAQ.

    Each test creates, opens, configures, and closes its own DAQ instance so
    tests are independent.
    """

    @classmethod
    def setUpClass(cls) -> None:
        _recorder.begin()

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            _recorder.finish()
        except Exception as exc:
            print(f"\n*** Failed to create Nominal events: {exc} ***")
            raise

    # -- helpers ----------------------------------------------------------

    def _create_daq(self) -> InstroDAQ:
        daq = InstroDAQ(
            name=NAME,
            driver=ArduinoFirmata(port=PORT, sampling_rate_hz=SAMPLE_RATE_HZ),
        )
        if DATASET_RID:
            daq.add_publisher(NominalCorePublisher(dataset_rid=DATASET_RID))
        daq.open()
        return daq

    def _configure_ai(self, daq: InstroDAQ) -> None:
        daq.configure_analog_channel(
            direction=Direction.INPUT,
            physical_channel=AI_CHANNEL_0,
            alias=AI_ALIAS_0,
            range_min=0.0,
            range_max=5.0,
        )
        daq.configure_analog_channel(
            direction=Direction.INPUT,
            physical_channel=AI_CHANNEL_1,
            alias=AI_ALIAS_1,
            range_min=0.0,
            range_max=5.0,
        )

    def _configure_ao(self, daq: InstroDAQ) -> None:
        daq.configure_analog_channel(
            direction=Direction.OUTPUT,
            physical_channel=AO_CHANNEL,
            alias=AO_ALIAS,
            range_min=0.0,
            range_max=5.0,
        )

    def _configure_digital_lines(self, daq: InstroDAQ) -> None:
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

    def _run_step(self, name: str, description: str, fn) -> None:
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
    # 1. Lifecycle
    # =====================================================================
    def test_01_lifecycle(self) -> None:
        """Open and close the Arduino connection."""

        def step() -> None:
            daq = self._create_daq()
            daq.close()

        self._run_step(
            "Lifecycle",
            "Open the Arduino via Firmata and close it cleanly.",
            step,
        )

    # =====================================================================
    # 2. Analog input — background daemon
    # =====================================================================
    def test_02_analog_input_background(self) -> None:
        """Read A0 and A1 via the background daemon."""

        def step() -> None:
            daq = self._create_daq()
            try:
                self._configure_ai(daq)
                daq.start()
                try:
                    time.sleep(0.5)
                    ch0 = daq.get_channel(f"{NAME}.{AI_ALIAS_0}", 1, wait_for_latest=True)
                    ch1 = daq.get_channel(f"{NAME}.{AI_ALIAS_1}", 1, wait_for_latest=False)
                    self.assertIsNotNone(ch0)
                    self.assertIsNotNone(ch1)
                    self.assertTrue(math.isfinite(ch0.latest), f"non-finite A0 read: {ch0.latest}")
                    self.assertTrue(math.isfinite(ch1.latest), f"non-finite A1 read: {ch1.latest}")
                    self.assertGreaterEqual(ch0.latest, 0.0)
                    self.assertLessEqual(ch0.latest, 5.0)
                    print(f"         A0={ch0.latest:.3f} V  A1={ch1.latest:.3f} V")
                finally:
                    daq.stop()
            finally:
                daq.close()

        self._run_step(
            "Analog input (background)",
            "Configure A0 and A1, start the background daemon, and verify finite readings arrive.",
            step,
        )

    # =====================================================================
    # 3. Analog output — PWM write
    # =====================================================================
    def test_03_analog_output_pwm(self) -> None:
        """Write a sweep of voltages to D10 (PWM)."""

        def step() -> None:
            daq = self._create_daq()
            try:
                self._configure_ao(daq)
                for v in PWM_TEST_VOLTAGES:
                    daq.write_analog_value(AO_ALIAS, v)
                    time.sleep(0.2)
                daq.write_analog_value(AO_ALIAS, 0.0)
            finally:
                daq.close()

        self._run_step(
            "Analog output (PWM)",
            f"Configure D10 as PWM output and write a sweep: {PWM_TEST_VOLTAGES} V.",
            step,
        )

    # =====================================================================
    # 4. Analog loopback — D10 PWM → A0
    # =====================================================================
    def test_04_analog_loopback(self) -> None:
        """Write D10 PWM and verify A0 reads a finite value in range (structural check only)."""

        def step() -> None:
            daq = self._create_daq()
            try:
                self._configure_ai(daq)
                self._configure_ao(daq)
                daq.write_analog_value(AO_ALIAS, 2.5)
                daq.start()
                try:
                    time.sleep(0.5)
                    ch0 = daq.get_channel(f"{NAME}.{AI_ALIAS_0}", 1, wait_for_latest=True)
                    self.assertTrue(math.isfinite(ch0.latest), f"non-finite loopback read: {ch0.latest}")
                    self.assertGreaterEqual(ch0.latest, 0.0)
                    self.assertLessEqual(ch0.latest, 5.0)
                    print(f"         D10 PWM (2.5 V duty) -> A0={ch0.latest:.3f} V")
                    if not LOOPBACK_WIRED:
                        self.skipTest("LOOPBACK_WIRED=False; structural check only")
                finally:
                    daq.stop()
                    daq.write_analog_value(AO_ALIAS, 0.0)
            finally:
                daq.close()

        self._run_step(
            "Analog loopback",
            "Write 2.5 V (50% duty) to D10, verify A0 reads a finite value in [0, 5] V. "
            "No tight tolerance: PWM loopback without filtering returns an instantaneous sample.",
            step,
        )

    # =====================================================================
    # 5. Digital line loopback — D2 out → D3 in
    # =====================================================================
    def test_05_digital_line_loopback(self) -> None:
        """Drive D2 and verify D3 reads back the same state."""

        def step() -> None:
            daq = self._create_daq()
            try:
                self._configure_digital_lines(daq)
                errs = []
                for state in (0, 1, 0, 1, 0):
                    daq.write_digital_line(DO_ALIAS, state)
                    time.sleep(0.05)
                    read = int(daq.read_digital_line(DI_ALIAS).latest)
                    flag = "" if (not LOOPBACK_WIRED or read == state) else "  <-- mismatch"
                    print(f"         D2<-{state} | D3={read}{flag}")
                    if LOOPBACK_WIRED and read != state:
                        errs.append(f"drove D2={state}, read D3={read}")
                daq.write_digital_line(DO_ALIAS, 0)
                self.assertFalse(errs, "; ".join(errs))
            finally:
                daq.write_digital_line(DO_ALIAS, 0)
                daq.close()

        self._run_step(
            "Digital line loopback",
            "Drive D2 through a 0/1 sequence and verify D3 reads back the same state "
            "(requires D2→D3 loopback wire when LOOPBACK_WIRED=True).",
            step,
        )

    # =====================================================================
    # 6. Sampling rate change
    # =====================================================================
    def test_06_sampling_rate_change(self) -> None:
        """Change the sampling rate mid-run and verify data still arrives."""

        def step() -> None:
            daq = self._create_daq()
            try:
                self._configure_ai(daq)
                daq.start()
                try:
                    time.sleep(0.3)
                    ch = daq.get_channel(f"{NAME}.{AI_ALIAS_0}", 1, wait_for_latest=True)
                    self.assertTrue(math.isfinite(ch.latest))
                    print(f"         before rate change: A0={ch.latest:.3f} V")

                    daq.driver.set_sampling_rate(50.0)
                    time.sleep(0.5)

                    ch = daq.get_channel(f"{NAME}.{AI_ALIAS_0}", 1, wait_for_latest=True)
                    self.assertTrue(math.isfinite(ch.latest))
                    print(f"         after rate change to 50 Hz: A0={ch.latest:.3f} V")
                finally:
                    daq.stop()
            finally:
                daq.close()

        self._run_step(
            "Sampling rate change",
            f"Start at {SAMPLE_RATE_HZ} Hz, change to 50 Hz via set_sampling_rate(), and verify A0 data still arrives.",
            step,
        )

    # =====================================================================
    # 7. Buffer-depth telemetry
    # =====================================================================
    def test_07_buffer_depth_telemetry(self) -> None:
        """Verify get_points_in_buffer reports a valid depth during acquisition."""

        def step() -> None:
            daq = self._create_daq()
            try:
                self._configure_ai(daq)
                daq.start()
                try:
                    time.sleep(0.5)
                    depth = daq.get_points_in_buffer().latest
                    print(f"         points_in_buffer = {depth}")
                    self.assertTrue(math.isfinite(depth) and depth >= 0, f"invalid buffer depth: {depth}")
                finally:
                    daq.stop()
            finally:
                daq.close()

        self._run_step(
            "Buffer-depth telemetry",
            "Run the background daemon and verify get_points_in_buffer() reports a finite, non-negative value.",
            step,
        )

    # =====================================================================
    # 8. Clean shutdown — outputs to safe state
    # =====================================================================
    def test_08_clean_shutdown(self) -> None:
        """Set all outputs to safe state."""

        def step() -> None:
            daq = self._create_daq()
            try:
                self._configure_ao(daq)
                self._configure_digital_lines(daq)
                daq.write_analog_value(AO_ALIAS, 0.0)
                daq.write_digital_line(DO_ALIAS, 0)
            finally:
                daq.close()

        self._run_step(
            "Clean shutdown — safe state",
            "Set D10 to 0 V (PWM off) and D2 to 0 as a final safety step.",
            step,
        )

    # =====================================================================
    # 9. Methods not implemented — reported as skipped
    # =====================================================================
    def test_09_hw_timing_unsupported(self) -> None:
        """configure_ai_hw_timing raises NotImplementedError on ArduinoFirmata."""
        self.skipTest("ArduinoFirmata has no hardware-timed buffered acquisition")

    def test_10_port_width_digital_unsupported(self) -> None:
        """write_digital_port / read_digital_port raise NotImplementedError."""
        self.skipTest("ArduinoFirmata does not support port-mode digital I/O")


if __name__ == "__main__":
    unittest.main()
