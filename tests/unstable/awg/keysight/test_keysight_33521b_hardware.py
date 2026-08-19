"""Keysight 33521B hardware smoke tests."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from instro.lib.transports import VisaConfig
from instro.unstable.awg.drivers import Keysight33521B
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
        Arbitrary(samples=_ARB_SAMPLES, sample_rate_hz=100_000.0),
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
    arbitrary = Arbitrary(samples=_ARB_SAMPLES, sample_rate_hz=100_000.0)
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
