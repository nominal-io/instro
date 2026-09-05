"""Optional EA PSB 10000 hardware smoke tests (bench unit: PSB 10080-60).

Both quadrants live in one file because the PSB grants remote control to a single owner: two
modules would each construct a device, and the second one's ``open`` would be refused by the box.
Opening both views off one device is the point, so the fixture exercises that first.
"""

from __future__ import annotations

import time

import pytest

from instro.eload import ELoadDriverBase, LoadMode, SlewRateDirection
from instro.lib.exceptions import FeatureNotSupportedError
from instro.lib.transports import VisaConfig
from instro.psu import PSUDriverBase
from instro.psu.drivers import EAPSB10000Visa

pytestmark = pytest.mark.hardware

# HARDWARE TEST SETUP - EDIT THESE VALUES BEFORE RUNNING THIS FILE.
# Set VISA_ADDRESS to the bench unit's VISA resource string.
# The PSB 10080-60 speaks raw-socket SCPI on port 5025 (not VXI-11/HiSLIP); default VisaConfig terminators work.
# Keep the programmed values comfortably inside the unit's ratings (PSB 10080-60: 80 V / 60 A).
VISA_ADDRESS = "TCPIP::192.168.0.3::5025::SOCKET"
CHANNEL = 1
PROGRAMMED_VOLTAGE = 12.0
PROGRAMMED_CURRENT_LIMIT = 2.0
NOMINAL_VOLTAGE = 80.0  # PSB 10080-60 rating, used as a sanity bound on readings
OVP_LEVEL = 40.0
OCP_LEVEL = 30.0
SINK_CURRENT = 1.0
SINK_POWER = 50.0
SINK_RESISTANCE = 10.0  # within the unit's 0.04-80 ohm sink-resistance band (SYST:NOM:RES:MIN?/MAX?)
VOLTAGE_READBACK_TOLERANCE = 0.25
# ~0.25% of the unit's 60 A rating. An unloaded PSB reads a small bias current (0.09 A observed on
# the bench unit), so a tighter bound here fails on the instrument's own measurement floor.
CURRENT_READBACK_TOLERANCE = 0.15
# The PSB accepts one socket at a time and needs a moment to release it, so a connect that follows a
# disconnect too closely is refused. Settle between them rather than skipping the whole module.
RECONNECT_SETTLE_SECONDS = 3.0
# The device parses an invalid command and queues the error asynchronously, so a SYST:ERR? that
# follows immediately can read the queue clean. Manual quotes 10-15 ms response over Ethernet.
ERROR_QUEUE_SETTLE_SECONDS = 0.1
CONNECT_ATTEMPTS = 3

ERROR_MATCH = "EA PSB 10000-series reported error"
UNSUPPORTED_MATCH = "is not supported by the EA PSB 10000-series"


@pytest.fixture(scope="module")
def device(request: pytest.FixtureRequest) -> EAPSB10000Visa:
    """Open both quadrants off one device; the second ``open`` failing means shared ownership is broken."""
    psb = EAPSB10000Visa(VisaConfig(visa_resource=VISA_ADDRESS))
    last_error: Exception | None = None
    for attempt in range(CONNECT_ATTEMPTS):
        try:
            psb.source.open()
            psb.sink.open()
            break
        except Exception as exc:
            last_error = exc
            psb.source.close()
            psb.sink.close()
            if attempt < CONNECT_ATTEMPTS - 1:
                time.sleep(RECONNECT_SETTLE_SECONDS)
    else:
        pytest.skip(f"EA PSB not reachable at {VISA_ADDRESS} after {CONNECT_ATTEMPTS} attempts: {last_error}")

    def cleanup() -> None:
        try:
            psb.source.output_enable(False, channel=CHANNEL)
        finally:
            psb.source.close()
            psb.sink.close()
            # Give the box time to release the socket before anything reconnects.
            time.sleep(RECONNECT_SETTLE_SECONDS)

    request.addfinalizer(cleanup)
    return psb


@pytest.fixture
def source(device: EAPSB10000Visa) -> PSUDriverBase:
    return device.source


@pytest.fixture
def sink(device: EAPSB10000Visa) -> ELoadDriverBase:
    return device.sink


@pytest.fixture(autouse=True)
def reset_before_each_test(device: EAPSB10000Visa) -> None:
    device._visa.write("*RST")
    # PSB 10080-60 does not implement *OPC?; a short settle is enough and *RST alone leaves SYST:ERR? clean.
    time.sleep(0.2)
    device._check_errors()
    device.source.output_enable(False, channel=CHANNEL)


def _queue_instrument_error(device: EAPSB10000Visa) -> None:
    device._visa.write("INSTRO:INVALID")
    time.sleep(ERROR_QUEUE_SETTLE_SECONDS)


# --- shared session -------------------------------------------------------


def test_both_quadrants_read_the_same_meter(source: PSUDriverBase, sink: ELoadDriverBase) -> None:
    """One physical meter, opposite conventions: the two views must report exact negatives."""
    assert source.get_current(channel=CHANNEL) == pytest.approx(-sink.get_current(channel=CHANNEL))
    assert source.get_voltage(channel=CHANNEL) == pytest.approx(sink.get_voltage(channel=CHANNEL))


def test_closing_one_quadrant_leaves_the_other_usable(device: EAPSB10000Visa) -> None:
    """The session must outlive the first close, or the surviving view cannot talk to the box."""
    device.source.close()
    try:
        # A successful, in-range reading is the whole assertion. Don't pin the value: with nothing
        # attached the terminals hold residual charge from earlier tests and decay slowly, so this
        # is not reliably zero.
        assert 0.0 <= device.sink.get_voltage(channel=CHANNEL) <= NOMINAL_VOLTAGE
    finally:
        device.source.open()


# --- source quadrant ------------------------------------------------------


def test_set_voltage(source: PSUDriverBase) -> None:
    source.set_current_limit(PROGRAMMED_CURRENT_LIMIT, channel=CHANNEL)
    source.set_voltage(PROGRAMMED_VOLTAGE, channel=CHANNEL)
    try:
        source.output_enable(True, channel=CHANNEL)
        time.sleep(1)

        assert source.get_voltage(channel=CHANNEL) == pytest.approx(
            PROGRAMMED_VOLTAGE,
            abs=VOLTAGE_READBACK_TOLERANCE,
        )
    finally:
        source.output_enable(False, channel=CHANNEL)


def test_set_voltage_raises_after_instrument_error(device: EAPSB10000Visa, source: PSUDriverBase) -> None:
    _queue_instrument_error(device)

    with pytest.raises(RuntimeError, match=ERROR_MATCH):
        source.set_voltage(PROGRAMMED_VOLTAGE, channel=CHANNEL)


def test_set_voltage_invalid_channel_raises_without_instrument_error(
    device: EAPSB10000Visa, source: PSUDriverBase
) -> None:
    with pytest.raises(ValueError, match="only has a single channel"):
        source.set_voltage(PROGRAMMED_VOLTAGE, channel=2)

    device._check_errors()


def test_set_current_limit_sources_no_current_unloaded(source: PSUDriverBase) -> None:
    source.set_current_limit(PROGRAMMED_CURRENT_LIMIT, channel=CHANNEL)
    source.set_voltage(PROGRAMMED_VOLTAGE, channel=CHANNEL)
    try:
        source.output_enable(True, channel=CHANNEL)
        time.sleep(1)

        # No load attached: the supply should source (approximately) no current.
        assert source.get_current(channel=CHANNEL) == pytest.approx(0.0, abs=CURRENT_READBACK_TOLERANCE)
    finally:
        source.output_enable(False, channel=CHANNEL)


def test_get_current_raises_after_instrument_error(device: EAPSB10000Visa, source: PSUDriverBase) -> None:
    _queue_instrument_error(device)

    with pytest.raises(RuntimeError, match=ERROR_MATCH):
        source.get_current(channel=CHANNEL)


def test_output_enable_round_trip(source: PSUDriverBase) -> None:
    assert source.get_output_status(channel=CHANNEL) is False

    source.set_current_limit(PROGRAMMED_CURRENT_LIMIT, channel=CHANNEL)
    source.set_voltage(PROGRAMMED_VOLTAGE, channel=CHANNEL)
    try:
        source.output_enable(True, channel=CHANNEL)
        time.sleep(1)
        assert source.get_output_status(channel=CHANNEL) is True

        source.output_enable(False, channel=CHANNEL)
        time.sleep(0.1)
        assert source.get_output_status(channel=CHANNEL) is False
    finally:
        source.output_enable(False, channel=CHANNEL)


def test_output_enable_raises_after_instrument_error(device: EAPSB10000Visa, source: PSUDriverBase) -> None:
    _queue_instrument_error(device)
    try:
        with pytest.raises(RuntimeError, match=ERROR_MATCH):
            source.output_enable(True, channel=CHANNEL)
    finally:
        source.output_enable(False, channel=CHANNEL)


def test_overvoltage_protection_level_round_trip(source: PSUDriverBase) -> None:
    source.set_overvoltage_protection_level(OVP_LEVEL, channel=CHANNEL)

    assert source.get_overvoltage_protection_level(channel=CHANNEL) == pytest.approx(OVP_LEVEL, abs=0.1)


def test_overcurrent_protection_level_round_trip(source: PSUDriverBase) -> None:
    source.set_overcurrent_protection_level(OCP_LEVEL, channel=CHANNEL)

    assert source.get_overcurrent_protection_level(channel=CHANNEL) == pytest.approx(OCP_LEVEL, abs=0.1)


def test_set_overcurrent_protection_level_raises_after_instrument_error(
    device: EAPSB10000Visa, source: PSUDriverBase
) -> None:
    _queue_instrument_error(device)

    with pytest.raises(RuntimeError, match=ERROR_MATCH):
        source.set_overcurrent_protection_level(OCP_LEVEL, channel=CHANNEL)


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("set_overvoltage_protection_enabled", (True,)),
        ("get_overvoltage_protection_enabled", ()),
        ("set_overvoltage_protection_delay", (0.1,)),
        ("get_overvoltage_protection_delay", ()),
        ("set_overcurrent_protection_enabled", (True,)),
        ("get_overcurrent_protection_enabled", ()),
        ("set_remote_sense_enabled", (True,)),
        ("get_remote_sense_enabled", ()),
    ],
)
def test_unsupported_source_optionals(
    device: EAPSB10000Visa, source: PSUDriverBase, method_name: str, args: tuple[object, ...]
) -> None:
    with pytest.raises(FeatureNotSupportedError, match=UNSUPPORTED_MATCH):
        getattr(source, method_name)(*args, channel=CHANNEL)

    device._check_errors()


# --- sink quadrant --------------------------------------------------------


@pytest.mark.parametrize("mode", [LoadMode.CC, LoadMode.CP])
def test_set_mode_selects_the_uip_set(device: EAPSB10000Visa, sink: ELoadDriverBase, mode: LoadMode) -> None:
    sink.set_mode(mode, channel=CHANNEL)

    device._check_errors()


def test_set_mode_cr_selects_the_uir_set(device: EAPSB10000Visa, sink: ELoadDriverBase) -> None:
    """CR is the only mode that needs a device reconfiguration, since it unlocks SINK:RESistance."""
    sink.set_mode(LoadMode.CR, channel=CHANNEL)

    device._check_errors()


@pytest.mark.parametrize(
    ("mode", "level"),
    [
        (LoadMode.CC, SINK_CURRENT),
        (LoadMode.CP, SINK_POWER),
        (LoadMode.CR, SINK_RESISTANCE),
    ],
)
def test_set_level_accepts_each_supported_mode(
    device: EAPSB10000Visa, sink: ELoadDriverBase, mode: LoadMode, level: float
) -> None:
    sink.set_mode(mode, channel=CHANNEL)

    sink.set_level(mode, level, channel=CHANNEL, curr_limit=None)

    device._check_errors()


def test_set_level_raises_after_instrument_error(device: EAPSB10000Visa, sink: ELoadDriverBase) -> None:
    _queue_instrument_error(device)

    with pytest.raises(RuntimeError, match=ERROR_MATCH):
        sink.set_level(LoadMode.CC, SINK_CURRENT, channel=CHANNEL, curr_limit=None)


def test_cv_is_unsupported_and_never_writes_the_shared_voltage_set_value(
    device: EAPSB10000Visa, sink: ELoadDriverBase, source: PSUDriverBase
) -> None:
    """The PSB has no sink voltage set value, so CV must not reach through to the source's setpoint."""
    source.set_voltage(PROGRAMMED_VOLTAGE, channel=CHANNEL)

    with pytest.raises(FeatureNotSupportedError, match="CV is not supported"):
        sink.set_mode(LoadMode.CV, channel=CHANNEL)
    with pytest.raises(FeatureNotSupportedError, match="CV is not supported"):
        sink.set_level(LoadMode.CV, 1.0, channel=CHANNEL, curr_limit=PROGRAMMED_CURRENT_LIMIT)

    source.output_enable(True, channel=CHANNEL)
    try:
        time.sleep(1)
        assert source.get_voltage(channel=CHANNEL) == pytest.approx(
            PROGRAMMED_VOLTAGE,
            abs=VOLTAGE_READBACK_TOLERANCE,
        )
    finally:
        source.output_enable(False, channel=CHANNEL)
    device._check_errors()


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("set_range", (LoadMode.CC, SINK_CURRENT)),
        ("set_slewrate", (SlewRateDirection.RISE, 1.0)),
        ("short_output", (True,)),
    ],
)
def test_unsupported_sink_methods(
    device: EAPSB10000Visa, sink: ELoadDriverBase, method_name: str, args: tuple[object, ...]
) -> None:
    with pytest.raises(FeatureNotSupportedError, match=UNSUPPORTED_MATCH):
        getattr(sink, method_name)(*args, channel=CHANNEL)

    device._check_errors()


def test_check_errors_raises_after_instrument_error(device: EAPSB10000Visa) -> None:
    _queue_instrument_error(device)

    with pytest.raises(RuntimeError, match=ERROR_MATCH):
        device._check_errors()
