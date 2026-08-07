"""Hardware integration test for the NI driver's typed input channel methods (NI-DAQmx) via InstroDAQ.

This test requires a physical NI cDAQ chassis with a single NI 9219 universal
analog input module. The 9219 is reconfigurable per channel, so one channel is
dedicated to each mode (voltage, thermocouple, current) and each test exercises
the matching typed-channel path
(``configure_voltage_input`` -> ``configure_ai_voltage_channel``,
``configure_thermocouple_input`` -> ``configure_ai_thermocouple_channel``)
rather than the generic analog path covered by ``test_ni_hardware.py``. Each
test step is recorded as an event on a Nominal Core asset.

============================================================================
NI cDAQ CONFIG
============================================================================

  Device Specs:
      - Mod1: 9219 (universal AI: voltage, current, thermocouple, RTD, bridge)

  Wiring, one channel per mode:
    VOLTAGE_CHANNEL  — Channel 0 ( + to pin 4 (HI) and - to pin 5 (LO) )
    TC_CHANNEL       — Channel 1 (pins 4 and 5)
    CURRENT_CHANNEL  — Channel 2 ( + to pin 4 (HI) and - to pin 5 (LO) )

  Set VOLTAGE_SOURCE_WIRED / THERMOCOUPLE_WIRED to False to run structure-only
  checks (no value asserts) for a mode with nothing attached.

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
DEVICE_ID = "cDAQ3"  # NI device name as shown in NI MAX (e.g. "Dev1" or a cDAQ chassis like "cDAQ1")
NAME = "ni_typed_channels"

# Set to a Nominal dataset RID to stream validation data via NominalCorePublisher;
# leave None to publish nowhere.
DATASET_RID = None

# One 9219 channel per mode.
VOLTAGE_CHANNEL, VOLTAGE_ALIAS = f"{DEVICE_ID}Mod1/ai0", "v0"
TC_CHANNEL, TC_ALIAS = f"{DEVICE_ID}Mod1/ai1", "tc0"
CURRENT_CHANNEL, CURRENT_ALIAS = f"{DEVICE_ID}Mod1/ai2", "i0"

# Voltage mode — the 9219 supports ±125 mV, ±1 V, ±4 V, ±15 V, and ±60 V.
VOLTAGE_RANGE_MIN, VOLTAGE_RANGE_MAX = -15.0, 15.0
# True when a known DC source is attached. Gates the expected-value check;
# structural checks always run.
VOLTAGE_SOURCE_WIRED = False
EXPECTED_VOLTAGE_V = 0.0
VOLTAGE_TOLERANCE_V = 0.05

# Thermocouple mode.
TC_TYPE_UNDER_TEST = TC_TYPE.K
TC_UNIT_UNDER_TEST = TC_UNIT.CELSIUS
TC_CJC_SOURCE = CJCSource.INTERNAL
# True when a thermocouple is physically attached. Gates the plausible-reading
# checks; structural checks always run.
THERMOCOUPLE_WIRED = True
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

    def _configure_voltage(self, daq: InstroDAQ, physical: str = VOLTAGE_CHANNEL, alias: str = VOLTAGE_ALIAS):
        """Configure the 9219 channel as a voltage input."""
        daq.configure_voltage_input(
            physical,
            alias=alias,
            range_min=VOLTAGE_RANGE_MIN,
            range_max=VOLTAGE_RANGE_MAX,
        )

    def _configure_thermocouple(self, daq: InstroDAQ, physical: str = TC_CHANNEL, alias: str = TC_ALIAS):
        """Configure the 9219 channel as a thermocouple input."""
        daq.configure_thermocouple_input(
            physical,
            TC_TYPE_UNDER_TEST,
            alias=alias,
            range_min=TC_RANGE_MIN,
            range_max=TC_RANGE_MAX,
            cjc_source=TC_CJC_SOURCE,
            unit=TC_UNIT_UNDER_TEST,
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
    # 1. Voltage input
    # =====================================================================
    def test_01_voltage_input(self):
        """Configure the 9219 channel as a voltage input and read it."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_voltage(daq)

                channel = daq.ai_channels[VOLTAGE_ALIAS]
                self.assertEqual(channel.physical_channel, VOLTAGE_CHANNEL)
                self.assertEqual((channel.range_min, channel.range_max), (VOLTAGE_RANGE_MIN, VOLTAGE_RANGE_MAX))

                errs = []
                for _ in range(3):
                    measured = daq.read_analog().latest
                    err = measured - EXPECTED_VOLTAGE_V
                    flag = (
                        ""
                        if (not VOLTAGE_SOURCE_WIRED or abs(err) <= VOLTAGE_TOLERANCE_V)
                        else "  <-- out of tolerance"
                    )
                    print(f"         {VOLTAGE_ALIAS} = {measured:.4f} V{flag}")
                    if not math.isfinite(measured):
                        errs.append(f"non-finite voltage read: {measured}")
                    if VOLTAGE_SOURCE_WIRED and abs(err) > VOLTAGE_TOLERANCE_V:
                        errs.append(f"{measured:.4f} V != {EXPECTED_VOLTAGE_V} V (err {err:+.4f} V)")
                    time.sleep(0.25)
                self.assertFalse(errs, "; ".join(errs))
            finally:
                daq.close()

        self._run_step(
            "Voltage input",
            f"Configure {VOLTAGE_CHANNEL} as a [{VOLTAGE_RANGE_MIN}, {VOLTAGE_RANGE_MAX}] V input and perform 3 reads.",
            step,
        )

    # =====================================================================
    # 2. Thermocouple input
    # =====================================================================
    def test_02_thermocouple_input(self):
        """Configure the 9219 channel as a thermocouple input and read plausible ambient temperatures."""

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


if __name__ == "__main__":
    unittest.main()
