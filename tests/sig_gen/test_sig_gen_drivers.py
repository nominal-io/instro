"""Tests for SigGenDriverBase and RigolDG1022 (mocked-transport)."""

from unittest.mock import MagicMock, call, patch

import pytest

from instro.sig_gen import SigGenDriverBase
from instro.sig_gen.drivers.rigol_dg1022 import RigolDG1022
from instro.sig_gen.types import (
    BurstMode,
    Channel,
    ClockSource,
    ModSource,
    ModWaveform,
    OutputPolarity,
    SweepSpacing,
    TriggerSlope,
    TriggerSource,
    VoltageUnit,
    WaveformType,
)

# --- SigGenDriverBase ---


class _MinimalDriver(SigGenDriverBase):
    @classmethod
    def match_idn(cls, idn: str) -> bool:
        return False

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def set_output_enable(self, channel: Channel, enable: bool) -> None:
        pass

    def set_output_load(self, channel: Channel, ohms: float | None) -> None:
        pass

    def set_output_polarity(self, channel: Channel, polarity: OutputPolarity) -> None:
        pass

    def apply_waveform(self, channel, waveform, frequency, amplitude, offset):
        pass

    def set_function(self, channel: Channel, waveform: WaveformType) -> None:
        pass

    def set_frequency(self, channel: Channel, frequency_hz: float) -> None:
        pass

    def set_amplitude(self, channel: Channel, amplitude: float) -> None:
        pass

    def set_offset(self, channel: Channel, offset_v: float) -> None:
        pass

    def set_phase(self, channel: Channel, phase_deg: float) -> None:
        pass

    def check_errors(self) -> None:
        pass


@pytest.fixture
def minimal_driver() -> _MinimalDriver:
    return _MinimalDriver()


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        # no-arg methods
        ("align_phase", ()),
        # single-arg methods (no channel)
        ("set_sync_output_enable", (True,)),
        ("set_am_state", (True,)),
        ("set_fm_state", (True,)),
        ("set_pm_state", (True,)),
        ("set_fsk_state", (True,)),
        ("set_sweep_state", (True,)),
        ("set_burst_state", (True,)),
        ("upload_arb_waveform_float", ([0.0],)),
        ("upload_arb_waveform_dac", ([0],)),
        ("set_coupling_state", (True,)),
        ("set_clock_source", (ClockSource.INTERNAL,)),
        # two-arg methods (channel + value)
        ("set_voltage_unit", (Channel.CH1, VoltageUnit.VPP)),
        ("set_high_level", (Channel.CH1, 5.0)),
        ("set_low_level", (Channel.CH1, 0.0)),
        ("set_square_duty_cycle", (Channel.CH1, 50.0)),
        ("set_ramp_symmetry", (Channel.CH1, 50.0)),
        ("set_pulse_period", (Channel.CH1, 0.001)),
        ("set_pulse_width", (Channel.CH1, 0.0005)),
        ("set_pulse_duty_cycle", (Channel.CH1, 50.0)),
    ],
)
def test_optional_methods_raise_not_implemented(
    minimal_driver: _MinimalDriver, method_name: str, args: tuple[object, ...]
) -> None:
    with pytest.raises(NotImplementedError, match=method_name):
        getattr(minimal_driver, method_name)(*args)


# --- RigolDG1022 ---


@pytest.fixture
def driver_and_visa():
    with patch("instro.sig_gen.drivers.rigol_dg1022.VisaDriver", autospec=True) as MockVisa:
        mock_visa = MockVisa.return_value
        mock_visa.lock.return_value.__enter__ = MagicMock(return_value=None)
        mock_visa.lock.return_value.__exit__ = MagicMock(return_value=False)
        mock_visa.query.return_value = '0,"No error"'
        driver = RigolDG1022("GPIB::10::INSTR")
        yield driver, mock_visa


def test_match_idn_true(driver_and_visa):
    driver, _ = driver_and_visa
    assert driver.match_idn("RIGOL TECHNOLOGIES,DG1022,DG1000000002,00.01.00.04.00")


def test_match_idn_false(driver_and_visa):
    driver, _ = driver_and_visa
    assert not driver.match_idn("KEYSIGHT,33500B,MY12345678,1.00")


def test_open_close(driver_and_visa):
    driver, mock_visa = driver_and_visa
    driver.open()
    mock_visa.open.assert_called_once()
    driver.close()
    mock_visa.close.assert_called_once()


def test_set_output_enable_ch1(driver_and_visa):
    driver, mock_visa = driver_and_visa
    driver.set_output_enable(Channel.CH1, True)
    mock_visa.write.assert_called_with("OUTP ON")


def test_set_output_enable_ch2(driver_and_visa):
    driver, mock_visa = driver_and_visa
    driver.set_output_enable(Channel.CH2, False)
    mock_visa.write.assert_called_with("OUTP:CH2 OFF")


def test_apply_waveform_ch1(driver_and_visa):
    driver, mock_visa = driver_and_visa
    driver.apply_waveform(Channel.CH1, WaveformType.SINE, 1000, 5, 0)
    mock_visa.write.assert_called_with("APPL:SIN 1000,5,0")


def test_apply_waveform_ch2(driver_and_visa):
    driver, mock_visa = driver_and_visa
    driver.apply_waveform(Channel.CH2, WaveformType.RAMP, 1500, 5, 1)
    mock_visa.write.assert_called_with("APPL:RAMP:CH2 1500,5,1")


def test_set_function_ch2(driver_and_visa):
    driver, mock_visa = driver_and_visa
    driver.set_function(Channel.CH2, WaveformType.SQUARE)
    mock_visa.write.assert_called_with("FUNC:CH2 SQU")


def test_set_frequency_ch1(driver_and_visa):
    driver, mock_visa = driver_and_visa
    driver.set_frequency(Channel.CH1, 1000)
    mock_visa.write.assert_called_with("FREQ 1000")


def test_set_frequency_ch2(driver_and_visa):
    driver, mock_visa = driver_and_visa
    driver.set_frequency(Channel.CH2, 1000)
    mock_visa.write.assert_called_with("FREQ:CH2 1000")


def test_set_amplitude_ch2(driver_and_visa):
    driver, mock_visa = driver_and_visa
    driver.set_amplitude(Channel.CH2, 3)
    mock_visa.write.assert_called_with("VOLT:CH2 3")


def test_set_phase_ch2(driver_and_visa):
    driver, mock_visa = driver_and_visa
    driver.set_phase(Channel.CH2, 90)
    mock_visa.write.assert_called_with("PHAS:CH2 90")


def test_align_phase(driver_and_visa):
    driver, mock_visa = driver_and_visa
    driver.align_phase()
    mock_visa.write.assert_called_with("PHAS:ALIGN")


def test_square_duty_cycle_ch2(driver_and_visa):
    driver, mock_visa = driver_and_visa
    driver.set_square_duty_cycle(Channel.CH2, 50)
    mock_visa.write.assert_called_with("FUNC:SQU:DCYC:CH2 50")


def test_pulse_width_ch2(driver_and_visa):
    driver, mock_visa = driver_and_visa
    driver.set_pulse_width(Channel.CH2, 0.005)
    mock_visa.write.assert_called_with("PULS:WIDT:CH2 0.005")


def test_trigger_source_internal(driver_and_visa):
    driver, mock_visa = driver_and_visa
    driver.set_trigger_source(TriggerSource.INTERNAL)
    # Internal trigger must be IMM, not INT
    mock_visa.write.assert_called_with("TRIG:SOUR IMM")


def test_burst_cycles_infinite(driver_and_visa):
    driver, mock_visa = driver_and_visa
    driver.set_burst_cycles(float("inf"))
    mock_visa.write.assert_called_with("BURS:NCYC INF")


def test_coupling_base_channel(driver_and_visa):
    driver, mock_visa = driver_and_visa
    driver.set_coupling_base_channel(Channel.CH1)
    mock_visa.write.assert_called_with("COUP:BASE:CH1")


def test_copy_channel(driver_and_visa):
    driver, mock_visa = driver_and_visa
    driver.copy_channel(Channel.CH1, Channel.CH2)
    # Must use > literal
    mock_visa.write.assert_called_with("COUP:CHANNC 1>2")


def test_upload_arb_waveform_dac(driver_and_visa):
    driver, mock_visa = driver_and_visa
    driver.upload_arb_waveform_dac([8192, 16383, 8192, 0])
    mock_visa.write.assert_called_with("DATA:DAC VOLATILE,8192,16383,8192,0")


def test_check_errors_raises_on_error(driver_and_visa):
    driver, mock_visa = driver_and_visa
    mock_visa.query.return_value = '1,"Hardware error"'
    with pytest.raises(RuntimeError, match="Rigol DG1022 error"):
        driver.check_errors()


def test_set_output_load_high_z(driver_and_visa):
    driver, mock_visa = driver_and_visa
    driver.set_output_load(Channel.CH1, None)
    mock_visa.write.assert_called_with("OUTP:LOAD INF")


def test_fsk_hop_frequency(driver_and_visa):
    driver, mock_visa = driver_and_visa
    driver.set_fsk_hop_frequency(2000)
    mock_visa.write.assert_called_with("FSK:FREQ 2000")


def test_sweep_spacing_linear(driver_and_visa):
    driver, mock_visa = driver_and_visa
    driver.set_sweep_spacing(SweepSpacing.LINEAR)
    mock_visa.write.assert_called_with("SWE:SPAC LIN")
