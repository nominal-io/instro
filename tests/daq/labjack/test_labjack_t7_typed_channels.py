"""Hardware integration test for the LabJack T7 driver's typed channel methods via InstroDAQ.

This test requires a physical LabJack T7 with a thermocouple connected, a
DAC0 -> AIN2 voltage loopback, and a FIO0 -> FIO1 digital loopback. It
exercises the typed-channel paths
(``configure_voltage_input`` -> ``configure_ai_voltage_channel``,
``configure_voltage_output`` -> ``configure_ao_voltage_channel``,
``configure_thermocouple_input`` -> ``configure_ai_thermocouple_channel``,
``configure_digital_input`` -> ``configure_di_channel``,
``configure_digital_output`` -> ``configure_do_channel``)
rather than the generic analog path covered by ``test_labjack_t7_hardware.py``.
The driver registers the thermocouple's AIN in the scan list as raw volts and
converts to temperature on read using the internal cold-junction sensor. Each
test step is recorded as an event on a Nominal Core asset.

============================================================================
LABJACK T7 WIRING
============================================================================

  TC input wiring:
    TC+  --->  AIN0
    TC-  --->  GND  (single-ended; the driver programs the ±0.1 V range and
                     AIN0_NEGATIVE_CH = GND)

  CJC: the internal temp sensor (AIN14 / TEMPERATURE_DEVICE_K, located between
  the AIN0-AIN3 screw terminals) is read alongside the thermocouple.

  Voltage loopback (wire DAC0 -> AIN2):
    DAC0 (AO, 0-5 V)  --->  AIN2  (AI)

  Digital loopback (wire FIO0 -> FIO1):
    FIO0 (driven as output)  --->  FIO1 (read as input)

  Set VOLTAGE_LOOPBACK_WIRED / THERMOCOUPLE_WIRED / DIGITAL_LOOPBACK_WIRED =
  False to run structure-only checks (no value asserts) for an unwired path.

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

    uv run pytest tests/daq/labjack/test_labjack_t7_typed_channels.py -m hardware -v -s

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
from instro.daq.scaling.thermocouple import TC_TYPE  # noqa: E402
from instro.daq.types import CJCSource  # noqa: E402
from instro.daq.units import parse_unit  # noqa: E402
from instro.lib.publishers import NominalCorePublisher  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration — edit before running
# ---------------------------------------------------------------------------
DEVICE_ID = "<LABJACK T7 SERIAL NUMBER>"  # LabJack T7 serial number (or "ANY" for the first device found)
NAME = "labjack_t7_typed_channels"

# Set to a Nominal dataset RID to stream validation data via NominalCorePublisher;
# leave None to publish nowhere.
DATASET_RID = None

# Physical configs. Set to false if nothing is connected
THERMOCOUPLE_WIRED = True
VOLTAGE_LOOPBACK_WIRED = True
DIGITAL_LOOPBACK_WIRED = True

# Thermocouple mode.
TC_CHANNEL, TC_ALIAS = "AIN0", "tc0"
TC_TYPE_UNDER_TEST = TC_TYPE.K
TC_UNIT_UNDER_TEST = "degC"
TC_CJC_SOURCE = CJCSource.INTERNAL

# Physical configuration for TC (if connected)
TC_RANGE_MIN, TC_RANGE_MAX = 0.0, 100.0
AMBIENT_MIN_C, AMBIENT_MAX_C = 10.0, 40.0

# Voltage mode — DAC0 looped back into the voltage input.
VOLTAGE_CHANNEL, VOLTAGE_ALIAS = "AIN2", "v0"
VOLTAGE_AO_CHANNEL, VOLTAGE_AO_ALIAS = "DAC0", "vao0"
VOLTAGE_RANGE_MIN, VOLTAGE_RANGE_MAX = -10.0, 10.0
VOLTAGE_AO_RANGE_MIN, VOLTAGE_AO_RANGE_MAX = 0.0, 5.0
VOLTAGE_TEST_VALUES = [0.0, 0.5, 1.25, 2.5, 3.3, 4.5]
VOLTAGE_TOLERANCE_V = 0.05

# Digital lines — one DO line looped back to one DI line.
DO_LINE, DO_ALIAS = "FIO0", "do0"
DI_LINE, DI_ALIAS = "FIO1", "di0"
DIGITAL_TEST_STATES = (0, 1, 0, 1, 0)


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
class TestLabJackT7TypedChannels(unittest.TestCase):
    """Hardware integration tests for the LabJack T7 driver's typed channel methods.

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
            driver=LabJackTSeriesDriver(device_id=DEVICE_ID),
        )
        if DATASET_RID:
            daq.add_publisher(NominalCorePublisher(dataset_rid=DATASET_RID))
        daq.open()
        return daq

    def _configure_voltage_input(self, daq: InstroDAQ, physical: str = VOLTAGE_CHANNEL, alias: str = VOLTAGE_ALIAS):
        """Configure the voltage input channel."""
        daq.configure_voltage_input(
            physical,
            alias=alias,
            range_min=VOLTAGE_RANGE_MIN,
            range_max=VOLTAGE_RANGE_MAX,
        )

    def _configure_voltage_output(
        self, daq: InstroDAQ, physical: str = VOLTAGE_AO_CHANNEL, alias: str = VOLTAGE_AO_ALIAS
    ):
        """Configure the voltage output channel looped back to the voltage input."""
        daq.configure_voltage_output(
            physical,
            alias=alias,
            range_min=VOLTAGE_AO_RANGE_MIN,
            range_max=VOLTAGE_AO_RANGE_MAX,
        )

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
    # 1. Voltage loopback — write the DAC, verify on the voltage input
    # =====================================================================
    def test_01_voltage_loopback(self):
        """Write known voltages to the DAC and verify they appear on the voltage input."""

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
        """Configure the thermocouple channel and read plausible ambient temperatures."""

        def step():
            daq = self._create_daq()
            try:
                self._configure_thermocouple(daq)

                channel = daq.ai_channels[TC_ALIAS]
                self.assertEqual(channel.physical_channel, TC_CHANNEL)
                self.assertEqual(channel.tc_type, TC_TYPE_UNDER_TEST)
                self.assertEqual(channel.unit, parse_unit(TC_UNIT_UNDER_TEST))

                errs = []
                for _ in range(3):
                    measured = daq.read_analog().latest
                    flag = (
                        ""
                        if (not THERMOCOUPLE_WIRED or AMBIENT_MIN_C <= measured <= AMBIENT_MAX_C)
                        else "  <-- outside plausible ambient range"
                    )
                    print(f"         {TC_ALIAS} = {measured:.3f} {TC_UNIT_UNDER_TEST}{flag}")
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
    # 3. Digital line loopback — drive the DO line, verify on the DI line
    # =====================================================================
    def test_03_digital_line_loopback(self):
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
