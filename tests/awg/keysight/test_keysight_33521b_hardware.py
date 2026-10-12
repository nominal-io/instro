"""Keysight 33521B hardware smoke tests."""

from __future__ import annotations

import sys
from collections.abc import Iterator

import pytest

from instro.awg.drivers import Keysight33521B
from instro.awg.types import (
    AmplitudeMeasurementUnit,
    Arbitrary,
    BurstTriggerSource,
    BurstType,
    GatePolarity,
    ModulationType,
    Pulse,
    Sawtooth,
    Sine,
    Square,
    StaticValue,
    SweepTriggerSource,
    SweepType,
    Triangle,
    Waveform,
)
from instro.lib.transports import VisaConfig

pytestmark = pytest.mark.hardware

# HARDWARE TEST SETUP - EDIT THESE VALUES BEFORE RUNNING THIS FILE.
# Set VISA_RESOURCE to the bench unit's VISA resource string (front-panel Utility >
# I/O Config shows the current LAN address/hostname or USB VISA alias). Set VISA_BACKEND
# to "@ivi" or "" for the system VISA library, or "@py" for pyvisa-py.
VISA_RESOURCE = "USB0::0x0957::0x2B07::MY52702203::INSTR"

VISA_BACKEND = None
CHANNEL = 1
INVALID_CHANNEL = 2

TEST_FREQUENCY_HZ = 1000.0
TEST_AMPLITUDE_VPP = 1.0
TEST_AMPLITUDE_VRMS = 0.25
TEST_OFFSET_V = 0.25
FREQUENCY_TOLERANCE_REL = 1e-4
AMPLITUDE_TOLERANCE_REL = 0.01
PHASE_TOLERANCE_DEG = 0.1

_ARB_SAMPLES = (0.0, 0.5, 1.0, 0.5, 0.0, -0.5, -1.0, -0.5, 0.25)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _shutdown_output(driver: Keysight33521B) -> None:
    driver.output_enable(CHANNEL, False)


def _reset_driver(driver: Keysight33521B) -> None:
    driver._visa.write("*CLS")
    driver._visa.write("*RST")
    driver._visa.write("*CLS")
    driver._check_errors()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def driver() -> Iterator[Keysight33521B]:
    awg_driver = Keysight33521B(
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
            _shutdown_output(awg_driver)
        awg_driver.close()


@pytest.fixture(autouse=True)
def reset_before_each_test(driver: Keysight33521B) -> None:
    _reset_driver(driver)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_01_connect_to_awg(driver: Keysight33521B) -> None:
    idn = driver._visa.query("*IDN?")
    print(f"\nIDN: {idn.strip()}")

    assert "33" in idn
    driver._check_errors()


def test_02_cycle_through_waveforms(driver: Keysight33521B) -> None:
    waveforms: list[Waveform] = [
        Sine(frequency_hz=TEST_FREQUENCY_HZ),
        Square(frequency_hz=TEST_FREQUENCY_HZ, duty_cycle_pct=30.0),
        Sawtooth(frequency_hz=TEST_FREQUENCY_HZ),
        Triangle(frequency_hz=TEST_FREQUENCY_HZ),
        Pulse(frequency_hz=TEST_FREQUENCY_HZ, width_s=0.0002),
        Arbitrary(samples=_ARB_SAMPLES, sample_rate_sas=100_000.0),
        StaticValue(value=TEST_OFFSET_V),
    ]

    for waveform in waveforms:
        driver.set_waveform(CHANNEL, waveform)
        driver._check_errors()
        readback = driver.get_waveform(CHANNEL)
        assert type(readback) is type(waveform), f"programmed {waveform}, read back {readback}"


def test_03_sine_frequency_and_phase_roundtrip(driver: Keysight33521B) -> None:
    driver.set_waveform(CHANNEL, Sine(frequency_hz=TEST_FREQUENCY_HZ, phase_deg=90.0))
    driver._check_errors()

    readback = driver.get_waveform(CHANNEL)
    assert isinstance(readback, Sine)
    assert readback.frequency_hz == pytest.approx(TEST_FREQUENCY_HZ, rel=FREQUENCY_TOLERANCE_REL)
    assert readback.phase_deg == pytest.approx(90.0, abs=PHASE_TOLERANCE_DEG)


def test_04_square_duty_cycle_roundtrip(driver: Keysight33521B) -> None:
    driver.set_waveform(CHANNEL, Square(frequency_hz=TEST_FREQUENCY_HZ, duty_cycle_pct=30.0))
    driver._check_errors()

    readback = driver.get_waveform(CHANNEL)
    assert isinstance(readback, Square)
    assert readback.duty_cycle_pct == pytest.approx(30.0, rel=0.01)


def test_05_sawtooth_readback(driver: Keysight33521B) -> None:
    driver.set_waveform(CHANNEL, Sawtooth(frequency_hz=TEST_FREQUENCY_HZ))
    driver._check_errors()

    readback = driver.get_waveform(CHANNEL)
    assert isinstance(readback, Sawtooth)
    assert readback.frequency_hz == pytest.approx(TEST_FREQUENCY_HZ, rel=FREQUENCY_TOLERANCE_REL)


def test_06_triangle_readback(driver: Keysight33521B) -> None:
    driver.set_waveform(CHANNEL, Triangle(frequency_hz=TEST_FREQUENCY_HZ))
    driver._check_errors()

    readback = driver.get_waveform(CHANNEL)
    assert isinstance(readback, Triangle)
    assert readback.frequency_hz == pytest.approx(TEST_FREQUENCY_HZ, rel=FREQUENCY_TOLERANCE_REL)


def test_07_pulse_width_roundtrip(driver: Keysight33521B) -> None:
    driver.set_waveform(CHANNEL, Pulse(frequency_hz=TEST_FREQUENCY_HZ, width_s=0.0002))
    driver._check_errors()

    readback = driver.get_waveform(CHANNEL)
    assert isinstance(readback, Pulse)
    assert readback.frequency_hz == pytest.approx(TEST_FREQUENCY_HZ, rel=FREQUENCY_TOLERANCE_REL)
    assert readback.width_s == pytest.approx(0.0002, rel=0.01)


def test_08_pulse_delay_roundtrip(driver: Keysight33521B) -> None:
    driver.set_waveform(CHANNEL, Pulse(frequency_hz=TEST_FREQUENCY_HZ, width_s=0.0002, delay_s=0.0001))
    driver._check_errors()

    readback = driver.get_waveform(CHANNEL)
    assert isinstance(readback, Pulse)
    assert readback.delay_s == pytest.approx(0.0001, rel=0.01)


def test_09_static_value_roundtrip(driver: Keysight33521B) -> None:
    driver.set_waveform(CHANNEL, StaticValue(value=TEST_OFFSET_V))
    driver._check_errors()

    readback = driver.get_waveform(CHANNEL)
    assert isinstance(readback, StaticValue)
    assert readback.value == pytest.approx(TEST_OFFSET_V, rel=0.01)


def test_10_arbitrary_download_and_cached_readback(driver: Keysight33521B) -> None:
    arbitrary = Arbitrary(samples=_ARB_SAMPLES, sample_rate_sas=100_000.0)
    driver.set_waveform(CHANNEL, arbitrary)
    driver._check_errors()

    readback = driver.get_waveform(CHANNEL)
    assert readback is arbitrary


def test_11_amplitude_vpp_roundtrip(driver: Keysight33521B) -> None:
    driver.set_waveform(CHANNEL, Sine(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_amplitude(CHANNEL, TEST_AMPLITUDE_VPP, AmplitudeMeasurementUnit.VPP)
    driver._check_errors()

    amplitude, unit = driver.get_amplitude(CHANNEL)
    assert unit is AmplitudeMeasurementUnit.VPP
    assert amplitude == pytest.approx(TEST_AMPLITUDE_VPP, rel=AMPLITUDE_TOLERANCE_REL)


def test_12_amplitude_vrms_roundtrip(driver: Keysight33521B) -> None:
    driver.set_waveform(CHANNEL, Sine(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_amplitude(CHANNEL, TEST_AMPLITUDE_VRMS, AmplitudeMeasurementUnit.VRMS)
    driver._check_errors()

    amplitude, unit = driver.get_amplitude(CHANNEL)
    assert unit is AmplitudeMeasurementUnit.VRMS
    assert amplitude == pytest.approx(TEST_AMPLITUDE_VRMS, rel=AMPLITUDE_TOLERANCE_REL)


def test_13_amplitude_vp_unit_rejected(driver: Keysight33521B) -> None:
    with pytest.raises(ValueError, match="VP is not supported"):
        driver.set_amplitude(CHANNEL, TEST_AMPLITUDE_VPP, AmplitudeMeasurementUnit.VP)

    driver._check_errors()


def test_14_offset_roundtrip(driver: Keysight33521B) -> None:
    driver.set_waveform(CHANNEL, Sine(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_amplitude(CHANNEL, TEST_AMPLITUDE_VPP, AmplitudeMeasurementUnit.VPP)
    driver.set_offset(CHANNEL, TEST_OFFSET_V)
    driver._check_errors()

    assert driver.get_offset(CHANNEL) == pytest.approx(TEST_OFFSET_V, rel=0.01)


def test_15_output_enable_toggle(driver: Keysight33521B) -> None:
    driver.set_waveform(CHANNEL, Sine(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_amplitude(CHANNEL, TEST_AMPLITUDE_VPP, AmplitudeMeasurementUnit.VPP)
    try:
        driver.output_enable(CHANNEL, True)
        driver._check_errors()
        assert driver.get_output_state(CHANNEL) is True
    finally:
        driver.output_enable(CHANNEL, False)

    assert driver.get_output_state(CHANNEL) is False


def test_16_output_load_50_ohm_roundtrip(driver: Keysight33521B) -> None:
    driver.set_output_load(CHANNEL, 50.0)
    driver._check_errors()

    assert driver.get_output_load(CHANNEL) == pytest.approx(50.0)


def test_17_output_load_high_z_roundtrip(driver: Keysight33521B) -> None:
    driver.set_output_load(CHANNEL, None)
    driver._check_errors()

    assert driver.get_output_load(CHANNEL) is None


def test_18_invalid_channel_rejected(driver: Keysight33521B) -> None:
    with pytest.raises(ValueError, match="only supports 1 channel"):
        driver.set_waveform(INVALID_CHANNEL, Sine(frequency_hz=TEST_FREQUENCY_HZ))

    driver._check_errors()


def test_19_check_errors_raises_after_invalid_command(driver: Keysight33521B) -> None:
    driver._visa.write("INSTRO:INVALID")

    try:
        with pytest.raises(RuntimeError, match="Keysight 33521B reported error"):
            driver._check_errors()
    finally:
        driver._visa.write("*CLS")


# ---------------------------------------------------------------------------
# Modulation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mod_type", "prefix", "carrier", "shape", "magnitude"),
    [
        (ModulationType.AM, "AM", Sine(frequency_hz=TEST_FREQUENCY_HZ), Sine(frequency_hz=100.0), 50.0),
        (
            ModulationType.PWM,
            "PWM",
            Pulse(frequency_hz=TEST_FREQUENCY_HZ, width_s=0.0002),
            Square(frequency_hz=100.0),
            50e-6,
        ),
        (ModulationType.PSK, "BPSK", Sine(frequency_hz=TEST_FREQUENCY_HZ), Triangle(frequency_hz=100.0), 90.0),
    ],
    ids=["am", "pwm", "psk"],
)
def test_20_set_modulation_enables_does_not_enable(
    driver: Keysight33521B,
    mod_type: ModulationType,
    prefix: str,
    carrier: Waveform,
    shape: Waveform,
    magnitude: float,
) -> None:
    driver.set_waveform(CHANNEL, carrier)
    driver.set_modulation(CHANNEL, mod_type, shape, magnitude)
    driver._check_errors()

    assert driver._visa.query(f"{prefix}:STAT?").strip() == "0"
    assert driver.get_modulation_type(CHANNEL) == mod_type
    assert driver.get_modulation_state(CHANNEL) is False

    driver.modulation_enable(CHANNEL, True)
    driver._check_errors()

    assert driver._visa.query(f"{prefix}:STAT?").strip() == "1"
    assert driver.get_modulation_state(CHANNEL) is True
    driver.modulation_enable(CHANNEL, False)
    driver._check_errors()


def test_21_modulation_enable_true_without_prior_configuration_raises(driver: Keysight33521B) -> None:
    """Order: modulation_enable(True) before any set_modulation call on a freshly reset instrument.

    *RST clears hardware modulation state but not the driver's software-side cache, so the cache is
    cleared here directly to simulate a driver that has never had set_modulation called on it -- the
    `driver` fixture is module-scoped and earlier tests (e.g. test_20) leave it populated.
    """
    driver._last_modulation_type = None

    with pytest.raises(RuntimeError, match="no modulation type currently configured"):
        driver.modulation_enable(CHANNEL, True)

    driver._check_errors()


def test_22_disable_then_reenable_uses_the_cached_type_across_a_type_switch(driver: Keysight33521B) -> None:
    """Order: set_modulation -> enable -> disable -> get_modulation_type -> set_modulation (new type) -> enable.

    Confirms the cached type survives a disable (so get_modulation_type keeps reporting it instead of
    raising), and that switching to a different modulation type while the old one is enabled leaves the
    instrument with only the newly enabled type active on hardware.
    """
    driver.set_waveform(CHANNEL, Sine(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_modulation(CHANNEL, ModulationType.FSK, Sawtooth(frequency_hz=100.0), 2000.0)
    driver.modulation_enable(CHANNEL, True)
    driver._check_errors()
    assert driver._visa.query("FSK:STAT?").strip() == "1"

    driver.modulation_enable(CHANNEL, False)
    driver._check_errors()
    assert driver._visa.query("FSK:STAT?").strip() == "0"
    assert driver.get_modulation_type(CHANNEL) == ModulationType.FSK

    driver.set_modulation(CHANNEL, ModulationType.PSK, Triangle(frequency_hz=100.0), 90.0)
    driver.modulation_enable(CHANNEL, True)
    driver._check_errors()

    assert driver._visa.query("FSK:STAT?").strip() == "0"
    assert driver._visa.query("BPSK:STAT?").strip() == "1"
    assert driver.get_modulation_type(CHANNEL) == ModulationType.PSK

    driver.modulation_enable(CHANNEL, False)
    driver._check_errors()


def test_23_only_one_modulation_type_enabled_at_a_time(driver: Keysight33521B) -> None:
    driver.set_waveform(CHANNEL, Sine(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_modulation(CHANNEL, ModulationType.FSK, Sawtooth(frequency_hz=100.0), 2000.0)
    driver.modulation_enable(CHANNEL, True)
    driver._check_errors()
    assert driver._visa.query("FSK:STAT?").strip() == "1"
    driver._check_errors()

    driver.set_modulation(CHANNEL, ModulationType.PSK, Triangle(frequency_hz=100.0), 90.0)
    driver.modulation_enable(CHANNEL, True)
    driver._check_errors()

    assert driver._visa.query("BPSK:STAT?").strip() == "1"
    assert driver.get_modulation_type(CHANNEL) == ModulationType.PSK

    driver.modulation_enable(CHANNEL, False)
    driver._check_errors()


def test_24_modulation_enable_persists_after_set_modulation(driver: Keysight33521B) -> None:
    driver.set_waveform(CHANNEL, Sine(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_modulation(CHANNEL, ModulationType.FSK, Sawtooth(frequency_hz=100.0), 2000.0)
    driver.modulation_enable(CHANNEL, True)
    driver._check_errors()
    assert driver.get_modulation_state(CHANNEL) is True
    assert driver.get_modulation_type(CHANNEL) == ModulationType.FSK
    driver._check_errors()

    driver.set_modulation(CHANNEL, ModulationType.PSK, Triangle(frequency_hz=100.0), 90.0)
    driver._check_errors()

    assert driver.get_modulation_state(CHANNEL) is True
    assert driver.get_modulation_type(CHANNEL) == ModulationType.PSK

    driver.modulation_enable(CHANNEL, False)
    driver._check_errors()


# ---------------------------------------------------------------------------
# Burst
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "carrier",
    [
        Sine(frequency_hz=TEST_FREQUENCY_HZ),
        Square(frequency_hz=TEST_FREQUENCY_HZ),
        Sawtooth(frequency_hz=TEST_FREQUENCY_HZ),
        Triangle(frequency_hz=TEST_FREQUENCY_HZ),
        Pulse(frequency_hz=TEST_FREQUENCY_HZ, width_s=0.0002),
        Arbitrary(samples=_ARB_SAMPLES, sample_rate_sas=100_000.0),
    ],
    ids=["sine", "square", "sawtooth", "triangle", "pulse", "arbitrary"],
)
def test_25_set_burst_ncycle_on_every_valid_carrier(driver: Keysight33521B, carrier: Waveform) -> None:
    driver.set_waveform(CHANNEL, carrier)
    driver.set_burst(CHANNEL, BurstType.NCYCLE)
    driver._check_errors()

    assert driver.get_burst_type(CHANNEL) is BurstType.NCYCLE

    driver.burst_enable(CHANNEL, True)
    driver._check_errors()
    assert driver.get_burst_state(CHANNEL) is True

    driver.burst_enable(CHANNEL, False)
    driver._check_errors()
    assert driver.get_burst_state(CHANNEL) is False


def test_26_set_burst_rejects_staticvalue_carrier(driver: Keysight33521B) -> None:
    driver.set_waveform(CHANNEL, StaticValue(value=TEST_OFFSET_V))

    with pytest.raises(ValueError, match="cannot burst a StaticValue"):
        driver.set_burst(CHANNEL, BurstType.NCYCLE)
    driver._check_errors()


def test_27_set_burst_accepts_untracked_arbitrary_carrier(driver: Keysight33521B) -> None:
    """Regression: pop the cache to simulate a driver instance that never downloaded this Arbitrary."""
    driver.set_waveform(CHANNEL, Arbitrary(samples=_ARB_SAMPLES, sample_rate_sas=100_000.0))
    driver._check_errors()
    driver._arb_waveforms.pop(CHANNEL)

    driver.set_burst(CHANNEL, BurstType.NCYCLE)
    driver._check_errors()

    assert driver.get_burst_type(CHANNEL) is BurstType.NCYCLE


@pytest.mark.parametrize(
    "burst_type", [BurstType.NCYCLE, BurstType.GATED, BurstType.INFINITE], ids=["ncycle", "gated", "infinite"]
)
def test_28_get_burst_type_matches_configured_type(driver: Keysight33521B, burst_type: BurstType) -> None:
    driver.set_waveform(CHANNEL, Square(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_burst(CHANNEL, burst_type)
    driver._check_errors()

    assert driver.get_burst_type(CHANNEL) is burst_type


def test_29_set_burst_infinite_reports_infinite_type_and_max_ncycles(driver: Keysight33521B) -> None:
    """get_burst_type infers INFINITE from the BURS:NCYC sentinel; the 33521B has no third BURS:MODE value."""
    driver.set_waveform(CHANNEL, Square(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_burst(CHANNEL, BurstType.INFINITE)
    driver._check_errors()

    assert driver.get_burst_type(CHANNEL) is BurstType.INFINITE
    assert driver.get_burst_ncycles(CHANNEL) == sys.maxsize


def test_30_set_burst_infinite_rejects_staticvalue_carrier(driver: Keysight33521B) -> None:
    driver.set_waveform(CHANNEL, StaticValue(value=TEST_OFFSET_V))

    with pytest.raises(ValueError, match="cannot burst a StaticValue"):
        driver.set_burst(CHANNEL, BurstType.INFINITE)
    driver._check_errors()


def test_31_set_burst_ncycle_leaves_a_prior_infinite_sentinel_until_ncycles_is_set(driver: Keysight33521B) -> None:
    """set_burst(NCYCLE) only rewrites BURS:MODE; a leftover BURS:NCYC INF is the caller's to clear."""
    driver.set_waveform(CHANNEL, Square(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_burst(CHANNEL, BurstType.INFINITE)
    driver._check_errors()
    assert driver.get_burst_ncycles(CHANNEL) == sys.maxsize

    driver.set_burst(CHANNEL, BurstType.NCYCLE)
    driver._check_errors()
    assert driver.get_burst_type(CHANNEL) is BurstType.INFINITE

    driver.set_burst_ncycles(CHANNEL, 10)
    driver._check_errors()

    assert driver.get_burst_type(CHANNEL) is BurstType.NCYCLE
    assert driver.get_burst_ncycles(CHANNEL) == 10

    assert driver.get_burst_ncycles(CHANNEL) == 10


@pytest.mark.parametrize(
    "source",
    [BurstTriggerSource.INTERNAL, BurstTriggerSource.EXTERNAL, BurstTriggerSource.MANUAL],
    ids=["internal", "external", "manual"],
)
def test_32_burst_trigger_roundtrip_matches_configured_source(
    driver: Keysight33521B, source: BurstTriggerSource
) -> None:
    """Confirms the driver's IMM/EXT/BUS <-> INTERNAL/EXTERNAL/MANUAL mapping against real TRIGger:SOURce state."""
    driver.set_waveform(CHANNEL, Square(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_burst(CHANNEL, BurstType.NCYCLE)
    driver._check_errors()

    driver.set_burst_trigger(CHANNEL, source)
    driver._check_errors()

    assert driver.get_burst_trigger(CHANNEL) is source


def test_33_set_burst_trigger_rejects_invalid_channel(driver: Keysight33521B) -> None:
    with pytest.raises(ValueError, match="only supports 1 channel"):
        driver.set_burst_trigger(INVALID_CHANNEL, BurstTriggerSource.MANUAL)

    driver._check_errors()


@pytest.mark.parametrize(
    "source",
    [BurstTriggerSource.INTERNAL, BurstTriggerSource.EXTERNAL, BurstTriggerSource.MANUAL],
    ids=["internal", "external", "manual"],
)
def test_34_set_burst_trigger_in_gated_mode(driver: Keysight33521B, source: BurstTriggerSource) -> None:
    """Untested gap: confirms whether GATED mode accepts a trigger source change, or rejects it like Rigol."""
    driver.set_waveform(CHANNEL, Square(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_burst(CHANNEL, BurstType.GATED)
    driver._check_errors()

    driver.set_burst_trigger(CHANNEL, source)
    driver._check_errors()

    assert driver.get_burst_trigger(CHANNEL) is source

    driver._check_errors()


def test_35_fire_burst_trigger_fires_when_source_already_manual(driver: Keysight33521B) -> None:
    """*TRG only fires once TRIGger:SOURce is BUS (mapped from BurstTriggerSource.MANUAL)."""
    driver.set_waveform(CHANNEL, Square(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_burst(CHANNEL, BurstType.NCYCLE)
    driver.set_burst_trigger(CHANNEL, BurstTriggerSource.MANUAL)
    driver._check_errors()

    driver.output_enable(CHANNEL, True)
    driver.burst_enable(CHANNEL, True)
    driver.fire_burst_trigger(CHANNEL)
    driver._check_errors()

    driver.output_enable(CHANNEL, False)
    driver.burst_enable(CHANNEL, False)


def test_36_fire_burst_trigger_rejects_non_manual_source_and_when_not_enabled(driver: Keysight33521B) -> None:
    driver.set_waveform(CHANNEL, Square(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_burst(CHANNEL, BurstType.NCYCLE)
    driver.set_burst_trigger(CHANNEL, BurstTriggerSource.EXTERNAL)
    driver.burst_enable(CHANNEL, True)
    driver._check_errors()

    with pytest.raises(ValueError, match="already MANUAL"):
        driver.fire_burst_trigger(CHANNEL)

    driver.burst_enable(CHANNEL, False)
    driver._check_errors()

    with pytest.raises(ValueError, match="burst mode is already"):
        driver.fire_burst_trigger(CHANNEL)

    driver._check_errors()


def test_37_fire_burst_trigger_rejects_gated_mode_before_touching_hardware(driver: Keysight33521B) -> None:
    """Software-side NCYCLE guard rejects GATED before *TRG is ever written, ahead of the -211 hardware would raise."""
    driver.set_waveform(CHANNEL, Square(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_burst(CHANNEL, BurstType.GATED)
    driver.set_burst_trigger(CHANNEL, BurstTriggerSource.MANUAL)
    driver.burst_enable(CHANNEL, True)
    driver._check_errors()

    with pytest.raises(ValueError, match="fires a single N-cycle burst"):
        driver.fire_burst_trigger(CHANNEL)

    # No SCPI error should be queued: the guard fired before *TRG was ever written.
    driver._check_errors()

    driver.burst_enable(CHANNEL, False)


def test_38_fire_burst_trigger_fires_when_configured_via_infinite(driver: Keysight33521B) -> None:
    """The NCYCLE guard also allows INFINITE, since firing is wire-identical to a plain NCYCLE burst."""
    driver.set_waveform(CHANNEL, Square(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_burst(CHANNEL, BurstType.INFINITE)
    driver.set_burst_trigger(CHANNEL, BurstTriggerSource.MANUAL)
    driver._check_errors()

    driver.output_enable(CHANNEL, True)
    driver.burst_enable(CHANNEL, True)
    driver.fire_burst_trigger(CHANNEL)
    driver._check_errors()

    driver.output_enable(CHANNEL, False)
    driver.burst_enable(CHANNEL, False)


def test_39_burst_delay_roundtrip_uses_shared_trigger_delay_node(driver: Keysight33521B) -> None:
    """The 33521B has no BURSt:TDELay; TRIGger:DELay is the shared burst/sweep/list trigger delay."""
    driver.set_burst_delay(CHANNEL, 0.001)
    driver._check_errors()

    assert driver.get_burst_delay(CHANNEL) == pytest.approx(0.001, rel=0.01)


def test_40_set_burst_delay_rejects_negative_value(driver: Keysight33521B) -> None:
    with pytest.raises(ValueError, match="delay_s must be non-negative"):
        driver.set_burst_delay(CHANNEL, -0.1)

    driver._check_errors()


@pytest.mark.parametrize("gate_polarity", [GatePolarity.NORM, GatePolarity.INV], ids=["norm", "inv"])
def test_41_burst_gate_polarity_roundtrip(driver: Keysight33521B, gate_polarity: GatePolarity) -> None:
    driver.set_burst_gate_polarity(CHANNEL, gate_polarity)
    driver._check_errors()

    assert driver.get_burst_gate_polarity(CHANNEL) is gate_polarity


def test_42_burst_ncycles_roundtrip(driver: Keysight33521B) -> None:
    driver.set_waveform(CHANNEL, Square(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_burst(CHANNEL, BurstType.NCYCLE)
    driver.set_burst_ncycles(CHANNEL, 10)
    driver._check_errors()

    assert driver.get_burst_ncycles(CHANNEL) == 10


def test_43_set_burst_ncycles_rejects_non_positive_value(driver: Keysight33521B) -> None:
    with pytest.raises(ValueError, match="n_cycles must be >= 1"):
        driver.set_burst_ncycles(CHANNEL, 0)

    driver._check_errors()


def test_44_set_burst_ncycles_silently_rounds_non_integer_value(driver: Keysight33521B) -> None:
    """Hardware-confirmed: BURS:NCYC 10.5 is accepted with no SCPI error and silently rounds to 10."""
    driver.set_waveform(CHANNEL, Square(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_burst(CHANNEL, BurstType.NCYCLE)

    # driver.set_burst_ncycles(CHANNEL, 10.5)  # type: ignore[arg-type]
    driver._visa.write("BURS:NCYC 10.5")
    driver._check_errors()

    assert driver.get_burst_ncycles(CHANNEL) == 10


def test_45_burst_period_roundtrip(driver: Keysight33521B) -> None:
    driver.set_waveform(CHANNEL, Square(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_burst(CHANNEL, BurstType.NCYCLE)
    driver.set_burst_period(CHANNEL, 0.05)
    driver._check_errors()

    assert driver.get_burst_period(CHANNEL) == pytest.approx(0.05, rel=0.01)


def test_46_set_burst_period_rejects_non_positive_value(driver: Keysight33521B) -> None:
    with pytest.raises(ValueError, match="period must be positive"):
        driver.set_burst_period(CHANNEL, 0.0)

    driver._check_errors()


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "carrier",
    [
        Sine(frequency_hz=TEST_FREQUENCY_HZ),
        Square(frequency_hz=TEST_FREQUENCY_HZ),
        Sawtooth(frequency_hz=TEST_FREQUENCY_HZ),
        Triangle(frequency_hz=TEST_FREQUENCY_HZ),
        Pulse(frequency_hz=TEST_FREQUENCY_HZ, width_s=0.0002),
        Arbitrary(samples=_ARB_SAMPLES, sample_rate_sas=100_000.0),
    ],
    ids=["sine", "square", "sawtooth", "triangle", "pulse", "arbitrary"],
)
def test_25_set_sweep_linear_on_every_valid_carrier(driver: Keysight33521B, carrier: Waveform) -> None:
    driver.set_waveform(CHANNEL, carrier)
    driver.set_sweep(CHANNEL, SweepType.LINEAR)
    driver._check_errors()

    assert driver.get_sweep_type(CHANNEL) is SweepType.LINEAR

    driver.sweep_enable(CHANNEL, True)
    driver._check_errors()
    assert driver.get_sweep_state(CHANNEL) is True

    driver.sweep_enable(CHANNEL, False)
    driver._check_errors()
    assert driver.get_sweep_state(CHANNEL) is False


def test_26_set_sweep_rejects_step_spacing(driver: Keysight33521B) -> None:
    driver.set_waveform(CHANNEL, Sine(frequency_hz=TEST_FREQUENCY_HZ))
    with pytest.raises(ValueError, match="does not support STEP sweep spacing"):
        driver.set_sweep(CHANNEL, SweepType.STEP)
    driver._check_errors()


@pytest.mark.parametrize("sweep_type", [SweepType.LINEAR, SweepType.LOG], ids=["linear", "log"])
def test_27_get_sweep_type_matches_configured_type(driver: Keysight33521B, sweep_type: SweepType) -> None:
    driver.set_waveform(CHANNEL, Sine(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_sweep(CHANNEL, sweep_type)
    driver._check_errors()

    assert driver.get_sweep_type(CHANNEL) is sweep_type


def test_28_sweep_enable_actually_drives_frequency_mode(driver: Keysight33521B) -> None:
    """Bench check for a manual ambiguity: does SWEep:STATe ON actually alias to FREQuency:MODE SWEep?"""
    driver.set_waveform(CHANNEL, Sine(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_sweep(CHANNEL, SweepType.LINEAR)
    driver.sweep_enable(CHANNEL, True)
    driver._check_errors()

    # The SWEep tutorial ends in SWEep:STATe ON; the FREQuency tutorial enables sweep via
    # FREQuency:MODE SWEep alone and never touches SWEep:STATe. This assumes the two are
    # aliased, mirroring how BURSt:STATe behaves elsewhere on this instrument.
    freq_mode = driver._visa.query("FREQ:MODE?").strip()
    assert freq_mode == "SWE", (
        f"SWEep:STATe ON did not switch FREQuency:MODE to SWEep (got {freq_mode!r} instead);"
        " sweep_enable/get_sweep_state must be reimplemented against FREQuency:MODE"
    )

    driver.sweep_enable(CHANNEL, False)
    driver._check_errors()


@pytest.mark.parametrize(
    "source",
    [SweepTriggerSource.INTERNAL, SweepTriggerSource.EXTERNAL, SweepTriggerSource.MANUAL],
    ids=["internal", "external", "manual"],
)
def test_29_sweep_trigger_roundtrip_matches_configured_source(
    driver: Keysight33521B, source: SweepTriggerSource
) -> None:
    """Confirms the driver's IMM/EXT/BUS <-> INTERNAL/EXTERNAL/MANUAL mapping against real TRIGger:SOURce state."""
    driver.set_waveform(CHANNEL, Sine(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_sweep(CHANNEL, SweepType.LINEAR)
    driver._check_errors()

    driver.set_sweep_trigger(CHANNEL, source)
    driver._check_errors()

    assert driver.get_sweep_trigger(CHANNEL) is source


def test_30_set_sweep_trigger_rejects_invalid_channel(driver: Keysight33521B) -> None:
    with pytest.raises(ValueError, match="only supports 1 channel"):
        driver.set_sweep_trigger(INVALID_CHANNEL, SweepTriggerSource.MANUAL)

    driver._check_errors()


def test_31_fire_sweep_trigger_fires_when_source_already_manual(driver: Keysight33521B) -> None:
    """*TRG only fires once TRIGger:SOURce is BUS (mapped from SweepTriggerSource.MANUAL)."""
    driver.set_waveform(CHANNEL, Sine(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_sweep(CHANNEL, SweepType.LINEAR)
    driver.set_sweep_trigger(CHANNEL, SweepTriggerSource.MANUAL)
    driver._check_errors()

    driver.output_enable(CHANNEL, True)
    driver.sweep_enable(CHANNEL, True)
    driver.fire_sweep_trigger(CHANNEL)
    driver._check_errors()

    driver.output_enable(CHANNEL, False)
    driver.sweep_enable(CHANNEL, False)


def test_32_fire_sweep_trigger_rejects_non_manual_source_and_when_not_enabled(driver: Keysight33521B) -> None:
    driver.set_waveform(CHANNEL, Sine(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_sweep(CHANNEL, SweepType.LINEAR)
    driver.set_sweep_trigger(CHANNEL, SweepTriggerSource.EXTERNAL)
    driver.sweep_enable(CHANNEL, True)
    driver._check_errors()

    with pytest.raises(ValueError, match="already MANUAL"):
        driver.fire_sweep_trigger(CHANNEL)

    driver.sweep_enable(CHANNEL, False)
    driver._check_errors()

    with pytest.raises(ValueError, match="sweep mode is already"):
        driver.fire_sweep_trigger(CHANNEL)

    driver._check_errors()


def test_33_sweep_start_end_freq_roundtrip(driver: Keysight33521B) -> None:
    driver.set_waveform(CHANNEL, Sine(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_sweep_start_freq(CHANNEL, 100.0)
    driver.set_sweep_end_freq(CHANNEL, 1000.0)
    driver._check_errors()

    assert driver.get_sweep_start_freq(CHANNEL) == pytest.approx(100.0, rel=FREQUENCY_TOLERANCE_REL)
    assert driver.get_sweep_end_freq(CHANNEL) == pytest.approx(1000.0, rel=FREQUENCY_TOLERANCE_REL)


def test_34_sweep_time_roundtrip(driver: Keysight33521B) -> None:
    driver.set_waveform(CHANNEL, Sine(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_sweep_time(CHANNEL, 0.5)
    driver._check_errors()

    assert driver.get_sweep_time(CHANNEL) == pytest.approx(0.5, rel=0.01)


def test_35_set_sweep_time_rejects_non_positive_value(driver: Keysight33521B) -> None:
    with pytest.raises(ValueError, match="sweep_time must be positive"):
        driver.set_sweep_time(CHANNEL, 0.0)

    driver._check_errors()


def test_36_sweep_stop_hold_and_return_time_roundtrip(driver: Keysight33521B) -> None:
    driver.set_waveform(CHANNEL, Sine(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_sweep_stop_hold_time(CHANNEL, 3.4)
    driver.set_sweep_return_time(CHANNEL, 5.6)
    driver._check_errors()

    assert driver.get_sweep_stop_hold_time(CHANNEL) == pytest.approx(3.4, rel=0.01)
    assert driver.get_sweep_return_time(CHANNEL) == pytest.approx(5.6, rel=0.01)


def test_37_set_sweep_stop_hold_and_return_time_reject_negative_value(driver: Keysight33521B) -> None:
    with pytest.raises(ValueError, match="hold_time must be non-negative"):
        driver.set_sweep_stop_hold_time(CHANNEL, -0.1)
    driver._check_errors()

    with pytest.raises(ValueError, match="return_time must be non-negative"):
        driver.set_sweep_return_time(CHANNEL, -0.1)
    driver._check_errors()


def test_38_set_sweep_accepts_staticvalue_but_enable_is_rejected_by_hardware(driver: Keysight33521B) -> None:
    """Hardware-confirmed: SWE:SPAC accepts a DC carrier, but SWE:STAT ON on it raises -221 "Settings conflict"."""
    driver.set_waveform(CHANNEL, StaticValue(value=TEST_OFFSET_V))
    driver.set_sweep(CHANNEL, SweepType.LINEAR)
    driver._check_errors()

    assert driver.get_sweep_type(CHANNEL) is SweepType.LINEAR

    with pytest.raises(RuntimeError, match=r'-221,"Settings conflict; not able to sweep this function"'):
        driver.sweep_enable(CHANNEL, True)
