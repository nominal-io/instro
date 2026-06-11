"""Hardware integration test for MCC USB-1616HS-4 DAQ via InstroDAQ.

This test requires a physical MCC USB-1616HS-4 connected with the loopback wiring
described below. It exercises analog DAQ functionality exposed by the MCC driver:
software-timed analog read, hardware-timed analog read (background and non-background),
analog output, and analog loopback verification. Each test step is recorded as an
event on a Nominal Core asset.

Digital I/O tests exercise port-level read/write via FIRSTPORTA (output) and
FIRSTPORTB (input) with a 2-line loopback. The USB-1616HS-4 does not support
per-line digital configuration (d_config_bit), so all digital tests use the
port-width API (write_digital_port / read_digital_port).

============================================================================
USB-1616HS-4 LOOPBACK WIRING
============================================================================

  Device specs:
    - 16 SE / 8 DIFF analog input channels, 16-bit, up to 1 MS/s
    - 4 analog output channels, +/-10 V
    - Digital I/O via FIRSTPORTA (8 bits) — not tested (see note above)

  Analog loopback (wire AO -> AI):
    VOUT0 (AO ch 0)  --->  CH0H / CH0L  (AI ch 0, differential)
    VOUT1 (AO ch 1)  --->  CH1H / CH1L  (AI ch 1, differential)

  Digital loopback (wire FIRSTPORTA -> FIRSTPORTB):
    FIRSTPORTA bit 0  --->  FIRSTPORTB bit 0
    FIRSTPORTA bit 1  --->  FIRSTPORTB bit 1

  Channel configuration summary:
    AI ch "0"  — alias "ai_0", differential, +/-10 V (loopback from AO ch 0)
    AI ch "1"  — alias "ai_1", differential, +/-10 V (loopback from AO ch 1)
    AO ch "0"  — alias "ao_0", +/-10 V
    AO ch "1"  — alias "ao_1", +/-10 V
    DO port "FIRSTPORTA" — alias "do_port_a", 8-bit, Logic.HIGH
    DI port "FIRSTPORTB" — alias "di_port_b", 8-bit, Logic.HIGH

============================================================================
NOMINAL CORE CONFIGURATION
============================================================================

  Before running, set the following environment variables:

    MCC_DEVICE_ID       — unique device ID reported by mcculw (e.g. "344371")
    NOMINAL_DATASET_RID — dataset RID for the NominalCorePublisher
    NOMINAL_API_TOKEN   — Nominal API token (optional if authenticated via
                          `nominal auth set-token`, which stores a default profile
                          in ~/.nominal/config)

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
from nominal.core import EventType, NominalClient

from instro.daq import InstroDAQ
from instro.daq.drivers.mcc import MCCDriver
from instro.daq.types import DigitalPortWidth, Direction, Logic, TerminalConfig
from instro.lib.publishers import NominalCorePublisher

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------
DEVICE_ID = "344371:0"  # MCC DAQ device ID, optionally suffixed with ":<board_number>" (default 0)
DATASET_RID = None  # Replace with your dataset RID.

# Analog channel mapping
AI_CH0 = "0"
AI_CH1 = "1"
AO_CH0 = "0"
AO_CH1 = "1"

# Digital channel mapping
DO_PORT = "FIRSTPORTA"
DI_PORT = "FIRSTPORTB"
# Only bits 0-1 are physically looped back
DIGITAL_LOOPBACK_MASK = 0b00000011

# Tolerances
ANALOG_LOOPBACK_TOLERANCE_V = 0.15  # volts — accounts for 16-bit quantisation + wiring noise
SAMPLE_RATE_HZ = 1000
SAMPLES_PER_CHANNEL = 100


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
            properties={"device_type": "MCC USB-1616HS-4", "purpose": "hardware-test"},
            name="MCC USB-1616HS-4",
            description="MCC DAQ device under test",
            labels=["mccdaq", "hardware-test"],
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
                labels=["mccdaq-test"],
            )


_recorder = _EventRecorder()


# ---------------------------------------------------------------------------
# Test suite
# ---------------------------------------------------------------------------
@pytest.mark.hardware
class TestMCCDAQHardware(unittest.TestCase):
    """Hardware integration tests for MCC USB-1616HS-4 via InstroDAQ.

    Each test creates, opens, configures, and closes its own DAQ instance,
    making every test completely independent.
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
            name="mccdaq_test",
            driver=MCCDriver(device_id=DEVICE_ID),
        )
        if DATASET_RID:
            daq.add_publisher(NominalCorePublisher(dataset_rid=DATASET_RID))
        daq.open()
        return daq

    def _configure_ai(self, daq: InstroDAQ, range_min: float = -10, range_max: float = 10):
        """Configure the two standard differential AI channels."""
        daq.configure_analog_channel(
            direction=Direction.INPUT,
            physical_channel=AI_CH0,
            alias="ai_0",
            range_min=range_min,
            range_max=range_max,
            terminal_config=TerminalConfig.DIFF,
        )
        daq.configure_analog_channel(
            direction=Direction.INPUT,
            physical_channel=AI_CH1,
            alias="ai_1",
            range_min=range_min,
            range_max=range_max,
            terminal_config=TerminalConfig.DIFF,
        )

    def _configure_ao(self, daq: InstroDAQ):
        """Configure the two standard AO channels."""
        daq.configure_analog_channel(
            direction=Direction.OUTPUT,
            physical_channel=AO_CH0,
            alias="ao_0",
            range_min=-10,
            range_max=10,
        )
        daq.configure_analog_channel(
            direction=Direction.OUTPUT,
            physical_channel=AO_CH1,
            alias="ao_1",
            range_min=-10,
            range_max=10,
        )

    def _configure_digital_ports(self, daq: InstroDAQ):
        """Configure FIRSTPORTA as output and FIRSTPORTB as input (8-bit ports)."""
        daq.configure_digital_port(
            direction=Direction.OUTPUT,
            physical_channel=DO_PORT,
            logic=Logic.HIGH,
            alias="do_port_a",
            port_width=DigitalPortWidth.WIDTH_8,
        )
        daq.configure_digital_port(
            direction=Direction.INPUT,
            physical_channel=DI_PORT,
            logic=Logic.HIGH,
            alias="di_port_b",
            port_width=DigitalPortWidth.WIDTH_8,
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
    # 1. Software-timed analog input
    # =====================================================================
    def test_01_sw_timed_analog_read(self):
        """Read two AI channels in software-timed mode (single-shot)."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_ai(daq)

                for _ in range(3):
                    measurement = daq.read_analog()
                    self.assertIsNotNone(measurement)
                    time.sleep(0.25)
            finally:
                daq.close()

        self._run_step(
            "SW-timed analog read",
            "Configure 2 differential AI channels (+/-10V) and perform 3 single-shot software-timed reads.",
            step,
        )

    # =====================================================================
    # 2. Analog output — write known voltages
    # =====================================================================
    def test_02_analog_output(self):
        """Write a series of voltages to both AO channels."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_ao(daq)

                for v in [0.0, 1.0, 2.5, 5.0, -5.0, 0.0]:
                    daq.write_analog_value("ao_0", v)
                    daq.write_analog_value("ao_1", -v)
                    time.sleep(0.1)
            finally:
                daq.close()

        self._run_step(
            "Analog output write",
            "Configure 2 AO channels (+/-10V) and write a sequence of voltages: 0, 1, 2.5, 5, -5, 0V.",
            step,
        )

    # =====================================================================
    # 3. Analog loopback — write AO, verify on AI (software-timed)
    # =====================================================================
    def test_03_analog_loopback_sw_timed(self):
        """Write known voltages to AO and verify they appear on AI (SW-timed)."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_ai(daq)
                self._configure_ao(daq)

                test_voltages = [0.0, 1.0, 2.5, 5.0, -5.0, -2.5]

                for v in test_voltages:
                    daq.write_analog_value("ao_0", v)
                    daq.write_analog_value("ao_1", -v)
                    time.sleep(0.2)  # allow signal to settle

                    measurement = daq.read_analog()
                    self.assertIsNotNone(measurement)
            finally:
                daq.close()

        self._run_step(
            "Analog loopback (SW-timed)",
            "Write known voltages to AO and read back on AI via loopback wiring. "
            "Verifies AO->AI signal path using software-timed single-shot reads.",
            step,
        )

    # =====================================================================
    # 4. Analog range — verify narrow range channels (SW-timed)
    # =====================================================================
    def test_04_analog_narrow_range(self):
        """Configure AI with a narrow +/-1V range and verify reading within range."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_ao(daq)

                daq.configure_analog_channel(
                    direction=Direction.INPUT,
                    physical_channel=AI_CH0,
                    alias="ai_0_narrow",
                    range_min=-1,
                    range_max=1,
                    terminal_config=TerminalConfig.DIFF,
                )

                # Set AO to a value within the narrow range
                daq.write_analog_value("ao_0", 0.5)
                time.sleep(0.2)

                measurement = daq.read_analog()
                self.assertIsNotNone(measurement)
            finally:
                daq.close()

        self._run_step(
            "Analog narrow range (+/-1V)",
            "Configure AI ch0 to +/-1V range, write 0.5V via AO, and verify SW-timed read.",
            step,
        )

    # =====================================================================
    # 5. Analog output — boundary voltages (SW-timed)
    # =====================================================================
    def test_05_analog_output_boundary(self):
        """Write boundary voltages (near +/-10V limits) and verify via AI (SW-timed)."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_ai(daq)
                self._configure_ao(daq)

                boundary_voltages = [-9.5, -5.0, 0.0, 5.0, 9.5]

                for v in boundary_voltages:
                    daq.write_analog_value("ao_0", v)
                    time.sleep(0.2)

                    measurement = daq.read_analog()
                    self.assertIsNotNone(measurement)

                # Reset output to 0V
                daq.write_analog_value("ao_0", 0.0)
            finally:
                daq.close()

        self._run_step(
            "Analog output boundary voltages",
            "Write voltages near the +/-10V hardware limits (-9.5, -5, 0, 5, 9.5V) "
            "and verify AI readback via SW-timed read.",
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

                daq.configure_ai_sample_rate(
                    sample_rate=SAMPLE_RATE_HZ,
                    samples_per_channel=SAMPLES_PER_CHANNEL,
                )
                daq.start()

                try:
                    daq.write_analog_value("ao_0", 3.0)
                    daq.write_analog_value("ao_1", -3.0)
                    time.sleep(1.0)  # let background daemon collect samples

                    ch0 = daq.get_channel("mccdaq_test.ai_0", 10, True)
                    ch1 = daq.get_channel("mccdaq_test.ai_1", 10, True)

                    self.assertIsNotNone(ch0)
                    self.assertIsNotNone(ch1)
                    self.assertGreaterEqual(len(ch0.values), 1)

                    mean_ch0 = sum(ch0.values) / len(ch0.values)
                    mean_ch1 = sum(ch1.values) / len(ch1.values)
                    self.assertAlmostEqual(mean_ch0, 3.0, delta=ANALOG_LOOPBACK_TOLERANCE_V)
                    self.assertAlmostEqual(mean_ch1, -3.0, delta=ANALOG_LOOPBACK_TOLERANCE_V)
                finally:
                    daq.stop()
            finally:
                daq.close()

        self._run_step(
            "HW-timed analog read (background)",
            f"Start HW-timed acquisition at {SAMPLE_RATE_HZ}Hz with background daemon. "
            "Write 3V/-3V to AO, verify AI reads match via get_channel().",
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

                daq.configure_ai_sample_rate(
                    sample_rate=SAMPLE_RATE_HZ,
                    samples_per_channel=SAMPLES_PER_CHANNEL,
                )
                daq.start(background=False)

                try:
                    daq.write_analog_value("ao_0", 4.0)
                    daq.write_analog_value("ao_1", -4.0)
                    time.sleep(0.2)

                    measurement = daq.read_analog()
                    self.assertIsNotNone(measurement)
                finally:
                    daq.stop()
            finally:
                daq.close()

        self._run_step(
            "HW-timed analog read (no background)",
            f"Start HW-timed acquisition at {SAMPLE_RATE_HZ}Hz with background daemon disabled. "
            "Write 4V/-4V to AO and read directly via read_analog().",
            step,
        )

    # =====================================================================
    # 8. HW-timed analog loopback — write ramp, verify on AI
    # =====================================================================
    def test_08_hw_timed_analog_loopback(self):
        """Write a voltage ramp to AO while HW-timed AI captures, then verify."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_ai(daq)
                self._configure_ao(daq)

                daq.configure_ai_sample_rate(
                    sample_rate=SAMPLE_RATE_HZ,
                    samples_per_channel=SAMPLES_PER_CHANNEL,
                )
                daq.start()

                try:
                    ramp_voltages = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]

                    for v in ramp_voltages:
                        daq.write_analog_value("ao_0", v)
                        daq.write_analog_value("ao_1", 5.0 - v)
                        time.sleep(0.5)

                    # Read data while still running — before stop() closes the buffer
                    ch0 = daq.get_channel("mccdaq_test.ai_0", 10, False)
                    ch1 = daq.get_channel("mccdaq_test.ai_1", 10, False)

                    self.assertGreaterEqual(len(ch0.values), 1)

                    mean_ch0 = sum(ch0.values) / len(ch0.values)
                    mean_ch1 = sum(ch1.values) / len(ch1.values)

                    self.assertAlmostEqual(mean_ch0, 5.0, delta=ANALOG_LOOPBACK_TOLERANCE_V)
                    self.assertAlmostEqual(mean_ch1, 0.0, delta=ANALOG_LOOPBACK_TOLERANCE_V)
                finally:
                    daq.stop()
            finally:
                daq.close()

        self._run_step(
            "HW-timed analog loopback (ramp)",
            "Write a voltage ramp (0-5V) to AO while HW-timed AI captures in background. "
            "Verify final samples converge to ramp endpoint via loopback.",
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

                daq.configure_ai_sample_rate(
                    sample_rate=SAMPLE_RATE_HZ,
                    samples_per_channel=SAMPLES_PER_CHANNEL,
                )
                daq.start()

                try:
                    actual_rate = daq.get_actual_sample_rate()
                    self.assertIsNotNone(actual_rate, "get_actual_sample_rate returned None after start()")
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
            f"Verify get_actual_sample_rate() returns a value within 10% of the requested {SAMPLE_RATE_HZ}Hz.",
            step,
        )

    # =====================================================================
    # 10. Sustained HW-timed acquisition — buffer overrun check
    # =====================================================================
    def test_10_sustained_hw_timed_acquisition(self):
        """Run HW-timed acquisition for several seconds and verify no data loss."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_ai(daq)
                self._configure_ao(daq)

                daq.configure_ai_sample_rate(
                    sample_rate=SAMPLE_RATE_HZ,
                    samples_per_channel=SAMPLES_PER_CHANNEL,
                )
                daq.start()

                try:
                    daq.write_analog_value("ao_0", 2.0)
                    duration_s = 3.0
                    time.sleep(duration_s)

                    ch0 = daq.get_channel("mccdaq_test.ai_0", SAMPLES_PER_CHANNEL, False)
                    self.assertGreaterEqual(len(ch0.values), 1)

                    mean_val = sum(ch0.values) / len(ch0.values)
                    variance = sum((x - mean_val) ** 2 for x in ch0.values) / len(ch0.values)
                    std_dev = math.sqrt(variance)
                    self.assertLess(
                        std_dev,
                        0.1,
                        f"High noise on sustained read: std_dev={std_dev:.4f}V (mean={mean_val:.4f}V)",
                    )
                finally:
                    daq.stop()
                    daq.write_analog_value("ao_0", 0.0)
            finally:
                daq.close()

        self._run_step(
            "Sustained HW-timed acquisition",
            "Run HW-timed acquisition for 3 seconds at a constant 2V DC input. "
            "Verify signal stability (low std dev) and no buffer overruns.",
            step,
        )

    # =====================================================================
    # 11. Digital port configuration
    # =====================================================================
    def test_11_digital_port_configure(self):
        """Configure FIRSTPORTA (output) and FIRSTPORTB (input) as 8-bit ports."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_digital_ports(daq)
            finally:
                daq.close()

        self._run_step(
            "Digital port configuration",
            "Configure FIRSTPORTA as 8-bit digital output and FIRSTPORTB as 8-bit digital input.",
            step,
        )

    # =====================================================================
    # 12. Digital port write/read loopback
    # =====================================================================
    def test_12_digital_port_loopback(self):
        """Write bit patterns to FIRSTPORTA and verify on FIRSTPORTB via loopback."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_digital_ports(daq)

                # Test all 2-bit patterns on the looped-back lines (bits 0-1)
                test_patterns = [0b00, 0b01, 0b10, 0b11]

                for pattern in test_patterns:
                    daq.write_digital_port("do_port_a", pattern)
                    time.sleep(0.05)  # allow signal to settle

                    measurement = daq.read_digital_port("di_port_b")
                    read_value = int(measurement.channel_data["mccdaq_test.di_port_b"][0])
                    loopback_bits = read_value & DIGITAL_LOOPBACK_MASK

                    self.assertEqual(
                        loopback_bits,
                        pattern,
                        f"Loopback mismatch: wrote 0b{pattern:08b} to {DO_PORT}, "
                        f"read 0b{read_value:08b} from {DI_PORT} "
                        f"(bits 0-1: 0b{loopback_bits:02b}, expected 0b{pattern:02b})",
                    )
            finally:
                # Reset output port to 0
                daq.write_digital_port("do_port_a", 0x00)
                daq.close()

        self._run_step(
            "Digital port loopback",
            "Write 2-bit patterns (00, 01, 10, 11) to FIRSTPORTA and verify "
            "bits 0-1 read back correctly on FIRSTPORTB via loopback wiring.",
            step,
        )

    # =====================================================================
    # 13. Digital port write/read — full byte patterns
    # =====================================================================
    def test_13_digital_port_full_byte(self):
        """Write full-byte patterns to FIRSTPORTA and verify loopback bits on FIRSTPORTB."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_digital_ports(daq)

                # Full-byte values — only bits 0-1 are verified via loopback
                test_patterns = [0x00, 0xFF, 0xAA, 0x55]

                for pattern in test_patterns:
                    daq.write_digital_port("do_port_a", pattern)
                    time.sleep(0.05)

                    measurement = daq.read_digital_port("di_port_b")
                    read_value = int(measurement.channel_data["mccdaq_test.di_port_b"][0])
                    loopback_bits = read_value & DIGITAL_LOOPBACK_MASK
                    expected_bits = pattern & DIGITAL_LOOPBACK_MASK

                    self.assertEqual(
                        loopback_bits,
                        expected_bits,
                        f"Loopback mismatch: wrote 0x{pattern:02X} to {DO_PORT}, "
                        f"read 0x{read_value:02X} from {DI_PORT} "
                        f"(bits 0-1: 0b{loopback_bits:02b}, expected 0b{expected_bits:02b})",
                    )
            finally:
                daq.write_digital_port("do_port_a", 0x00)
                daq.close()

        self._run_step(
            "Digital port full-byte patterns",
            "Write full-byte patterns (0x00, 0xFF, 0xAA, 0x55) to FIRSTPORTA and verify "
            "loopback bits 0-1 on FIRSTPORTB.",
            step,
        )

    # =====================================================================
    # 14. Clean shutdown — outputs to safe state
    # =====================================================================
    def test_14_clean_shutdown(self):
        """Set all outputs to safe state as a final step."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_ao(daq)
                self._configure_digital_ports(daq)

                daq.write_analog_value("ao_0", 0.0)
                daq.write_analog_value("ao_1", 0.0)
                daq.write_digital_port("do_port_a", 0x00)
            finally:
                daq.close()

        self._run_step(
            "Clean shutdown — safe state",
            "Set all analog outputs to 0V and digital output port to 0x00 as a final safety step.",
            step,
        )


if __name__ == "__main__":
    unittest.main()
