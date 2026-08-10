"""Hardware integration test for the LabJack T4 driver's typed channel methods via InstroDAQ.

This test requires a physical LabJack T4 with a thermocouple connected through
an LJTick-InAmp (the T4's 12-bit ADC cannot resolve a bare thermocouple). It
exercises the typed-channel path
(``configure_thermocouple_input`` -> ``configure_ai_thermocouple_channel``)
rather than the generic analog path covered by ``test_labjack_t4_hardware.py``.
The driver registers the thermocouple's AIN in the scan list as raw volts,
backs the InAmp's gain/offset out via ``tc_input_scaler``, and converts to
temperature on read using a snapshot of the internal cold-junction sensor.
Each test step is recorded as an event on a Nominal Core asset.

============================================================================
LABJACK T4 WIRING
============================================================================

  TC input wiring (via LJTick-InAmp):
    LJTick-InAmp mounted on the FIO4/FIO5 screw-terminal block (VS/GND powered)
    TC+  --->  InAmp INA+
    TC-  --->  InAmp INA-
    InAmp GND  --100kohm resistor--> InAmp INA-

    51x InAmp OUTA drives AIN4; jumpers set to x51 gain and 1.25 V offset (100kohm resistor GND -> INA-),
    matching TC_INPUT_SCALER below.

  CJC: the internal temp sensor (TEMPERATURE_DEVICE_K, ±5 °C on the T4) is
  snapshotted outside the stream; it cannot be read while streaming.

  Set THERMOCOUPLE_WIRED = False to run structure-only checks (no value
  asserts) with nothing connected.

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

    uv run pytest tests/daq/labjack/test_labjack_t4_typed_channels.py -m hardware -v -s

"""

import math
import time
import unittest
from datetime import timedelta

import pytest

pytest.importorskip("labjack")

from nominal.core import EventType, NominalClient  # noqa: E402

from instro.daq import InstroDAQ  # noqa: E402
from instro.daq.drivers.labjack import LabJackTSeriesDriver  # noqa: E402
from instro.daq.scaling.scaling import ReverseLinearScaler  # noqa: E402
from instro.daq.scaling.thermocouple import TC_TYPE, TC_UNIT  # noqa: E402
from instro.daq.types import CJCSource  # noqa: E402
from instro.lib.publishers import NominalCorePublisher  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration — edit before running
# ---------------------------------------------------------------------------
DEVICE_ID = "<LABJACK T4 SERIAL NUMBER>"  # LabJack T4 serial number (or "ANY" for the first device found)
NAME = "labjack_t4_typed_channels"

# Set to a Nominal dataset RID to stream validation data via NominalCorePublisher;
# leave None to publish nowhere.
DATASET_RID = None

# Physical configs. Set to false if nothing is connected
THERMOCOUPLE_WIRED = True

# Thermocouple mode — the LJTick-InAmp's gain/offset jumpers must match this scaler.
TC_CHANNEL, TC_ALIAS = "AIN4", "tc0"
TC_TYPE_UNDER_TEST = TC_TYPE.K
TC_UNIT_UNDER_TEST = TC_UNIT.CELSIUS
TC_CJC_SOURCE = CJCSource.INTERNAL
TC_INPUT_SCALER = ReverseLinearScaler(gain=51, offset=1.25, units="V")

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
class TestLabJackT4TypedChannels(unittest.TestCase):
    """Hardware integration tests for the LabJack T4 driver's typed channel methods.

    Each test creates, opens, configures, and closes its own DAQ instance,
    making every test independent. A fresh open() also resets the LabJack
    stream engine between acquisitions.
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
            driver=LabJackTSeriesDriver(device_id=DEVICE_ID, tc_input_scaler=TC_INPUT_SCALER),
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


if __name__ == "__main__":
    unittest.main()
