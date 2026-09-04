"""InstroAWG JSON config hardware tests.

One shared test script against real hardware for every supported AWG vendor: the `awg`
fixture is parametrized by `driver_name`, and only the per-vendor config in `CONFIGS`
changes between runs (VISA resource, channel count, sweep spacing, whether a
start-hold-time is declared). Test bodies read their expectations back out of
`awg._config` wherever possible, so most tests are identical for every vendor; the few
places where AWG capability genuinely differs (IDN string, channel count, sweep
start-hold-time support) branch explicitly on `driver_name`.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest

from instro.lib.transports import VisaConfig, VisaDriver
from instro.lib.types import Measurement
from instro.unstable.awg import InstroAWG
from instro.unstable.awg.config import build_waveform
from instro.unstable.awg.types import Sine, Square

pytestmark = pytest.mark.hardware

# HARDWARE TEST SETUP - EDIT THESE VALUES BEFORE RUNNING THIS FILE.
RIGOL = "RigolDG1022Z"
KEYSIGHT = "Keysight33521B"

# Keysight33521B is connected over LAN here; swap in a USB VISA resource (e.g.
# "USB0::0x0957::0x2B07::MY52702203::INSTR") to run against a USB-connected unit instead.
VISA_RESOURCES: dict[str, str] = {
    RIGOL: "USB0::0x1AB1::0x0642::DG1ZA000000000::INSTR",
    KEYSIGHT: "USB0::0x0957::0x2B07::MY52702203::INSTR",
}
VISA_BACKENDS: dict[str, str | None] = {
    RIGOL: "@py",
    KEYSIGHT: None,
}
NUM_CHANNELS: dict[str, int] = {
    RIGOL: 2,
    KEYSIGHT: 1,
}

TEST_FREQUENCY_HZ = 1000.0
TEST_AMPLITUDE_VPP = 1.0
TEST_OFFSET_V = 0.25
TEST_MOD_MAGNITUDE = 50.0
TEST_MOD_BASEBAND_HZ = 100.0
TEST_BURST_NCYCLES = 5
TEST_BURST_PERIOD_S = 0.01
TEST_SWEEP_START_HZ = 100.0
TEST_SWEEP_END_HZ = 900.0
TEST_SWEEP_TIME_S = 1.0
TEST_SWEEP_HOLD_TIME_S = 0.1
TEST_SWEEP_RETURN_TIME_S = 0.1

FREQUENCY_TOLERANCE_REL = 1e-4
AMPLITUDE_TOLERANCE_REL = 0.01
PHASE_TOLERANCE_DEG = 0.1
TIME_TOLERANCE_REL = 0.01


# ---------------------------------------------------------------------------
# Per-vendor config
# ---------------------------------------------------------------------------


def _channel_1_config(sweep_type: str, include_start_hold_time: bool) -> dict:
    sweep = {
        "type": sweep_type,
        "enable": False,
        "start_frequency": TEST_SWEEP_START_HZ,
        "end_frequency": TEST_SWEEP_END_HZ,
        "sweep_time": TEST_SWEEP_TIME_S,
        "stop_hold_time": TEST_SWEEP_HOLD_TIME_S,
        "return_time": TEST_SWEEP_RETURN_TIME_S,
    }
    if include_start_hold_time:
        sweep["start_hold_time"] = TEST_SWEEP_HOLD_TIME_S

    return {
        "waveform": {"shape": "sine", "frequency_hz": TEST_FREQUENCY_HZ, "phase_deg": 0.0},
        "amplitude": {"value": TEST_AMPLITUDE_VPP, "unit": "VPP"},
        "offset": TEST_OFFSET_V,
        "modulation": {
            "type": {"name": "AM", "magnitude": TEST_MOD_MAGNITUDE},
            "baseband_shape": {"shape": "sine", "frequency_hz": TEST_MOD_BASEBAND_HZ, "phase_deg": 0.0},
            "enable": False,
        },
        "burst": {"type": "NCYCLE", "enable": False, "ncycles": TEST_BURST_NCYCLES, "period": TEST_BURST_PERIOD_S},
        "sweep": sweep,
    }


# Only RigolDG1022Z has a second channel; used to exercise multi-channel config application.
_CHANNEL_2_CONFIG = {
    "waveform": {"shape": "square", "frequency_hz": TEST_FREQUENCY_HZ, "duty_cycle_pct": 30.0, "phase_deg": 0.0},
    "amplitude": {"value": TEST_AMPLITUDE_VPP, "unit": "VPP"},
}


def _make_config(driver_name: str) -> dict:
    channels = {
        "1": _channel_1_config(
            sweep_type="STEP" if driver_name == RIGOL else "LINEAR",
            include_start_hold_time=driver_name == RIGOL,
        )
    }
    if driver_name == RIGOL:
        channels["2"] = _CHANNEL_2_CONFIG

    visa: dict = {"visa_resource": VISA_RESOURCES[driver_name]}
    if VISA_BACKENDS[driver_name] is not None:
        visa["visa_backend"] = VISA_BACKENDS[driver_name]

    return {
        "device": {"name": f"hw_test_awg_{driver_name.lower()}"},
        "driver": {
            "name": driver_name,
            "num_channels": NUM_CHANNELS[driver_name],
            "connection_type": "visa",
            "visa": visa,
        },
        "channels": channels,
    }


CONFIGS: dict[str, dict] = {RIGOL: _make_config(RIGOL), KEYSIGHT: _make_config(KEYSIGHT)}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _measurement_value(measurement: Measurement | None) -> float:
    assert measurement is not None
    return measurement.latest


def _measurement_label(measurement: Measurement | None) -> str:
    """Categorical reads (modulation/burst/sweep type) publish the enum's ``value``, not the enum."""
    assert measurement is not None
    return measurement.latest


def _reset_instrument(visa_config: VisaConfig) -> None:
    visa = VisaDriver(visa_config)
    visa.open()
    try:
        visa.write("*CLS")
        visa.write("*RST")
        time.sleep(0.5)
        visa.write("*CLS")
    finally:
        visa.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", params=[RIGOL, KEYSIGHT])
def driver_name(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture(scope="module")
def awg(driver_name: str) -> Iterator[InstroAWG]:
    config = CONFIGS[driver_name]
    _reset_instrument(VisaConfig(**config["driver"]["visa"]))

    instance = InstroAWG(config=config)
    opened = False
    try:
        instance.open()
        opened = True
        yield instance
    finally:
        if opened:
            for channel in range(1, instance._num_channels + 1):
                instance.output_enable(channel, False)
        instance.close()


# ---------------------------------------------------------------------------
# Connectivity / construction
# ---------------------------------------------------------------------------


def test_01_connects_and_identifies_instrument(awg: InstroAWG, driver_name: str) -> None:
    idn = awg._driver._visa.query("*IDN?")
    print(f"\n[{driver_name}] IDN: {idn.strip()}")

    if driver_name == KEYSIGHT:
        assert "33" in idn
    else:
        assert "DG1" in idn.upper()


def test_02_config_matches_expected_driver_and_channel_count(awg: InstroAWG, driver_name: str) -> None:
    assert type(awg._driver).__name__ == driver_name
    assert awg._config is not None
    assert awg._config.driver.name == driver_name
    assert awg._num_channels == NUM_CHANNELS[driver_name]


def test_03_config_application_never_enables_output(awg: InstroAWG) -> None:
    for channel_key in awg._config.channels:
        assert _measurement_value(awg.get_output_state(int(channel_key))) == 0.0


# ---------------------------------------------------------------------------
# Waveform / amplitude / offset
# ---------------------------------------------------------------------------


def test_04_config_applies_waveform_amplitude_and_offset_on_channel_1(awg: InstroAWG) -> None:
    channel_config = awg._config.channels["1"]
    expected_waveform = build_waveform(channel_config.waveform)

    readback = awg.get_waveform(1)
    assert isinstance(readback, Sine)
    assert readback.frequency_hz == pytest.approx(expected_waveform.frequency_hz, rel=FREQUENCY_TOLERANCE_REL)
    assert readback.phase_deg == pytest.approx(expected_waveform.phase_deg, abs=PHASE_TOLERANCE_DEG)

    amplitude, unit = awg.get_amplitude(1)
    assert unit is channel_config.amplitude.unit
    assert amplitude == pytest.approx(channel_config.amplitude.value, rel=AMPLITUDE_TOLERANCE_REL)

    assert _measurement_value(awg.get_offset(1)) == pytest.approx(channel_config.offset, rel=0.01)


# ---------------------------------------------------------------------------
# Modulation
# ---------------------------------------------------------------------------


def test_05_config_applies_modulation_without_enabling(awg: InstroAWG) -> None:
    modulation = awg._config.channels["1"].modulation

    assert _measurement_label(awg.get_modulation_type(1)) == modulation.type.name.value
    assert _measurement_value(awg.get_modulation_state(1)) == 0.0


def test_06_modulation_enable_toggle(awg: InstroAWG) -> None:
    try:
        awg.modulation_enable(1, True)
        assert _measurement_value(awg.get_modulation_state(1)) == 1.0
    finally:
        awg.modulation_enable(1, False)

    assert _measurement_value(awg.get_modulation_state(1)) == 0.0


# ---------------------------------------------------------------------------
# Burst
# ---------------------------------------------------------------------------


def test_07_config_applies_burst_without_enabling(awg: InstroAWG) -> None:
    burst = awg._config.channels["1"].burst

    assert _measurement_label(awg.get_burst_type(1)) == burst.type.value
    assert _measurement_value(awg.get_burst_ncycles(1)) == burst.ncycles
    assert _measurement_value(awg.get_burst_period(1)) == pytest.approx(burst.period, rel=TIME_TOLERANCE_REL)
    assert _measurement_value(awg.get_burst_state(1)) == 0.0


def test_08_burst_enable_toggle(awg: InstroAWG) -> None:
    try:
        awg.burst_enable(1, True)
        assert _measurement_value(awg.get_burst_state(1)) == 1.0
    finally:
        awg.burst_enable(1, False)

    assert _measurement_value(awg.get_burst_state(1)) == 0.0


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------


def test_09_config_applies_sweep_without_enabling(awg: InstroAWG) -> None:
    sweep = awg._config.channels["1"].sweep

    assert _measurement_label(awg.get_sweep_type(1)) == sweep.type.value
    assert _measurement_value(awg.get_sweep_start_freq(1)) == pytest.approx(
        sweep.start_frequency, rel=FREQUENCY_TOLERANCE_REL
    )
    assert _measurement_value(awg.get_sweep_end_freq(1)) == pytest.approx(
        sweep.end_frequency, rel=FREQUENCY_TOLERANCE_REL
    )
    assert _measurement_value(awg.get_sweep_time(1)) == pytest.approx(sweep.sweep_time, rel=TIME_TOLERANCE_REL)
    assert _measurement_value(awg.get_sweep_stop_hold_time(1)) == pytest.approx(
        sweep.stop_hold_time, rel=TIME_TOLERANCE_REL
    )
    assert _measurement_value(awg.get_sweep_return_time(1)) == pytest.approx(sweep.return_time, rel=TIME_TOLERANCE_REL)
    assert _measurement_value(awg.get_sweep_state(1)) == 0.0


def test_10_sweep_enable_toggle(awg: InstroAWG) -> None:
    try:
        awg.sweep_enable(1, True)
        assert _measurement_value(awg.get_sweep_state(1)) == 1.0
    finally:
        awg.sweep_enable(1, False)

    assert _measurement_value(awg.get_sweep_state(1)) == 0.0


# ---------------------------------------------------------------------------
# Vendor capability differences
# ---------------------------------------------------------------------------


def test_11_sweep_start_hold_time_support_differs_by_instrument(awg: InstroAWG, driver_name: str) -> None:
    """The 33521B has no :HTIMe:STARt node; only DG1022Z declares/roundtrips start_hold_time."""
    if driver_name == KEYSIGHT:
        with pytest.raises(NotImplementedError):
            awg.set_sweep_start_hold_time(1, TEST_SWEEP_HOLD_TIME_S)
        with pytest.raises(NotImplementedError):
            awg.get_sweep_start_hold_time(1)
    else:
        sweep = awg._config.channels["1"].sweep
        assert _measurement_value(awg.get_sweep_start_hold_time(1)) == pytest.approx(
            sweep.start_hold_time, rel=TIME_TOLERANCE_REL
        )


def test_12_channel_count_differs_by_instrument(awg: InstroAWG, driver_name: str) -> None:
    """The 33521B has 1 physical channel; the DG1022Z has 2, and channel 2's config was applied too."""
    if driver_name == KEYSIGHT:
        with pytest.raises(ValueError, match="channel"):
            awg.set_waveform(2, Sine(frequency_hz=TEST_FREQUENCY_HZ))
    else:
        channel_2_config = awg._config.channels["2"]
        expected_waveform = build_waveform(channel_2_config.waveform)

        readback = awg.get_waveform(2)
        assert isinstance(readback, Square)
        assert readback.frequency_hz == pytest.approx(expected_waveform.frequency_hz, rel=FREQUENCY_TOLERANCE_REL)
        assert readback.duty_cycle_pct == pytest.approx(expected_waveform.duty_cycle_pct, rel=0.01)
