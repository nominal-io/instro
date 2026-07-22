"""Optional EA PSB/PSBE hardware smoke tests (bench unit: PSB 10080-60)."""

from __future__ import annotations

import time

import pytest

from instro.eload import LoadMode, SlewRateDirection
from instro.lib.exceptions import FeatureNotSupportedError
from instro.lib.transports import VisaConfig
from instro.psu.drivers.ea_psb import EAPSB

pytestmark = pytest.mark.hardware

# HARDWARE TEST SETUP - EDIT THESE VALUES BEFORE RUNNING THIS FILE.
# Set VISA_ADDRESS to the bench unit's VISA resource string.
# The PSB 10080-60 speaks raw-socket SCPI on port 5025 (not VXI-11/HiSLIP); default VisaConfig terminators work.
# Keep the programmed values comfortably inside the unit's ratings (PSB 10080-60: 80 V / 60 A).
VISA_ADDRESS = "TCPIP::192.168.0.3::5025::SOCKET"
CHANNEL = 1
PROGRAMMED_VOLTAGE = 12.0
PROGRAMMED_CURRENT_LIMIT = 2.0
OVP_LEVEL = 40.0
OCP_LEVEL = 30.0
SINK_CURRENT = 1.0
SINK_POWER = 50.0
SINK_RESISTANCE = 10.0  # within the unit's 0.04-80 ohm sink-resistance band (SYST:NOM:RES:MIN?/MAX?)
SINK_CV_VOLTAGE = 12.0
VOLTAGE_READBACK_TOLERANCE = 0.25
CURRENT_READBACK_TOLERANCE = 0.05


@pytest.fixture(scope="module")
def driver(request: pytest.FixtureRequest) -> EAPSB:
    psb_driver = EAPSB(VisaConfig(visa_resource=VISA_ADDRESS))
    try:
        psb_driver.open()
    except Exception as exc:
        psb_driver.close()
        pytest.skip(f"EA PSB not reachable at {VISA_ADDRESS}: {exc}")

    def cleanup() -> None:
        try:
            psb_driver.output_enable(False, channel=CHANNEL)
        finally:
            psb_driver.close()

    request.addfinalizer(cleanup)
    return psb_driver


@pytest.fixture(autouse=True)
def reset_before_each_test(driver: EAPSB) -> None:
    driver._visa.write("*RST")
    # PSB 10080-60 does not implement *OPC?; a short settle is enough and *RST alone leaves SYST:ERR? clean.
    time.sleep(0.2)
    driver._check_errors()
    driver.output_enable(False, channel=CHANNEL)


def _queue_instrument_error(driver: EAPSB) -> None:
    driver._visa.write("INSTRO:INVALID")


def test_set_voltage(driver: EAPSB) -> None:
    driver.set_current_limit(PROGRAMMED_CURRENT_LIMIT, channel=CHANNEL)
    driver.set_voltage(PROGRAMMED_VOLTAGE, channel=CHANNEL)
    try:
        driver.output_enable(True, channel=CHANNEL)
        time.sleep(1)

        assert driver.get_voltage(channel=CHANNEL) == pytest.approx(
            PROGRAMMED_VOLTAGE,
            abs=VOLTAGE_READBACK_TOLERANCE,
        )
    finally:
        driver.output_enable(False, channel=CHANNEL)


def test_set_voltage_raises_after_instrument_error(driver: EAPSB) -> None:
    _queue_instrument_error(driver)

    with pytest.raises(RuntimeError, match="EA PSB reported error"):
        driver.set_voltage(PROGRAMMED_VOLTAGE, channel=CHANNEL)


def test_set_voltage_invalid_channel_raises_without_instrument_error(driver: EAPSB) -> None:
    with pytest.raises(ValueError, match="EA PSB channel must be 1"):
        driver.set_voltage(PROGRAMMED_VOLTAGE, channel=2)

    driver._check_errors()


def test_get_voltage(driver: EAPSB) -> None:
    driver.set_current_limit(PROGRAMMED_CURRENT_LIMIT, channel=CHANNEL)
    driver.set_voltage(PROGRAMMED_VOLTAGE, channel=CHANNEL)
    try:
        driver.output_enable(True, channel=CHANNEL)
        time.sleep(1)

        voltage = driver.get_voltage(channel=CHANNEL)

        assert voltage == pytest.approx(PROGRAMMED_VOLTAGE, abs=VOLTAGE_READBACK_TOLERANCE)
    finally:
        driver.output_enable(False, channel=CHANNEL)


def test_get_voltage_raises_after_instrument_error(driver: EAPSB) -> None:
    _queue_instrument_error(driver)

    with pytest.raises(RuntimeError, match="EA PSB reported error"):
        driver.get_voltage(channel=CHANNEL)


def test_set_current_limit(driver: EAPSB) -> None:
    driver.set_current_limit(PROGRAMMED_CURRENT_LIMIT, channel=CHANNEL)
    driver.set_voltage(PROGRAMMED_VOLTAGE, channel=CHANNEL)
    try:
        driver.output_enable(True, channel=CHANNEL)
        time.sleep(1)

        # No load attached: the supply should source (approximately) no current.
        assert driver.get_current(channel=CHANNEL) == pytest.approx(0.0, abs=CURRENT_READBACK_TOLERANCE)
    finally:
        driver.output_enable(False, channel=CHANNEL)


def test_set_current_limit_raises_after_instrument_error(driver: EAPSB) -> None:
    _queue_instrument_error(driver)

    with pytest.raises(RuntimeError, match="EA PSB reported error"):
        driver.set_current_limit(PROGRAMMED_CURRENT_LIMIT, channel=CHANNEL)


def test_get_current(driver: EAPSB) -> None:
    driver.set_current_limit(PROGRAMMED_CURRENT_LIMIT, channel=CHANNEL)
    driver.set_voltage(PROGRAMMED_VOLTAGE, channel=CHANNEL)
    try:
        driver.output_enable(True, channel=CHANNEL)
        time.sleep(1)

        current = driver.get_current(channel=CHANNEL)

        assert current == pytest.approx(0.0, abs=CURRENT_READBACK_TOLERANCE)
    finally:
        driver.output_enable(False, channel=CHANNEL)


def test_get_current_raises_after_instrument_error(driver: EAPSB) -> None:
    _queue_instrument_error(driver)

    with pytest.raises(RuntimeError, match="EA PSB reported error"):
        driver.get_current(channel=CHANNEL)


def test_output_enable(driver: EAPSB) -> None:
    driver.set_current_limit(PROGRAMMED_CURRENT_LIMIT, channel=CHANNEL)
    driver.set_voltage(PROGRAMMED_VOLTAGE, channel=CHANNEL)
    try:
        driver.output_enable(True, channel=CHANNEL)
        time.sleep(1)
        assert driver.get_output_status(channel=CHANNEL) is True

        driver.output_enable(False, channel=CHANNEL)
        time.sleep(0.1)
        assert driver.get_output_status(channel=CHANNEL) is False
    finally:
        driver.output_enable(False, channel=CHANNEL)


def test_output_enable_raises_after_instrument_error(driver: EAPSB) -> None:
    _queue_instrument_error(driver)
    try:
        with pytest.raises(RuntimeError, match="EA PSB reported error"):
            driver.output_enable(True, channel=CHANNEL)
    finally:
        driver.output_enable(False, channel=CHANNEL)


def test_output_disable_readback(driver: EAPSB) -> None:
    assert driver.get_output_status(channel=CHANNEL) is False

    driver.set_current_limit(PROGRAMMED_CURRENT_LIMIT, channel=CHANNEL)
    driver.set_voltage(PROGRAMMED_VOLTAGE, channel=CHANNEL)
    try:
        driver.output_enable(True, channel=CHANNEL)
        time.sleep(1)

        assert driver.get_output_status(channel=CHANNEL) is True
    finally:
        driver.output_enable(False, channel=CHANNEL)


def test_get_output_status_raises_after_instrument_error(driver: EAPSB) -> None:
    _queue_instrument_error(driver)

    with pytest.raises(RuntimeError, match="EA PSB reported error"):
        driver.get_output_status(channel=CHANNEL)


def test_overvoltage_protection_level_round_trip(driver: EAPSB) -> None:
    driver.set_overvoltage_protection_level(OVP_LEVEL, channel=CHANNEL)

    assert driver.get_overvoltage_protection_level(channel=CHANNEL) == pytest.approx(OVP_LEVEL, abs=0.1)


def test_set_overvoltage_protection_level_raises_after_instrument_error(driver: EAPSB) -> None:
    _queue_instrument_error(driver)

    with pytest.raises(RuntimeError, match="EA PSB reported error"):
        driver.set_overvoltage_protection_level(OVP_LEVEL, channel=CHANNEL)


def test_overcurrent_protection_level_round_trip(driver: EAPSB) -> None:
    driver.set_overcurrent_protection_level(OCP_LEVEL, channel=CHANNEL)

    assert driver.get_overcurrent_protection_level(channel=CHANNEL) == pytest.approx(OCP_LEVEL, abs=0.1)


def test_set_overcurrent_protection_level_raises_after_instrument_error(driver: EAPSB) -> None:
    _queue_instrument_error(driver)

    with pytest.raises(RuntimeError, match="EA PSB reported error"):
        driver.set_overcurrent_protection_level(OCP_LEVEL, channel=CHANNEL)


def test_set_mode_cc_cp_cv_select_uip(driver: EAPSB) -> None:
    for mode in (LoadMode.CC, LoadMode.CP, LoadMode.CV):
        driver.set_mode(mode, channel=CHANNEL)
        driver._check_errors()


def test_set_mode_cr_selects_uir(driver: EAPSB) -> None:
    driver.set_mode(LoadMode.CR, channel=CHANNEL)
    driver._check_errors()


def test_set_level_cc(driver: EAPSB) -> None:
    driver.set_mode(LoadMode.CC, channel=CHANNEL)

    driver.set_level(LoadMode.CC, SINK_CURRENT, channel=CHANNEL, curr_limit=None)

    driver._check_errors()


def test_set_level_cp(driver: EAPSB) -> None:
    driver.set_mode(LoadMode.CP, channel=CHANNEL)

    driver.set_level(LoadMode.CP, SINK_POWER, channel=CHANNEL, curr_limit=None)

    driver._check_errors()


def test_set_level_cr(driver: EAPSB) -> None:
    driver.set_mode(LoadMode.CR, channel=CHANNEL)

    driver.set_level(LoadMode.CR, SINK_RESISTANCE, channel=CHANNEL, curr_limit=None)

    driver._check_errors()


def test_set_level_cv_with_curr_limit(driver: EAPSB) -> None:
    driver.set_mode(LoadMode.CV, channel=CHANNEL)

    driver.set_level(LoadMode.CV, SINK_CV_VOLTAGE, channel=CHANNEL, curr_limit=PROGRAMMED_CURRENT_LIMIT)

    driver._check_errors()


def test_set_level_raises_after_instrument_error(driver: EAPSB) -> None:
    _queue_instrument_error(driver)

    with pytest.raises(RuntimeError, match="EA PSB reported error"):
        driver.set_level(LoadMode.CC, SINK_CURRENT, channel=CHANNEL, curr_limit=None)


def test_set_range_unsupported(driver: EAPSB) -> None:
    with pytest.raises(FeatureNotSupportedError, match="set_range is not supported by the EA PSB/PSBE"):
        driver.set_range(LoadMode.CC, SINK_CURRENT, channel=CHANNEL)

    driver._check_errors()


def test_set_slewrate_unsupported(driver: EAPSB) -> None:
    with pytest.raises(FeatureNotSupportedError, match="set_slewrate is not supported by the EA PSB/PSBE"):
        driver.set_slewrate(SlewRateDirection.RISE, 1.0, channel=CHANNEL)

    driver._check_errors()


def test_short_output_unsupported(driver: EAPSB) -> None:
    with pytest.raises(FeatureNotSupportedError, match="short_output is not supported by the EA PSB/PSBE"):
        driver.short_output(True, channel=CHANNEL)

    driver._check_errors()


def test_set_overvoltage_protection_enabled_unsupported(driver: EAPSB) -> None:
    with pytest.raises(
        FeatureNotSupportedError,
        match="set_overvoltage_protection_enabled is not supported by the EA PSB/PSBE",
    ):
        driver.set_overvoltage_protection_enabled(True, channel=CHANNEL)


def test_get_overvoltage_protection_enabled_unsupported(driver: EAPSB) -> None:
    with pytest.raises(
        FeatureNotSupportedError,
        match="get_overvoltage_protection_enabled is not supported by the EA PSB/PSBE",
    ):
        driver.get_overvoltage_protection_enabled(channel=CHANNEL)


def test_set_overvoltage_protection_delay_unsupported(driver: EAPSB) -> None:
    with pytest.raises(
        FeatureNotSupportedError,
        match="set_overvoltage_protection_delay is not supported by the EA PSB/PSBE",
    ):
        driver.set_overvoltage_protection_delay(0.1, channel=CHANNEL)


def test_get_overvoltage_protection_delay_unsupported(driver: EAPSB) -> None:
    with pytest.raises(
        FeatureNotSupportedError,
        match="get_overvoltage_protection_delay is not supported by the EA PSB/PSBE",
    ):
        driver.get_overvoltage_protection_delay(channel=CHANNEL)


def test_set_overcurrent_protection_enabled_unsupported(driver: EAPSB) -> None:
    with pytest.raises(
        FeatureNotSupportedError,
        match="set_overcurrent_protection_enabled is not supported by the EA PSB/PSBE",
    ):
        driver.set_overcurrent_protection_enabled(True, channel=CHANNEL)


def test_get_overcurrent_protection_enabled_unsupported(driver: EAPSB) -> None:
    with pytest.raises(
        FeatureNotSupportedError,
        match="get_overcurrent_protection_enabled is not supported by the EA PSB/PSBE",
    ):
        driver.get_overcurrent_protection_enabled(channel=CHANNEL)


def test_set_remote_sense_enabled_unsupported(driver: EAPSB) -> None:
    with pytest.raises(
        FeatureNotSupportedError,
        match="set_remote_sense_enabled is not supported by the EA PSB/PSBE",
    ):
        driver.set_remote_sense_enabled(True, channel=CHANNEL)


def test_get_remote_sense_enabled_unsupported(driver: EAPSB) -> None:
    with pytest.raises(
        FeatureNotSupportedError,
        match="get_remote_sense_enabled is not supported by the EA PSB/PSBE",
    ):
        driver.get_remote_sense_enabled(channel=CHANNEL)


def test_check_errors_raises_after_instrument_error(driver: EAPSB) -> None:
    _queue_instrument_error(driver)

    with pytest.raises(RuntimeError, match="EA PSB reported error"):
        driver._check_errors()
