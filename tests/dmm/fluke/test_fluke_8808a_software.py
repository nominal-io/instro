"""Software tests for the Fluke 8808A RS-232 DMM driver (unstable)."""

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest

from instro.dmm.types import MeasurementFunction
from instro.lib import InstroError
from instro.lib.transports.visa import Parity, SerialConfig
from instro.unstable.dmm.drivers import Fluke8808A

IDN = b"FLUKE, 8808A, 1234567, 1.0 D1.0\r\n"
OK = b"=>\r\n"
OPEN_COMMANDS = ["*RST", "*CLS", "REMS", "FORMAT 1", "TRIGGER 1"]


@pytest.fixture
def serial_cls() -> Iterator[MagicMock]:
    with patch("instro.unstable.dmm.drivers.fluke_8808a.serial.Serial", autospec=True) as cls:
        yield cls


@pytest.fixture
def ser(serial_cls: MagicMock) -> MagicMock:
    return serial_cls.return_value


@pytest.fixture
def fluke(ser: MagicMock) -> Fluke8808A:
    """An opened driver on a meter that sends prompts, with the open() traffic cleared."""
    ser.readline.side_effect = [IDN, OK] + [OK] * len(OPEN_COMMANDS)
    driver = Fluke8808A("/dev/ttyUSB0")
    driver.open()
    ser.reset_mock()
    return driver


def _writes(ser: MagicMock) -> list[str]:
    return [c.args[0].decode("ascii").rstrip("\r\n") for c in ser.write.call_args_list]


def test_open_applies_serial_config_and_resets_into_remote(serial_cls: MagicMock, ser: MagicMock) -> None:
    ser.readline.side_effect = [IDN, OK] + [OK] * len(OPEN_COMMANDS)
    Fluke8808A("COM3", SerialConfig(baud_rate=19200, parity=Parity.EVEN, data_bits=7), lockout=True).open()

    kwargs = serial_cls.call_args.kwargs
    assert (kwargs["port"], kwargs["baudrate"], kwargs["parity"], kwargs["bytesize"]) == ("COM3", 19200, "E", 7)
    assert _writes(ser) == ["\x03", "*IDN?", "*RST", "*CLS", "RWLS", "FORMAT 1", "TRIGGER 1"]


def test_open_rejects_other_instrument_and_closes_port(ser: MagicMock) -> None:
    ser.readline.side_effect = [b"FLUKE, 45, 0, 1.0\r\n", OK]
    driver = Fluke8808A("/dev/ttyUSB0")
    with pytest.raises(InstroError, match="not a Fluke 8808A"):
        driver.open()
    ser.close.assert_called_once_with()
    with pytest.raises(InstroError, match="not open"):
        driver.measure_dc_voltage()


def test_without_prompts_writes_chain_esr_check(ser: MagicMock) -> None:
    # No prompt after *IDN? (Echo off, per the manual): every non-query is followed by *ESR?.
    ser.readline.side_effect = [IDN, b""] + [b"0\r\n"] * len(OPEN_COMMANDS) + [b"32\r\n"]
    driver = Fluke8808A("/dev/ttyUSB0")
    driver.open()
    assert _writes(ser)[2:] == [f"{c}; *ESR?" for c in OPEN_COMMANDS]

    with pytest.raises(InstroError, match=r"\*ESR\? = 32"):
        driver.set_measurement_function(MeasurementFunction.AC_VOLTAGE)


def test_close_returns_to_local(fluke: Fluke8808A, ser: MagicMock) -> None:
    ser.readline.side_effect = [OK]
    fluke.close()
    fluke.close()
    assert _writes(ser) == ["LOCS"]
    ser.close.assert_called_once_with()


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
    fluke: Fluke8808A, ser: MagicMock, function: MeasurementFunction, expected: str
) -> None:
    ser.readline.side_effect = [OK]
    fluke.set_measurement_function(function)
    assert _writes(ser) == [expected]


@pytest.mark.parametrize(("prompt", "reason"), [(b"?>\r\n", "command error"), (b"!>\r\n", "execution error")])
def test_error_prompt_raises(fluke: Fluke8808A, ser: MagicMock, prompt: bytes, reason: str) -> None:
    ser.readline.side_effect = [prompt]
    with pytest.raises(InstroError, match=reason):
        fluke.set_digits(5)


def test_measure_skips_echo_and_parses_reading(fluke: Fluke8808A, ser: MagicMock) -> None:
    ser.readline.side_effect = [b"MEAS1?\r\n", b"+1.2345E+0\r\n", OK]
    assert fluke.measure_dc_voltage() == pytest.approx(1.2345)
    assert _writes(ser) == ["MEAS1?"]


def test_read_timeout_raises(fluke: Fluke8808A, ser: MagicMock) -> None:
    ser.readline.side_effect = [b""]
    with pytest.raises(InstroError, match="timed out"):
        fluke.measure_dc_voltage()


@pytest.mark.parametrize(
    ("value", "expected"), [(None, "AUTO"), (5.0, "RANGE 3"), (20.0, "RANGE 3"), (-0.1, "RANGE 1")]
)
def test_set_range_picks_smallest_covering_range(
    fluke: Fluke8808A, ser: MagicMock, value: float | None, expected: str
) -> None:
    ser.readline.side_effect = [OK]
    fluke.set_dc_voltage_range(value)
    assert _writes(ser) == [expected]


def test_set_range_rejects_value_above_top_range(fluke: Fluke8808A, ser: MagicMock) -> None:
    ser.readline.side_effect = [OK, OK]
    fluke.set_measurement_function(MeasurementFunction.AC_VOLTAGE)
    with pytest.raises(ValueError, match="750"):
        fluke.set_ac_voltage_range(1000.0)


def test_set_range_requires_matching_active_function(fluke: Fluke8808A, ser: MagicMock) -> None:
    # RANGE applies to whichever function is active, so a mismatched setter must not send it.
    with pytest.raises(InstroError, match="DC_CURRENT"):
        fluke.set_dc_current_range(0.2)
    ser.write.assert_not_called()


@pytest.mark.parametrize(("digits", "expected"), [(5, "RATE S"), (4, "RATE F")])
def test_set_digits_maps_to_rate(fluke: Fluke8808A, ser: MagicMock, digits: int, expected: str) -> None:
    ser.readline.side_effect = [OK]
    fluke.set_digits(digits)
    assert _writes(ser) == [expected]


def test_set_digits_rejects_unsupported(fluke: Fluke8808A) -> None:
    with pytest.raises(ValueError, match="4 or 5"):
        fluke.set_digits(6)
