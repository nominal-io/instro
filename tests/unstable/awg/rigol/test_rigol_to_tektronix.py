"""Cross-instrument hardware test: verify Rigol DG1022Z waveforms against Tektronix scope measurements.

Wiring: AWG_CHANNEL's output feeds directly (BNC, no probe) into SCOPE_CHANNEL. For the standard
waveform, amplitude, DC-level, and arbitrary tests, the scope's own FREQUENCY/VPP/VAVG measurements
are compared against the values programmed on the AWG. Duty cycle is instead computed directly from
raw waveform samples (see _measured_duty_cycle_pct): the scope's built-in DUTY_CYCLE measurement
(PDUTY) reproducibly reports zero population on this instrument/firmware, even against signals
independently confirmed clean from those same samples. Modulation doesn't map onto any of the
scope's built-in measurement types (no AM-depth or FM/PM-deviation measurement), so it gets a looser
sanity check instead: confirm the output is genuinely live (VPP above a floor) rather than asserting
an exact value.
"""

from __future__ import annotations

import math
import time
from collections.abc import Iterator
from typing import cast

import pytest

from instro.lib.transports import VisaConfig
from instro.scope import Coupling, InstroScope, ScopeMeasurementType, TriggerMode, TriggerSlope, TriggerType
from instro.scope.drivers import Tektronix2SeriesMSO
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
# Set both VISA_RESOURCE constants to the bench units' VISA resource strings. Set the VISA_BACKEND
# constants to "@ivi" or "" for the system VISA library, or "@py" for pyvisa-py.
RIGOL_VISA_RESOURCE = "USB0::0x1AB1::0x0642::DG1ZA000000000::INSTR"
RIGOL_VISA_BACKEND = "@py"
SCOPE_VISA_RESOURCE = "USB0::1689::261::SGVJ016092::0::INSTR"
SCOPE_VISA_BACKEND = "@py"
NUM_SCOPE_CHANNELS = 4  # MSO24 has 4 analog channels; use 2 for an MSO22.

AWG_CHANNEL = 1
SCOPE_CHANNEL = 1  # AWG_CHANNEL's output feeds directly (BNC, no probe) into this scope channel.

TEST_FREQUENCY_HZ = 1000.0
MOD_FREQUENCY_HZ = 100.0
TEST_AMPLITUDE_VPP = 2.0
TEST_OFFSET_V = 0.25

FREQUENCY_TOLERANCE_REL = 0.05
AMPLITUDE_TOLERANCE_REL = 0.10
DUTY_TOLERANCE_ABS_PCT = 5.0
DC_LEVEL_TOLERANCE_ABS_V = 0.05
MIN_VPP_V = 0.3  # floor confirming the output is genuinely live, for the loose sanity checks

SETTLE_S = 6.0  # let the scope acquire fresh data after changing the AWG output or scope config

# A measurement type's first use on a channel can race its Tektronix slot's creation against a
# fresh acquisition (Tektronix2SeriesMSO._wait_for_measurement_ready's internal 2s timeout can
# elapse first); a plain re-read of the same slot after one more acquisition lands resolves it.
MEASUREMENT_NAN_RETRIES = 3
MEASUREMENT_RETRY_DELAY_S = 1.0

# ~3 cycles of TEST_FREQUENCY_HZ (1 kHz) across the screen.
STANDARD_HORIZONTAL_S_PER_DIV = 2e-4

_ARB_SAMPLES = (0.0, 0.5, 1.0, 0.5, 0.0, -0.5, -1.0, -0.5, 0.25)
_ARB_SAMPLE_RATE_HZ = 100_000.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _measure(scope: InstroScope, measurement_type: ScopeMeasurementType) -> float:
    value = scope.measure(measurement_type, channel=SCOPE_CHANNEL).latest
    retries_left = MEASUREMENT_NAN_RETRIES
    while math.isnan(value) and retries_left > 0:
        time.sleep(MEASUREMENT_RETRY_DELAY_S)
        value = scope.measure(measurement_type, channel=SCOPE_CHANNEL).latest
        retries_left -= 1
    return value


def _measured_duty_cycle_pct(scope: InstroScope) -> float:
    """Compute duty cycle from raw waveform samples; see the module docstring for why."""
    voltages = scope.fetch_waveform(channel=SCOPE_CHANNEL).values
    midpoint = (max(voltages) + min(voltages)) / 2.0
    above_midpoint = sum(1 for v in voltages if v > midpoint)
    return 100.0 * above_midpoint / len(voltages)


def _drive_waveform(rigol: RigolDG1022Z, waveform: Waveform, amplitude_vpp: float = TEST_AMPLITUDE_VPP) -> None:
    rigol.set_waveform(AWG_CHANNEL, waveform)
    if not isinstance(waveform, StaticValue):
        rigol.set_amplitude(AWG_CHANNEL, amplitude_vpp, AmplitudeMeasurementUnit.VPP)
    rigol.check_errors()


def _enable_and_settle(
    rigol: RigolDG1022Z, scope: InstroScope, vertical_scale: float, horizontal_scale: float, trigger_level: float
) -> None:
    rigol.output_enable(AWG_CHANNEL, True)
    rigol.check_errors()
    scope.set_vertical_scale(vertical_scale, channel=SCOPE_CHANNEL)
    scope.set_horizontal_scale(horizontal_scale)
    scope.set_trigger_level(trigger_level)
    scope.run()
    time.sleep(SETTLE_S)


def _teardown_signal(rigol: RigolDG1022Z, scope: InstroScope) -> None:
    rigol.output_enable(AWG_CHANNEL, False)
    scope.stop_acquisition()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def rigol() -> Iterator[RigolDG1022Z]:
    awg_driver = RigolDG1022Z(
        VisaConfig(
            visa_resource=RIGOL_VISA_RESOURCE,
            visa_backend=RIGOL_VISA_BACKEND,
        )
    )
    opened = False
    try:
        awg_driver.open()
        opened = True
        yield awg_driver
    finally:
        if opened:
            awg_driver.output_enable(AWG_CHANNEL, False)
        awg_driver.close()


@pytest.fixture(autouse=True)
def reset_rigol_before_each_test(rigol: RigolDG1022Z) -> None:
    rigol._visa.write("*CLS")
    rigol._visa.write("*RST")
    time.sleep(0.5)
    rigol._visa.write("*CLS")
    rigol.check_errors()
    # Direct BNC into the scope's high-Z input, with no external 50 ohm terminator: without this
    # the Rigol assumes a 50 ohm load and the scope reads ~2x the programmed Vpp (open-circuit
    # doubling from the AWG's internal 50 ohm source impedance).
    rigol.set_output_load(AWG_CHANNEL, None)


@pytest.fixture(scope="module")
def scope() -> Iterator[InstroScope]:
    tek_scope = InstroScope(
        name="hw_validate",
        driver=Tektronix2SeriesMSO(
            VisaConfig(
                visa_resource=SCOPE_VISA_RESOURCE,
                visa_backend=SCOPE_VISA_BACKEND,
            )
        ),
        num_channels=NUM_SCOPE_CHANNELS,
        publishers=None,
    )
    opened = False
    try:
        tek_scope.open()
        opened = True
        # The Python-side measurement-slot cache (Tektronix2SeriesMSO._measurement_slots) starts
        # empty every run, but the scope's own slots persist across runs — without clearing them
        # here, each run's setup_measurement() adds another duplicate FREQUENCY/PDUTY/PK2PK/MEAN
        # set via ADDMEAS. Past a certain accumulated slot count, new slots stop getting live data
        # and measure() reads the vendor's invalid-measurement sentinel (NaN) forever.
        cast(Tektronix2SeriesMSO, tek_scope._driver).clear_measurements()
        tek_scope.set_coupling(Coupling.DC, channel=SCOPE_CHANNEL)
        tek_scope.set_probe_attenuation(1, channel=SCOPE_CHANNEL)  # direct BNC feed, no 10x probe
        tek_scope.set_trigger_source(channel=SCOPE_CHANNEL)
        tek_scope.set_trigger_type(TriggerType.EDGE)
        tek_scope.set_trigger_slope(TriggerSlope.RISING)
        tek_scope.set_trigger_mode(TriggerMode.AUTO)  # free-runs even without a clean edge
        yield tek_scope
    finally:
        if opened:
            try:
                tek_scope.stop_acquisition()
            except Exception:  # noqa: BLE001 - best-effort safe state
                pass
        tek_scope.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_01_connects_to_both_instruments(rigol: RigolDG1022Z, scope: InstroScope) -> None:
    """Confirm both VISA sessions are open and responding, before checking what they identify as."""
    rigol.check_errors()
    scope.get_horizontal_scale()


def test_02_identifies_both_instruments(rigol: RigolDG1022Z, scope: InstroScope) -> None:
    awg_idn = rigol._visa.query("*IDN?")
    scope_idn = cast(Tektronix2SeriesMSO, scope._driver)._visa.query("*IDN?")
    print(f"\nAWG IDN: {awg_idn.strip()}\nScope IDN: {scope_idn.strip()}")

    assert "DG1" in awg_idn.upper()
    assert "TEK" in scope_idn.upper()
    rigol.check_errors()


@pytest.mark.parametrize(
    "waveform",
    [
        Sine(frequency_hz=TEST_FREQUENCY_HZ),
        Square(frequency_hz=TEST_FREQUENCY_HZ, duty_cycle_pct=50.0),
        Sawtooth(frequency_hz=TEST_FREQUENCY_HZ),
        Triangle(frequency_hz=TEST_FREQUENCY_HZ),
        Pulse(frequency_hz=TEST_FREQUENCY_HZ, width_s=0.0002),
    ],
    ids=["sine", "square", "sawtooth", "triangle", "pulse"],
)
def test_03_standard_waveform_frequency_matches_scope(
    rigol: RigolDG1022Z, scope: InstroScope, waveform: Waveform
) -> None:
    _drive_waveform(rigol, waveform)
    try:
        _enable_and_settle(rigol, scope, TEST_AMPLITUDE_VPP / 4.0, STANDARD_HORIZONTAL_S_PER_DIV, trigger_level=0.0)
        measured_freq = _measure(scope, ScopeMeasurementType.FREQUENCY)
        assert measured_freq == pytest.approx(TEST_FREQUENCY_HZ, rel=FREQUENCY_TOLERANCE_REL), (
            f"programmed {TEST_FREQUENCY_HZ} Hz, scope measured {measured_freq} Hz"
        )
    finally:
        _teardown_signal(rigol, scope)


def test_04_square_duty_cycle_matches_scope(rigol: RigolDG1022Z, scope: InstroScope) -> None:
    _drive_waveform(rigol, Square(frequency_hz=TEST_FREQUENCY_HZ, duty_cycle_pct=30.0))
    try:
        _enable_and_settle(rigol, scope, TEST_AMPLITUDE_VPP / 4.0, STANDARD_HORIZONTAL_S_PER_DIV, trigger_level=0.0)
        duty = _measured_duty_cycle_pct(scope)
        assert abs(duty - 30.0) < DUTY_TOLERANCE_ABS_PCT, f"programmed 30% duty, scope measured {duty}%"
    finally:
        _teardown_signal(rigol, scope)


@pytest.mark.parametrize("amplitude_vpp", [1.0, 2.0, 3.0], ids=["1vpp", "2vpp", "3vpp"])
def test_05_amplitude_vpp_matches_scope(rigol: RigolDG1022Z, scope: InstroScope, amplitude_vpp: float) -> None:
    _drive_waveform(rigol, Sine(frequency_hz=TEST_FREQUENCY_HZ), amplitude_vpp=amplitude_vpp)
    try:
        _enable_and_settle(rigol, scope, amplitude_vpp / 4.0, STANDARD_HORIZONTAL_S_PER_DIV, trigger_level=0.0)
        measured_vpp = _measure(scope, ScopeMeasurementType.VPP)
        assert measured_vpp == pytest.approx(amplitude_vpp, rel=AMPLITUDE_TOLERANCE_REL), (
            f"programmed {amplitude_vpp} Vpp, scope measured {measured_vpp} Vpp"
        )
    finally:
        _teardown_signal(rigol, scope)


def test_06_static_value_dc_level_matches_scope(rigol: RigolDG1022Z, scope: InstroScope) -> None:
    _drive_waveform(rigol, StaticValue(value=TEST_OFFSET_V))
    try:
        _enable_and_settle(
            rigol,
            scope,
            vertical_scale=0.1,
            horizontal_scale=STANDARD_HORIZONTAL_S_PER_DIV,
            trigger_level=TEST_OFFSET_V,
        )
        measured_dc = _measure(scope, ScopeMeasurementType.VAVG)
        assert abs(measured_dc - TEST_OFFSET_V) < DC_LEVEL_TOLERANCE_ABS_V, (
            f"programmed {TEST_OFFSET_V} V DC, scope measured {measured_dc} V"
        )
    finally:
        _teardown_signal(rigol, scope)


def test_07_arbitrary_waveform_signal_present(rigol: RigolDG1022Z, scope: InstroScope) -> None:
    # Whether the driver's sample_rate_hz maps to the arbitrary waveform's output frequency
    # directly, or needs dividing by the sample count for this vendor's :SOUR:APPL:ARB form, isn't
    # confirmed against the programming manual — asserting a specific expected frequency here would
    # risk baking in a guess, so this is a loose sanity check only.
    _drive_waveform(rigol, Arbitrary(samples=_ARB_SAMPLES, sample_rate_hz=_ARB_SAMPLE_RATE_HZ))
    try:
        _enable_and_settle(rigol, scope, TEST_AMPLITUDE_VPP / 4.0, STANDARD_HORIZONTAL_S_PER_DIV, trigger_level=0.0)
        measured_freq = _measure(scope, ScopeMeasurementType.FREQUENCY)
        assert measured_freq > 0, f"expected a positive measured frequency, got {measured_freq}"
        measured_vpp = _measure(scope, ScopeMeasurementType.VPP)
        assert measured_vpp > MIN_VPP_V, f"VPP {measured_vpp} V below {MIN_VPP_V} V floor — is the output live?"
    finally:
        _teardown_signal(rigol, scope)


@pytest.mark.parametrize(
    ("mod_type", "shape"),
    [
        (ModulationType.AM, Sine(frequency_hz=MOD_FREQUENCY_HZ)),
        (ModulationType.FM, Square(frequency_hz=MOD_FREQUENCY_HZ)),
        (ModulationType.PM, Triangle(frequency_hz=MOD_FREQUENCY_HZ)),
        (ModulationType.ASK, Sine(frequency_hz=MOD_FREQUENCY_HZ)),
        (ModulationType.FSK, Sawtooth(frequency_hz=MOD_FREQUENCY_HZ)),
    ],
    ids=["am", "fm", "pm", "ask", "fsk"],
)
def test_08_all_modulation_types_signal_present(
    rigol: RigolDG1022Z, scope: InstroScope, mod_type: ModulationType, shape: Waveform
) -> None:
    _drive_waveform(rigol, Square(frequency_hz=TEST_FREQUENCY_HZ))
    rigol.modulate(AWG_CHANNEL, mod_type, shape, 50.0)
    rigol.check_errors()
    try:
        _enable_and_settle(rigol, scope, TEST_AMPLITUDE_VPP / 4.0, STANDARD_HORIZONTAL_S_PER_DIV, trigger_level=0.0)
        measured_vpp = _measure(scope, ScopeMeasurementType.VPP)
        assert measured_vpp > MIN_VPP_V, f"VPP {measured_vpp} V below {MIN_VPP_V} V floor — is the output live?"
    finally:
        _teardown_signal(rigol, scope)
