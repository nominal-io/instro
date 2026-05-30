"""Tests for Modbus config validation rules.

Tests cover:
- RegisterDef: swap applicability, scale restrictions for coils/discrete
- ModbusConfig: register overlap detection within same type, cross-type allowed
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from instro.lib.types import DeviceInfo, LinearScale
from instro.modbus import ModbusConfig, RegisterDef

CONFIGS_DIR = Path(__file__).parent / "configs"


# ============ RegisterDef Swap Validation ============


class TestSwapValidation:
    def test_word_swap_rejected_for_uint16(self):
        with pytest.raises(ValidationError, match="word_swap is not applicable"):
            RegisterDef(name="bad", starting_address=0, data_type="uint16", word_swap=True)

    def test_word_swap_rejected_for_int16(self):
        with pytest.raises(ValidationError, match="word_swap is not applicable"):
            RegisterDef(name="bad", starting_address=0, data_type="int16", word_swap=True)

    def test_word_swap_rejected_for_bool(self):
        with pytest.raises(ValidationError, match="word_swap is not applicable"):
            RegisterDef(name="bad", starting_address=0, data_type="bool", word_swap=True)

    def test_word_swap_allowed_for_32bit(self):
        reg = RegisterDef(name="ok", starting_address=0, data_type="uint32", word_swap=True)
        assert reg.word_swap is True

    def test_word_swap_allowed_for_64bit(self):
        reg = RegisterDef(name="ok", starting_address=0, data_type="float64", word_swap=True)
        assert reg.word_swap is True

    def test_long_swap_rejected_for_32bit(self):
        with pytest.raises(ValidationError, match="long_swap is not applicable"):
            RegisterDef(name="bad", starting_address=0, data_type="uint32", long_swap=True)

    def test_long_swap_rejected_for_16bit(self):
        with pytest.raises(ValidationError, match="long_swap is not applicable"):
            RegisterDef(name="bad", starting_address=0, data_type="uint16", long_swap=True)

    def test_long_swap_allowed_for_64bit(self):
        reg = RegisterDef(name="ok", starting_address=0, data_type="uint64", long_swap=True)
        assert reg.long_swap is True


# ============ Scale Restrictions ============


class TestScaleRestrictions:
    def test_scale_rejected_for_coil(self):
        with pytest.raises(ValidationError, match="scale is not allowed"):
            RegisterDef(
                name="bad",
                starting_address=0,
                register_type="coil",
                scale=LinearScale(gain=2.0),
            )

    def test_scale_rejected_for_discrete(self):
        with pytest.raises(ValidationError, match="scale is not allowed"):
            RegisterDef(
                name="bad",
                starting_address=0,
                register_type="discrete",
                scale=LinearScale(gain=2.0),
            )

    def test_scale_allowed_for_holding(self):
        reg = RegisterDef(
            name="ok",
            starting_address=0,
            register_type="holding",
            scale=LinearScale(gain=0.1),
        )
        assert reg.scale is not None

    def test_scale_allowed_for_input(self):
        reg = RegisterDef(
            name="ok",
            starting_address=0,
            register_type="input",
            scale=LinearScale(gain=0.1),
        )
        assert reg.scale is not None


# ============ Register Overlap Detection ============


class TestRegisterOverlap:
    def test_overlap_holding_registers_from_json(self):
        with pytest.raises(ValidationError, match="overlap"):
            ModbusConfig.from_json(CONFIGS_DIR / "invalid_overlap_holding_registers.json")

    def test_overlap_coils_from_json(self):
        with pytest.raises(ValidationError, match="overlap"):
            ModbusConfig.from_json(CONFIGS_DIR / "invalid_overlap_coils.json")

    def test_same_address_different_types_is_valid(self):
        config = ModbusConfig.from_json(CONFIGS_DIR / "valid_same_address_different_types.json")
        assert len(config.registers) == 4

    def test_adjacent_registers_no_overlap(self):
        """uint32 at addr 10 spans 10-11, uint16 at addr 12 is fine."""
        config = ModbusConfig(
            device=DeviceInfo(name="adj"),
            registers=[
                RegisterDef(name="a", starting_address=10, data_type="uint32"),
                RegisterDef(name="b", starting_address=12, data_type="uint16"),
            ],
        )
        assert len(config.registers) == 2

    def test_overlap_programmatic(self):
        """uint32 at addr 10 spans 10-11, uint16 at addr 11 overlaps."""
        with pytest.raises(ValidationError, match="overlap"):
            ModbusConfig(
                device=DeviceInfo(name="bad"),
                registers=[
                    RegisterDef(name="a", starting_address=10, data_type="uint32"),
                    RegisterDef(name="b", starting_address=11, data_type="uint16"),
                ],
            )
