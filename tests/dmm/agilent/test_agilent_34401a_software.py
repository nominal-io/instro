"""Software tests for the Agilent/HP/Keysight 34401A DMM driver."""

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest

from instro.dmm.drivers import Agilent34401A
from instro.lib.transports import SerialConfig, VisaConfig


@pytest.fixture
def agilent_visa_cls() -> Iterator[MagicMock]:
    with patch("instro.dmm.drivers.agilent_a34401a.VisaDriver", autospec=True) as driver_cls:
        yield driver_cls


@pytest.fixture
def agilent_visa(agilent_visa_cls: MagicMock) -> MagicMock:
    visa = agilent_visa_cls.return_value
    visa.query.return_value = '0,"No error"'
    return visa


@pytest.fixture
def agilent(agilent_visa_cls: MagicMock) -> Agilent34401A:
    return Agilent34401A("ASRL3::INSTR")


def test_agilent_init_builds_visa_from_resource(agilent_visa_cls: MagicMock) -> None:
    Agilent34401A("ASRL3::INSTR")

    agilent_visa_cls.assert_called_once_with("ASRL3::INSTR")


def test_agilent_init_accepts_prebuilt_connection_config(agilent_visa_cls: MagicMock) -> None:
    config = VisaConfig(
        visa_resource="ASRL3::INSTR",
        serial_config=SerialConfig(baud_rate=19_200),
    )

    Agilent34401A(config)

    agilent_visa_cls.assert_called_once_with(config)


def test_agilent_open_clears_and_takes_remote(agilent: Agilent34401A, agilent_visa: MagicMock) -> None:
    agilent.open()
    agilent_visa.open.assert_called_once()
    assert [c.args[0] for c in agilent_visa.write.call_args_list] == ["*CLS", "SYST:REM"]


def test_agilent_close_closes_visa(agilent: Agilent34401A, agilent_visa: MagicMock) -> None:
    agilent.close()
    agilent_visa.close.assert_called_once()


def test_agilent_measure_dc_voltage_uses_meas_query(agilent: Agilent34401A, agilent_visa: MagicMock) -> None:
    agilent_visa.query.side_effect = ["1.234", '0,"No error"']
    assert agilent.measure_dc_voltage() == pytest.approx(1.234)
    assert agilent_visa.query.call_args_list[0].args == ("MEAS:VOLT:DC?",)


def test_agilent_measure_with_range_and_resolution_includes_params(
    agilent: Agilent34401A, agilent_visa: MagicMock
) -> None:
    agilent.set_dc_voltage_range(10.0)
    agilent.set_digits(6)
    agilent_visa.query.side_effect = ["0.5", '0,"No error"']

    agilent.measure_dc_voltage()

    cmd = agilent_visa.query.call_args_list[0].args[0]
    assert cmd.startswith("MEAS:VOLT:DC?")
    assert "1.000000e+01" in cmd
    assert "1.000000e-05" in cmd


def test_agilent_measure_with_range_only_appends_range(agilent: Agilent34401A, agilent_visa: MagicMock) -> None:
    # Range set without set_digits must still reach the wire (issue #145).
    agilent.set_dc_voltage_range(10.0)
    agilent_visa.query.side_effect = ["0.5", '0,"No error"']

    agilent.measure_dc_voltage()

    cmd = agilent_visa.query.call_args_list[0].args[0]
    assert cmd == "MEAS:VOLT:DC? 1.000000e+01"


def test_agilent_per_function_range_methods_share_state(agilent: Agilent34401A) -> None:
    # The 34401A applies one shared range cache regardless of function. All
    # per-function range setters should write to the same private slot.
    agilent.set_dc_current_range(5.0)
    assert agilent._range == 5.0
    agilent.set_two_wire_resistance_range(1000.0)
    assert agilent._range == 1000.0


def test_agilent_set_digits_rejects_invalid(agilent: Agilent34401A) -> None:
    with pytest.raises(ValueError, match="34401A"):
        agilent.set_digits(7)


def test_agilent_check_errors_passes_on_zero(agilent: Agilent34401A, agilent_visa: MagicMock) -> None:
    agilent_visa.query.return_value = '0,"No error"'
    agilent._check_errors()


def test_agilent_check_errors_raises_on_nonzero(agilent: Agilent34401A, agilent_visa: MagicMock) -> None:
    agilent_visa.query.return_value = '-113,"Undefined header"'
    with pytest.raises(RuntimeError, match="Agilent 34401A reported error"):
        agilent._check_errors()
