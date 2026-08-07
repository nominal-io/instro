"""Hardware integration test for the NI driver's typed channel methods (NI-DAQmx) via InstroDAQ.

This test requires a physical NI cDAQ chassis with a NI 9219 universal analog
input module plus a voltage output module and a current output module. The 9219
is reconfigurable per channel, so one input channel is dedicated to each mode
(voltage, thermocouple, current) and each test exercises the matching
typed-channel path
(``configure_voltage_input`` -> ``configure_ai_voltage_channel``,
``configure_voltage_output`` -> ``configure_ao_voltage_channel``,
``configure_thermocouple_input`` -> ``configure_ai_thermocouple_channel``,
``configure_current_input`` -> ``configure_ai_current_channel``,
``configure_current_output`` -> ``configure_ao_current_channel``,
``configure_digital_input`` -> ``configure_di_channel``,
``configure_digital_output`` -> ``configure_do_channel``)
rather than the generic analog path covered by ``test_ni_hardware.py``. The
voltage and current modes are verified through a loopback from their output
module back into the 9219. Each test step is recorded as an event on a Nominal
Core asset.

============================================================================
NI cDAQ CONFIG
============================================================================

  Device Specs (cDAQ 9189 8-slot):
      - Mod1: 9219 (universal AI: voltage, current, thermocouple)
      - Mod2: 9263 (voltage AO)
      - Mod3: 9265 (current AO)
      - Mod4: 9403 (DO)
      - Mod5: 9401 (DI)

  TC input wiring:
    TC  --->  TC_CHANNEL (Mod1/ai1) ( Using pins 4 and 5 )

  Loopback wiring (wire AO -> 9219 AI):
    VOLTAGE_AO_CHANNEL (Mod2/ao0)  --->  VOLTAGE_CHANNEL (Mod1/ai0) ( + to pin 4 (HI) and - to pin 5 (LO) )
    CURRENT_AO_CHANNEL (Mod3/ao0)  --->  CURRENT_CHANNEL (Mod1/ai2) ( + to pin 3 (HI) and - to pin 5 (LO) )

  Digital loopback wiring (wire DO -> DI):
    DO_LINE (Mod4/port0/line16)  --->  DI_LINE (Mod5/port0/line0)

  Set VOLTAGE_LOOPBACK_WIRED / CURRENT_LOOPBACK_WIRED / THERMOCOUPLE_WIRED /
  DIGITAL_LOOPBACK_WIRED to False to run structure-only checks (no value
  asserts) for an unwired path.

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

    uv run pytest tests/daq/ni/test_ni_typed_channels.py -m hardware -v -s

"""

import math
import time
import unittest
from datetime import timedelta

import pytest

pytest.importorskip("nidaqmx")

from nominal.core import EventType, NominalClient  # noqa: E402

from instro.daq import InstroDAQ  # noqa: E402
from instro.daq.drivers.ni import NIDAQDriver  # noqa: E402
from instro.daq.scaling.thermocouple import TC_TYPE, TC_UNIT  # noqa: E402
from instro.daq.types import CJCSource  # noqa: E402
from instro.lib.publishers import NominalCorePublisher  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration — edit before running
# ---------------------------------------------------------------------------
DEVICE_ID = "<NI DEVICE NAME>"  # NI device name as shown in NI MAX (e.g. "Dev1" or a cDAQ chassis like "cDAQ1")
NAME = "ni_typed_channels"

# Set to a Nominal dataset RID to stream validation data via NominalCorePublisher;
# leave None to publish nowhere.
DATASET_RID = None

# Physical configs. Set to false if simulating device
VOLTAGE_LOOPBACK_WIRED = True
CURRENT_LOOPBACK_WIRED = False
THERMOCOUPLE_WIRED = True
DIGITAL_LOOPBACK_WIRED = True

# One 9219 input channel per mode.
VOLTAGE_CHANNEL, VOLTAGE_ALIAS = f"{DEVICE_ID}Mod1/ai2", "v0"
TC_CHANNEL, TC_ALIAS = f"{DEVICE_ID}Mod1/ai1", "tc0"
CURRENT_CHANNEL, CURRENT_ALIAS = f"{DEVICE_ID}Mod1/ai3", "i0"

# Output channels looped back into the 9219.
VOLTAGE_AO_CHANNEL, VOLTAGE_AO_ALIAS = f"{DEVICE_ID}Mod2/ao3", "vao0"
CURRENT_AO_CHANNEL, CURRENT_AO_ALIAS = f"{DEVICE_ID}Mod3/ao0", "iao0"

# Digital lines — one DO line looped back to one DI line (DevN/portM/lineP form).
DO_LINE, DO_ALIAS = f"{DEVICE_ID}Mod4/port0/line16", "do20"
DI_LINE, DI_ALIAS = f"{DEVICE_ID}Mod5/port0/line0", "di0"
DIGITAL_TEST_STATES = (0, 1, 0, 1, 0)

# Voltage mode — the 9219 supports ±125 mV, ±1 V, ±4 V, ±15 V, and ±60 V.
VOLTAGE_RANGE_MIN, VOLTAGE_RANGE_MAX = -15.0, 15.0
VOLTAGE_AO_RANGE_MIN, VOLTAGE_AO_RANGE_MAX = -10.0, 10.0
VOLTAGE_TEST_VALUES = [0.0, 0.5, 1.25, 2.5, 3.3, 4.5]
VOLTAGE_TOLERANCE_V = 0.05

# Current mode — the 9219 reads ±25 mA; the 9265 sources 0-20 mA.
CURRENT_RANGE_MIN, CURRENT_RANGE_MAX = -0.025, 0.025
CURRENT_AO_RANGE_MIN, CURRENT_AO_RANGE_MAX = 0.0, 0.02
CURRENT_TEST_VALUES = [0.0, 0.004, 0.008, 0.012, 0.016, 0.02]
CURRENT_TOLERANCE_A = 0.0005

# Thermocouple mode.
TC_TYPE_UNDER_TEST = TC_TYPE.K
TC_UNIT_UNDER_TEST = TC_UNIT.CELSIUS
TC_CJC_SOURCE = CJCSource.INTERNAL

# Physical configuration for TC (if connected)
TC_RANGE_MIN, TC_RANGE_MAX = 0.0, 100.0
AMBIENT_MIN_C, AMBIENT_MAX_C = 10.0, 40.0


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
class TestNITypedChannels(unittest.TestCase):
    """Hardware integration tests for the NI driver's typed channel methods.

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

    def _configure_voltage_input(self, daq: InstroDAQ, physical: str = VOLTAGE_CHANNEL, alias: str = VOLTAGE_ALIAS):
        """Configure the 9219 voltage input channel."""
        daq.configure_voltage_input(
            physical,
            alias=alias,
            range_min=VOLTAGE_RANGE_MIN,
            range_max=VOLTAGE_RANGE_MAX,
        )

    def _configure_voltage_output(
        self, daq: InstroDAQ, physical: str = VOLTAGE_AO_CHANNEL, alias: str = VOLTAGE_AO_ALIAS
    ):
        """Configure the voltage output channel looped back to the 9219."""
        daq.configure_voltage_output(
            physical,
            alias=alias,
            range_min=VOLTAGE_AO_RANGE_MIN,
            range_max=VOLTAGE_AO_RANGE_MAX,
        )

    def _configure_current_input(self, daq: InstroDAQ, physical: str = CURRENT_CHANNEL, alias: str = CURRENT_ALIAS):
        """Configure the 9219 current input channel."""
        daq.configure_current_input(
            physical,
            alias=alias,
            range_min=CURRENT_RANGE_MIN,
            range_max=CURRENT_RANGE_MAX,
        )

    def _configure_current_output(
        self, daq: InstroDAQ, physical: str = CURRENT_AO_CHANNEL, alias: str = CURRENT_AO_ALIAS
    ):
        """Configure the current output channel looped back to the 9219."""
        daq.configure_current_output(
            physical,
            alias=alias,
            range_min=CURRENT_AO_RANGE_MIN,
            range_max=CURRENT_AO_RANGE_MAX,
        )

    def _configure_thermocouple(self, daq: InstroDAQ, physical: str = TC_CHANNEL, alias: str = TC_ALIAS):
        """Configure the 9219 thermocouple input channel."""
        daq.configure_thermocouple_input(
            physical,
            TC_TYPE_UNDER_TEST,
            alias=alias,
            range_min=TC_RANGE_MIN,
            range_max=TC_RANGE_MAX,
            cjc_source=TC_CJC_SOURCE,
            unit=TC_UNIT_UNDER_TEST,
        )

    def _configure_digital_lines(self, daq: InstroDAQ):
        """Configure DO_LINE as a digital output and DI_LINE as a digital input."""
        daq.configure_digital_output(DO_LINE, alias=DO_ALIAS)
        daq.configure_digital_input(DI_LINE, alias=DI_ALIAS)

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
    # 1. Voltage loopback — write the AO, verify on the 9219 voltage input
    # =====================================================================
    def test_01_voltage_loopback(self):
        """Write known voltages to the AO and verify they appear on the 9219 voltage input."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_voltage_input(daq)
                self._configure_voltage_output(daq)

                channel = daq.ai_channels[VOLTAGE_ALIAS]
                self.assertEqual(channel.physical_channel, VOLTAGE_CHANNEL)
                self.assertEqual((channel.range_min, channel.range_max), (VOLTAGE_RANGE_MIN, VOLTAGE_RANGE_MAX))

                errs = []
                for v in VOLTAGE_TEST_VALUES:
                    daq.write_analog_value(VOLTAGE_AO_ALIAS, v)
                    time.sleep(0.05)  # let the output settle
                    measured = daq.read_analog().latest
                    err = measured - v
                    flag = (
                        ""
                        if (not VOLTAGE_LOOPBACK_WIRED or abs(err) <= VOLTAGE_TOLERANCE_V)
                        else "  <-- out of tolerance"
                    )
                    print(
                        f"         {VOLTAGE_AO_ALIAS}={v:.3f} V | "
                        f"{VOLTAGE_ALIAS}={measured:.4f} V | err={err:+.4f} V{flag}"
                    )
                    if not math.isfinite(measured):
                        errs.append(f"non-finite read at {v} V")
                    if VOLTAGE_LOOPBACK_WIRED and abs(err) > VOLTAGE_TOLERANCE_V:
                        errs.append(
                            f"{VOLTAGE_AO_ALIAS}={v} V -> {VOLTAGE_ALIAS}={measured:.4f} V "
                            f"(err {err:+.4f} V > {VOLTAGE_TOLERANCE_V} V)"
                        )
                daq.write_analog_value(VOLTAGE_AO_ALIAS, 0.0)
                self.assertFalse(errs, "; ".join(errs))
            finally:
                daq.close()

        self._run_step(
            "Voltage loopback",
            f"Write a sweep of voltages ({VOLTAGE_TEST_VALUES} V) to {VOLTAGE_AO_CHANNEL} and verify each "
            f"reads back on {VOLTAGE_CHANNEL} within {VOLTAGE_TOLERANCE_V} V.",
            step,
        )

    # =====================================================================
    # 2. Thermocouple input
    # =====================================================================
    def test_02_thermocouple_input(self):
        """Configure the 9219 thermocouple channel and read plausible ambient temperatures."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_thermocouple(daq)

                channel = daq.ai_channels[TC_ALIAS]
                self.assertEqual(channel.physical_channel, TC_CHANNEL)
                self.assertEqual(channel.tc_type, TC_TYPE_UNDER_TEST)
                self.assertEqual(channel.unit, TC_UNIT_UNDER_TEST)

                errs = []
                for _ in range(3):
                    measured = daq.read_analog().latest
                    flag = (
                        ""
                        if (not THERMOCOUPLE_WIRED or AMBIENT_MIN_C <= measured <= AMBIENT_MAX_C)
                        else "  <-- outside plausible ambient range"
                    )
                    print(f"         {TC_ALIAS} = {measured:.3f} {TC_UNIT_UNDER_TEST.value}{flag}")
                    if not math.isfinite(measured):
                        errs.append(f"non-finite thermocouple read: {measured}")
                    if THERMOCOUPLE_WIRED and not AMBIENT_MIN_C <= measured <= AMBIENT_MAX_C:
                        errs.append(f"{measured:.3f} outside [{AMBIENT_MIN_C}, {AMBIENT_MAX_C}]")
                    time.sleep(0.25)
                self.assertFalse(errs, "; ".join(errs))
            finally:
                daq.close()

        self._run_step(
            "Thermocouple input",
            f"Configure {TC_CHANNEL} as a type {TC_TYPE_UNDER_TEST.value} thermocouple input "
            f"and perform 3 reads, checking each lands in the plausible ambient band.",
            step,
        )

    # =====================================================================
    # 3. Current loopback — write the AO, verify on the 9219 current input
    # =====================================================================
    def test_03_current_loopback(self):
        """Write known currents to the AO and verify they appear on the 9219 current input."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_current_input(daq)
                self._configure_current_output(daq)

                channel = daq.ai_channels[CURRENT_ALIAS]
                self.assertEqual(channel.physical_channel, CURRENT_CHANNEL)
                self.assertEqual((channel.range_min, channel.range_max), (CURRENT_RANGE_MIN, CURRENT_RANGE_MAX))

                errs = []
                for i in CURRENT_TEST_VALUES:
                    daq.write_analog_value(CURRENT_AO_ALIAS, i)
                    time.sleep(0.05)  # let the output settle
                    measured = daq.read_analog().latest
                    err = measured - i
                    flag = (
                        ""
                        if (not CURRENT_LOOPBACK_WIRED or abs(err) <= CURRENT_TOLERANCE_A)
                        else "  <-- out of tolerance"
                    )
                    print(
                        f"         {CURRENT_AO_ALIAS}={i * 1000:.3f} mA | "
                        f"{CURRENT_ALIAS}={measured * 1000:.4f} mA | err={err * 1000:+.4f} mA{flag}"
                    )
                    if not math.isfinite(measured):
                        errs.append(f"non-finite read at {i} A")
                    if CURRENT_LOOPBACK_WIRED and abs(err) > CURRENT_TOLERANCE_A:
                        errs.append(
                            f"{CURRENT_AO_ALIAS}={i} A -> {CURRENT_ALIAS}={measured:.6f} A "
                            f"(err {err:+.6f} A > {CURRENT_TOLERANCE_A} A)"
                        )
                daq.write_analog_value(CURRENT_AO_ALIAS, 0.0)
                self.assertFalse(errs, "; ".join(errs))
            finally:
                daq.close()

        self._run_step(
            "Current loopback",
            f"Write a sweep of currents ({CURRENT_TEST_VALUES} A) to {CURRENT_AO_CHANNEL} and verify each "
            f"reads back on {CURRENT_CHANNEL} within {CURRENT_TOLERANCE_A} A.",
            step,
        )

    # =====================================================================
    # 4. Digital line loopback — drive the DO line, verify on the DI line
    # =====================================================================
    def test_04_digital_line_loopback(self):
        """Drive DO_LINE and verify the state on DI_LINE via single-line loopback."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_digital_lines(daq)

                self.assertEqual(daq.do_channels[DO_ALIAS].physical_channel, DO_LINE)
                self.assertEqual(daq.di_channels[DI_ALIAS].physical_channel, DI_LINE)

                errs = []
                for state in DIGITAL_TEST_STATES:
                    daq.write_digital_line(DO_ALIAS, state)
                    time.sleep(0.05)
                    read = int(daq.read_digital_line(DI_ALIAS).latest)
                    flag = "" if (not DIGITAL_LOOPBACK_WIRED or read == state) else "  <-- mismatch"
                    print(f"         {DO_ALIAS}<-{state} | {DI_ALIAS}={read}{flag}")
                    if DIGITAL_LOOPBACK_WIRED and read != state:
                        errs.append(f"drove {DO_ALIAS}={state}, read {DI_ALIAS}={read}")
                self.assertFalse(errs, "; ".join(errs))
            finally:
                daq.write_digital_line(DO_ALIAS, 0)
                daq.close()

        self._run_step(
            "Digital line loopback",
            f"Drive {DO_LINE} through a {list(DIGITAL_TEST_STATES)} sequence and verify {DI_LINE} reads back "
            "the same state via single-line loopback wiring.",
            step,
        )


if __name__ == "__main__":
    unittest.main()
