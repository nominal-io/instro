"""Hardware integration test for the Keysight 34980A via InstroDAQ.

This test requires a physical 34980A mainframe reachable over VISA (LAN/USB/GPIB)
with at least one 34922A multiplexer and the optional internal DMM fitted. It
exercises the DAQ functionality the 34980A driver implements: software-timed
analog read (mux channel -> internal DMM), multi-channel scanning, hardware-timed
(timer-triggered) scanning, buffer-depth telemetry, and relay open/close.

Unlike the LabJack T4 there is no onboard analog output, so there is no DAC->AIN
loopback. Analog reads are checked for structure (finite float) always, and for
value only when a known DC source is wired to the input channel (KNOWN_SOURCE_WIRED).

The 34980A driver implements no analog output and (in a mux-only frame) there is
no digital I/O module, so those are reported as skipped. Relay control IS
supported by this driver and is verified against ROUT:CLOS?/ROUT:OPEN?.

============================================================================
34980A WIRING / SETUP
============================================================================

  Required:
    - 34922A 70-channel armature multiplexer in MUX_SLOT
    - Internal DMM fitted (HAS_INTERNAL_DMM = True)
    - VISA connectivity (set RESOURCE)

  Channel configuration summary (MUX_SLOT = 1 shown):
    AI ch "1001" — alias "ai0"  (scanned to internal DMM, DC volts)
    AI ch "1002" — alias "ai1"  (second channel for scan test)
    Relay "1003"               — closed/opened in the relay test

  Optional known-source check:
    Wire a stable DC voltage (within the configured range) to AI_CHANNEL, set
    KNOWN_SOURCE_WIRED = True, and set EXPECTED_VOLTAGE. Leave False for
    structure-only checks.

  Safety: open() issues *RST, which opens all relays. The relay test closes then
  re-opens RELAY_CHANNEL. Point MUX_SLOT/RELAY_CHANNEL at an unused channel if a
  live source or DUT is wired in.

============================================================================
RUNNING
============================================================================

    pytest -m hardware -v -s

"""

import math
import time
import unittest
from types import SimpleNamespace

import pytest

from instro.daq import InstroDAQ
from instro.daq.drivers.keysight_34980a import Keysight34980A  # ADJUST PATH if needed
from instro.daq.types import DigitalPortWidth, Direction, Logic

# ---------------------------------------------------------------------------
# Configuration — edit before running
# ---------------------------------------------------------------------------
RESOURCE = "USB0::2391::1287::MY44001757::0::INSTR"  # <-- set to your 34980A's actual IP
NAME = "ks34980a_validate"

# Slot holding the 34922A and the channels used by the tests.
MUX_SLOT = 4
AI_CHANNEL, AI_ALIAS = f"{MUX_SLOT}060", "ai0"  # 1060 = slot 1, channel 60 (your 1 V source)
AI_CHANNEL_2, AI_ALIAS_2 = f"{MUX_SLOT}002", "ai1"
RELAY_CHANNEL = f"{MUX_SLOT}003"

# Internal DMM is required for any analog measurement.
HAS_INTERNAL_DMM = True

# Strict value check for analog read when a known DC source is wired to AI_CHANNEL.
KNOWN_SOURCE_WIRED = True
EXPECTED_VOLTAGE = 1.0
VOLTAGE_TOLERANCE_V = 0.05

# The 34922A armature mux scans up to ~100 ch/s; keep timer rates modest.
SAMPLE_RATE_HZ = 10.0
SAMPLES_PER_CHANNEL = 5

# Digital I/O needs a digital module (e.g. 34950A). 34950A is in slot 8.
HAS_DIGITAL_MODULE = True
DIGITAL_SLOT = 8
# NOTE: confirm these are valid 34950A digital LINE addresses for your module;
# 001/002 are placeholders. The loopback assert only fires if DO is physically
# jumpered to DI and DIGITAL_LOOPBACK_WIRED is True.
DO_LINE, DO_ALIAS = "8101/0", "do0"  # slot 8, bank 1 ch 101, bit 0  -> output
DI_LINE, DI_ALIAS = "8201/0", "di0"  # slot 8, bank 2 ch 201, bit 0  -> input
DIGITAL_LOOPBACK_WIRED = True

DO_PORT, DO_PORT_ALIAS = f"{DIGITAL_SLOT}101", "do_port"  # bank 1, channel 101
DI_PORT, DI_PORT_ALIAS = f"{DIGITAL_SLOT}201", "di_port"  # bank 2, channel 201


# ---------------------------------------------------------------------------
# Test suite
# ---------------------------------------------------------------------------
@pytest.mark.hardware
class TestKeysight34980AHardware(unittest.TestCase):
    """Hardware integration tests for the Keysight 34980A via InstroDAQ.

    Each test creates, opens, configures, and closes its own DAQ instance, making
    every test independent. A fresh open() also issues *RST, which resets relays
    and clears the scan list between tests.
    """

    # -- helpers ----------------------------------------------------------

    def _create_daq(self) -> InstroDAQ:
        """Create and open a fresh DAQ instance."""
        daq = InstroDAQ(name=NAME, driver=Keysight34980A(RESOURCE))
        daq.open()
        return daq

    def _configure_ai(
        self,
        daq: InstroDAQ,
        physical: str = AI_CHANNEL,
        alias: str = AI_ALIAS,
        range_min: float = -10,
        range_max: float = 10,
    ):
        """Configure a mux input channel measured by the internal DMM (DC volts)."""
        daq.configure_analog_channel(
            direction=Direction.INPUT,
            physical_channel=physical,
            alias=alias,
            range_min=range_min,
            range_max=range_max,
        )

    def _assert_34980a(self, daq: InstroDAQ):
        """Verify the connected device is a 34980A before running checks."""
        idn = daq.driver._visa.query("*IDN?").strip()
        print(f"         *IDN? = {idn}")
        self.assertIn("34980A", idn, f"Connected device is not a 34980A: {idn!r}")

    # =====================================================================
    # 1. Device info and IDN
    # =====================================================================
    def test_01_device_info_and_idn(self):
        """Verify the connected device is a 34980A and record its SCPI version."""
        daq = self._create_daq()
        try:
            self._assert_34980a(daq)
            ver = daq.driver._visa.query("SYST:VERS?").strip()
            print(f"         SCPI version = {ver}")
        finally:
            daq.close()

    # =====================================================================
    # 2. Software-timed analog input
    # =====================================================================
    def test_02_sw_timed_analog_read(self):
        """Read a mux channel through the internal DMM in software-timed mode."""
        if not HAS_INTERNAL_DMM:
            self.skipTest("requires internal DMM")
        daq = self._create_daq()
        try:
            self._configure_ai(daq)
            for _ in range(3):
                measurement = daq.read_analog()
                self.assertIsNotNone(measurement)
                v = measurement.latest
                self.assertTrue(math.isfinite(v), f"non-finite SW-timed read: {v}")
                print(f"         {AI_CHANNEL} (sw-timed) = {v:.6f} V")
                if KNOWN_SOURCE_WIRED:
                    self.assertAlmostEqual(v, EXPECTED_VOLTAGE, delta=VOLTAGE_TOLERANCE_V)
                time.sleep(0.25)
        finally:
            daq.close()

    # =====================================================================
    # 3. Relay close/open — the 34980A's switching capability
    # =====================================================================
    def test_03_relay_close_open_roundtrip(self):
        """Close then open a mux relay and verify state via ROUT:CLOS?/ROUT:OPEN?."""
        daq = self._create_daq()
        relay = SimpleNamespace(physical_channel=RELAY_CHANNEL)
        try:
            daq.driver.close_relay(relay)
            time.sleep(0.05)  # relay settle
            closed = daq.driver._visa.query(f"ROUT:CLOS? (@{RELAY_CHANNEL})").strip()
            print(f"         after close: ROUT:CLOS? (@{RELAY_CHANNEL}) = {closed}")
            self.assertEqual(closed, "1", "relay did not report closed")

            daq.driver.open_relay(relay)
            time.sleep(0.05)
            opened = daq.driver._visa.query(f"ROUT:OPEN? (@{RELAY_CHANNEL})").strip()
            print(f"         after open:  ROUT:OPEN? (@{RELAY_CHANNEL}) = {opened}")
            self.assertEqual(opened, "1", "relay did not report open")
        finally:
            try:
                daq.driver.open_relay(relay)  # leave channel open
            except Exception:
                pass
            daq.close()

    # =====================================================================
    # 4. Multi-channel scan
    # =====================================================================
    def test_04_multi_channel_scan(self):
        """Configure two mux channels and scan both to the internal DMM."""
        if not HAS_INTERNAL_DMM:
            self.skipTest("requires internal DMM")
        daq = self._create_daq()
        try:
            self._configure_ai(daq, physical=AI_CHANNEL, alias=AI_ALIAS)
            self._configure_ai(daq, physical=AI_CHANNEL_2, alias=AI_ALIAS_2)
            measurement = daq.read_analog()
            self.assertIsNotNone(measurement)
            daq.driver._check_errors()  # scan of both channels left no error queued
            print(f"         scanned channels {AI_CHANNEL}, {AI_CHANNEL_2} with no SCPI error")
        finally:
            daq.close()

    # =====================================================================
    # 5. Hardware-timed (timer-triggered) scan
    # =====================================================================
    def test_05_hw_timed_scan(self):
        """Start a timer-triggered scan and fetch buffered readings."""
        if not HAS_INTERNAL_DMM:
            self.skipTest("requires internal DMM")
        daq = self._create_daq()
        try:
            self._configure_ai(daq)
            daq.configure_ai_sample_rate(
                sample_rate=SAMPLE_RATE_HZ,
                samples_per_channel=SAMPLES_PER_CHANNEL,
            )
            daq.start(background=False)
            try:
                # No background daemon: read_analog() dispatches to the driver's fetch_analog().
                measurement = daq.read_analog()
                self.assertIsNotNone(measurement)
                vals = measurement.values
                self.assertGreaterEqual(len(vals), SAMPLES_PER_CHANNEL)
                self.assertTrue(all(math.isfinite(v) for v in vals), f"non-finite HW-timed fetch: n={len(vals)}")
                print(f"         fetched {len(vals)} samples at {SAMPLE_RATE_HZ} Hz")
            finally:
                daq.stop()
        finally:
            daq.close()

    # =====================================================================
    # 6. Buffer-depth telemetry (background acquisition)
    # =====================================================================
    def test_06_buffer_depth_telemetry(self):
        """Verify get_points_in_buffer reports a valid depth during background acquisition."""
        if not HAS_INTERNAL_DMM:
            self.skipTest("requires internal DMM")
        daq = self._create_daq()
        try:
            self._configure_ai(daq)
            daq.configure_ai_sample_rate(
                sample_rate=SAMPLE_RATE_HZ,
                samples_per_channel=SAMPLES_PER_CHANNEL,
            )
            daq.start()
            try:
                time.sleep(1.0)  # let the buffer accumulate
                depth = daq.get_points_in_buffer().latest
                print(f"         points_in_buffer telemetry = {depth}")
                self.assertTrue(math.isfinite(depth) and depth >= 0, f"invalid buffer depth: {depth}")
            finally:
                daq.stop()
        finally:
            daq.close()

    # =====================================================================
    # 7. Digital line write/read loopback (only with a digital module)
    # =====================================================================
    def test_07_digital_line_loopback(self):
        """Drive a DO line and read it back on a DI line via loopback (needs 34950A-class module)."""
        if not HAS_DIGITAL_MODULE:
            self.skipTest("no digital I/O module (e.g. 34950A) in this mainframe configuration")
        daq = self._create_daq()
        try:
            daq.configure_digital_line(
                direction=Direction.OUTPUT, physical_channel=DO_LINE, logic=Logic.HIGH, alias=DO_ALIAS
            )
            daq.configure_digital_line(
                direction=Direction.INPUT, physical_channel=DI_LINE, logic=Logic.HIGH, alias=DI_ALIAS
            )
            errs = []
            for state in (0, 1, 0, 1, 0):
                daq.write_digital_line(DO_ALIAS, state)
                time.sleep(0.05)
                read = int(daq.read_digital_line(DI_ALIAS).latest)
                flag = "" if (not DIGITAL_LOOPBACK_WIRED or read == state) else "  <-- mismatch"
                print(f"         {DO_LINE}<-{state} | {DI_LINE}={read}{flag}")
                if DIGITAL_LOOPBACK_WIRED and read != state:
                    errs.append(f"drove {DO_LINE}={state}, read {DI_LINE}={read}")
            daq.write_digital_line(DO_ALIAS, 0)
            self.assertFalse(errs, "; ".join(errs))
        finally:
            daq.close()

    # =====================================================================
    # 8. Final error sweep
    # =====================================================================
    def test_08_error_queue_clean(self):
        """After a mix of operations, the SCPI error queue should be empty."""
        daq = self._create_daq()
        try:
            if HAS_INTERNAL_DMM:
                self._configure_ai(daq)
                daq.read_analog()
            relay = SimpleNamespace(physical_channel=RELAY_CHANNEL)
            daq.driver.close_relay(relay)
            daq.driver.open_relay(relay)
            daq.driver._check_errors()  # raises RuntimeError if anything is queued
            print("         SCPI error queue clean after analog + relay ops")
        finally:
            daq.close()

    # =====================================================================
    # 9. Analog output is unsupported — verify it is rejected, not silent
    # =====================================================================
    def test_09_analog_output_unsupported(self):
        """The 34980A driver implements no analog output (no 34951A/34952A DAC).

        This is a negative test: configuring an analog OUTPUT channel must raise
        rather than silently succeed, confirming the unsupported path fails loudly.
        """
        daq = self._create_daq()
        try:
            with self.assertRaises((NotImplementedError, AttributeError, RuntimeError, ValueError, TypeError)):
                daq.configure_analog_channel(
                    direction=Direction.OUTPUT,
                    physical_channel=AI_CHANNEL,
                    alias="ao0",
                    range_min=-10,
                    range_max=10,
                )
            print("         analog output correctly rejected (no AO path on 34980A)")
        finally:
            daq.close()

    # =====================================================================
    # 10. Digital PORT (byte-wide) write/read on the 34950A
    # =====================================================================
    def test_10_digital_port_write_read(self):
        """Write a byte to an output port and read an input port back (34950A, 8-bit).

        Writes whole bytes rather than single bits. Only bit 0 is physically
        jumpered (bank1 bit0 -> bank2 bit0), so when DIGITAL_LOOPBACK_WIRED is
        True the assertion checks bit 0 of the readback; the other 7 input bits
        are unconnected and ignored. With it False this is a structural check
        that the byte write/read path runs without a SCPI error.
        """
        if not HAS_DIGITAL_MODULE:
            self.skipTest("no digital I/O module (e.g. 34950A) in this mainframe configuration")
        daq = self._create_daq()
        try:
            daq.configure_digital_port(
                direction=Direction.OUTPUT,
                physical_channel=DO_PORT,
                alias=DO_PORT_ALIAS,
                port_width=DigitalPortWidth.WIDTH_8,
                logic=Logic.HIGH,
            )
            daq.configure_digital_port(
                direction=Direction.INPUT,
                physical_channel=DI_PORT,
                alias=DI_PORT_ALIAS,
                port_width=DigitalPortWidth.WIDTH_8,
                logic=Logic.HIGH,
            )
            errs = []
            for value in (0x00, 0x01, 0xFF, 0xAA, 0x00):
                daq.write_digital_port(DO_PORT_ALIAS, value)
                time.sleep(0.05)
                read = int(daq.read_digital_port(DI_PORT_ALIAS).latest)
                bit0_ok = (read & 0x01) == (value & 0x01)
                flag = "" if (not DIGITAL_LOOPBACK_WIRED or bit0_ok) else "  <-- bit0 mismatch"
                print(f"         {DO_PORT}<-0x{value:02X} | {DI_PORT}=0x{read:02X}{flag}")
                if DIGITAL_LOOPBACK_WIRED and not bit0_ok:
                    errs.append(f"bit0: wrote {value & 1}, read {read & 1} (full byte 0x{read:02X})")
            daq.write_digital_port(DO_PORT_ALIAS, 0x00)
            self.assertFalse(errs, "; ".join(errs))
        finally:
            daq.close()


if __name__ == "__main__":
    unittest.main()
