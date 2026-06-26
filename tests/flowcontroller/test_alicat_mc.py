"""Tests for the AlicatMC driver: transport ownership, wire commands, and helpers."""

from collections.abc import Iterator
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from instro.lib.transports.visa import SerialConfig, VisaConfig
from instro.unstable.flowcontroller import (
    MASS_FLOW_KEY,
    PRESSURE_KEY,
    SETPOINT_KEY,
    TEMPERATURE_KEY,
    VOLUMETRIC_FLOW_KEY,
)
from instro.unstable.flowcontroller.drivers.alicat_mc import AlicatMC, GasMixEntry, GasTypeEntry

_SAMPLE_RESPONSE = "A +13.5424 +24.5782 +16.6670 +15.4443 +25.0000 N2"

_VALID_MIX = [GasMixEntry(Decimal("50.00"), 1), GasMixEntry(Decimal("50.00"), 8)]


@pytest.fixture
def visa_driver_cls() -> Iterator[MagicMock]:
    with patch("instro.unstable.flowcontroller.drivers.alicat_mc.VisaDriver", autospec=True) as driver_cls:
        yield driver_cls


@pytest.fixture
def visa_mock(visa_driver_cls: MagicMock) -> MagicMock:
    visa = visa_driver_cls.return_value
    visa.query.return_value = _SAMPLE_RESPONSE
    return visa


@pytest.fixture
def alicat(visa_driver_cls: MagicMock) -> AlicatMC:
    return AlicatMC("ASRL19::INSTR")


def test_init_coerces_string_to_visa_config(visa_driver_cls: MagicMock) -> None:
    AlicatMC("ASRL19::INSTR")

    visa_driver_cls.assert_called_once()
    cfg = visa_driver_cls.call_args[0][0]
    assert isinstance(cfg, VisaConfig)
    assert cfg.visa_resource == "ASRL19::INSTR"
    assert cfg.serial_config.baud_rate == 19200
    assert cfg.terminator.read == "\r"
    assert cfg.terminator.write == "\r"


def test_init_accepts_prebuilt_visa_config(visa_driver_cls: MagicMock) -> None:
    config = VisaConfig(
        visa_resource="ASRL19::INSTR",
        serial_config=SerialConfig(baud_rate=19200),
    )
    AlicatMC(config)
    visa_driver_cls.assert_called_once_with(config)


def test_open_opens_visa(alicat: AlicatMC, visa_mock: MagicMock) -> None:
    alicat.open()
    visa_mock.open.assert_called_once()


def test_close_closes_visa(alicat: AlicatMC, visa_mock: MagicMock) -> None:
    alicat.close()
    visa_mock.close.assert_called_once()


def test_get_flow_data_queries_device_id(alicat: AlicatMC, visa_mock: MagicMock) -> None:
    alicat.get_flow_data()
    visa_mock.query.assert_called_once_with("A")


def test_get_flow_data_parses_response(alicat: AlicatMC, visa_mock: MagicMock) -> None:
    data = alicat.get_flow_data()
    assert data[PRESSURE_KEY] == pytest.approx(13.5424)
    assert data[TEMPERATURE_KEY] == pytest.approx(24.5782)
    assert data[VOLUMETRIC_FLOW_KEY] == pytest.approx(16.6670)
    assert data[MASS_FLOW_KEY] == pytest.approx(15.4443)
    assert data[SETPOINT_KEY] == pytest.approx(25.0)


def test_set_setpoint_sends_command(alicat: AlicatMC, visa_mock: MagicMock) -> None:
    alicat.set_setpoint(50.0)
    visa_mock.query.assert_called_once_with("As50.0")


def test_select_gas_sends_command(alicat: AlicatMC, visa_mock: MagicMock) -> None:
    alicat.known_gas_types = [GasTypeEntry(identifier=8, name="N2")]
    alicat.select_gas("N2")
    visa_mock.query.assert_called_once_with("Ag8")


def test_tare_flow_sends_command(alicat: AlicatMC, visa_mock: MagicMock) -> None:
    alicat.tare_flow()
    visa_mock.query.assert_called_once_with("Av")


def test_custom_device_id_is_used(visa_driver_cls: MagicMock) -> None:
    visa = visa_driver_cls.return_value
    visa.query.return_value = "B +0.0000 +0.0000 +0.0000 +14.7000 +25.0000 Air"
    alicat = AlicatMC("ASRL19::INSTR", device_id="B")
    alicat.get_flow_data()
    visa.query.assert_called_once_with("B")


# --- GasMixEntry.sum_mixture_percentages ---


def test_sum_mixture_percentages_exact_100() -> None:
    entries = [GasMixEntry(Decimal("50.00"), 1), GasMixEntry(Decimal("50.00"), 2)]
    assert GasMixEntry.sum_mixture_percentages(entries) == Decimal("100.00")


def test_sum_mixture_percentages_just_under_100() -> None:
    entries = [GasMixEntry(Decimal("49.99"), 1), GasMixEntry(Decimal("50.00"), 2)]
    assert GasMixEntry.sum_mixture_percentages(entries) == Decimal("99.99")


def test_sum_mixture_percentages_just_over_100() -> None:
    entries = [GasMixEntry(Decimal("50.01"), 1), GasMixEntry(Decimal("50.00"), 2)]
    assert GasMixEntry.sum_mixture_percentages(entries) == Decimal("100.01")


def test_sum_mixture_percentages_three_components_100() -> None:
    entries = [
        GasMixEntry(Decimal("33.33"), 1),
        GasMixEntry(Decimal("33.33"), 2),
        GasMixEntry(Decimal("33.34"), 3),
    ]
    assert GasMixEntry.sum_mixture_percentages(entries) == Decimal("100.00")


# --- define_gas_mixture ValueError tests ---


def test_define_gas_mixture_raises_on_empty_name(alicat: AlicatMC) -> None:
    with pytest.raises(ValueError, match="between 1 and 6 chars"):
        alicat.define_gas_mixture("", _VALID_MIX)


def test_define_gas_mixture_raises_on_name_too_long(alicat: AlicatMC) -> None:
    with pytest.raises(ValueError, match="between 1 and 6 chars"):
        alicat.define_gas_mixture("TOOLONG", _VALID_MIX)


def test_define_gas_mixture_raises_on_none_name(alicat: AlicatMC) -> None:
    with pytest.raises(ValueError, match="between 1 and 6 chars"):
        alicat.define_gas_mixture(None, _VALID_MIX)  # type: ignore[arg-type]


def test_define_gas_mixture_raises_on_too_few_components(alicat: AlicatMC) -> None:
    with pytest.raises(ValueError, match="between 2 and 5 components"):
        alicat.define_gas_mixture("MIX", [GasMixEntry(Decimal("100.00"), 1)])


def test_define_gas_mixture_raises_on_too_many_components(alicat: AlicatMC) -> None:
    six_entries = [GasMixEntry(Decimal("16.67"), i) for i in range(5)] + [GasMixEntry(Decimal("16.65"), 5)]
    with pytest.raises(ValueError, match="between 2 and 5 components"):
        alicat.define_gas_mixture("MIX", six_entries)


def test_define_gas_mixture_raises_when_sum_below_100(alicat: AlicatMC) -> None:
    mixture = [GasMixEntry(Decimal("49.99"), 1), GasMixEntry(Decimal("50.00"), 8)]
    with pytest.raises(ValueError, match="must sum to 100"):
        alicat.define_gas_mixture("MIX", mixture)


def test_define_gas_mixture_raises_when_sum_above_100(alicat: AlicatMC) -> None:
    mixture = [GasMixEntry(Decimal("50.01"), 1), GasMixEntry(Decimal("50.00"), 8)]
    with pytest.raises(ValueError, match="must sum to 100"):
        alicat.define_gas_mixture("MIX", mixture)
