"""Hardware validation for the GW Instek GPD-3303D via InstroPSU. Self-contained; no publishers.

Wiring / stimulus:
    Nothing connected to any output terminal (open circuit). CH1 and CH2 are exercised; CH3 is the
    fixed output and is only checked to be rejected. With no load, an enabled channel regulates in
    constant-voltage mode and reads about 0 A.

Run (verbose, one line per check):
    uv run pytest tests/psu/gw_instek/test_gw_instek_gpd3303_hardware.py -m hardware -v

Leaves the supply with its output off and both channels set to 0 V / 0 A.
"""

import sys
import time
from collections.abc import Callable, Iterator

import pytest

from instro.lib.exceptions import FeatureNotSupportedError
from instro.lib.transports.visa import VisaConfig
from instro.psu import InstroPSU
from instro.psu.drivers import GWInstekGPD3303

pytestmark = pytest.mark.hardware

# HARDWARE TEST SETUP - EDIT THESE VALUES BEFORE RUNNING THIS FILE.
# Find the port with `ls /dev/cu.*` (macOS) or the COM port in Device Manager (Windows); serial is 9600 8N1.
# NI-VISA numbers serial ports (ASRL1::INSTR); pyvisa-py names them by device path, so pin the backend with the path.
RESOURCE = "ASRL/dev/cu.usbserial-AQ02VCSL::INSTR"
VISA_BACKEND: str | None = "@py"
CHANNELS = (1, 2)
PROGRAMMED_VOLTAGE = 2.0
PROGRAMMED_CURRENT_LIMIT = 0.10
MAX_SAFE_VOLTAGE = 12.0
MAX_SAFE_CURRENT = 1.0
VOLTAGE_TOLERANCE = 0.25  # meter resolution is 0.1 V; allow a few counts of regulation error
CURRENT_TOLERANCE = 0.05
OUT_OF_RANGE_VOLTAGE = 40.0  # above the 32 V full scale; the device must reject it
OUTPUT_SETTLE_SECONDS = 1.0
BURST_REPEATS = 25

assert PROGRAMMED_VOLTAGE <= MAX_SAFE_VOLTAGE and PROGRAMMED_CURRENT_LIMIT <= MAX_SAFE_CURRENT


def _restore_safe_state(psu: InstroPSU) -> None:
    """Output off, then 0 V / 0 A on each channel; every step runs even if an earlier one fails, then any failure is raised."""
    steps: list[Callable[[], object]] = [lambda: psu.output_enable(False, 1)]
    for channel in CHANNELS:
        steps.append(lambda c=channel: psu.set_voltage(0.0, c))
        steps.append(lambda c=channel: psu.set_current_limit(0.0, c))
    errors: list[Exception] = []
    try:
        for step in steps:
            try:
                step()
            except Exception as exc:  # noqa: BLE001 - keep restoring the remaining steps
                errors.append(exc)
    finally:
        psu.close()
    if errors:
        raise RuntimeError(f"supply may not be at its safe state; {len(errors)} restore step(s) failed: {errors}")


@pytest.fixture(scope="module")
def psu() -> Iterator[InstroPSU]:
    instrument = InstroPSU(
        name="hw_validate",
        driver=GWInstekGPD3303(VisaConfig(RESOURCE, visa_backend=VISA_BACKEND)),
        num_channels=2,
        publishers=None,
    )
    instrument.open()
    try:
        instrument.output_enable(False, 1)
        yield instrument
    finally:
        _restore_safe_state(instrument)


@pytest.mark.parametrize("channel", CHANNELS)
def test_setpoint_roundtrip(psu: InstroPSU, channel: int) -> None:
    psu.set_voltage(PROGRAMMED_VOLTAGE, channel)
    psu.set_current_limit(PROGRAMMED_CURRENT_LIMIT, channel)
    voltage = psu.get_voltage_setpoint(channel)
    current = psu.get_current_setpoint(channel)

    assert voltage is not None and current is not None
    assert float(voltage.latest) == pytest.approx(PROGRAMMED_VOLTAGE, abs=0.05)
    assert float(current.latest) == pytest.approx(PROGRAMMED_CURRENT_LIMIT, abs=0.005)


def test_setpoint_resolution_is_100mv_and_10ma(psu: InstroPSU) -> None:
    psu.set_voltage(3.37, 1)
    psu.set_current_limit(0.123, 1)
    voltage = psu.get_voltage_setpoint(1)
    current = psu.get_current_setpoint(1)

    assert voltage is not None and current is not None
    assert float(voltage.latest) == pytest.approx(3.4, abs=0.01)
    assert float(current.latest) == pytest.approx(0.12, abs=0.001)


def test_out_of_range_voltage_raises_and_is_not_applied(psu: InstroPSU) -> None:
    psu.set_voltage(PROGRAMMED_VOLTAGE, 1)

    with pytest.raises(RuntimeError, match="(?i)out of range"):
        psu.set_voltage(OUT_OF_RANGE_VOLTAGE, 1)

    after = psu.get_voltage_setpoint(1)
    assert after is not None
    assert float(after.latest) == pytest.approx(PROGRAMMED_VOLTAGE, abs=0.05)


def test_back_to_back_writes_roundtrip_without_delay(psu: InstroPSU) -> None:
    for i in range(BURST_REPEATS):
        voltage = PROGRAMMED_VOLTAGE + 0.1 * (i % 5)
        current = PROGRAMMED_CURRENT_LIMIT + 0.01 * (i % 5)
        psu.set_voltage(voltage, 1)
        psu.set_current_limit(current, 1)
        voltage_readback = psu.get_voltage_setpoint(1)
        current_readback = psu.get_current_setpoint(1)

        assert voltage_readback is not None and current_readback is not None
        assert float(voltage_readback.latest) == pytest.approx(voltage, abs=0.05), f"burst {i}"
        assert float(current_readback.latest) == pytest.approx(current, abs=0.005), f"burst {i}"


def test_back_to_back_out_of_range_writes_are_all_reported(psu: InstroPSU) -> None:
    for _ in range(BURST_REPEATS):
        with pytest.raises(RuntimeError, match="(?i)out of range"):
            psu.set_voltage(OUT_OF_RANGE_VOLTAGE, 1)


def test_channel_3_is_not_programmable(psu: InstroPSU) -> None:
    with pytest.raises(FeatureNotSupportedError, match="channel 3"):
        psu.set_voltage(1.0, 3)


def test_channel_4_is_invalid(psu: InstroPSU) -> None:
    with pytest.raises(ValueError, match="channel must be 1, 2, or 3"):
        psu.get_voltage(4)


@pytest.mark.parametrize("channel", CHANNELS)
def test_output_off_reports_off_status_and_mode(psu: InstroPSU, channel: int) -> None:
    psu.output_enable(False, 1)
    status = psu.get_output_status(channel)
    mode = psu.get_operating_mode(channel)

    assert status is not None and mode is not None
    assert not bool(status.latest)
    assert mode.latest == "OFF"


@pytest.mark.parametrize("channel", CHANNELS)
def test_output_on_reads_back_setpoint_and_regulates_cv(psu: InstroPSU, channel: int) -> None:
    for c in CHANNELS:
        psu.set_voltage(PROGRAMMED_VOLTAGE, c)
        psu.set_current_limit(PROGRAMMED_CURRENT_LIMIT, c)
    psu.output_enable(True, 1)
    try:
        time.sleep(OUTPUT_SETTLE_SECONDS)
        status = psu.get_output_status(channel)
        voltage = psu.get_voltage(channel)
        current = psu.get_current(channel)
        mode = psu.get_operating_mode(channel)
    finally:
        psu.output_enable(False, 1)

    assert status is not None and voltage is not None and current is not None and mode is not None
    assert bool(status.latest)
    assert float(voltage.latest) == pytest.approx(PROGRAMMED_VOLTAGE, abs=VOLTAGE_TOLERANCE)
    assert float(current.latest) == pytest.approx(0.0, abs=CURRENT_TOLERANCE)
    assert mode.latest == "CV"


def test_repeated_status_queries_keep_replies_in_sync(psu: InstroPSU) -> None:
    for _ in range(5):
        psu.get_output_status(1)
    voltage = psu.get_voltage(1)

    assert voltage is not None
    assert float(voltage.latest) >= 0.0


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("set_overvoltage_protection_level", (10.0, 1)),
        ("get_overvoltage_protection_level", (1,)),
        ("set_overvoltage_protection_enabled", (True, 1)),
        ("get_overvoltage_protection_enabled", (1,)),
        ("set_overvoltage_protection_delay", (0.1, 1)),
        ("get_overvoltage_protection_delay", (1,)),
        ("set_overcurrent_protection_level", (0.5, 1)),
        ("get_overcurrent_protection_level", (1,)),
        ("set_overcurrent_protection_enabled", (True, 1)),
        ("get_overcurrent_protection_enabled", (1,)),
        ("clear_protection", (1,)),
        ("get_overvoltage_protection_tripped", (1,)),
        ("get_overcurrent_protection_tripped", (1,)),
        ("set_remote_sense_enabled", (True, 1)),
        ("get_remote_sense_enabled", (1,)),
    ],
)
def test_protection_and_remote_sense_are_unsupported(psu: InstroPSU, method: str, args: tuple) -> None:
    with pytest.raises(FeatureNotSupportedError):
        getattr(psu, method)(*args)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-m", "hardware", "-v"]))
