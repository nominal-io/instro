"""Optional Rigol DP800-series hardware smoke tests."""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta

import pytest
from nominal.core import EventType, NominalClient

from instro.lib.exceptions import FeatureNotSupportedError
from instro.lib.publishers import NominalCorePublisher
from instro.lib.transports import VisaConfig
from instro.lib.types import Measurement
from instro.psu.drivers.rigol_dp800 import RigolDP800

pytestmark = pytest.mark.hardware

# HARDWARE TEST SETUP - EDIT THESE VALUES BEFORE RUNNING THIS FILE.
# Set VISA_RESOURCE to the bench unit's VISA resource string. Set VISA_BACKEND to
# "@ivi" or "" for the system VISA library, or "@py" for pyvisa-py.
# Keep the programmed values comfortably inside the specific unit's ratings.
# For DP811/DP813, set CHANNELS to one channel with supports_remote_sense=True.
# For DP821/DP822, keep channel 2 supports_remote_sense=True and remove channel 3.
VISA_RESOURCE = "TCPIP0::IP_ADDRESS::INSTR"
VISA_BACKEND = "@ivi"
INVALID_CHANNEL = 4

# ---------------------------------------------------------------------------
# Nominal Core configuration — edit before running.
# ---------------------------------------------------------------------------
# Set DATASET_RID to a Nominal dataset RID to stream readings and test events to
# Nominal Core. Leave as None to skip all Nominal publishing (tests still run normally).
DATASET_RID: str | None = None
NOMINAL_PROFILE = "default"


@dataclass(frozen=True)
class ChannelConfig:
    channel: int
    programmed_voltage: float
    programmed_current_limit: float
    ovp_level: float
    ocp_level: float
    voltage_readback_tolerance: float
    current_readback_tolerance: float
    supports_remote_sense: bool


CHANNELS = [
    ChannelConfig(
        channel=1,
        programmed_voltage=1.0,
        programmed_current_limit=0.1,
        ovp_level=5.0,
        ocp_level=0.5,
        voltage_readback_tolerance=0.15,
        current_readback_tolerance=0.02,
        supports_remote_sense=False,
    ),
    ChannelConfig(
        channel=2,
        programmed_voltage=1.0,
        programmed_current_limit=0.1,
        ovp_level=5.0,
        ocp_level=0.5,
        voltage_readback_tolerance=0.15,
        current_readback_tolerance=0.02,
        supports_remote_sense=False,
    ),
    ChannelConfig(
        channel=3,
        programmed_voltage=1.0,
        programmed_current_limit=0.1,
        ovp_level=5.0,
        ocp_level=0.5,
        voltage_readback_tolerance=0.15,
        current_readback_tolerance=0.02,
        supports_remote_sense=False,
    ),
]


# ---------------------------------------------------------------------------
# Nominal Core helpers
# ---------------------------------------------------------------------------


def _get_client() -> NominalClient:
    return NominalClient.from_profile(NOMINAL_PROFILE)


class _EventRecorder:
    """Collects test events during execution, then creates them on a Nominal asset."""

    def __init__(self) -> None:
        self._client: NominalClient | None = None
        self._events: list[dict] = []

    def begin(self) -> None:
        self._client = _get_client()

    def record_event(
        self,
        name: str,
        start_ns: int,
        end_ns: int,
        passed: bool,
        description: str = "",
    ) -> None:
        self._events.append({
            "name": name,
            "start_ns": start_ns,
            "end_ns": end_ns,
            "passed": passed,
            "description": description,
        })

    def finish(self) -> None:
        assert self._client is not None
        asset = self._client.get_or_create_asset_by_properties(
            properties={"device_type": "Rigol DP800-series PSU", "purpose": "hardware-test"},
            name="Rigol DP800-series PSU",
            description="Rigol DP800 PSU under test",
            labels=["rigol-dp800", "hardware-test"],
        )
        for evt in self._events:
            duration_ns = evt["end_ns"] - evt["start_ns"]
            self._client.create_event(
                name=evt["name"],
                type=EventType.SUCCESS if evt["passed"] else EventType.ERROR,
                start=evt["start_ns"],
                duration=timedelta(microseconds=duration_ns / 1_000),
                description=evt["description"],
                assets=[asset],
                properties={"status": "PASS" if evt["passed"] else "FAIL"},
                labels=["dp800-test"],
            )


_recorder = _EventRecorder()
_publisher: NominalCorePublisher | None = None


def _stream(channel: str, value: float) -> None:
    """Stream a single scalar reading to Nominal Core. No-op when DATASET_RID is None."""
    if _publisher is None:
        return
    _publisher.publish(Measurement(
        timestamps=[time.time_ns()],
        channel_data={channel: [value]},
    ))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _shutdown_outputs(driver: RigolDP800) -> None:
    for channel_config in CHANNELS:
        driver.output_enable(False, channel=channel_config.channel)


def _reset_driver(driver: RigolDP800) -> None:
    driver._visa.write("*CLS")
    driver._visa.write("*RST")
    time.sleep(0.25)
    driver._visa.write("*CLS")
    driver._check_errors()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def nominal_session() -> Iterator[None]:
    global _publisher
    if DATASET_RID:
        _recorder.begin()
        _publisher = NominalCorePublisher(dataset_rid=DATASET_RID)
    yield
    if _publisher is not None:
        _publisher.close()
        _publisher = None
    if DATASET_RID:
        try:
            _recorder.finish()
        except Exception as exc:
            print(f"\n*** Failed to create Nominal events: {exc} ***")


@pytest.fixture(scope="module")
def driver() -> Iterator[RigolDP800]:
    psu_driver = RigolDP800(
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


@pytest.fixture(autouse=True)
def reset_before_each_test(driver: RigolDP800) -> None:
    _reset_driver(driver)


@pytest.fixture(autouse=True)
def record_test_event(request: pytest.FixtureRequest) -> Iterator[None]:
    if not DATASET_RID:
        yield
        return
    start_ns = time.time_ns()
    passed = True
    exc_str = ""
    try:
        yield
    except Exception as exc:
        passed = False
        exc_str = str(exc)
        raise
    finally:
        description = request.node.nodeid
        if exc_str:
            description += f"\n\nError: {exc_str}"
        _recorder.record_event(
            request.node.name, start_ns, time.time_ns(), passed=passed, description=description
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_query_status(driver: RigolDP800) -> None:
    status = driver.query_status()

    assert set(status) == {f"ch{channel_config.channel}" for channel_config in CHANNELS}
    for channel_config in CHANNELS:
        channel_status = status[f"ch{channel_config.channel}"]
        assert channel_status["enable"] is False
        assert channel_status["mode"] in {"off", "CC", "CV", "UNREGULATED"}
        assert isinstance(channel_status["OVP"], bool)
        assert isinstance(channel_status["OCP"], bool)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_voltage(driver: RigolDP800, channel_config: ChannelConfig) -> None:
    driver.set_current_limit(channel_config.programmed_current_limit, channel=channel_config.channel)
    driver.set_voltage(channel_config.programmed_voltage, channel=channel_config.channel)
    try:
        driver.output_enable(True, channel=channel_config.channel)
        time.sleep(1)

        voltage = driver.get_voltage(channel=channel_config.channel)
        _stream(f"ch{channel_config.channel}.voltage_v", voltage)
        assert voltage == pytest.approx(
            channel_config.programmed_voltage,
            abs=channel_config.voltage_readback_tolerance,
        )
    finally:
        driver.output_enable(False, channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_voltage(driver: RigolDP800, channel_config: ChannelConfig) -> None:
    driver.set_current_limit(channel_config.programmed_current_limit, channel=channel_config.channel)
    driver.set_voltage(channel_config.programmed_voltage, channel=channel_config.channel)
    try:
        driver.output_enable(True, channel=channel_config.channel)
        time.sleep(1)

        voltage = driver.get_voltage(channel=channel_config.channel)
        _stream(f"ch{channel_config.channel}.voltage_v", voltage)

        assert voltage == pytest.approx(
            channel_config.programmed_voltage,
            abs=channel_config.voltage_readback_tolerance,
        )
    finally:
        driver.output_enable(False, channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_current_limit(driver: RigolDP800, channel_config: ChannelConfig) -> None:
    driver.set_current_limit(channel_config.programmed_current_limit, channel=channel_config.channel)
    driver.set_voltage(channel_config.programmed_voltage, channel=channel_config.channel)
    try:
        driver.output_enable(True, channel=channel_config.channel)
        time.sleep(1)

        current = driver.get_current(channel=channel_config.channel)
        _stream(f"ch{channel_config.channel}.current_a", current)
        assert current == pytest.approx(
            0.0,
            abs=channel_config.current_readback_tolerance,
        )
    finally:
        driver.output_enable(False, channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_current(driver: RigolDP800, channel_config: ChannelConfig) -> None:
    driver.set_current_limit(channel_config.programmed_current_limit, channel=channel_config.channel)
    driver.set_voltage(channel_config.programmed_voltage, channel=channel_config.channel)
    try:
        driver.output_enable(True, channel=channel_config.channel)
        time.sleep(1)

        current = driver.get_current(channel=channel_config.channel)
        _stream(f"ch{channel_config.channel}.current_a", current)

        assert current == pytest.approx(
            0.0,
            abs=channel_config.current_readback_tolerance,
        )
    finally:
        driver.output_enable(False, channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_output_enable(driver: RigolDP800, channel_config: ChannelConfig) -> None:
    driver.set_current_limit(channel_config.programmed_current_limit, channel=channel_config.channel)
    driver.set_voltage(channel_config.programmed_voltage, channel=channel_config.channel)
    try:
        driver.output_enable(True, channel=channel_config.channel)
        state_on = driver.get_output_status(channel=channel_config.channel)
        _stream(f"ch{channel_config.channel}.output_enabled", float(state_on))
        assert state_on is True

        driver.output_enable(False, channel=channel_config.channel)
        state_off = driver.get_output_status(channel=channel_config.channel)
        _stream(f"ch{channel_config.channel}.output_enabled", float(state_off))
        assert state_off is False
    finally:
        driver.output_enable(False, channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_output_status(driver: RigolDP800, channel_config: ChannelConfig) -> None:
    state_initial = driver.get_output_status(channel=channel_config.channel)
    _stream(f"ch{channel_config.channel}.output_enabled", float(state_initial))
    assert state_initial is False

    driver.set_current_limit(channel_config.programmed_current_limit, channel=channel_config.channel)
    driver.set_voltage(channel_config.programmed_voltage, channel=channel_config.channel)
    try:
        driver.output_enable(True, channel=channel_config.channel)

        state_on = driver.get_output_status(channel=channel_config.channel)
        _stream(f"ch{channel_config.channel}.output_enabled", float(state_on))
        assert state_on is True
    finally:
        driver.output_enable(False, channel=channel_config.channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_overvoltage_protection_level(driver: RigolDP800, channel_config: ChannelConfig) -> None:
    driver.set_overvoltage_protection_level(channel_config.ovp_level, channel=channel_config.channel)

    level = driver.get_overvoltage_protection_level(channel=channel_config.channel)
    _stream(f"ch{channel_config.channel}.ovp_level_v", level)
    assert level == pytest.approx(channel_config.ovp_level)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_overvoltage_protection_level(driver: RigolDP800, channel_config: ChannelConfig) -> None:
    driver.set_overvoltage_protection_level(channel_config.ovp_level, channel=channel_config.channel)

    level = driver.get_overvoltage_protection_level(channel=channel_config.channel)
    _stream(f"ch{channel_config.channel}.ovp_level_v", level)

    assert level == pytest.approx(channel_config.ovp_level)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_overvoltage_protection_enabled(driver: RigolDP800, channel_config: ChannelConfig) -> None:
    driver.set_overvoltage_protection_level(channel_config.ovp_level, channel=channel_config.channel)
    driver.set_overvoltage_protection_enabled(True, channel=channel_config.channel)
    state_on = driver.get_overvoltage_protection_enabled(channel=channel_config.channel)
    _stream(f"ch{channel_config.channel}.ovp_enabled", float(state_on))
    assert state_on is True

    driver.set_overvoltage_protection_enabled(False, channel=channel_config.channel)
    state_off = driver.get_overvoltage_protection_enabled(channel=channel_config.channel)
    _stream(f"ch{channel_config.channel}.ovp_enabled", float(state_off))
    assert state_off is False


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_overvoltage_protection_enabled(driver: RigolDP800, channel_config: ChannelConfig) -> None:
    driver.set_overvoltage_protection_level(channel_config.ovp_level, channel=channel_config.channel)
    driver.set_overvoltage_protection_enabled(False, channel=channel_config.channel)
    state_off = driver.get_overvoltage_protection_enabled(channel=channel_config.channel)
    _stream(f"ch{channel_config.channel}.ovp_enabled", float(state_off))
    assert state_off is False

    driver.set_overvoltage_protection_enabled(True, channel=channel_config.channel)

    state_on = driver.get_overvoltage_protection_enabled(channel=channel_config.channel)
    _stream(f"ch{channel_config.channel}.ovp_enabled", float(state_on))
    assert state_on is True


def test_set_overvoltage_protection_delay_unsupported(driver: RigolDP800) -> None:
    with pytest.raises(FeatureNotSupportedError, match="OVP delay command"):
        driver.set_overvoltage_protection_delay(0.1, channel=CHANNELS[0].channel)


def test_get_overvoltage_protection_delay_unsupported(driver: RigolDP800) -> None:
    with pytest.raises(FeatureNotSupportedError, match="OVP delay query"):
        driver.get_overvoltage_protection_delay(channel=CHANNELS[0].channel)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_overcurrent_protection_level(driver: RigolDP800, channel_config: ChannelConfig) -> None:
    driver.set_overcurrent_protection_level(channel_config.ocp_level, channel=channel_config.channel)

    level = driver.get_overcurrent_protection_level(channel=channel_config.channel)
    _stream(f"ch{channel_config.channel}.ocp_level_a", level)
    assert level == pytest.approx(channel_config.ocp_level)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_overcurrent_protection_level(driver: RigolDP800, channel_config: ChannelConfig) -> None:
    driver.set_overcurrent_protection_level(channel_config.ocp_level, channel=channel_config.channel)

    level = driver.get_overcurrent_protection_level(channel=channel_config.channel)
    _stream(f"ch{channel_config.channel}.ocp_level_a", level)

    assert level == pytest.approx(channel_config.ocp_level)


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_overcurrent_protection_enabled(driver: RigolDP800, channel_config: ChannelConfig) -> None:
    driver.set_overcurrent_protection_level(channel_config.ocp_level, channel=channel_config.channel)
    driver.set_overcurrent_protection_enabled(True, channel=channel_config.channel)
    state_on = driver.get_overcurrent_protection_enabled(channel=channel_config.channel)
    _stream(f"ch{channel_config.channel}.ocp_enabled", float(state_on))
    assert state_on is True

    driver.set_overcurrent_protection_enabled(False, channel=channel_config.channel)
    state_off = driver.get_overcurrent_protection_enabled(channel=channel_config.channel)
    _stream(f"ch{channel_config.channel}.ocp_enabled", float(state_off))
    assert state_off is False


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_overcurrent_protection_enabled(driver: RigolDP800, channel_config: ChannelConfig) -> None:
    driver.set_overcurrent_protection_level(channel_config.ocp_level, channel=channel_config.channel)
    driver.set_overcurrent_protection_enabled(False, channel=channel_config.channel)
    state_off = driver.get_overcurrent_protection_enabled(channel=channel_config.channel)
    _stream(f"ch{channel_config.channel}.ocp_enabled", float(state_off))
    assert state_off is False

    driver.set_overcurrent_protection_enabled(True, channel=channel_config.channel)

    state_on = driver.get_overcurrent_protection_enabled(channel=channel_config.channel)
    _stream(f"ch{channel_config.channel}.ocp_enabled", float(state_on))
    assert state_on is True


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_set_remote_sense_enabled(driver: RigolDP800, channel_config: ChannelConfig) -> None:
    if not channel_config.supports_remote_sense:
        with pytest.raises(RuntimeError, match="Rigol DP800-series PSU reported error"):
            driver.set_remote_sense_enabled(True, channel=channel_config.channel)
        return

    try:
        driver.set_remote_sense_enabled(True, channel=channel_config.channel)
        state_on = driver.get_remote_sense_enabled(channel=channel_config.channel)
        _stream(f"ch{channel_config.channel}.remote_sense_enabled", float(state_on))
        assert state_on is True
    finally:
        driver.set_remote_sense_enabled(False, channel=channel_config.channel)

    state_off = driver.get_remote_sense_enabled(channel=channel_config.channel)
    _stream(f"ch{channel_config.channel}.remote_sense_enabled", float(state_off))
    assert state_off is False


@pytest.mark.parametrize("channel_config", CHANNELS, ids=lambda config: f"channel_{config.channel}")
def test_get_remote_sense_enabled(driver: RigolDP800, channel_config: ChannelConfig) -> None:
    if not channel_config.supports_remote_sense:
        with pytest.raises(FeatureNotSupportedError, match="remote sense is not supported"):
            driver.get_remote_sense_enabled(channel=channel_config.channel)
        return

    driver.set_remote_sense_enabled(False, channel=channel_config.channel)
    state_off = driver.get_remote_sense_enabled(channel=channel_config.channel)
    _stream(f"ch{channel_config.channel}.remote_sense_enabled", float(state_off))
    assert state_off is False

    try:
        driver.set_remote_sense_enabled(True, channel=channel_config.channel)

        state_on = driver.get_remote_sense_enabled(channel=channel_config.channel)
        _stream(f"ch{channel_config.channel}.remote_sense_enabled", float(state_on))
        assert state_on is True
    finally:
        driver.set_remote_sense_enabled(False, channel=channel_config.channel)


def test_check_errors_raises_after_instrument_error(driver: RigolDP800) -> None:
    driver._visa.write("INSTRO:INVALID")

    with pytest.raises(RuntimeError, match="Rigol DP800-series PSU reported error"):
        driver._check_errors()


def test_set_voltage_out_of_range_raises_value_error(driver: RigolDP800) -> None:
    with pytest.raises(ValueError, match="out of range"):
        driver.set_voltage(10_000.0, channel=CHANNELS[0].channel)


def test_set_current_limit_out_of_range_raises_value_error(driver: RigolDP800) -> None:
    with pytest.raises(ValueError, match="out of range"):
        driver.set_current_limit(10_000.0, channel=CHANNELS[0].channel)


def test_set_overvoltage_protection_level_out_of_range_raises_value_error(driver: RigolDP800) -> None:
    with pytest.raises(ValueError, match="out of range"):
        driver.set_overvoltage_protection_level(10_000.0, channel=CHANNELS[0].channel)


def test_set_overcurrent_protection_level_out_of_range_raises_value_error(driver: RigolDP800) -> None:
    with pytest.raises(ValueError, match="out of range"):
        driver.set_overcurrent_protection_level(10_000.0, channel=CHANNELS[0].channel)


def test_invalid_channel_raises_instrument_error(driver: RigolDP800) -> None:
    try:
        with pytest.raises(RuntimeError, match="Rigol DP800-series PSU reported error"):
            driver.set_voltage(1.0, channel=INVALID_CHANNEL)
    finally:
        driver._visa.write("*CLS")
