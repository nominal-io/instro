"""Software tests for the Siglent SPD3303 PSU driver."""

from collections.abc import Iterator
from unittest.mock import MagicMock, call, patch

import pytest

from instro.lib.exceptions import FeatureNotSupportedError
from instro.psu.drivers.siglent_spd3303 import SiglentSPD3303


@pytest.fixture
def siglent_visa_cls() -> Iterator[MagicMock]:
    with patch("instro.psu.drivers.siglent_spd3303.VisaDriver", autospec=True) as cls:
        yield cls


@pytest.fixture
def siglent_visa(siglent_visa_cls: MagicMock) -> MagicMock:
    visa = siglent_visa_cls.return_value
    visa.query.return_value = '+0,"No error"'
    return visa


@pytest.fixture
def siglent(siglent_visa_cls: MagicMock) -> SiglentSPD3303:
    return SiglentSPD3303("USB0::Siglent::SN::INSTR")


def test_siglent_init_builds_visa_driver_from_resource(siglent_visa_cls: MagicMock) -> None:
    SiglentSPD3303("USB0::Siglent::SN::INSTR")

    siglent_visa_cls.assert_called_once_with("USB0::Siglent::SN::INSTR")


def test_siglent_open_close_delegate_to_visa(siglent: SiglentSPD3303, siglent_visa: MagicMock) -> None:
    siglent.open()
    siglent.close()

    siglent_visa.open.assert_called_once()
    siglent_visa.close.assert_called_once()


def test_siglent_set_voltage_writes_per_channel(siglent: SiglentSPD3303, siglent_visa: MagicMock) -> None:
    siglent.set_voltage(2.5, channel=1)

    siglent_visa.write.assert_called_once_with("CH1:VOLT 2.500")
    siglent_visa.query.assert_called_once_with("SYST:ERR?")


def test_siglent_get_voltage_returns_float(siglent: SiglentSPD3303, siglent_visa: MagicMock) -> None:
    siglent_visa.query.side_effect = ["3.300", '+0,"No error"']

    assert siglent.get_voltage(channel=2) == pytest.approx(3.3)
    assert siglent_visa.query.call_args_list == [call("MEAS:VOLT? CH2"), call("SYST:ERR?")]


def test_siglent_set_current_limit_writes_per_channel(siglent: SiglentSPD3303, siglent_visa: MagicMock) -> None:
    siglent.set_current_limit(0.5, channel=2)

    siglent_visa.write.assert_called_once_with("CH2:CURR 0.500")
    siglent_visa.query.assert_called_once_with("SYST:ERR?")


def test_siglent_get_current_returns_float(siglent: SiglentSPD3303, siglent_visa: MagicMock) -> None:
    siglent_visa.query.side_effect = ["0.250", '+0,"No error"']

    assert siglent.get_current(channel=1) == pytest.approx(0.25)
    assert siglent_visa.query.call_args_list == [call("MEAS:CURR? CH1"), call("SYST:ERR?")]


def test_siglent_output_enable_formats_on_per_channel(siglent: SiglentSPD3303, siglent_visa: MagicMock) -> None:
    siglent.output_enable(True, channel=2)

    siglent_visa.write.assert_called_once_with("OUTP CH2,ON")
    siglent_visa.query.assert_called_once_with("SYST:ERR?")


def test_siglent_output_enable_formats_off_per_channel(siglent: SiglentSPD3303, siglent_visa: MagicMock) -> None:
    siglent.output_enable(False, channel=1)

    siglent_visa.write.assert_called_once_with("OUTP CH1,OFF")
    siglent_visa.query.assert_called_once_with("SYST:ERR?")


def test_siglent_get_output_status_decodes_channel_one_enabled(
    siglent: SiglentSPD3303,
    siglent_visa: MagicMock,
) -> None:
    siglent_visa.query.side_effect = ["10", '+0,"No error"']

    assert siglent.get_output_status(channel=1) is True
    assert siglent_visa.query.call_args_list == [call("SYST:STAT?"), call("SYST:ERR?")]


def test_siglent_get_output_status_decodes_channel_two_enabled(
    siglent: SiglentSPD3303,
    siglent_visa: MagicMock,
) -> None:
    siglent_visa.query.side_effect = ["20", '+0,"No error"']

    assert siglent.get_output_status(channel=2) is True
    assert siglent_visa.query.call_args_list == [call("SYST:STAT?"), call("SYST:ERR?")]


def test_siglent_get_output_status_decodes_disabled(siglent: SiglentSPD3303, siglent_visa: MagicMock) -> None:
    siglent_visa.query.side_effect = ["00", '+0,"No error"']

    assert siglent.get_output_status(channel=1) is False
    assert siglent_visa.query.call_args_list == [call("SYST:STAT?"), call("SYST:ERR?")]


def test_siglent_query_status_decodes_bitmap(siglent: SiglentSPD3303, siglent_visa: MagicMock) -> None:
    siglent_visa.query.side_effect = ["35", '+0,"No error"']

    assert siglent.query_status() == {
        "ch1_mode": "CC",
        "ch2_mode": "CV",
        "psu_mode": "INDEPENDENT",
        "ch1_enable": True,
        "ch2_enable": True,
    }


def test_siglent_check_errors_raises_on_nonzero(siglent: SiglentSPD3303, siglent_visa: MagicMock) -> None:
    siglent_visa.query.return_value = '-100,"Command error"'

    with pytest.raises(RuntimeError, match="Siglent PSU reported error"):
        siglent.set_voltage(1.0, channel=1)


def test_siglent_set_voltage_channel_three_unsupported_does_not_send_scpi(
    siglent: SiglentSPD3303,
    siglent_visa: MagicMock,
) -> None:
    with pytest.raises(FeatureNotSupportedError, match="set_voltage is not supported for channel 3"):
        siglent.set_voltage(2.5, channel=3)

    siglent_visa.write.assert_not_called()
    siglent_visa.query.assert_not_called()


def test_siglent_get_voltage_channel_three_unsupported_does_not_send_scpi(
    siglent: SiglentSPD3303,
    siglent_visa: MagicMock,
) -> None:
    with pytest.raises(FeatureNotSupportedError, match="get_voltage is not supported for channel 3"):
        siglent.get_voltage(channel=3)

    siglent_visa.write.assert_not_called()
    siglent_visa.query.assert_not_called()


def test_siglent_set_current_limit_channel_three_unsupported_does_not_send_scpi(
    siglent: SiglentSPD3303,
    siglent_visa: MagicMock,
) -> None:
    with pytest.raises(FeatureNotSupportedError, match="set_current_limit is not supported for channel 3"):
        siglent.set_current_limit(0.5, channel=3)

    siglent_visa.write.assert_not_called()
    siglent_visa.query.assert_not_called()


def test_siglent_get_current_channel_three_unsupported_does_not_send_scpi(
    siglent: SiglentSPD3303,
    siglent_visa: MagicMock,
) -> None:
    with pytest.raises(FeatureNotSupportedError, match="get_current is not supported for channel 3"):
        siglent.get_current(channel=3)

    siglent_visa.write.assert_not_called()
    siglent_visa.query.assert_not_called()


def test_siglent_output_enable_channel_three_unsupported_does_not_send_scpi(
    siglent: SiglentSPD3303,
    siglent_visa: MagicMock,
) -> None:
    with pytest.raises(FeatureNotSupportedError, match="output_enable is not supported for channel 3"):
        siglent.output_enable(True, channel=3)

    siglent_visa.write.assert_not_called()
    siglent_visa.query.assert_not_called()


def test_siglent_get_output_status_channel_three_unsupported_does_not_send_scpi(
    siglent: SiglentSPD3303,
    siglent_visa: MagicMock,
) -> None:
    with pytest.raises(FeatureNotSupportedError, match="get_output_status is not supported for channel 3"):
        siglent.get_output_status(channel=3)

    siglent_visa.write.assert_not_called()
    siglent_visa.query.assert_not_called()


def test_siglent_set_overvoltage_protection_level_unsupported(
    siglent: SiglentSPD3303,
    siglent_visa: MagicMock,
) -> None:
    with pytest.raises(FeatureNotSupportedError, match="set_overvoltage_protection_level is not supported"):
        siglent.set_overvoltage_protection_level(12.0, channel=1)

    siglent_visa.write.assert_not_called()
    siglent_visa.query.assert_not_called()


def test_siglent_get_overvoltage_protection_level_unsupported(
    siglent: SiglentSPD3303,
    siglent_visa: MagicMock,
) -> None:
    with pytest.raises(FeatureNotSupportedError, match="get_overvoltage_protection_level is not supported"):
        siglent.get_overvoltage_protection_level(channel=1)

    siglent_visa.write.assert_not_called()
    siglent_visa.query.assert_not_called()


def test_siglent_set_overvoltage_protection_enabled_unsupported(
    siglent: SiglentSPD3303,
    siglent_visa: MagicMock,
) -> None:
    with pytest.raises(FeatureNotSupportedError, match="set_overvoltage_protection_enabled is not supported"):
        siglent.set_overvoltage_protection_enabled(True, channel=1)

    siglent_visa.write.assert_not_called()
    siglent_visa.query.assert_not_called()


def test_siglent_get_overvoltage_protection_enabled_unsupported(
    siglent: SiglentSPD3303,
    siglent_visa: MagicMock,
) -> None:
    with pytest.raises(FeatureNotSupportedError, match="get_overvoltage_protection_enabled is not supported"):
        siglent.get_overvoltage_protection_enabled(channel=1)

    siglent_visa.write.assert_not_called()
    siglent_visa.query.assert_not_called()


def test_siglent_set_overvoltage_protection_delay_unsupported(
    siglent: SiglentSPD3303,
    siglent_visa: MagicMock,
) -> None:
    with pytest.raises(FeatureNotSupportedError, match="set_overvoltage_protection_delay is not supported"):
        siglent.set_overvoltage_protection_delay(0.25, channel=1)

    siglent_visa.write.assert_not_called()
    siglent_visa.query.assert_not_called()


def test_siglent_get_overvoltage_protection_delay_unsupported(
    siglent: SiglentSPD3303,
    siglent_visa: MagicMock,
) -> None:
    with pytest.raises(FeatureNotSupportedError, match="get_overvoltage_protection_delay is not supported"):
        siglent.get_overvoltage_protection_delay(channel=1)

    siglent_visa.write.assert_not_called()
    siglent_visa.query.assert_not_called()


def test_siglent_set_overcurrent_protection_level_unsupported(
    siglent: SiglentSPD3303,
    siglent_visa: MagicMock,
) -> None:
    with pytest.raises(FeatureNotSupportedError, match="set_overcurrent_protection_level is not supported"):
        siglent.set_overcurrent_protection_level(1.0, channel=1)

    siglent_visa.write.assert_not_called()
    siglent_visa.query.assert_not_called()


def test_siglent_get_overcurrent_protection_level_unsupported(
    siglent: SiglentSPD3303,
    siglent_visa: MagicMock,
) -> None:
    with pytest.raises(FeatureNotSupportedError, match="get_overcurrent_protection_level is not supported"):
        siglent.get_overcurrent_protection_level(channel=1)

    siglent_visa.write.assert_not_called()
    siglent_visa.query.assert_not_called()


def test_siglent_set_overcurrent_protection_enabled_unsupported(
    siglent: SiglentSPD3303,
    siglent_visa: MagicMock,
) -> None:
    with pytest.raises(FeatureNotSupportedError, match="set_overcurrent_protection_enabled is not supported"):
        siglent.set_overcurrent_protection_enabled(True, channel=1)

    siglent_visa.write.assert_not_called()
    siglent_visa.query.assert_not_called()


def test_siglent_get_overcurrent_protection_enabled_unsupported(
    siglent: SiglentSPD3303,
    siglent_visa: MagicMock,
) -> None:
    with pytest.raises(FeatureNotSupportedError, match="get_overcurrent_protection_enabled is not supported"):
        siglent.get_overcurrent_protection_enabled(channel=1)

    siglent_visa.write.assert_not_called()
    siglent_visa.query.assert_not_called()


def test_siglent_set_remote_sense_enabled_unsupported(
    siglent: SiglentSPD3303,
    siglent_visa: MagicMock,
) -> None:
    with pytest.raises(FeatureNotSupportedError, match="set_remote_sense_enabled is not supported"):
        siglent.set_remote_sense_enabled(True, channel=1)

    siglent_visa.write.assert_not_called()
    siglent_visa.query.assert_not_called()


def test_siglent_get_remote_sense_enabled_unsupported(
    siglent: SiglentSPD3303,
    siglent_visa: MagicMock,
) -> None:
    with pytest.raises(FeatureNotSupportedError, match="get_remote_sense_enabled is not supported"):
        siglent.get_remote_sense_enabled(channel=1)

    siglent_visa.write.assert_not_called()
    siglent_visa.query.assert_not_called()
