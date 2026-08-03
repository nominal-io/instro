"""Rigol DG1022Z hardware smoke tests."""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest

from instro.lib.transports import VisaConfig
from instro.unstable.awg.drivers import RigolDG1022Z
from instro.unstable.awg.types import (
    AmplitudeMeasurementUnit,
    Arbitrary,
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
# Outputs are only enabled briefly at low amplitude; leave them unconnected or on a scope.
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
# Tests
# ---------------------------------------------------------------------------


def test_01_connect_to_awg(driver: RigolDG1022Z) -> None:
    idn = driver._visa.query("*IDN?")
    print(f"\nIDN: {idn.strip()}")

    assert "DG1" in idn.upper()
    driver.check_errors()


def test_02_cycle_through_waveforms(driver: RigolDG1022Z) -> None:
    waveforms: list[Waveform] = [
        Sine(frequency_hz=TEST_FREQUENCY_HZ),
        Square(frequency_hz=TEST_FREQUENCY_HZ, duty_cycle_pct=30.0),
        Sawtooth(frequency_hz=TEST_FREQUENCY_HZ),
        Triangle(frequency_hz=TEST_FREQUENCY_HZ),
        Pulse(frequency_hz=TEST_FREQUENCY_HZ, width_s=0.0002),
        Arbitrary(samples=_ARB_SAMPLES, sample_rate_hz=100_000.0),
        StaticValue(value=TEST_OFFSET_V),
    ]

    for waveform in waveforms:
        driver.set_waveform(1, waveform)
        driver.check_errors()
        readback = driver.get_waveform(1)
        assert type(readback) is type(waveform), f"programmed {waveform}, read back {readback}"


@pytest.mark.parametrize("channel", CHANNELS, ids=lambda channel: f"channel_{channel}")
def test_03_sine_frequency_and_phase_roundtrip(driver: RigolDG1022Z, channel: int) -> None:
    driver.set_waveform(channel, Sine(frequency_hz=TEST_FREQUENCY_HZ, phase_deg=90.0))
    driver.check_errors()

    readback = driver.get_waveform(channel)
    assert isinstance(readback, Sine)
    assert readback.frequency_hz == pytest.approx(TEST_FREQUENCY_HZ, rel=FREQUENCY_TOLERANCE_REL)
    assert readback.phase_deg == pytest.approx(90.0, abs=PHASE_TOLERANCE_DEG)


@pytest.mark.parametrize("channel", CHANNELS, ids=lambda channel: f"channel_{channel}")
def test_04_square_duty_cycle_roundtrip(driver: RigolDG1022Z, channel: int) -> None:
    driver.set_waveform(channel, Square(frequency_hz=TEST_FREQUENCY_HZ, duty_cycle_pct=30.0))
    driver.check_errors()

    readback = driver.get_waveform(channel)
    assert isinstance(readback, Square)
    assert readback.duty_cycle_pct == pytest.approx(30.0, rel=0.01)


def test_05_sawtooth_readback(driver: RigolDG1022Z) -> None:
    driver.set_waveform(1, Sawtooth(frequency_hz=TEST_FREQUENCY_HZ))
    driver.check_errors()

    readback = driver.get_waveform(1)
    assert isinstance(readback, Sawtooth)
    assert readback.frequency_hz == pytest.approx(TEST_FREQUENCY_HZ, rel=FREQUENCY_TOLERANCE_REL)


def test_06_triangle_readback(driver: RigolDG1022Z) -> None:
    driver.set_waveform(1, Triangle(frequency_hz=TEST_FREQUENCY_HZ))
    driver.check_errors()

    readback = driver.get_waveform(1)
    assert isinstance(readback, Triangle)
    assert readback.frequency_hz == pytest.approx(TEST_FREQUENCY_HZ, rel=FREQUENCY_TOLERANCE_REL)


def test_07_pulse_width_roundtrip(driver: RigolDG1022Z) -> None:
    driver.set_waveform(1, Pulse(frequency_hz=TEST_FREQUENCY_HZ, width_s=0.0002))
    driver.check_errors()

    readback = driver.get_waveform(1)
    assert isinstance(readback, Pulse)
    assert readback.frequency_hz == pytest.approx(TEST_FREQUENCY_HZ, rel=FREQUENCY_TOLERANCE_REL)
    assert readback.width_s == pytest.approx(0.0002, rel=0.01)


def test_08_static_value_roundtrip(driver: RigolDG1022Z) -> None:
    driver.set_waveform(1, StaticValue(value=TEST_OFFSET_V))
    driver.check_errors()

    readback = driver.get_waveform(1)
    assert isinstance(readback, StaticValue)
    assert readback.value == pytest.approx(TEST_OFFSET_V, rel=0.01)


def test_09_arbitrary_download_and_cached_readback(driver: RigolDG1022Z) -> None:
    arbitrary = Arbitrary(samples=_ARB_SAMPLES, sample_rate_hz=100_000.0)
    driver.set_waveform(1, arbitrary)
    driver.check_errors()

    readback = driver.get_waveform(1)
    assert readback is arbitrary


@pytest.mark.parametrize("channel", CHANNELS, ids=lambda channel: f"channel_{channel}")
def test_10_amplitude_vpp_roundtrip(driver: RigolDG1022Z, channel: int) -> None:
    driver.set_waveform(channel, Sine(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_amplitude(channel, TEST_AMPLITUDE_VPP, AmplitudeMeasurementUnit.VPP)
    driver.check_errors()

    amplitude, unit = driver.get_amplitude(channel)
    assert unit is AmplitudeMeasurementUnit.VPP
    assert amplitude == pytest.approx(TEST_AMPLITUDE_VPP, rel=AMPLITUDE_TOLERANCE_REL)


@pytest.mark.parametrize("channel", CHANNELS, ids=lambda channel: f"channel_{channel}")
def test_11_amplitude_vrms_roundtrip(driver: RigolDG1022Z, channel: int) -> None:
    driver.set_waveform(channel, Sine(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_amplitude(channel, TEST_AMPLITUDE_VRMS, AmplitudeMeasurementUnit.VRMS)
    driver.check_errors()

    amplitude, unit = driver.get_amplitude(channel)
    assert unit is AmplitudeMeasurementUnit.VRMS
    assert amplitude == pytest.approx(TEST_AMPLITUDE_VRMS, rel=AMPLITUDE_TOLERANCE_REL)


def test_12_amplitude_vp_unit_rejected(driver: RigolDG1022Z) -> None:
    with pytest.raises(ValueError, match="no VP amplitude unit"):
        driver.set_amplitude(1, TEST_AMPLITUDE_VPP, AmplitudeMeasurementUnit.VP)

    driver.check_errors()


@pytest.mark.parametrize("channel", CHANNELS, ids=lambda channel: f"channel_{channel}")
def test_13_offset_roundtrip(driver: RigolDG1022Z, channel: int) -> None:
    driver.set_waveform(channel, Sine(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_amplitude(channel, TEST_AMPLITUDE_VPP, AmplitudeMeasurementUnit.VPP)
    driver.set_offset(channel, TEST_OFFSET_V)
    driver.check_errors()

    assert driver.get_offset(channel) == pytest.approx(TEST_OFFSET_V, rel=0.01)


@pytest.mark.parametrize("channel", CHANNELS, ids=lambda channel: f"channel_{channel}")
def test_14_output_enable_toggle(driver: RigolDG1022Z, channel: int) -> None:
    driver.set_waveform(channel, Sine(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_amplitude(channel, TEST_AMPLITUDE_VPP, AmplitudeMeasurementUnit.VPP)
    try:
        driver.output_enable(channel, True)
        driver.check_errors()
        assert driver.get_output_state(channel) is True
    finally:
        driver.output_enable(channel, False)

    assert driver.get_output_state(channel) is False


def test_15_output_load_50_ohm_roundtrip(driver: RigolDG1022Z) -> None:
    driver.set_output_load(1, 50.0)
    driver.check_errors()

    assert driver.get_output_load(1) == pytest.approx(50.0)


def test_16_output_load_high_z_roundtrip(driver: RigolDG1022Z) -> None:
    driver.set_output_load(1, None)
    driver.check_errors()

    assert driver.get_output_load(1) is None


def test_17_align_phase_completes_without_error(driver: RigolDG1022Z) -> None:
    for channel in CHANNELS:
        driver.set_waveform(channel, Sine(frequency_hz=TEST_FREQUENCY_HZ, phase_deg=0.0))

    driver.align_phase()
    driver.check_errors()


def test_18_pulse_delay_rejected(driver: RigolDG1022Z) -> None:
    with pytest.raises(ValueError, match="cannot program a pulse delay"):
        driver.set_waveform(1, Pulse(frequency_hz=TEST_FREQUENCY_HZ, width_s=0.0002, delay_s=0.0001))

    driver.check_errors()


def test_19_invalid_channel_rejected(driver: RigolDG1022Z) -> None:
    with pytest.raises(ValueError, match="channel must be 1 or 2"):
        driver.set_waveform(INVALID_CHANNEL, Sine(frequency_hz=TEST_FREQUENCY_HZ))

    driver.check_errors()


def test_20_check_errors_raises_after_invalid_command(driver: RigolDG1022Z) -> None:
    driver._visa.write("INSTRO:INVALID")

    try:
        with pytest.raises(RuntimeError, match="Rigol DG1022Z reported error"):
            driver.check_errors()
    finally:
        driver._visa.write("*CLS")


def test_21_amplitude_dbm_roundtrip(driver: RigolDG1022Z) -> None:
    driver.set_waveform(1, Sine(frequency_hz=TEST_FREQUENCY_HZ))
    driver.set_output_load(1, 50.0)
    driver.set_amplitude(1, TEST_AMPLITUDE_DBM, AmplitudeMeasurementUnit.DBM)
    driver.check_errors()

    amplitude, unit = driver.get_amplitude(1)
    assert unit is AmplitudeMeasurementUnit.DBM
    assert amplitude == pytest.approx(TEST_AMPLITUDE_DBM, abs=AMPLITUDE_DBM_TOLERANCE_ABS)


def test_22_modulate_am_enables_and_reports_stat_on(driver: RigolDG1022Z) -> None:
    driver.set_waveform(1, Square(frequency_hz=TEST_FREQUENCY_HZ))
    driver.modulate(1, ModulationType.AM, Sine(frequency_hz=100.0), 50.0)
    try:
        driver.check_errors()
        assert driver._visa.query(":SOUR1:AM:STAT?").strip() == "ON"
    finally:
        driver.disable_modulation(1)


def test_23_modulate_rejects_unsupported_modulator_shape(driver: RigolDG1022Z) -> None:
    driver.set_waveform(1, Square(frequency_hz=TEST_FREQUENCY_HZ))

    with pytest.raises(ValueError, match="cannot use Pulse"):
        driver.modulate(1, ModulationType.AM, Pulse(frequency_hz=TEST_FREQUENCY_HZ, width_s=0.0002), 50.0)

    driver.check_errors()


def test_24_disable_modulation_turns_off_stat(driver: RigolDG1022Z) -> None:
    driver.set_waveform(1, Square(frequency_hz=TEST_FREQUENCY_HZ))
    driver.modulate(1, ModulationType.AM, Sine(frequency_hz=100.0), 50.0)
    try:
        driver.check_errors()
        assert driver._visa.query(":SOUR1:AM:STAT?").strip() == "ON"
    finally:
        driver.disable_modulation(1)
    driver.check_errors()

    assert driver._visa.query(":SOUR1:AM:STAT?").strip() == "OFF"


def test_25_modulate_pwm_enables_and_reports_stat_on(driver: RigolDG1022Z) -> None:
    driver.set_waveform(1, Pulse(frequency_hz=TEST_FREQUENCY_HZ, width_s=0.0002))
    driver.modulate(1, ModulationType.PWM, Square(frequency_hz=100.0), 50e-6)
    try:
        driver.check_errors()
        assert driver._visa.query(":SOUR1:PWM:STAT?").strip() == "ON"
    finally:
        driver.disable_modulation(1)
    driver.check_errors()

    assert driver._visa.query(":SOUR1:PWM:STAT?").strip() == "OFF"


def test_26_modulate_pwm_rejects_non_pulse_carrier(driver: RigolDG1022Z) -> None:
    driver.set_waveform(1, Sine(frequency_hz=TEST_FREQUENCY_HZ))

    with pytest.raises(ValueError, match="can only apply PWM modulation to a Pulse carrier"):
        driver.modulate(1, ModulationType.PWM, Square(frequency_hz=100.0), 50e-6)

    driver.check_errors()


def test_27_modulate_psk_enables_and_reports_stat_on(driver: RigolDG1022Z) -> None:
    driver.set_waveform(1, Square(frequency_hz=TEST_FREQUENCY_HZ))
    driver.modulate(1, ModulationType.PSK, Triangle(frequency_hz=100.0), 90.0)
    try:
        driver.check_errors()
        assert driver._visa.query(":SOUR1:PSK:STAT?").strip() == "ON"
    finally:
        driver.disable_modulation(1)
    driver.check_errors()

    assert driver._visa.query(":SOUR1:PSK:STAT?").strip() == "OFF"
