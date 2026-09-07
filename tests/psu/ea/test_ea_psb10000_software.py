"""EA PSB 10000-series device and both quadrant drivers over mocked transports.

Two fixture families, because the sections test different layers. Ownership runs a real
``VisaDriver`` over mocked pyvisa, since the accounting under test lives in ``TransportBase``;
the command-mapping sections mock ``VisaDriver`` itself and assert wire-level SCPI.
"""

from collections.abc import Iterator
from unittest.mock import MagicMock, call, patch

import pytest
from pyvisa.constants import InterfaceType

from instro.eload import InstroELoad
from instro.eload.types import LoadMode, SlewRateDirection
from instro.lib.exceptions import FeatureNotSupportedError
from instro.lib.transports import VisaConfig
from instro.psu import InstroPSU
from instro.psu.drivers.ea_psb10000 import EAPSB10000Visa, EAPSB10000VisaSink, EAPSB10000VisaSource

RESOURCE = "TCPIP0::192.168.0.2::5025::SOCKET"

# Canned replies keyed by the exact SCPI query. Measurements carry a unit suffix, OUTP? answers
# ON/OFF, and the box sits in U/I/R so selecting a CC sink has to ask for the U/I/P set.
_QUERY_RESPONSES = {
    "SYST:ERR?": '0,"No error"',
    "SYST:LOCK:OWN?": "REMOTE",
    "SYST:CONF:MODE?": "UIR",
    "MEAS:VOLT?": "48.00V",
    "MEAS:CURR?": "-20.00A",
    "OUTP?": "ON",
    "VOLT:PROT?": "60.00V",
    "CURR:PROT?": "25.00A",
}


def _replies(**overrides: str):
    """Query side effect over the canned replies, with ``overrides`` applied."""
    responses = {**_QUERY_RESPONSES, **overrides}
    return lambda cmd: responses[cmd]


def _writes(mock: MagicMock) -> list[str]:
    return [c.args[0] for c in mock.write.call_args_list]


@pytest.fixture
def resource():
    """Real ``VisaDriver`` over mocked pyvisa; ``open_resource`` returns one mock, so counts are per-box."""
    with patch("instro.lib.transports.visa.pyvisa.ResourceManager") as rm_class:
        rm = MagicMock()
        rm_class.return_value = rm
        inst = MagicMock()
        inst.interface_type = InterfaceType.tcpip
        inst.query.side_effect = _replies()
        rm.open_resource.return_value = inst
        yield rm, inst


@pytest.fixture
def dev(resource) -> EAPSB10000Visa:
    return EAPSB10000Visa(RESOURCE)


@pytest.fixture
def visa_cls() -> Iterator[MagicMock]:
    with patch("instro.psu.drivers.ea_psb10000.VisaDriver", autospec=True) as cls:
        yield cls


@pytest.fixture
def visa(visa_cls: MagicMock) -> MagicMock:
    inst = visa_cls.return_value
    inst.query.side_effect = _replies()
    return inst


@pytest.fixture
def source(visa_cls: MagicMock) -> EAPSB10000VisaSource:
    return EAPSB10000Visa(RESOURCE).source  # type: ignore[return-value]


@pytest.fixture
def sink(visa_cls: MagicMock) -> EAPSB10000VisaSink:
    return EAPSB10000Visa(RESOURCE).sink  # type: ignore[return-value]


# --- device: construction and shared ownership ----------------------------


def test_accepts_a_visa_config(resource) -> None:
    rm, _ = resource
    EAPSB10000Visa(VisaConfig(visa_resource=RESOURCE)).source.open()
    rm.open_resource.assert_called_once_with(RESOURCE)


def test_quadrants_are_stable_and_distinct(dev: EAPSB10000Visa) -> None:
    assert dev.source is dev.source
    assert dev.sink is dev.sink
    assert dev.source is not dev.sink


def test_both_quadrants_share_one_session_and_one_remote_lock(dev: EAPSB10000Visa, resource) -> None:
    """The box grants remote control to one owner, so the setup must run exactly once."""
    rm, inst = resource
    dev.source.open()
    dev.sink.open()

    rm.open_resource.assert_called_once_with(RESOURCE)
    assert _writes(inst).count("SYST:LOCK ON") == 1


def test_session_survives_until_the_last_quadrant_closes(dev: EAPSB10000Visa, resource) -> None:
    _, inst = resource
    dev.source.open()
    dev.sink.open()

    dev.source.close()
    assert "SYST:LOCK OFF" not in _writes(inst)
    inst.close.assert_not_called()
    assert dev.sink.get_voltage(channel=1) == pytest.approx(48.0)

    dev.sink.close()
    assert _writes(inst).count("SYST:LOCK OFF") == 1
    inst.close.assert_called_once()


def test_teardown_releases_the_lock_before_closing_the_session(dev: EAPSB10000Visa, resource) -> None:
    """Closing first would strand a real box remote-locked with no session left to unlock it."""
    _, inst = resource
    dev.source.open()
    dev.source.close()
    assert inst.mock_calls.index(call.write("SYST:LOCK OFF")) < inst.mock_calls.index(call.close())


def test_reopening_the_same_quadrant_is_idempotent(dev: EAPSB10000Visa, resource) -> None:
    _, inst = resource
    dev.source.open()
    dev.source.open()
    assert _writes(inst).count("SYST:LOCK ON") == 1


def test_failed_setup_strands_no_owner(dev: EAPSB10000Visa, resource) -> None:
    """A stranded holder would make the retry report not-first-owner and skip SYST:LOCK ON forever."""
    _, inst = resource
    inst.query.side_effect = lambda cmd: {"SYST:ERR?": '-201,"Cannot be done in local mode"'}[cmd]
    with pytest.raises(RuntimeError, match="reported error"):
        dev.source.open()

    inst.query.side_effect = _replies()
    dev.source.open()
    assert _writes(inst).count("SYST:LOCK ON") == 2


def test_open_raises_when_ownership_is_not_remote(dev: EAPSB10000Visa, resource) -> None:
    _, inst = resource
    inst.query.side_effect = _replies(**{"SYST:LOCK:OWN?": "LOCAL"})
    with pytest.raises(RuntimeError, match="remote lock"):
        dev.source.open()


def test_one_meter_two_sign_conventions(dev: EAPSB10000Visa, resource) -> None:
    """The same MEAS:CURR? reply is source-positive through the PSU and sink-positive through the E-Load."""
    dev.source.open()
    assert dev.source.get_current(channel=1) == pytest.approx(-20.0)
    assert dev.sink.get_current(channel=1) == pytest.approx(20.0)


def test_both_quadrants_drive_one_instrument_pair(dev: EAPSB10000Visa, resource) -> None:
    _, inst = resource
    psu = InstroPSU(name="psb.source", driver=dev.source, num_channels=1)
    eload = InstroELoad(name="psb.sink", driver=dev.sink)

    psu.open()
    eload.open()

    psu.set_voltage(48, channel=1)
    psu.set_current_limit(20, channel=1)
    psu.output_enable(True, channel=1)
    eload.set_mode(LoadMode.CC)
    eload.set_level(10)

    writes = _writes(inst)
    for expected in ("VOLT 48.000", "CURR 20.000", "OUTP ON", "SYST:CONF:MODE UIP", "SINK:CURR 10.000"):
        assert expected in writes

    psu.close()
    eload.close()
    inst.close.assert_called_once()


# --- source quadrant: command mapping -------------------------------------


def test_source_set_voltage_writes_checked(source: EAPSB10000VisaSource, visa: MagicMock) -> None:
    source.set_voltage(48.0, channel=1)
    visa.write.assert_called_once_with("VOLT 48.000")
    visa.query.assert_called_once_with("SYST:ERR?")


def test_source_get_voltage_strips_unit_suffix(source: EAPSB10000VisaSource, visa: MagicMock) -> None:
    assert source.get_voltage(channel=1) == pytest.approx(48.0)
    assert visa.query.call_args_list == [call("MEAS:VOLT?"), call("SYST:ERR?")]


def test_source_set_current_limit_writes_checked(source: EAPSB10000VisaSource, visa: MagicMock) -> None:
    source.set_current_limit(20.0, channel=1)
    visa.write.assert_called_once_with("CURR 20.000")


def test_source_get_current_keeps_the_device_sign_convention(source: EAPSB10000VisaSource, visa: MagicMock) -> None:
    """Source-positive: a regenerating PSB reads negative through the PSU surface."""
    visa.query.side_effect = ["-5.00A", '0,"No error"']
    assert source.get_current(channel=1) == pytest.approx(-5.0)


def test_source_output_enable_writes_on_off_words(source: EAPSB10000VisaSource, visa: MagicMock) -> None:
    source.output_enable(True, channel=1)
    source.output_enable(False, channel=1)
    assert _writes(visa) == ["OUTP ON", "OUTP OFF"]


@pytest.mark.parametrize(("reply", "expected"), [("ON", True), ("OFF", False)])
def test_source_get_output_status_parses_word_reply(
    source: EAPSB10000VisaSource, visa: MagicMock, reply: str, expected: bool
) -> None:
    visa.query.side_effect = [reply, '0,"No error"']
    assert source.get_output_status(channel=1) is expected


def test_source_overvoltage_protection_level_round_trip(source: EAPSB10000VisaSource, visa: MagicMock) -> None:
    source.set_overvoltage_protection_level(60.0, channel=1)
    assert _writes(visa) == ["VOLT:PROT 60.000"]
    assert source.get_overvoltage_protection_level(channel=1) == pytest.approx(60.0)


def test_source_overcurrent_protection_level_round_trip(source: EAPSB10000VisaSource, visa: MagicMock) -> None:
    source.set_overcurrent_protection_level(25.0, channel=1)
    assert _writes(visa) == ["CURR:PROT 25.000"]
    assert source.get_overcurrent_protection_level(channel=1) == pytest.approx(25.0)


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
def test_source_unsupported_optionals_raise_without_touching_the_wire(
    source: EAPSB10000VisaSource, visa: MagicMock, method_name: str, args: tuple[object, ...]
) -> None:
    with pytest.raises(FeatureNotSupportedError, match=f"{method_name} is not supported"):
        getattr(source, method_name)(*args, channel=1)
    visa.write.assert_not_called()
    visa.query.assert_not_called()


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("set_voltage", (48.0,)),
        ("get_voltage", ()),
        ("set_current_limit", (20.0,)),
        ("get_current", ()),
        ("output_enable", (True,)),
        ("get_output_status", ()),
        ("set_overvoltage_protection_level", (60.0,)),
        ("get_overvoltage_protection_level", ()),
        ("set_overcurrent_protection_level", (25.0,)),
        ("get_overcurrent_protection_level", ()),
    ],
)
def test_source_invalid_channel_raises_without_touching_the_wire(
    source: EAPSB10000VisaSource, visa: MagicMock, method_name: str, args: tuple[object, ...]
) -> None:
    """The PSB is single-channel and never puts the channel on the wire, so nothing downstream would catch this."""
    with pytest.raises(ValueError, match="only has a single channel"):
        getattr(source, method_name)(*args, channel=2)
    visa.write.assert_not_called()
    visa.query.assert_not_called()


# --- sink quadrant: command mapping ---------------------------------------


@pytest.mark.parametrize(
    ("mode", "current", "expected"),
    [
        (LoadMode.CC, "UIR", "SYST:CONF:MODE UIP"),
        (LoadMode.CP, "UIR", "SYST:CONF:MODE UIP"),
        (LoadMode.CR, "UIP", "SYST:CONF:MODE UIR"),
    ],
)
def test_sink_set_mode_switches_the_operation_mode_when_the_set_value_needs_unlocking(
    sink: EAPSB10000VisaSink, visa: MagicMock, mode: LoadMode, current: str, expected: str
) -> None:
    visa.query.side_effect = _replies(**{"SYST:CONF:MODE?": current})
    sink.set_mode(mode, channel=1)
    visa.write.assert_called_once_with(expected)


@pytest.mark.parametrize(
    ("mode", "current"),
    [(LoadMode.CC, "UIP"), (LoadMode.CP, "U/I/P"), (LoadMode.CR, "UIR")],
)
def test_sink_set_mode_leaves_a_matching_operation_mode_alone(
    sink: EAPSB10000VisaSink, visa: MagicMock, mode: LoadMode, current: str
) -> None:
    """This configuration is device-global, so the driver must not rewrite it when it already matches."""
    visa.query.side_effect = _replies(**{"SYST:CONF:MODE?": current})
    sink.set_mode(mode, channel=1)
    visa.write.assert_not_called()


@pytest.mark.parametrize(
    ("method_name", "args", "kwargs"),
    [
        ("set_mode", (LoadMode.CV,), {}),
        ("set_level", (LoadMode.CV, 24.0), {"curr_limit": 15.0}),
    ],
)
def test_sink_cv_is_unsupported_and_never_writes_the_shared_voltage_set_value(
    sink: EAPSB10000VisaSink,
    visa: MagicMock,
    method_name: str,
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> None:
    """The PSB's sink quadrant has set values for current, power, and resistance only; VOLT belongs to the source."""
    with pytest.raises(FeatureNotSupportedError, match="CV is not supported"):
        getattr(sink, method_name)(*args, channel=1, **kwargs)
    visa.write.assert_not_called()
    visa.query.assert_not_called()


@pytest.mark.parametrize(
    ("mode", "value", "expected"),
    [
        (LoadMode.CC, 10.0, "SINK:CURR 10.000"),
        (LoadMode.CP, 500.0, "SINK:POW 500.000"),
        (LoadMode.CR, 5.0, "SINK:RES 5.000"),
    ],
)
def test_sink_set_level_writes_the_sink_setpoint(
    sink: EAPSB10000VisaSink, visa: MagicMock, mode: LoadMode, value: float, expected: str
) -> None:
    sink.set_level(mode, value, channel=1, curr_limit=None)
    visa.write.assert_called_once_with(expected)


def test_sink_set_level_ignores_curr_limit_outside_cv(sink: EAPSB10000VisaSink, visa: MagicMock) -> None:
    """``curr_limit`` is CV-only per the contract, and CV is unsupported, so it never reaches the wire."""
    sink.set_level(LoadMode.CC, 10.0, channel=1, curr_limit=15.0)
    assert _writes(visa) == ["SINK:CURR 10.000"]


def test_sink_output_enable_writes_on_off_words(sink: EAPSB10000VisaSink, visa: MagicMock) -> None:
    sink.output_enable(True, channel=1)
    sink.output_enable(False, channel=1)
    assert _writes(visa) == ["OUTP ON", "OUTP OFF"]


def test_sink_get_voltage_strips_unit_suffix(sink: EAPSB10000VisaSink, visa: MagicMock) -> None:
    assert sink.get_voltage(channel=1) == pytest.approx(48.0)
    assert visa.query.call_args_list == [call("MEAS:VOLT?"), call("SYST:ERR?")]


def test_sink_get_current_negates_the_device_sign_to_sink_positive(sink: EAPSB10000VisaSink, visa: MagicMock) -> None:
    """Instro E-Load convention is positive into the load; the PSB reports sink as negative."""
    assert sink.get_current(channel=1) == pytest.approx(20.0)


def test_sink_get_current_reports_sourcing_as_negative(sink: EAPSB10000VisaSink, visa: MagicMock) -> None:
    visa.query.side_effect = ["7.50A", '0,"No error"']
    assert sink.get_current(channel=1) == pytest.approx(-7.5)


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("set_range", (LoadMode.CC, 10.0)),
        ("set_slewrate", (SlewRateDirection.RISE, 1.0)),
        ("short_output", (True,)),
    ],
)
def test_sink_unsupported_methods_raise_without_touching_the_wire(
    sink: EAPSB10000VisaSink, visa: MagicMock, method_name: str, args: tuple[object, ...]
) -> None:
    with pytest.raises(FeatureNotSupportedError, match=f"{method_name} is not supported"):
        getattr(sink, method_name)(*args, channel=1)
    visa.write.assert_not_called()
    visa.query.assert_not_called()


@pytest.mark.parametrize(
    ("method_name", "args", "kwargs"),
    [
        ("set_mode", (LoadMode.CC,), {}),
        ("set_level", (LoadMode.CC, 10.0), {"curr_limit": None}),
        ("output_enable", (True,), {}),
        ("get_voltage", (), {}),
        ("get_current", (), {}),
    ],
)
def test_sink_invalid_channel_raises_without_touching_the_wire(
    sink: EAPSB10000VisaSink,
    visa: MagicMock,
    method_name: str,
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="only has a single channel"):
        getattr(sink, method_name)(*args, channel=2, **kwargs)
    visa.write.assert_not_called()
    visa.query.assert_not_called()
