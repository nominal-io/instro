"""Rigol DG1022Z sweep hardware tests.

Full coverage of the sweep-specific driver surface: sweep type, sweep enable/state, trigger
source, frequency bounds, and timing, plus their channel validation. Where no invalid input
exists in software, the invalid case exercises a hardware-rejected combination instead.

Some invalid-combination assertions (marked below) encode the DG1000Z's documented sweep
restrictions but have not been re-confirmed against this bench; verify them against real
hardware before relying on them in CI, per this repo's convention of bench-verifying rejections
before trusting them (see test_rigol_dg1022z_hardware.py's GATED/INFINITE burst-trigger guards).
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest

from instro.lib.transports import VisaConfig
from instro.unstable.awg.drivers import RigolDG1022Z
from instro.unstable.awg.types import (
    Sawtooth,
    Sine,
    Square,
    StaticValue,
    SweepTriggerSource,
    SweepType,
    Triangle,
    Waveform,
)

pytestmark = pytest.mark.hardware

# HARDWARE TEST SETUP - EDIT THESE VALUES BEFORE RUNNING THIS FILE.
# Set VISA_RESOURCE to the bench unit's VISA resource string. Set VISA_BACKEND to
# "@ivi" or "" for the system VISA library, or "@py" for pyvisa-py.
VISA_RESOURCE = "USB0::0x1AB1::0x0642::DG1ZA000000000::INSTR"

VISA_BACKEND = "@py"
CHANNELS = (1, 2)
INVALID_CHANNEL = 3

TEST_FREQUENCY_HZ = 1000.0
FREQUENCY_TOLERANCE_REL = 1e-4

TEST_SWEEP_START_HZ = 100.0
TEST_SWEEP_END_HZ = 900.0
TEST_SWEEP_TIME_S = 1.0
TEST_SWEEP_HOLD_TIME_S = 0.1
TEST_SWEEP_RETURN_TIME_S = 0.1
SWEEP_TIME_TOLERANCE_REL = 0.01

# (method_name, args-after-channel) for every sweep method that takes a channel as its first
# argument. Used to drive one parametrized invalid-channel test across the whole sweep surface.
_SWEEP_METHODS_REQUIRING_CHANNEL: list[tuple[str, tuple[object, ...]]] = [
    ("set_sweep", (SweepType.LINEAR,)),
    ("get_sweep_type", ()),
    ("sweep_enable", (True,)),
    ("get_sweep_state", ()),
    ("set_sweep_trigger", (SweepTriggerSource.INTERNAL,)),
    ("get_sweep_trigger", ()),
    ("fire_sweep_trigger", ()),
    ("set_sweep_start_freq", (TEST_SWEEP_START_HZ,)),
    ("get_sweep_start_freq", ()),
    ("set_sweep_end_freq", (TEST_SWEEP_END_HZ,)),
    ("get_sweep_end_freq", ()),
    ("set_sweep_time", (TEST_SWEEP_TIME_S,)),
    ("get_sweep_time", ()),
    ("set_sweep_hold_time", (TEST_SWEEP_HOLD_TIME_S,)),
    ("get_sweep_hold_time", ()),
    ("set_sweep_return_time", (TEST_SWEEP_RETURN_TIME_S,)),
    ("get_sweep_return_time", ()),
]

# Basic waveforms the DG1000Z documents as sweep-capable (Sine, Square, Ramp). Pulse and
# Arbitrary are deliberately excluded: their sweep support isn't confirmed on this bench.
_SWEEPABLE_WAVEFORMS: list[Waveform] = [
    Sine(frequency_hz=TEST_FREQUENCY_HZ),
    Square(frequency_hz=TEST_FREQUENCY_HZ),
    Sawtooth(frequency_hz=TEST_FREQUENCY_HZ),
    Triangle(frequency_hz=TEST_FREQUENCY_HZ),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _shutdown_outputs(driver: RigolDG1022Z) -> None:
    for channel in CHANNELS:
        driver.output_enable(channel, False)


def _reset_driver(driver: RigolDG1022Z) -> None:
    driver._visa.write("*CLS")
    driver._visa.write("*RST")
    time.sleep(0.5)
    driver._visa.write("*CLS")
    driver._check_errors()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def driver() -> Iterator[RigolDG1022Z]:
    awg_driver = RigolDG1022Z(
        VisaConfig(
            visa_resource=VISA_RESOURCE,
            visa_backend=VISA_BACKEND,
        )
    )
    opened = False
    try:
        awg_driver.open()
        opened = True
        yield awg_driver
    finally:
        if opened:
            _shutdown_outputs(awg_driver)
        awg_driver.close()


@pytest.fixture(autouse=True)
def reset_before_each_test(driver: RigolDG1022Z) -> None:
    _reset_driver(driver)


# ---------------------------------------------------------------------------
# Connectivity
# ---------------------------------------------------------------------------


def test_01_connect_to_awg(driver: RigolDG1022Z) -> None:
    idn = driver._visa.query("*IDN?")
    print(f"\nIDN: {idn.strip()}")

    assert "DG1" in idn.upper()
    driver._check_errors()


# ---------------------------------------------------------------------------
# set_sweep / get_sweep_type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sweep_type", list(SweepType), ids=lambda sweep_type: sweep_type.value.lower())
def test_02_set_and_get_sweep_type_roundtrip(driver: RigolDG1022Z, sweep_type: SweepType) -> None:
    """Also guards the abbreviated :SWE:SPAC? readback (LIN/LOG/STE) that get_sweep_type() must tolerate."""
    driver.set_waveform(1, Sine(frequency_hz=TEST_FREQUENCY_HZ))

    driver.set_sweep(1, sweep_type)
    driver._check_errors()

    assert driver.get_sweep_type(1) is sweep_type


@pytest.mark.parametrize(
    "carrier",
    _SWEEPABLE_WAVEFORMS,
    ids=["sine", "square", "sawtooth", "triangle"],
)
def test_03_set_sweep_type_roundtrip_across_sweepable_waveforms(driver: RigolDG1022Z, carrier: Waveform) -> None:
    driver.set_waveform(1, carrier)

    driver.set_sweep(1, SweepType.LINEAR)
    driver._check_errors()

    assert driver.get_sweep_type(1) is SweepType.LINEAR


# ---------------------------------------------------------------------------
# sweep_enable / get_sweep_state
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("channel", CHANNELS, ids=lambda channel: f"channel_{channel}")
def test_04_sweep_enable_toggle(driver: RigolDG1022Z, channel: int) -> None:
    driver.set_waveform(channel, Sine(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_sweep(channel, SweepType.LINEAR)

    try:
        driver.sweep_enable(channel, True)
        driver._check_errors()
        assert driver.get_sweep_state(channel) is True
    finally:
        driver.sweep_enable(channel, False)

    assert driver.get_sweep_state(channel) is False


@pytest.mark.parametrize(
    "carrier",
    _SWEEPABLE_WAVEFORMS,
    ids=["sine", "square", "sawtooth", "triangle"],
)
def test_05_sweep_enable_on_every_sweepable_waveform(driver: RigolDG1022Z, carrier: Waveform) -> None:
    driver.set_waveform(1, carrier)
    driver.set_sweep(1, SweepType.LINEAR)

    try:
        driver.sweep_enable(1, True)
        driver._check_errors()
        assert driver.get_sweep_state(1) is True
    finally:
        driver.sweep_enable(1, False)


def test_06_set_sweep_rejects_static_value_carrier(driver: RigolDG1022Z) -> None:
    driver.set_waveform(1, StaticValue(value=0.5))

    with pytest.raises(ValueError, match="cannot sweep a StaticValue"):
        driver.set_sweep(1, SweepType.LINEAR)

    driver._check_errors()


# ---------------------------------------------------------------------------
# set_sweep_trigger / get_sweep_trigger
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source", list(SweepTriggerSource), ids=lambda source: source.value.lower())
def test_07_set_and_get_sweep_trigger_roundtrip(driver: RigolDG1022Z, source: SweepTriggerSource) -> None:
    driver.set_waveform(1, Sine(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_sweep(1, SweepType.LINEAR)

    driver.set_sweep_trigger(1, source)
    driver._check_errors()

    assert driver.get_sweep_trigger(1) is source


# ---------------------------------------------------------------------------
# Frequency bounds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("channel", CHANNELS, ids=lambda channel: f"channel_{channel}")
def test_08_sweep_frequency_bounds_roundtrip(driver: RigolDG1022Z, channel: int) -> None:
    driver.set_waveform(channel, Sine(frequency_hz=TEST_FREQUENCY_HZ))

    driver.set_sweep_start_freq(channel, TEST_SWEEP_START_HZ)
    driver.set_sweep_end_freq(channel, TEST_SWEEP_END_HZ)
    driver._check_errors()

    assert driver.get_sweep_start_freq(channel) == pytest.approx(TEST_SWEEP_START_HZ, rel=FREQUENCY_TOLERANCE_REL)
    assert driver.get_sweep_end_freq(channel) == pytest.approx(TEST_SWEEP_END_HZ, rel=FREQUENCY_TOLERANCE_REL)


def test_09_sweep_frequency_bounds_roundtrip_reversed(driver: RigolDG1022Z) -> None:
    """A start frequency above the end frequency configures a downward sweep; not an error case."""
    driver.set_waveform(1, Sine(frequency_hz=TEST_FREQUENCY_HZ))

    driver.set_sweep_start_freq(1, TEST_SWEEP_END_HZ)
    driver.set_sweep_end_freq(1, TEST_SWEEP_START_HZ)
    driver._check_errors()

    assert driver.get_sweep_start_freq(1) == pytest.approx(TEST_SWEEP_END_HZ, rel=FREQUENCY_TOLERANCE_REL)
    assert driver.get_sweep_end_freq(1) == pytest.approx(TEST_SWEEP_START_HZ, rel=FREQUENCY_TOLERANCE_REL)


# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("channel", CHANNELS, ids=lambda channel: f"channel_{channel}")
def test_10_sweep_timing_roundtrip(driver: RigolDG1022Z, channel: int) -> None:
    driver.set_waveform(channel, Sine(frequency_hz=TEST_FREQUENCY_HZ))

    driver.set_sweep_time(channel, TEST_SWEEP_TIME_S)
    driver.set_sweep_hold_time(channel, TEST_SWEEP_HOLD_TIME_S)
    driver.set_sweep_return_time(channel, TEST_SWEEP_RETURN_TIME_S)
    driver._check_errors()

    assert driver.get_sweep_time(channel) == pytest.approx(TEST_SWEEP_TIME_S, rel=SWEEP_TIME_TOLERANCE_REL)
    assert driver.get_sweep_hold_time(channel) == pytest.approx(TEST_SWEEP_HOLD_TIME_S, rel=SWEEP_TIME_TOLERANCE_REL)
    assert driver.get_sweep_return_time(channel) == pytest.approx(
        TEST_SWEEP_RETURN_TIME_S, rel=SWEEP_TIME_TOLERANCE_REL
    )


# ---------------------------------------------------------------------------
# fire_sweep_trigger
# ---------------------------------------------------------------------------


def test_11_fire_sweep_trigger_fires_when_source_already_manual(driver: RigolDG1022Z) -> None:
    driver.set_waveform(1, Sine(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_sweep(1, SweepType.LINEAR)
    driver.set_sweep_trigger(1, SweepTriggerSource.MANUAL)
    driver._check_errors()

    try:
        driver.output_enable(1, True)
        driver.sweep_enable(1, True)
        driver.fire_sweep_trigger(1)
        driver._check_errors()
    finally:
        driver.sweep_enable(1, False)
        driver.output_enable(1, False)


def test_12_fire_sweep_trigger_rejects_non_manual_source(driver: RigolDG1022Z) -> None:
    driver.set_waveform(1, Sine(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_sweep(1, SweepType.LINEAR)
    driver.set_sweep_trigger(1, SweepTriggerSource.EXTERNAL)
    driver._check_errors()

    try:
        driver.sweep_enable(1, True)

        with pytest.raises(ValueError, match="already MANUAL"):
            driver.fire_sweep_trigger(1)

        driver._check_errors()
    finally:
        driver.sweep_enable(1, False)


def test_13_fire_sweep_trigger_rejects_when_sweep_not_enabled(driver: RigolDG1022Z) -> None:
    driver.set_waveform(1, Sine(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_sweep(1, SweepType.LINEAR)
    driver.set_sweep_trigger(1, SweepTriggerSource.MANUAL)
    driver._check_errors()

    with pytest.raises(ValueError, match="sweep mode is already"):
        driver.fire_sweep_trigger(1)

    driver._check_errors()


# ---------------------------------------------------------------------------
# Channel validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method_name", "args"),
    _SWEEP_METHODS_REQUIRING_CHANNEL,
    ids=[method_name for method_name, _ in _SWEEP_METHODS_REQUIRING_CHANNEL],
)
def test_14_sweep_methods_reject_invalid_channel(
    driver: RigolDG1022Z, method_name: str, args: tuple[object, ...]
) -> None:
    method = getattr(driver, method_name)

    with pytest.raises(ValueError, match="channel must be 1 or 2"):
        method(INVALID_CHANNEL, *args)

    driver._check_errors()


# ---------------------------------------------------------------------------
# Full workflow
# ---------------------------------------------------------------------------


def test_15_full_sweep_configuration_workflow(driver: RigolDG1022Z) -> None:
    driver.set_waveform(1, Sine(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_sweep(1, SweepType.LINEAR)
    driver.set_sweep_start_freq(1, TEST_SWEEP_START_HZ)
    driver.set_sweep_end_freq(1, TEST_SWEEP_END_HZ)
    driver.set_sweep_time(1, TEST_SWEEP_TIME_S)
    driver.set_sweep_hold_time(1, TEST_SWEEP_HOLD_TIME_S)
    driver.set_sweep_return_time(1, TEST_SWEEP_RETURN_TIME_S)
    driver._check_errors()

    try:
        driver.output_enable(1, True)
        driver.sweep_enable(1, True)
        driver._check_errors()

        assert driver.get_sweep_type(1) is SweepType.LINEAR
        assert driver.get_sweep_state(1) is True
        assert driver.get_sweep_start_freq(1) == pytest.approx(TEST_SWEEP_START_HZ, rel=FREQUENCY_TOLERANCE_REL)
        assert driver.get_sweep_end_freq(1) == pytest.approx(TEST_SWEEP_END_HZ, rel=FREQUENCY_TOLERANCE_REL)
        assert driver.get_sweep_time(1) == pytest.approx(TEST_SWEEP_TIME_S, rel=SWEEP_TIME_TOLERANCE_REL)
        assert driver.get_sweep_hold_time(1) == pytest.approx(TEST_SWEEP_HOLD_TIME_S, rel=SWEEP_TIME_TOLERANCE_REL)
        assert driver.get_sweep_return_time(1) == pytest.approx(TEST_SWEEP_RETURN_TIME_S, rel=SWEEP_TIME_TOLERANCE_REL)
    finally:
        driver.sweep_enable(1, False)
        driver.output_enable(1, False)

    assert driver.get_sweep_state(1) is False
