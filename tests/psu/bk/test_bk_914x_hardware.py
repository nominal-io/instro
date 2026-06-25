"""Optional B&K Precision 914X-series hardware smoke tests."""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from instro.lib.exceptions import FeatureNotSupportedError
from instro.lib.transports import VisaConfig
from instro.psu.drivers.bk_914x import BK914X

pytestmark = pytest.mark.hardware

# HARDWARE TEST SETUP - EDIT THESE VALUES BEFORE RUNNING THIS FILE.
# Set VISA_RESOURCE to the bench unit's VISA resource string. Set VISA_BACKEND
# to "@ivi" for NI-VISA or Keysight IO Libraries, or "@py" for pyvisa-py.
# Keep the programmed values comfortably inside the specific unit's ratings.
VISA_RESOURCE = "TCPIP0::IP_ADDRESS::5025::SOCKET"
VISA_BACKEND = "@ivi"


@dataclass(frozen=True)
class ChannelConfig:
    channel: int
    programmed_voltage: float
    programmed_current_limit: float
    ovp_level: float
    ocp_level: float
    voltage_readback_tolerance: float
    current_readback_tolerance: float


CHANNELS = [
    ChannelConfig(
        channel=1,
        programmed_voltage=1.0,
        programmed_current_limit=0.1,
        ovp_level=5.0,
        ocp_level=0.5,
        voltage_readback_tolerance=0.15,
        current_readback_tolerance=0.02,
    ),
    ChannelConfig(
        channel=2,
        programmed_voltage=1.0,
        programmed_current_limit=0.1,
        ovp_level=5.0,
        ocp_level=0.5,
        voltage_readback_tolerance=0.15,
        current_readback_tolerance=0.02,
    ),
    ChannelConfig(
        channel=3,
        programmed_voltage=1.0,
        programmed_current_limit=0.1,
        ovp_level=5.0,
        ocp_level=0.5,
        voltage_readback_tolerance=0.15,
        current_readback_tolerance=0.02,
    ),
]


def _query_identity(driver: BK914X) -> str:
    with driver._visa.lock():
        return driver._query_locked("*IDN?")


def _shutdown_outputs(driver: BK914X) -> None:
    for channel_config in CHANNELS:
        driver.output_enable(False, channel=channel_config.channel)


def _clear_driver_error_state(driver: BK914X) -> None:
    driver._visa.write("*CLS")
    driver._active_channel = None


@pytest.fixture(scope="module")
def driver() -> Iterator[BK914X]:
    psu_driver = BK914X(
        VisaConfig(
            visa_resource=VISA_RESOURCE,
            visa_backend=VISA_BACKEND,
        )
    )
    opened = False
    try:
        psu_driver.open()
        opened = True
        yield psu_driver
    finally:
        if opened:
            _shutdown_outputs(psu_driver)
        psu_driver.close()


@pytest.fixture(scope="module", autouse=True)
def report_instrument_identity(driver: BK914X) -> None:
    identity = _query_identity(driver)
    print(f"\n[BK914X] *IDN? -> {identity}")


@pytest.fixture(autouse=True)
def reset_before_each_test(driver: BK914X) -> None:
    driver._visa.write("*RST")
    driver._active_channel = None
    driver._check_errors()


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_voltage(driver: BK914X, channel_config: ChannelConfig) -> None:
    driver.set_current_limit(channel_config.programmed_current_limit, channel=channel_config.channel)
    driver.set_voltage(channel_config.programmed_voltage, channel=channel_config.channel)
    try:
        driver.output_enable(True, channel=channel_config.channel)
        time.sleep(1)

        measured_voltage = driver.get_voltage(channel=channel_config.channel)
        print(
            f"\n[BK914X] ch{channel_config.channel} set_voltage: "
            f"programmed={channel_config.programmed_voltage:.3f} V, "
            f"measured={measured_voltage:.3f} V "
            f"(tol +/-{channel_config.voltage_readback_tolerance:.3f} V)"
        )
        assert measured_voltage == pytest.approx(
            channel_config.programmed_voltage,
            abs=channel_config.voltage_readback_tolerance,
        )
    finally:
        driver.output_enable(False, channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_voltage(driver: BK914X, channel_config: ChannelConfig) -> None:
    driver.set_current_limit(channel_config.programmed_current_limit, channel=channel_config.channel)
    driver.set_voltage(channel_config.programmed_voltage, channel=channel_config.channel)
    try:
        driver.output_enable(True, channel=channel_config.channel)
        time.sleep(1)

        voltage = driver.get_voltage(channel=channel_config.channel)
        print(
            f"\n[BK914X] ch{channel_config.channel} get_voltage: "
            f"programmed={channel_config.programmed_voltage:.3f} V, "
            f"measured={voltage:.3f} V "
            f"(tol +/-{channel_config.voltage_readback_tolerance:.3f} V)"
        )

        assert voltage == pytest.approx(
            channel_config.programmed_voltage,
            abs=channel_config.voltage_readback_tolerance,
        )
    finally:
        driver.output_enable(False, channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_current_limit(driver: BK914X, channel_config: ChannelConfig) -> None:
    driver.set_current_limit(channel_config.programmed_current_limit, channel=channel_config.channel)
    driver.set_voltage(channel_config.programmed_voltage, channel=channel_config.channel)
    try:
        driver.output_enable(True, channel=channel_config.channel)
        time.sleep(1)

        measured_current = driver.get_current(channel=channel_config.channel)
        print(
            f"\n[BK914X] ch{channel_config.channel} set_current_limit: "
            f"limit={channel_config.programmed_current_limit:.3f} A, "
            f"measured={measured_current:.3f} A (expected ~0 A, no load)"
        )
        assert measured_current == pytest.approx(
            0.0,
            abs=channel_config.current_readback_tolerance,
        )
    finally:
        driver.output_enable(False, channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_current(driver: BK914X, channel_config: ChannelConfig) -> None:
    driver.set_current_limit(channel_config.programmed_current_limit, channel=channel_config.channel)
    driver.set_voltage(channel_config.programmed_voltage, channel=channel_config.channel)
    try:
        driver.output_enable(True, channel=channel_config.channel)
        time.sleep(1)

        current = driver.get_current(channel=channel_config.channel)
        print(
            f"\n[BK914X] ch{channel_config.channel} get_current: "
            f"limit={channel_config.programmed_current_limit:.3f} A, "
            f"measured={current:.3f} A (expected ~0 A, no load)"
        )

        assert current == pytest.approx(
            0.0,
            abs=channel_config.current_readback_tolerance,
        )
    finally:
        driver.output_enable(False, channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_output_enable(driver: BK914X, channel_config: ChannelConfig) -> None:
    driver.output_enable(True, channel=channel_config.channel)
    status_on = driver.get_output_status(channel=channel_config.channel)
    driver.output_enable(False, channel=channel_config.channel)
    status_off = driver.get_output_status(channel=channel_config.channel)
    print(
        f"\n[BK914X] ch{channel_config.channel} output_enable: "
        f"after enable(True)={status_on}, after enable(False)={status_off}"
    )

    assert status_on is True
    assert status_off is False


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_output_status(driver: BK914X, channel_config: ChannelConfig) -> None:
    status_initial = driver.get_output_status(channel=channel_config.channel)

    driver.output_enable(True, channel=channel_config.channel)
    status_enabled = driver.get_output_status(channel=channel_config.channel)
    print(
        f"\n[BK914X] ch{channel_config.channel} get_output_status: "
        f"initial={status_initial}, after enable(True)={status_enabled}"
    )

    assert status_initial is False
    assert status_enabled is True


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_overvoltage_protection_level(driver: BK914X, channel_config: ChannelConfig) -> None:
    driver.set_overvoltage_protection_level(channel_config.ovp_level, channel=channel_config.channel)

    level = driver.get_overvoltage_protection_level(channel=channel_config.channel)
    print(
        f"\n[BK914X] ch{channel_config.channel} set_overvoltage_protection_level: "
        f"programmed={channel_config.ovp_level:.3f} V, readback={level:.3f} V"
    )

    assert level == pytest.approx(channel_config.ovp_level)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_overvoltage_protection_level(driver: BK914X, channel_config: ChannelConfig) -> None:
    driver.set_overvoltage_protection_level(channel_config.ovp_level, channel=channel_config.channel)

    level = driver.get_overvoltage_protection_level(channel=channel_config.channel)
    print(
        f"\n[BK914X] ch{channel_config.channel} get_overvoltage_protection_level: "
        f"programmed={channel_config.ovp_level:.3f} V, readback={level:.3f} V"
    )

    assert level == pytest.approx(channel_config.ovp_level)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_overvoltage_protection_enabled_unsupported(driver: BK914X, channel_config: ChannelConfig) -> None:
    with pytest.raises(
        FeatureNotSupportedError,
        match="set_overvoltage_protection_enabled is not supported by the B&K Precision 914X-series PSU",
    ):
        driver.set_overvoltage_protection_enabled(False, channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_overvoltage_protection_enabled_unsupported(driver: BK914X, channel_config: ChannelConfig) -> None:
    with pytest.raises(
        FeatureNotSupportedError,
        match="get_overvoltage_protection_enabled is not supported by the B&K Precision 914X-series PSU",
    ):
        driver.get_overvoltage_protection_enabled(channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_overvoltage_protection_delay_unsupported(driver: BK914X, channel_config: ChannelConfig) -> None:
    with pytest.raises(
        FeatureNotSupportedError,
        match="set_overvoltage_protection_delay is not supported by the B&K Precision 914X-series PSU",
    ):
        driver.set_overvoltage_protection_delay(0.1, channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_overvoltage_protection_delay_unsupported(driver: BK914X, channel_config: ChannelConfig) -> None:
    with pytest.raises(
        FeatureNotSupportedError,
        match="get_overvoltage_protection_delay is not supported by the B&K Precision 914X-series PSU",
    ):
        driver.get_overvoltage_protection_delay(channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_overcurrent_protection_level(driver: BK914X, channel_config: ChannelConfig) -> None:
    driver.set_overcurrent_protection_level(channel_config.ocp_level, channel=channel_config.channel)

    level = driver.get_overcurrent_protection_level(channel=channel_config.channel)
    print(
        f"\n[BK914X] ch{channel_config.channel} set_overcurrent_protection_level: "
        f"programmed={channel_config.ocp_level:.3f} A, readback={level:.3f} A"
    )

    assert level == pytest.approx(channel_config.ocp_level)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_overcurrent_protection_level(driver: BK914X, channel_config: ChannelConfig) -> None:
    driver.set_overcurrent_protection_level(channel_config.ocp_level, channel=channel_config.channel)

    level = driver.get_overcurrent_protection_level(channel=channel_config.channel)
    print(
        f"\n[BK914X] ch{channel_config.channel} get_overcurrent_protection_level: "
        f"programmed={channel_config.ocp_level:.3f} A, readback={level:.3f} A"
    )

    assert level == pytest.approx(channel_config.ocp_level)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_overcurrent_protection_enabled(driver: BK914X, channel_config: ChannelConfig) -> None:
    driver.set_overcurrent_protection_enabled(True, channel=channel_config.channel)
    enabled_on = driver.get_overcurrent_protection_enabled(channel=channel_config.channel)

    driver.set_overcurrent_protection_enabled(False, channel=channel_config.channel)
    enabled_off = driver.get_overcurrent_protection_enabled(channel=channel_config.channel)
    print(
        f"\n[BK914X] ch{channel_config.channel} set_overcurrent_protection_enabled: "
        f"after set(True)={enabled_on}, after set(False)={enabled_off}"
    )

    assert enabled_on is True
    assert enabled_off is False


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_overcurrent_protection_enabled(driver: BK914X, channel_config: ChannelConfig) -> None:
    driver.set_overcurrent_protection_enabled(False, channel=channel_config.channel)
    enabled_off = driver.get_overcurrent_protection_enabled(channel=channel_config.channel)

    driver.set_overcurrent_protection_enabled(True, channel=channel_config.channel)
    enabled_on = driver.get_overcurrent_protection_enabled(channel=channel_config.channel)
    print(
        f"\n[BK914X] ch{channel_config.channel} get_overcurrent_protection_enabled: "
        f"after set(False)={enabled_off}, after set(True)={enabled_on}"
    )

    assert enabled_off is False
    assert enabled_on is True


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_remote_sense_enabled(driver: BK914X, channel_config: ChannelConfig) -> None:
    try:
        driver.set_remote_sense_enabled(True, channel=channel_config.channel)
        enabled_on = driver.get_remote_sense_enabled(channel=channel_config.channel)
        print(f"\n[BK914X] ch{channel_config.channel} set_remote_sense_enabled: after set(True)={enabled_on}")
        assert enabled_on is True
    finally:
        driver.set_remote_sense_enabled(False, channel=channel_config.channel)

    enabled_off = driver.get_remote_sense_enabled(channel=channel_config.channel)
    print(f"\n[BK914X] ch{channel_config.channel} set_remote_sense_enabled: after set(False)={enabled_off}")
    assert enabled_off is False


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_remote_sense_enabled(driver: BK914X, channel_config: ChannelConfig) -> None:
    driver.set_remote_sense_enabled(False, channel=channel_config.channel)
    enabled_off = driver.get_remote_sense_enabled(channel=channel_config.channel)
    assert enabled_off is False

    try:
        driver.set_remote_sense_enabled(True, channel=channel_config.channel)
        enabled_on = driver.get_remote_sense_enabled(channel=channel_config.channel)
        print(
            f"\n[BK914X] ch{channel_config.channel} get_remote_sense_enabled: "
            f"after set(False)={enabled_off}, after set(True)={enabled_on}"
        )

        assert enabled_on is True
    finally:
        driver.set_remote_sense_enabled(False, channel=channel_config.channel)


def test_check_errors_raises_after_instrument_error(driver: BK914X) -> None:
    driver._visa.write("INSTRO:INVALID")

    with pytest.raises(RuntimeError, match="BK914X PSU reported error") as excinfo:
        driver._check_errors()
    print(f"\n[BK914X] invalid command reported: {excinfo.value}")


def test_set_voltage_out_of_range_raises_instrument_error(driver: BK914X) -> None:
    try:
        with pytest.raises(RuntimeError, match="BK914X PSU reported error") as excinfo:
            driver.set_voltage(10_000.0, channel=1)
        print(f"\n[BK914X] set_voltage out-of-range reported: {excinfo.value}")
    finally:
        _clear_driver_error_state(driver)


def test_set_current_limit_out_of_range_raises_instrument_error(driver: BK914X) -> None:
    try:
        with pytest.raises(RuntimeError, match="BK914X PSU reported error") as excinfo:
            driver.set_current_limit(10_000.0, channel=1)
        print(f"\n[BK914X] set_current_limit out-of-range reported: {excinfo.value}")
    finally:
        _clear_driver_error_state(driver)


def test_set_overvoltage_protection_level_out_of_range_raises_instrument_error(driver: BK914X) -> None:
    try:
        with pytest.raises(RuntimeError, match="BK914X PSU reported error") as excinfo:
            driver.set_overvoltage_protection_level(10_000.0, channel=1)
        print(f"\n[BK914X] set_overvoltage_protection_level out-of-range reported: {excinfo.value}")
    finally:
        _clear_driver_error_state(driver)


def test_set_overcurrent_protection_level_out_of_range_raises_instrument_error(driver: BK914X) -> None:
    try:
        with pytest.raises(RuntimeError, match="BK914X PSU reported error") as excinfo:
            driver.set_overcurrent_protection_level(10_000.0, channel=1)
        print(f"\n[BK914X] set_overcurrent_protection_level out-of-range reported: {excinfo.value}")
    finally:
        _clear_driver_error_state(driver)


def test_get_voltage_invalid_channel_raises_instrument_error(driver: BK914X) -> None:
    try:
        with pytest.raises(RuntimeError, match="BK914X PSU reported error") as excinfo:
            driver.get_voltage(channel=4)
        print(f"\n[BK914X] get_voltage invalid-channel reported: {excinfo.value}")
    finally:
        _clear_driver_error_state(driver)
