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
from instro.unstable.flowcontroller.drivers.alicat_constants import (
    LOOP_VARIABLE_ABS_PRESSURE,
    LOOP_VARIABLE_GAUGE_PRESSURE,
    LOOP_VARIABLE_MASS_FLOW,
    LOOP_VARIABLE_PRESSURE_DIFF,
    LOOP_VARIABLE_VOL_FLOW,
    LoopVariable,
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


def test_process_value_returns_mass_flow(alicat: AlicatMC, visa_mock: MagicMock) -> None:
    def mock_query_response(cmd: str) -> str:
        if "LR" in cmd:
            return "A 37 +15.4443"  # LR response: unit loop_var setpoint
        return _SAMPLE_RESPONSE

    visa_mock.query.side_effect = mock_query_response
    assert alicat.process_value == pytest.approx(15.4443)


def test_process_value_source_is_mass_flow_key(alicat: AlicatMC, visa_mock: MagicMock) -> None:
    def mock_query_response(cmd: str) -> str:
        if "LR" in cmd:
            return "A 37 +15.4443"  # LR response: unit loop_var setpoint
        return _SAMPLE_RESPONSE

    visa_mock.query.side_effect = mock_query_response
    assert alicat.process_value_source == MASS_FLOW_KEY


def test_set_setpoint_sends_command(alicat: AlicatMC, visa_mock: MagicMock) -> None:
    setpoint_to_apply = 50.0
    alicat.set_setpoint(setpoint_to_apply)
    visa_mock.query.assert_called_once_with(f"As {setpoint_to_apply:f}")


def test_select_working_fluid_sends_command(alicat: AlicatMC, visa_mock: MagicMock) -> None:
    alicat.known_gas_types = [GasTypeEntry(identifier=8, name="N2")]
    alicat.select_working_fluid("N2")
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


# --- Loop control variable (process value source) tests ---


def test_process_value_lazy_initializes_loop_variable(alicat: AlicatMC, visa_mock: MagicMock) -> None:
    """Process value property triggers loop variable query on first access."""

    def mock_query_response(cmd: str) -> str:
        if "LR" in cmd:
            return "A 37 +15.4443"  # LR response: unit loop_var setpoint
        return _SAMPLE_RESPONSE

    visa_mock.query.side_effect = mock_query_response
    assert alicat._cached_loop_variable is None
    value = alicat.process_value
    assert alicat._cached_loop_variable is not None
    assert value == pytest.approx(15.4443)


def test_process_value_source_lazy_initializes_loop_variable(alicat: AlicatMC, visa_mock: MagicMock) -> None:
    """Process value source property triggers loop variable query on first access."""

    def mock_query_response(cmd: str) -> str:
        if "LR" in cmd:
            return "A 37 +15.4443"
        return _SAMPLE_RESPONSE

    visa_mock.query.side_effect = mock_query_response
    assert alicat._cached_loop_variable is None
    source = alicat.process_value_source
    assert alicat._cached_loop_variable is not None
    assert source == MASS_FLOW_KEY


def test_process_value_returns_mass_flow_by_default(alicat: AlicatMC, visa_mock: MagicMock) -> None:
    """Process value defaults to mass flow (MASS_FLOW loop variable)."""
    visa_mock.query.return_value = _SAMPLE_RESPONSE
    alicat._cached_loop_variable = LOOP_VARIABLE_MASS_FLOW
    alicat._cached_loop_variable_key = MASS_FLOW_KEY
    assert alicat.process_value == pytest.approx(15.4443)


def test_process_value_returns_volumetric_flow_when_set(alicat: AlicatMC, visa_mock: MagicMock) -> None:
    """Process value returns volumetric flow when loop variable is set to VOLUMETRIC_FLOW."""
    visa_mock.query.return_value = _SAMPLE_RESPONSE
    alicat._cached_loop_variable = LOOP_VARIABLE_VOL_FLOW
    alicat._cached_loop_variable_key = VOLUMETRIC_FLOW_KEY
    assert alicat.process_value == pytest.approx(16.6670)


def test_process_value_returns_pressure_for_absolute_pressure(alicat: AlicatMC, visa_mock: MagicMock) -> None:
    """Process value returns pressure when loop variable is set to ABSOLUTE_PRESSURE."""
    visa_mock.query.return_value = _SAMPLE_RESPONSE
    alicat._cached_loop_variable = LOOP_VARIABLE_ABS_PRESSURE
    alicat._cached_loop_variable_key = PRESSURE_KEY
    assert alicat.process_value == pytest.approx(13.5424)


def test_process_value_returns_pressure_for_gauge_pressure(alicat: AlicatMC, visa_mock: MagicMock) -> None:
    """Process value returns pressure when loop variable is set to GAUGE_PRESSURE."""
    visa_mock.query.return_value = _SAMPLE_RESPONSE
    alicat._cached_loop_variable = LOOP_VARIABLE_GAUGE_PRESSURE
    alicat._cached_loop_variable_key = PRESSURE_KEY
    assert alicat.process_value == pytest.approx(13.5424)


def test_process_value_raises_on_differential_pressure(alicat: AlicatMC, visa_mock: MagicMock) -> None:
    """Process value raises NotImplementedError for differential pressure (unsupported by Alicat MC)."""
    visa_mock.query.return_value = _SAMPLE_RESPONSE
    alicat._cached_loop_variable = LOOP_VARIABLE_PRESSURE_DIFF
    with pytest.raises(NotImplementedError, match="Alicat MC does not support differential pressure"):
        alicat.process_value


def test_set_loop_control_variable_sends_command(alicat: AlicatMC, visa_mock: MagicMock) -> None:
    """Set loop control variable sends LV command with the loop variable code."""
    visa_mock.query.return_value = "A +25.0000"  # LV response: unit setpoint
    setpoint = alicat.set_loop_control_variable(LoopVariable.VOLUMETRIC_FLOW)
    visa_mock.query.assert_called_with("ALV 36")
    assert setpoint == pytest.approx(25.0)


def test_set_loop_control_variable_updates_cache(alicat: AlicatMC, visa_mock: MagicMock) -> None:
    """Set loop control variable updates the cached loop variable and key."""
    visa_mock.query.return_value = "A +25.0000"
    alicat.set_loop_control_variable(LoopVariable.VOLUMETRIC_FLOW)
    assert alicat._cached_loop_variable == LOOP_VARIABLE_VOL_FLOW
    assert alicat._cached_loop_variable_key == VOLUMETRIC_FLOW_KEY


def test_set_loop_control_variable_with_absolute_pressure(alicat: AlicatMC, visa_mock: MagicMock) -> None:
    """Set loop control variable with absolute pressure updates cache correctly."""
    visa_mock.query.return_value = "A +13.5424"
    alicat.set_loop_control_variable(LoopVariable.ABSOLUTE_PRESSURE)
    assert alicat._cached_loop_variable == LOOP_VARIABLE_ABS_PRESSURE
    assert alicat._cached_loop_variable_key == PRESSURE_KEY


# --- define_gas_mixture cache invalidation tests ---


def test_define_gas_mixture_invalidates_gas_type_cache(alicat: AlicatMC, visa_mock: MagicMock) -> None:
    """Define gas mixture invalidates the known_gas_types cache so next call fetches fresh list."""
    alicat.known_gas_types = [GasTypeEntry(identifier=1, name="N2"), GasTypeEntry(identifier=8, name="O2")]
    visa_mock.query.return_value = "A 241"

    alicat.define_gas_mixture("MIX", _VALID_MIX)

    assert alicat.known_gas_types == []


def test_define_gas_mixture_then_select_new_mixture(alicat: AlicatMC, visa_mock: MagicMock) -> None:
    """After defining a mixture, select_working_fluid works once gas list is refreshed."""
    alicat.known_gas_types = [GasTypeEntry(identifier=1, name="N2")]

    def mock_query_response(cmd: str) -> str:
        if "gm " in cmd:
            return "A 241"
        elif "g241" in cmd:
            return _SAMPLE_RESPONSE
        return _SAMPLE_RESPONSE

    visa_mock.query.side_effect = mock_query_response

    alicat.define_gas_mixture("MIX", _VALID_MIX, gas_id=241)

    assert alicat.known_gas_types == []

    alicat.known_gas_types = [
        GasTypeEntry(identifier=1, name="N2"),
        GasTypeEntry(identifier=241, name="MIX"),
    ]
    alicat.select_working_fluid("MIX")

    visa_mock.query.assert_called_with("Ag241")


# --- Regression tests for code review findings ---


def test_set_setpoint_int_uses_rounding(alicat: AlicatMC, visa_mock: MagicMock) -> None:
    """set_setpoint_int should round, not truncate. Vendor manual example: 7296 not 7295."""
    visa_mock.query.return_value = _SAMPLE_RESPONSE
    # Example: 64000 * (15.44/20 - (-20/40)) = 64000 * (0.772 + 0.5) = 81088
    # But from manual: 64000 * (15.44/20) = 49408 (unidirectional 0-20)
    setpoint = alicat.set_setpoint_int(15.44, 20.0, 0.0)
    assert setpoint == pytest.approx(25.0)
    # The sent command should use round(), not int()
    call_args = visa_mock.query.call_args[0][0]
    # 64000 * 15.44/20 = 49408 (exact with rounding)
    assert "49408" in call_args


def test_tare_flow_catches_device_error_not_transport_error(alicat: AlicatMC, visa_mock: MagicMock) -> None:
    """tare_flow should catch AlicatDeviceError (?) but let transport errors propagate."""
    # Simulate device error response
    visa_mock.query.return_value = "?"
    with pytest.raises(NotImplementedError, match="does not support flow rate tare-ing"):
        alicat.tare_flow()

    # Simulate transport error (not-open) - should NOT be caught as NotImplementedError
    visa_mock.query.side_effect = RuntimeError("VISA not open")
    with pytest.raises(RuntimeError, match="VISA not open"):
        alicat.tare_flow()


def test_query_loop_variable_raises_on_unknown_code(alicat: AlicatMC, visa_mock: MagicMock) -> None:
    """_query_loop_variable should raise RuntimeError for unknown loop variable codes."""
    visa_mock.query.return_value = "A 99 +15.4443"  # 99 is not a valid LoopVariable
    with pytest.raises(RuntimeError, match="Unknown loop variable code"):
        alicat._query_loop_variable()
    # Cache should not be set
    assert alicat._cached_loop_variable is None


def test_define_gas_mixture_validates_response_length(alicat: AlicatMC, visa_mock: MagicMock) -> None:
    """define_gas_mixture should raise if response is malformed."""
    visa_mock.query.return_value = "A"  # Too short, missing gas_id
    with pytest.raises(RuntimeError, match="malformed response"):
        alicat.define_gas_mixture("MIX", _VALID_MIX)


def test_sum_mixture_percentages_validates_serialized_sum() -> None:
    """sum_mixture_percentages should validate the serialized (rounded) sum, not the raw sum."""
    # Raw sum: 33.335 + 33.335 + 33.33 = 100.00 (exactly)
    # Serialized: 33.34 + 33.34 + 33.33 = 100.01 (after rounding to 2 dp)
    entries = [
        GasMixEntry(Decimal("33.335"), 1),
        GasMixEntry(Decimal("33.335"), 2),
        GasMixEntry(Decimal("33.33"), 3),
    ]
    # The serialized sum is 100.01, so validation would catch it
    total = GasMixEntry.sum_mixture_percentages(entries)
    assert total == Decimal("100.01")


def test_set_setpoint_sends_fixed_point_notation(alicat: AlicatMC, visa_mock: MagicMock) -> None:
    """set_setpoint should send fixed-point notation, not scientific notation."""
    visa_mock.query.return_value = _SAMPLE_RESPONSE
    alicat.set_setpoint(0.00001)  # Very small value that could trigger scientific notation
    call_args = visa_mock.query.call_args[0][0]
    # f-string :f format gives 6 decimal places by default: "0.000010"
    assert "As 0.000010" in call_args
    assert "e-" not in call_args
