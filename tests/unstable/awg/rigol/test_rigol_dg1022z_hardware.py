"""Rigol DG1022Z hardware smoke tests.

One to two tests per feature: one valid sequence, one invalid/error-check sequence. Where no
invalid input exists, the second test covers a distinct valid path or a hardware-only stress
case instead of a contrived failure.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest

from instro.lib.transports import VisaConfig
from instro.unstable.awg.drivers import RigolDG1022Z
from instro.unstable.awg.types import (
    AmplitudeMeasurementUnit,
    Arbitrary,
    BurstTriggerSource,
    BurstType,
    ModulationType,
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
# Set VISA_RESOURCE to the bench unit's VISA resource string. Set VISA_BACKEND to
# "@ivi" or "" for the system VISA library, or "@py" for pyvisa-py.
VISA_RESOURCE = "USB0::6833::1602::DG1ZA000000000::0::INSTR"

VISA_BACKEND = "@py"
CHANNELS = (1, 2)
INVALID_CHANNEL = 3

TEST_FREQUENCY_HZ = 1000.0
TEST_AMPLITUDE_VPP = 1.0
TEST_AMPLITUDE_VRMS = 0.25
TEST_AMPLITUDE_DBM = 0.0
TEST_OFFSET_V = 0.25
FREQUENCY_TOLERANCE_REL = 1e-4
AMPLITUDE_TOLERANCE_REL = 0.01
AMPLITUDE_DBM_TOLERANCE_ABS = 0.1
PHASE_TOLERANCE_DEG = 0.1

_ARB_SAMPLES = (0.0, 0.5, 1.0, 0.5, 0.0, -0.5, -1.0, -0.5, 0.25)

_ATTR_TOLERANCES: dict[str, float] = {
    "frequency_hz": FREQUENCY_TOLERANCE_REL,
    "duty_cycle_pct": 0.01,
    "width_s": 0.01,
    "value": 0.01,
}


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
    driver.check_errors()


def _assert_waveform_matches(readback: Waveform, expected_type: type, checks: dict[str, float]) -> None:
    assert isinstance(readback, expected_type)
    for attr, expected in checks.items():
        if attr == "phase_deg":
            assert getattr(readback, attr) == pytest.approx(expected, abs=PHASE_TOLERANCE_DEG)
        else:
            assert getattr(readback, attr) == pytest.approx(expected, rel=_ATTR_TOLERANCES.get(attr, 0.01))


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
    driver.check_errors()


# ---------------------------------------------------------------------------
# set_waveform / get_waveform
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("channel", "waveform", "expected_type", "checks"),
    [
        (
            1,
            Sine(frequency_hz=TEST_FREQUENCY_HZ, phase_deg=90.0),
            Sine,
            {"frequency_hz": TEST_FREQUENCY_HZ, "phase_deg": 90.0},
        ),
        (
            2,
            Sine(frequency_hz=TEST_FREQUENCY_HZ, phase_deg=90.0),
            Sine,
            {"frequency_hz": TEST_FREQUENCY_HZ, "phase_deg": 90.0},
        ),
        (1, Square(frequency_hz=TEST_FREQUENCY_HZ, duty_cycle_pct=30.0), Square, {"duty_cycle_pct": 30.0}),
        (1, Sawtooth(frequency_hz=TEST_FREQUENCY_HZ), Sawtooth, {"frequency_hz": TEST_FREQUENCY_HZ}),
        (1, Triangle(frequency_hz=TEST_FREQUENCY_HZ), Triangle, {"frequency_hz": TEST_FREQUENCY_HZ}),
        (
            1,
            Pulse(frequency_hz=TEST_FREQUENCY_HZ, width_s=0.0002),
            Pulse,
            {"frequency_hz": TEST_FREQUENCY_HZ, "width_s": 0.0002},
        ),
        (1, StaticValue(value=TEST_OFFSET_V), StaticValue, {"value": TEST_OFFSET_V}),
    ],
    ids=["sine_ch1", "sine_ch2", "square", "sawtooth", "triangle", "pulse", "staticvalue"],
)
def test_02_set_and_get_waveform_roundtrip(
    driver: RigolDG1022Z,
    channel: int,
    waveform: Waveform,
    expected_type: type,
    checks: dict[str, float],
) -> None:
    driver.set_waveform(channel, waveform)
    driver.check_errors()

    readback = driver.get_waveform(channel)
    _assert_waveform_matches(readback, expected_type, checks)


@pytest.mark.parametrize(
    ("channel", "waveform", "match"),
    [
        (INVALID_CHANNEL, Sine(frequency_hz=TEST_FREQUENCY_HZ), "channel must be 1 or 2"),
        (1, Pulse(frequency_hz=TEST_FREQUENCY_HZ, width_s=0.0002, delay_s=0.0001), "cannot program a pulse delay"),
    ],
    ids=["invalid_channel", "pulse_nonzero_delay"],
)
def test_03_set_waveform_rejects_invalid_input(
    driver: RigolDG1022Z, channel: int, waveform: Waveform, match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        driver.set_waveform(channel, waveform)

    driver.check_errors()


def test_04_arbitrary_waveform_download_and_cached_readback(driver: RigolDG1022Z) -> None:
    arbitrary = Arbitrary(samples=_ARB_SAMPLES, sample_rate_hz=100_000.0)
    driver.set_waveform(1, arbitrary)
    driver.check_errors()

    assert driver.get_waveform(1) is arbitrary


# ---------------------------------------------------------------------------
# Amplitude, offset, output, load, phase
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("amplitude", "unit"),
    [
        (TEST_AMPLITUDE_VPP, AmplitudeMeasurementUnit.VPP),
        (TEST_AMPLITUDE_VRMS, AmplitudeMeasurementUnit.VRMS),
        (TEST_AMPLITUDE_DBM, AmplitudeMeasurementUnit.DBM),
    ],
    ids=["vpp", "vrms", "dbm"],
)
def test_05_amplitude_roundtrip(driver: RigolDG1022Z, amplitude: float, unit: AmplitudeMeasurementUnit) -> None:
    driver.set_waveform(1, Sine(frequency_hz=TEST_FREQUENCY_HZ))
    if unit is AmplitudeMeasurementUnit.DBM:
        driver.set_output_load(1, 50.0)
    driver.set_amplitude(1, amplitude, unit)
    driver.check_errors()

    readback_amplitude, readback_unit = driver.get_amplitude(1)
    assert readback_unit is unit
    if unit is AmplitudeMeasurementUnit.DBM:
        assert readback_amplitude == pytest.approx(amplitude, abs=AMPLITUDE_DBM_TOLERANCE_ABS)
    else:
        assert readback_amplitude == pytest.approx(amplitude, rel=AMPLITUDE_TOLERANCE_REL)


def test_06_set_amplitude_rejects_vp_unit(driver: RigolDG1022Z) -> None:
    with pytest.raises(ValueError, match="no VP amplitude unit"):
        driver.set_amplitude(1, TEST_AMPLITUDE_VPP, AmplitudeMeasurementUnit.VP)

    driver.check_errors()


@pytest.mark.parametrize("channel", CHANNELS, ids=lambda channel: f"channel_{channel}")
def test_07_offset_roundtrip(driver: RigolDG1022Z, channel: int) -> None:
    driver.set_waveform(channel, Sine(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_amplitude(channel, TEST_AMPLITUDE_VPP, AmplitudeMeasurementUnit.VPP)
    driver.set_offset(channel, TEST_OFFSET_V)
    driver.check_errors()

    assert driver.get_offset(channel) == pytest.approx(TEST_OFFSET_V, rel=0.01)


@pytest.mark.parametrize("channel", CHANNELS, ids=lambda channel: f"channel_{channel}")
def test_08_output_enable_toggle(driver: RigolDG1022Z, channel: int) -> None:
    driver.set_waveform(channel, Sine(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_amplitude(channel, TEST_AMPLITUDE_VPP, AmplitudeMeasurementUnit.VPP)
    try:
        driver.output_enable(channel, True)
        driver.check_errors()
        assert driver.get_output_state(channel) is True
    finally:
        driver.output_enable(channel, False)

    assert driver.get_output_state(channel) is False


def test_09_output_load_roundtrip_and_high_z(driver: RigolDG1022Z) -> None:
    driver.set_output_load(1, 50.0)
    driver.check_errors()
    assert driver.get_output_load(1) == pytest.approx(50.0)

    driver.set_output_load(1, None)
    driver.check_errors()
    assert driver.get_output_load(1) is None


def test_10_align_phase_completes_without_error(driver: RigolDG1022Z) -> None:
    for channel in CHANNELS:
        driver.set_waveform(channel, Sine(frequency_hz=TEST_FREQUENCY_HZ, phase_deg=0.0))

    driver.align_phase()
    driver.check_errors()


def test_11_check_errors_raises_after_invalid_command(driver: RigolDG1022Z) -> None:
    driver._visa.write("INSTRO:INVALID")

    try:
        with pytest.raises(RuntimeError, match="Rigol DG1022Z reported error"):
            driver.check_errors()
    finally:
        driver._visa.write("*CLS")


# ---------------------------------------------------------------------------
# Modulation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mod_type", "carrier", "shape", "magnitude"),
    [
        (ModulationType.AM, Square(frequency_hz=TEST_FREQUENCY_HZ), Sine(frequency_hz=100.0), 50.0),
        (
            ModulationType.PWM,
            Pulse(frequency_hz=TEST_FREQUENCY_HZ, width_s=0.0002),
            Square(frequency_hz=100.0),
            50e-6,
        ),
        (ModulationType.PSK, Square(frequency_hz=TEST_FREQUENCY_HZ), Triangle(frequency_hz=100.0), 90.0),
    ],
    ids=["am", "pwm", "psk"],
)
def test_12_set_modulation_and_modulation_enable_reports_type_specific_stat(
    driver: RigolDG1022Z,
    mod_type: ModulationType,
    carrier: Waveform,
    shape: Waveform,
    magnitude: float,
) -> None:
    driver.set_waveform(1, carrier)
    driver.set_modulation(1, mod_type, shape, magnitude)
    driver.modulation_enable(1, True)
    driver.check_errors()

    try:
        assert driver._visa.query(f":SOUR1:{mod_type.value}:STAT?").strip() == "ON"
    finally:
        driver.modulation_enable(1, False)
    driver.check_errors()

    assert driver._visa.query(f":SOUR1:{mod_type.value}:STAT?").strip() == "OFF"


@pytest.mark.parametrize(
    ("carrier", "mod_type", "shape", "magnitude", "match"),
    [
        (
            Square(frequency_hz=TEST_FREQUENCY_HZ),
            ModulationType.AM,
            Pulse(frequency_hz=TEST_FREQUENCY_HZ, width_s=0.0002),
            50.0,
            "cannot use Pulse",
        ),
        (
            Sine(frequency_hz=TEST_FREQUENCY_HZ),
            ModulationType.PWM,
            Square(frequency_hz=100.0),
            50e-6,
            "can only apply PWM modulation to a Pulse carrier",
        ),
    ],
    ids=["unsupported_modulator_shape", "pwm_rejects_non_pulse_carrier"],
)
def test_13_set_modulation_rejects_invalid_input(
    driver: RigolDG1022Z,
    carrier: Waveform,
    mod_type: ModulationType,
    shape: Waveform,
    magnitude: float,
    match: str,
) -> None:
    driver.set_waveform(1, carrier)

    with pytest.raises(ValueError, match=match):
        driver.set_modulation(1, mod_type, shape, magnitude)

    driver.check_errors()


def test_14_modulation_enable_re_arms_after_disable_without_remodulating(driver: RigolDG1022Z) -> None:
    driver.set_waveform(1, Square(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_modulation(1, ModulationType.AM, Sine(frequency_hz=100.0), 50.0)
    driver.modulation_enable(1, True)
    driver.modulation_enable(1, False)
    try:
        driver.modulation_enable(1, True)
        driver.check_errors()
        assert driver._visa.query(":SOUR1:AM:STAT?").strip() == "ON"
    finally:
        driver.modulation_enable(1, False)
    driver.check_errors()


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
        Arbitrary(samples=_ARB_SAMPLES, sample_rate_hz=100_000.0),
    ],
    ids=["sine", "square", "sawtooth", "triangle", "pulse", "arbitrary"],
)
def test_15_set_burst_ncycle_on_every_valid_carrier(driver: RigolDG1022Z, carrier: Waveform) -> None:
    driver.set_waveform(1, carrier)
    driver.set_burst(1, BurstType.NCYCLE)
    driver.check_errors()

    assert driver.get_burst_type(1) is BurstType.NCYCLE

    driver.burst_enable(1, True)
    driver.check_errors()
    assert driver.get_burst_state(1) is True

    driver.burst_enable(1, False)
    driver.check_errors()
    assert driver.get_burst_state(1) is False


@pytest.mark.parametrize(
    "burst_type",
    [BurstType.NCYCLE, BurstType.GATED, BurstType.INFINITE],
    ids=["ncycle", "gated", "infinite"],
)
def test_16_get_burst_type_matches_configured_type(driver: RigolDG1022Z, burst_type: BurstType) -> None:
    driver.set_waveform(1, Square(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_burst(1, burst_type)
    driver.check_errors()

    assert driver.get_burst_type(1) is burst_type


def test_17_set_burst_trigger_rejects_gated_mode(driver: RigolDG1022Z) -> None:
    """Regression guard: :BURS:TRIG:SOUR is rejected (-220) once :BURS:MODE GAT is set."""
    driver.set_waveform(1, Square(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_burst(1, BurstType.GATED)
    driver.check_errors()

    with pytest.raises(ValueError, match="GATED burst mode"):
        driver.set_burst_trigger(1, BurstTriggerSource.EXTERNAL)

    driver.check_errors()
