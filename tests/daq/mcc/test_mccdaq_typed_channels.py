"""Hardware integration test for the MCC driver's typed channel methods via InstroDAQ.

This test requires a physical thermocouple-capable MCC device (e.g. USB-2408,
E-TC, USB-TC) with a thermocouple connected. It exercises the typed-channel
path (``configure_thermocouple_input`` -> ``configure_ai_thermocouple_channel``)
rather than the generic analog path covered by ``test_mccdaq_hardware.py``.
The Universal Library applies cold-junction compensation internally and
returns temperature directly. Each test step is recorded as an event on a
Nominal Core asset.

Analog input only: the USB-2404-UI has no digital I/O and no analog output
(its UL User's Guide page lists analog input as its only feature). The typed
digital channel methods (``configure_digital_input`` /
``configure_digital_output``) are covered against the USB-1616HS-4 in
``test_mccdaq_hardware.py`` instead.

============================================================================
MCC THERMOCOUPLE WIRING
============================================================================

  TC input wiring (channel 0; terminal labels vary by model):
    TC+  --->  CH0H  (channel 0 high)
    TC-  --->  CH0L  (channel 0 low)

  CJC: the device's built-in cold-junction sensors are used by the Universal
  Library automatically; no CJC wiring or configuration is needed.

  Voltage input wiring (channel 1). This device has no analog output, so the
  voltage comes from an external supply rather than an AO loopback. Set the
  supply to EXTERNAL_VOLTAGE volts and wire it as:
    source +  --->  CH1H  (channel 1 high)
    source -  --->  CH1L  (channel 1 low)

  Set THERMOCOUPLE_WIRED / EXTERNAL_VOLTAGE_WIRED = False to run structure-only
  checks (no value asserts) for whichever input is not connected.

============================================================================
NOMINAL CORE CONFIGURATION
============================================================================

  Before running, configure:

    DEVICE_ID           — MCC device unique ID, optionally suffixed with
                          ":<board_number>" (default 0)
    DEVICE_MODEL        — device model name used for the Nominal asset
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

    uv run pytest tests/daq/mcc/test_mccdaq_typed_channels.py -m hardware -v -s

"""

import math
import time
import unittest
from datetime import timedelta

import pytest

pytest.importorskip("mcculw")

from nominal.core import EventType, NominalClient  # noqa: E402

from instro.daq import InstroDAQ  # noqa: E402
from instro.daq.drivers.mcc import MCCDriver  # noqa: E402
from instro.daq.scaling.thermocouple import TC_TYPE, TC_UNIT  # noqa: E402
from instro.daq.types import CJCSource  # noqa: E402
from instro.lib.publishers import NominalCorePublisher  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration — edit before running
# ---------------------------------------------------------------------------
DEVICE_ID = "297D859"  # MCC device unique ID, optionally suffixed with ":<board_number>" (default 0)
DEVICE_MODEL = "MCC USB-2404-UI"  # device model name; used for the Nominal asset
NAME = "mccdaq_typed_channels"

# Set to a Nominal dataset RID to stream validation data via NominalCorePublisher;
# leave None to publish nowhere.
DATASET_RID = None

# Physical configs. Set to false if nothing is connected
THERMOCOUPLE_WIRED = True
EXTERNAL_VOLTAGE_WIRED = True

# Thermocouple mode.
TC_CHANNEL, TC_ALIAS = "0", "tc0"
TC_TYPE_UNDER_TEST = TC_TYPE.K
TC_UNIT_UNDER_TEST = TC_UNIT.CELSIUS
TC_CJC_SOURCE = CJCSource.INTERNAL

# Physical configuration for TC (if connected)
TC_RANGE_MIN, TC_RANGE_MAX = 0.0, 100.0
AMBIENT_MIN_C, AMBIENT_MAX_C = 10.0, 40.0

# Voltage mode. This device has no analog output, so an external supply drives the channel.
VOLTAGE_CHANNEL, VOLTAGE_ALIAS = "1", "v1"
VOLTAGE_RANGE_MIN, VOLTAGE_RANGE_MAX = -10.0, 10.0

# Voltage the external supply applies to VOLTAGE_CHANNEL, in volts (if connected)
EXTERNAL_VOLTAGE = 2.5
VOLTAGE_TOLERANCE_V = 0.05


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
            properties={"device_type": DEVICE_MODEL, "purpose": "hardware-test"},
            name=DEVICE_MODEL,
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
class TestMCCDAQTypedChannels(unittest.TestCase):
    """Hardware integration tests for the MCC driver's typed channel methods.

    Each test creates, opens, configures, and closes its own DAQ instance,
    making every test independent.
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
            driver=MCCDriver(device_id=DEVICE_ID),
        )
        if DATASET_RID:
            daq.add_publisher(NominalCorePublisher(dataset_rid=DATASET_RID))
        daq.open()
        return daq

    def _configure_thermocouple(self, daq: InstroDAQ, physical: str = TC_CHANNEL, alias: str = TC_ALIAS):
        """Configure the thermocouple input channel."""
        daq.configure_thermocouple_input(
            physical,
            TC_TYPE_UNDER_TEST,
            alias=alias,
            range_min=TC_RANGE_MIN,
            range_max=TC_RANGE_MAX,
            cjc_source=TC_CJC_SOURCE,
            unit=TC_UNIT_UNDER_TEST,
        )

    def _configure_voltage_input(self, daq: InstroDAQ, physical: str = VOLTAGE_CHANNEL, alias: str = VOLTAGE_ALIAS):
        """Configure the voltage input channel driven by the external supply."""
        daq.configure_voltage_input(
            physical,
            alias=alias,
            range_min=VOLTAGE_RANGE_MIN,
            range_max=VOLTAGE_RANGE_MAX,
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
    # 1. Thermocouple input
    # =====================================================================
    def test_01_thermocouple_input(self):
        """Configure the thermocouple channel and read plausible ambient temperatures."""

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
    # 2. Voltage input — externally supplied, this device has no analog output
    # =====================================================================
    def test_02_voltage_input(self):
        """Configure the voltage channel and verify it reads the externally applied voltage."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_voltage_input(daq)

                channel = daq.ai_channels[VOLTAGE_ALIAS]
                self.assertEqual(channel.physical_channel, VOLTAGE_CHANNEL)
                self.assertEqual((channel.range_min, channel.range_max), (VOLTAGE_RANGE_MIN, VOLTAGE_RANGE_MAX))

                errs = []
                for _ in range(3):
                    measured = daq.read_analog().latest
                    err = measured - EXTERNAL_VOLTAGE
                    flag = (
                        ""
                        if (not EXTERNAL_VOLTAGE_WIRED or abs(err) <= VOLTAGE_TOLERANCE_V)
                        else "  <-- out of tolerance"
                    )
                    print(f"         {VOLTAGE_ALIAS} = {measured:.4f} V | err={err:+.4f} V{flag}")
                    if not math.isfinite(measured):
                        errs.append(f"non-finite voltage read: {measured}")
                    if EXTERNAL_VOLTAGE_WIRED and abs(err) > VOLTAGE_TOLERANCE_V:
                        errs.append(
                            f"{VOLTAGE_ALIAS}={measured:.4f} V vs {EXTERNAL_VOLTAGE} V applied "
                            f"(err {err:+.4f} V > {VOLTAGE_TOLERANCE_V} V)"
                        )
                    time.sleep(0.25)
                self.assertFalse(errs, "; ".join(errs))
            finally:
                daq.close()

        self._run_step(
            "Voltage input",
            f"Configure {VOLTAGE_CHANNEL} as a voltage input and perform 3 reads, checking each lands "
            f"within {VOLTAGE_TOLERANCE_V} V of the {EXTERNAL_VOLTAGE} V external supply.",
            step,
        )


if __name__ == "__main__":
    unittest.main()
