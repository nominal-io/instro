"""Software tests for the Fluke 8808A RS-232 DMM driver (unstable)."""

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
from pyvisa.constants import StatusCode
from pyvisa.errors import VisaIOError

from instro.dmm.types import MeasurementFunction
from instro.lib import InstroError
from instro.lib.transports.visa import VisaConfig
from instro.unstable.dmm.drivers import Fluke8808A

RESOURCE = "ASRL/dev/ttyUSB0::INSTR"
IDN = "FLUKE, 8808A, 1234567, 1.0 D1.0\r"
OK = "=>\r"
OPEN_COMMANDS = ["*RST", "*CLS", "REMS", "FORMAT 1", "TRIGGER 1"]


@pytest.fixture
def visa_cls() -> Iterator[MagicMock]:
    with patch("instro.unstable.dmm.drivers.fluke_8808a.VisaDriver", autospec=True) as cls:
        yield cls


@pytest.fixture
def visa(visa_cls: MagicMock) -> MagicMock:
    return visa_cls.return_value


@pytest.fixture
def fluke(visa: MagicMock) -> Fluke8808A:
    """An opened driver on a meter that sends prompts, with the open() traffic cleared."""
    visa.read.side_effect = [OK, IDN, OK] + [OK] * len(OPEN_COMMANDS)
    driver = Fluke8808A(RESOURCE)
    driver.open()
    visa.reset_mock()
    return driver


def _writes(visa: MagicMock) -> list[str]:
    return [c.args[0] for c in visa.write.call_args_list]


def test_init_builds_visa_from_config(visa_cls: MagicMock) -> None:
    config = VisaConfig(visa_resource=RESOURCE)
    Fluke8808A(config)
    visa_cls.assert_called_once_with(config)


def test_open_clears_and_resets_into_remote(visa: MagicMock) -> None:
    visa.read.side_effect = [OK, IDN, OK] + [OK] * len(OPEN_COMMANDS)
    Fluke8808A(RESOURCE, lockout=True).open()

    visa.write_raw.assert_called_once_with(b"\x03")
    assert _writes(visa) == ["*IDN?", "*RST", "*CLS", "RWLS", "FORMAT 1", "TRIGGER 1"]


def test_open_rejects_other_instrument_and_closes(visa: MagicMock) -> None:
    visa.read.side_effect = [OK, "FLUKE, 45, 0, 1.0\r", OK]
    with pytest.raises(InstroError, match="not a Fluke 8808A"):
        Fluke8808A(RESOURCE).open()
    visa.close.assert_called_once_with()


def test_without_prompts_writes_chain_esr_check(visa: MagicMock) -> None:
    # No prompt after *IDN? (Echo off, per the manual): every non-query is followed by *ESR?.
    timeout = VisaIOError(StatusCode.error_timeout)
    visa.read.side_effect = [OK, IDN, timeout] + ["0\r"] * len(OPEN_COMMANDS) + ["32\r"]
    driver = Fluke8808A(RESOURCE)
    driver.open()
    assert _writes(visa)[1:] == [f"{c}; *ESR?" for c in OPEN_COMMANDS]

    with pytest.raises(InstroError, match=r"\*ESR\? = 32"):
        driver.set_measurement_function(MeasurementFunction.AC_VOLTAGE)


def test_close_returns_to_local(fluke: Fluke8808A, visa: MagicMock) -> None:
    visa.is_open = True
    visa.read.side_effect = [OK]
    fluke.close()
    assert _writes(visa) == ["LOCS"]
    visa.close.assert_called_once_with()


@pytest.mark.parametrize(
    ("function", "expected"),
    [
        (MeasurementFunction.DC_VOLTAGE, "VDC"),
        (MeasurementFunction.AC_VOLTAGE, "VAC"),
        (MeasurementFunction.DC_CURRENT, "ADC"),
        (MeasurementFunction.AC_CURRENT, "AAC"),
        (MeasurementFunction.TWO_WIRE_RESISTANCE, "OHMS; WIRE2"),
        (MeasurementFunction.FOUR_WIRE_RESISTANCE, "OHMS; WIRE4"),
    ],
)
def test_set_measurement_function(
    fluke: Fluke8808A, visa: MagicMock, function: MeasurementFunction, expected: str
) -> None:
    visa.read.side_effect = [OK]
    fluke.set_measurement_function(function)
    assert _writes(visa) == [expected]


@pytest.mark.parametrize(("prompt", "reason"), [("?>\r", "command error"), ("!>\r", "execution error")])
def test_error_prompt_raises(fluke: Fluke8808A, visa: MagicMock, prompt: str, reason: str) -> None:
    visa.read.side_effect = [prompt]
    with pytest.raises(InstroError, match=reason):
        fluke.set_digits(5)


def test_measure_skips_echo_and_parses_reading(fluke: Fluke8808A, visa: MagicMock) -> None:
    visa.read.side_effect = ["MEAS1?\r", "+1.2345E+0\r", OK]
    assert fluke.measure_dc_voltage() == pytest.approx(1.2345)
    assert _writes(visa) == ["MEAS1?"]


@pytest.mark.parametrize(
    ("value", "expected"), [(None, "AUTO"), (5.0, "RANGE 3"), (20.0, "RANGE 3"), (-0.1, "RANGE 1")]
)
def test_set_range_picks_smallest_covering_range(
    fluke: Fluke8808A, visa: MagicMock, value: float | None, expected: str
) -> None:
    visa.read.side_effect = [OK]
    fluke.set_dc_voltage_range(value)
    assert _writes(visa) == [expected]


def test_set_range_rejects_value_above_top_range(fluke: Fluke8808A, visa: MagicMock) -> None:
    visa.read.side_effect = [OK]
    fluke.set_measurement_function(MeasurementFunction.AC_VOLTAGE)
    with pytest.raises(ValueError, match="750"):
        fluke.set_ac_voltage_range(1000.0)


def test_set_range_requires_matching_active_function(fluke: Fluke8808A, visa: MagicMock) -> None:
    # RANGE applies to whichever function is active, so a mismatched setter must not send it.
    with pytest.raises(InstroError, match="DC_CURRENT"):
        fluke.set_dc_current_range(0.2)
    visa.write.assert_not_called()


@pytest.mark.parametrize(("digits", "expected"), [(5, "RATE S"), (4, "RATE F")])
def test_set_digits_maps_to_rate(fluke: Fluke8808A, visa: MagicMock, digits: int, expected: str) -> None:
    visa.read.side_effect = [OK]
    fluke.set_digits(digits)
    assert _writes(visa) == [expected]


def test_set_digits_rejects_unsupported(fluke: Fluke8808A) -> None:
    with pytest.raises(ValueError, match="4 or 5"):
        fluke.set_digits(6)
