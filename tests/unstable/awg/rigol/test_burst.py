"""Throwaway full-coverage HARDWARE test suite for RigolDG1022Z burst functionality.

Not part of the permanent suite; not numbered like test_rigol_dg1022z_hardware.py.
Covers every burst-related method against the real instrument, valid and invalid cases.
Delete before committing.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest

from instro.lib.transports import VisaConfig
from instro.unstable.awg.drivers import RigolDG1022Z
from instro.unstable.awg.types import (
    Arbitrary,
    BurstTriggerSource,
    BurstType,
    GatePolarity,
    Pulse,
    Sawtooth,
    Sine,
    Square,
    StaticValue,
    Triangle,
    Waveform,
)

pytestmark = pytest.mark.hardware

# HARDWARE TEST SETUP - EDIT THESE VALUES BEFORE RUNNING THIS FILE.
VISA_RESOURCE = "TCPIP0::169.254.10.1::INSTR"
VISA_BACKEND = "@py"
CHANNELS = (1, 2)
INVALID_CHANNEL = 3

TEST_FREQUENCY_HZ = 1000.0
_ARB_SAMPLES = (0.0, 0.5, 1.0, 0.5, 0.0, -0.5, -1.0, -0.5, 0.25)


# ---------------------------------------------------------------------------
# Helpers / fixtures (self-contained duplicate of test_rigol_dg1022z_hardware.py's pattern)
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


@pytest.fixture(scope="module")
def driver() -> Iterator[RigolDG1022Z]:
    awg_driver = RigolDG1022Z(VisaConfig(visa_resource=VISA_RESOURCE, visa_backend=VISA_BACKEND))
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
# set_burst / get_burst_type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "carrier",
    [
        Sine(frequency_hz=TEST_FREQUENCY_HZ),
        Square(frequency_hz=TEST_FREQUENCY_HZ),
        Sawtooth(frequency_hz=TEST_FREQUENCY_HZ),
        Triangle(frequency_hz=TEST_FREQUENCY_HZ),
        Pulse(frequency_hz=TEST_FREQUENCY_HZ, width_s=0.0002),
        Arbitrary(samples=_ARB_SAMPLES, sample_rate_hz=100_000.0),
    ],
    ids=["sine", "square", "sawtooth", "triangle", "pulse", "arbitrary"],
)
@pytest.mark.parametrize("channel", CHANNELS, ids=lambda c: f"channel_{c}")
@pytest.mark.parametrize("burst_type", list(BurstType), ids=lambda b: b.name.lower())
def test_set_burst_and_get_burst_type_every_type_carrier_channel(
    driver: RigolDG1022Z, burst_type: BurstType, channel: int, carrier: Waveform
) -> None:
    driver.set_waveform(channel, carrier)
    driver.set_burst(channel, burst_type)
    driver._check_errors()

    assert driver.get_burst_type(channel) is burst_type


def test_set_burst_rejects_staticvalue_carrier(driver: RigolDG1022Z) -> None:
    driver.set_waveform(1, StaticValue(value=0.5))

    with pytest.raises(ValueError, match="cannot burst a StaticValue"):
        driver.set_burst(1, BurstType.NCYCLE)

    driver._check_errors()


def test_set_burst_rejects_invalid_burst_type(driver: RigolDG1022Z) -> None:
    driver.set_waveform(1, Sine(frequency_hz=TEST_FREQUENCY_HZ))

    with pytest.raises(TypeError, match="burst_type must be a BurstType"):
        driver.set_burst(1, "NCYCLE")  # type: ignore[arg-type]

    driver._check_errors()


def test_set_burst_rejects_invalid_channel(driver: RigolDG1022Z) -> None:
    with pytest.raises(ValueError, match="channel must be 1 or 2"):
        driver.set_burst(INVALID_CHANNEL, BurstType.NCYCLE)

    driver._check_errors()


def test_get_burst_type_rejects_invalid_channel(driver: RigolDG1022Z) -> None:
    with pytest.raises(ValueError, match="channel must be 1 or 2"):
        driver.get_burst_type(0)

    driver._check_errors()


# ---------------------------------------------------------------------------
# burst_enable / get_burst_state
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("channel", CHANNELS, ids=lambda c: f"channel_{c}")
def test_burst_enable_toggle_roundtrip(driver: RigolDG1022Z, channel: int) -> None:
    driver.set_waveform(channel, Square(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_burst(channel, BurstType.NCYCLE)
    driver._check_errors()

    driver.burst_enable(channel, True)
    driver._check_errors()
    assert driver.get_burst_state(channel) is True

    driver.burst_enable(channel, False)
    driver._check_errors()
    assert driver.get_burst_state(channel) is False


def test_burst_enable_rejects_invalid_channel(driver: RigolDG1022Z) -> None:
    with pytest.raises(ValueError, match="channel must be 1 or 2"):
        driver.burst_enable(INVALID_CHANNEL, True)

    driver._check_errors()


def test_get_burst_state_rejects_invalid_channel(driver: RigolDG1022Z) -> None:
    with pytest.raises(ValueError, match="channel must be 1 or 2"):
        driver.get_burst_state(-1)

    driver._check_errors()


# ---------------------------------------------------------------------------
# set_burst_trigger / get_burst_trigger
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("burst_type", "source"),
    [
        (BurstType.NCYCLE, BurstTriggerSource.INTERNAL),
        (BurstType.NCYCLE, BurstTriggerSource.EXTERNAL),
        (BurstType.NCYCLE, BurstTriggerSource.MANUAL),
        (BurstType.INFINITE, BurstTriggerSource.EXTERNAL),
        (BurstType.INFINITE, BurstTriggerSource.MANUAL),
    ],
    ids=["ncycle_internal", "ncycle_external", "ncycle_manual", "infinite_external", "infinite_manual"],
)
def test_burst_trigger_roundtrip_matches_configured_source(
    driver: RigolDG1022Z, burst_type: BurstType, source: BurstTriggerSource
) -> None:
    driver.set_waveform(1, Square(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_burst(1, burst_type)
    driver._check_errors()

    driver.set_burst_trigger(1, source)
    driver._check_errors()

    assert driver.get_burst_trigger(1) is source


def test_set_burst_trigger_rejects_gated_mode(driver: RigolDG1022Z) -> None:
    """Regression: :BURS:TRIG:SOUR is rejected (-220) once :BURS:MODE GAT is set, for any source."""
    driver.set_waveform(1, Square(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_burst(1, BurstType.GATED)
    driver._check_errors()

    with pytest.raises(ValueError, match="GATED burst mode"):
        driver.set_burst_trigger(1, BurstTriggerSource.EXTERNAL)

    driver._check_errors()


def test_set_burst_trigger_rejects_internal_source_in_infinite_mode(driver: RigolDG1022Z) -> None:
    """Regression: INTERNAL trigger during INFINITE burst is rejected (-220) on the bench."""
    driver.set_waveform(1, Square(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_burst(1, BurstType.INFINITE)
    driver._check_errors()

    with pytest.raises(ValueError, match="INFINITE burst mode"):
        driver.set_burst_trigger(1, BurstTriggerSource.INTERNAL)

    driver._check_errors()


def test_set_burst_trigger_rejects_invalid_source_type(driver: RigolDG1022Z) -> None:
    driver.set_waveform(1, Square(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_burst(1, BurstType.NCYCLE)
    driver._check_errors()

    with pytest.raises(TypeError, match="source must be a BurstTriggerSource"):
        driver.set_burst_trigger(1, "EXT")  # type: ignore[arg-type]

    driver._check_errors()


def test_set_burst_trigger_rejects_invalid_channel(driver: RigolDG1022Z) -> None:
    with pytest.raises(ValueError, match="channel must be 1 or 2"):
        driver.set_burst_trigger(INVALID_CHANNEL, BurstTriggerSource.EXTERNAL)

    driver._check_errors()


def test_get_burst_trigger_rejects_invalid_channel(driver: RigolDG1022Z) -> None:
    with pytest.raises(ValueError, match="channel must be 1 or 2"):
        driver.get_burst_trigger(INVALID_CHANNEL)

    driver._check_errors()


# ---------------------------------------------------------------------------
# fire_burst_trigger
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "burst_type",
    [BurstType.NCYCLE, BurstType.INFINITE],
    ids=["ncycle", "infinite"],
)
def test_fire_burst_trigger_fires_when_source_already_manual(driver: RigolDG1022Z, burst_type: BurstType) -> None:
    driver.set_waveform(1, Square(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_burst(1, burst_type)
    driver.set_burst_trigger(1, BurstTriggerSource.MANUAL)
    driver._check_errors()

    driver.output_enable(1, True)
    driver.fire_burst_trigger(1)
    driver._check_errors()

    driver.output_enable(1, False)


def test_fire_burst_trigger_rejects_non_manual_source_explicit(driver: RigolDG1022Z) -> None:
    driver.set_waveform(1, Square(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_burst(1, BurstType.NCYCLE)
    driver.set_burst_trigger(1, BurstTriggerSource.EXTERNAL)
    driver._check_errors()

    with pytest.raises(ValueError, match="already MANUAL"):
        driver.fire_burst_trigger(1)

    driver._check_errors()


def test_fire_burst_trigger_rejects_non_manual_source_gated(driver: RigolDG1022Z) -> None:
    """GATED locks the trigger source to EXTERNAL; rejected the same as any other non-MANUAL source."""
    driver.set_waveform(1, Square(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_burst(1, BurstType.GATED)
    driver._check_errors()

    with pytest.raises(ValueError, match="already MANUAL"):
        driver.fire_burst_trigger(1)

    driver._check_errors()


def test_fire_burst_trigger_rejects_invalid_channel(driver: RigolDG1022Z) -> None:
    with pytest.raises(ValueError, match="channel must be 1 or 2"):
        driver.fire_burst_trigger(INVALID_CHANNEL)

    driver._check_errors()


# ---------------------------------------------------------------------------
# set_burst_delay / get_burst_delay
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("channel", CHANNELS, ids=lambda c: f"channel_{c}")
@pytest.mark.parametrize("delay_s", [0.0, 1e-6, 0.001, 0.05], ids=["zero", "1us", "1ms", "50ms"])
def test_burst_delay_roundtrip(driver: RigolDG1022Z, channel: int, delay_s: float) -> None:
    driver.set_burst_delay(channel, delay_s)
    driver._check_errors()

    assert driver.get_burst_delay(channel) == pytest.approx(delay_s, abs=1e-7)


@pytest.mark.parametrize("delay_s", [-1e-9, -1.0], ids=["tiny_negative", "large_negative"])
def test_set_burst_delay_rejects_negative_value(driver: RigolDG1022Z, delay_s: float) -> None:
    with pytest.raises(ValueError, match="delay_s must be non-negative"):
        driver.set_burst_delay(1, delay_s)


def test_set_burst_delay_rejects_invalid_channel(driver: RigolDG1022Z) -> None:
    with pytest.raises(ValueError, match="channel must be 1 or 2"):
        driver.set_burst_delay(INVALID_CHANNEL, 0.001)


def test_get_burst_delay_rejects_invalid_channel(driver: RigolDG1022Z) -> None:
    with pytest.raises(ValueError, match="channel must be 1 or 2"):
        driver.get_burst_delay(INVALID_CHANNEL)

    driver._check_errors()


# ---------------------------------------------------------------------------
# set_burst_gate_polarity / get_burst_gate_polarity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("channel", CHANNELS, ids=lambda c: f"channel_{c}")
@pytest.mark.parametrize("gate_polarity", list(GatePolarity), ids=lambda g: g.name.lower())
def test_burst_gate_polarity_roundtrip(driver: RigolDG1022Z, channel: int, gate_polarity: GatePolarity) -> None:
    driver.set_burst_gate_polarity(channel, gate_polarity)
    driver._check_errors()

    assert driver.get_burst_gate_polarity(channel) is gate_polarity


def test_set_burst_gate_polarity_rejects_invalid_type(driver: RigolDG1022Z) -> None:
    with pytest.raises(TypeError, match="gate_polarity must be a GatePolarity"):
        driver.set_burst_gate_polarity(1, "NORM")  # type: ignore[arg-type]


def test_set_burst_gate_polarity_rejects_invalid_channel(driver: RigolDG1022Z) -> None:
    with pytest.raises(ValueError, match="channel must be 1 or 2"):
        driver.set_burst_gate_polarity(INVALID_CHANNEL, GatePolarity.NORM)


def test_get_burst_gate_polarity_rejects_invalid_channel(driver: RigolDG1022Z) -> None:
    with pytest.raises(ValueError, match="channel must be 1 or 2"):
        driver.get_burst_gate_polarity(INVALID_CHANNEL)

    driver._check_errors()


# ---------------------------------------------------------------------------
# set_burst_ncycles / get_burst_ncycles
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("channel", CHANNELS, ids=lambda c: f"channel_{c}")
@pytest.mark.parametrize("n_cycles", [1, 5, 1000], ids=["one", "five", "thousand"])
def test_burst_ncycles_roundtrip(driver: RigolDG1022Z, channel: int, n_cycles: int) -> None:
    driver.set_burst_ncycles(channel, n_cycles)
    driver._check_errors()

    assert driver.get_burst_ncycles(channel) == n_cycles


@pytest.mark.parametrize("n_cycles", [0, -1, -1000], ids=["zero", "neg_one", "large_negative"])
def test_set_burst_ncycles_rejects_non_positive_value(driver: RigolDG1022Z, n_cycles: int) -> None:
    with pytest.raises(ValueError, match="n_cycles must be >= 1"):
        driver.set_burst_ncycles(1, n_cycles)


def test_set_burst_ncycles_rejects_invalid_channel(driver: RigolDG1022Z) -> None:
    with pytest.raises(ValueError, match="channel must be 1 or 2"):
        driver.set_burst_ncycles(INVALID_CHANNEL, 10)


def test_get_burst_ncycles_rejects_invalid_channel(driver: RigolDG1022Z) -> None:
    with pytest.raises(ValueError, match="channel must be 1 or 2"):
        driver.get_burst_ncycles(INVALID_CHANNEL)

    driver._check_errors()


# ---------------------------------------------------------------------------
# set_burst_period / get_burst_period
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("channel", CHANNELS, ids=lambda c: f"channel_{c}")
@pytest.mark.parametrize("period", [0.001, 0.05, 1.0], ids=["1ms", "50ms", "1s"])
def test_burst_period_roundtrip(driver: RigolDG1022Z, channel: int, period: float) -> None:
    driver.set_burst_period(channel, period)
    driver._check_errors()

    assert driver.get_burst_period(channel) == pytest.approx(period, rel=1e-3)


@pytest.mark.parametrize("period", [0.0, -0.001, -100.0], ids=["zero", "small_negative", "large_negative"])
def test_set_burst_period_rejects_non_positive_value(driver: RigolDG1022Z, period: float) -> None:
    with pytest.raises(ValueError, match="period must be positive"):
        driver.set_burst_period(1, period)


def test_set_burst_period_rejects_invalid_channel(driver: RigolDG1022Z) -> None:
    with pytest.raises(ValueError, match="channel must be 1 or 2"):
        driver.set_burst_period(INVALID_CHANNEL, 0.1)


def test_get_burst_period_rejects_invalid_channel(driver: RigolDG1022Z) -> None:
    with pytest.raises(ValueError, match="channel must be 1 or 2"):
        driver.get_burst_period(INVALID_CHANNEL)

    driver._check_errors()


# ---------------------------------------------------------------------------
# Cross-method integration
# ---------------------------------------------------------------------------


def test_full_burst_lifecycle_ncycle_manual_trigger(driver: RigolDG1022Z) -> None:
    """set_burst -> ncycles -> period -> gate polarity (unused but shouldn't interfere) -> trigger(MANUAL) -> enable -> fire."""
    driver.set_waveform(1, Square(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_burst(1, BurstType.NCYCLE)
    driver.set_burst_ncycles(1, 3)
    driver.set_burst_period(1, 0.05)
    driver.set_burst_gate_polarity(1, GatePolarity.NORM)
    driver.set_burst_trigger(1, BurstTriggerSource.MANUAL)
    driver._check_errors()

    assert driver.get_burst_type(1) is BurstType.NCYCLE
    assert driver.get_burst_ncycles(1) == 3
    assert driver.get_burst_period(1) == pytest.approx(0.05, rel=1e-3)
    assert driver.get_burst_trigger(1) is BurstTriggerSource.MANUAL

    driver.burst_enable(1, True)
    driver._check_errors()
    assert driver.get_burst_state(1) is True

    driver.output_enable(1, True)
    driver.fire_burst_trigger(1)
    driver._check_errors()
    driver.output_enable(1, False)

    driver.burst_enable(1, False)
    driver._check_errors()
    assert driver.get_burst_state(1) is False


def test_full_burst_lifecycle_gated_external_trigger(driver: RigolDG1022Z) -> None:
    """set_burst(GATED) -> gate polarity -> explicit trigger source rejected -> burst still enables."""
    driver.set_waveform(1, Square(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_burst(1, BurstType.GATED)
    driver.set_burst_gate_polarity(1, GatePolarity.INV)
    driver._check_errors()

    with pytest.raises(ValueError, match="GATED burst mode"):
        driver.set_burst_trigger(1, BurstTriggerSource.EXTERNAL)

    driver.burst_enable(1, True)
    driver._check_errors()
    assert driver.get_burst_state(1) is True
    assert driver.get_burst_type(1) is BurstType.GATED

    driver.burst_enable(1, False)
    driver._check_errors()
