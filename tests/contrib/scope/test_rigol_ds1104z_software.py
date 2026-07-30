"""Software tests for the Rigol DS1104Z oscilloscope driver (contrib)."""

import math
from collections.abc import Iterator
from unittest.mock import MagicMock, call, patch

import pytest

from instro.contrib.scope.drivers.rigol_ds1104z import RigolDS1104Z
from instro.scope.types import (
    AcquisitionMode,
    AcquisitionState,
    Coupling,
    ScopeMeasurementType,
    TriggerMode,
    TriggerSlope,
    TriggerStatus,
    TriggerType,
)

CHANNEL = 1


def _make_cm() -> MagicMock:
    """Build a MagicMock that supports `with self._visa.lock():`/`with self._visa.temporary_timeout(ms):`."""
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=None)
    cm.__exit__ = MagicMock(return_value=False)
    return cm


@pytest.fixture
def ds_visa_cls() -> Iterator[MagicMock]:
    with patch("instro.contrib.scope.drivers.rigol_ds1104z.VisaDriver", autospec=True) as cls:
        yield cls


@pytest.fixture
def ds_visa(ds_visa_cls: MagicMock) -> MagicMock:
    visa = ds_visa_cls.return_value
    visa.lock.return_value = _make_cm()
    visa.temporary_timeout.return_value = _make_cm()
    return visa


@pytest.fixture
def ds(ds_visa_cls: MagicMock) -> RigolDS1104Z:
    return RigolDS1104Z("USB0::0x1AB1::0x04CE::DS1ZA000000000::INSTR")


def test_init_passes_resource_to_visa(ds_visa_cls: MagicMock) -> None:
    RigolDS1104Z("USB0::0x1AB1::0x04CE::DS1ZA000000000::INSTR")
    ds_visa_cls.assert_called_once_with("USB0::0x1AB1::0x04CE::DS1ZA000000000::INSTR")


def test_open_close_delegate(ds: RigolDS1104Z, ds_visa: MagicMock) -> None:
    ds.open()
    ds_visa.open.assert_called_once()
    ds_visa.write.assert_called_once_with("*CLS")

    ds.close()
    ds_visa.close.assert_called_once()


def test_check_errors_passes_on_zero(ds: RigolDS1104Z, ds_visa: MagicMock) -> None:
    ds_visa.query.return_value = '0,"No error"'
    ds.check_errors()
    ds_visa.query.assert_called_once_with(":SYSTem:ERRor?")


def test_check_errors_raises_on_nonzero(ds: RigolDS1104Z, ds_visa: MagicMock) -> None:
    ds_visa.query.return_value = '-113,"Undefined header"'
    with pytest.raises(RuntimeError, match="-113"):
        ds.check_errors()


def test_set_get_vertical_scale(ds: RigolDS1104Z, ds_visa: MagicMock) -> None:
    ds.set_vertical_scale(0.5, channel=CHANNEL)
    ds_visa.write.assert_called_once_with(":CHANnel1:SCALe 5.000000E-01")

    ds_visa.query.return_value = "5.000000e-01"
    assert ds.get_vertical_scale(channel=CHANNEL) == pytest.approx(0.5)
    ds_visa.query.assert_called_once_with(":CHANnel1:SCALe?")


def test_set_get_vertical_offset(ds: RigolDS1104Z, ds_visa: MagicMock) -> None:
    ds.set_vertical_offset(0.1, channel=CHANNEL)
    ds_visa.write.assert_called_once_with(":CHANnel1:OFFSet 1.000000E-01")

    ds_visa.query.return_value = "1.000000e-01"
    assert ds.get_vertical_offset(channel=CHANNEL) == pytest.approx(0.1)


def test_set_get_coupling(ds: RigolDS1104Z, ds_visa: MagicMock) -> None:
    ds.set_coupling(Coupling.AC, channel=CHANNEL)
    ds_visa.write.assert_called_once_with(":CHANnel1:COUPling AC")

    ds_visa.query.return_value = "DC"
    assert ds.get_coupling(channel=CHANNEL) == Coupling.DC


def test_set_get_probe_attenuation(ds: RigolDS1104Z, ds_visa: MagicMock) -> None:
    ds.set_probe_attenuation(10, channel=CHANNEL)
    ds_visa.write.assert_called_once_with(":CHANnel1:PROBe 10")

    ds_visa.query.return_value = "1.000000e+01"
    assert ds.get_probe_attenuation(channel=CHANNEL) == pytest.approx(10.0)


def test_set_get_horizontal_scale(ds: RigolDS1104Z, ds_visa: MagicMock) -> None:
    ds.set_horizontal_scale(1e-6)
    ds_visa.write.assert_called_once_with(":TIMebase:MAIN:SCALe 1.000000E-06")

    ds_visa.query.return_value = "1.000000e-06"
    assert ds.get_horizontal_scale() == pytest.approx(1e-6)


def test_get_sample_rate(ds: RigolDS1104Z, ds_visa: MagicMock) -> None:
    ds_visa.query.return_value = "2.000000e+09"
    assert ds.get_sample_rate() == pytest.approx(2e9)
    ds_visa.query.assert_called_once_with(":ACQuire:SRATe?")


def test_set_get_acquisition_mode(ds: RigolDS1104Z, ds_visa: MagicMock) -> None:
    ds.set_acquisition_mode(AcquisitionMode.AVERAGE)
    ds_visa.write.assert_called_once_with(":ACQuire:TYPE AVERages")

    ds_visa.query.return_value = "HRES"
    assert ds.get_acquisition_mode() == AcquisitionMode.HIGH_RESOLUTION


def test_acquisition_mode_envelope_rejected(ds: RigolDS1104Z, ds_visa: MagicMock) -> None:
    with pytest.raises(NotImplementedError, match="ENVELOPE"):
        ds.set_acquisition_mode(AcquisitionMode.ENVELOPE)
    ds_visa.write.assert_not_called()


def test_set_get_average_count(ds: RigolDS1104Z, ds_visa: MagicMock) -> None:
    ds.set_average_count(128)
    ds_visa.write.assert_called_once_with(":ACQuire:AVERages 128")

    ds_visa.query.return_value = "128"
    assert ds.get_average_count() == 128


def test_run_stop_single(ds: RigolDS1104Z, ds_visa: MagicMock) -> None:
    ds.run()
    ds.stop()
    ds.single()
    assert ds_visa.write.call_args_list == [call(":RUN"), call(":STOP"), call(":SINGle")]


def test_digitize_completes_on_stop(ds: RigolDS1104Z, ds_visa: MagicMock) -> None:
    ds_visa.query.return_value = "STOP"
    ds.digitize(timeout=1.0)
    ds_visa.write.assert_called_once_with(":SINGle")


def test_digitize_raises_timeout(ds: RigolDS1104Z, ds_visa: MagicMock) -> None:
    ds_visa.query.return_value = "WAIT"
    with pytest.raises(TimeoutError):
        ds.digitize(timeout=0.05)
    ds_visa.write.assert_any_call(":STOP")


def test_get_acquisition_state(ds: RigolDS1104Z, ds_visa: MagicMock) -> None:
    ds_visa.query.return_value = "STOP"
    assert ds.get_acquisition_state() == AcquisitionState.STOPPED

    ds_visa.query.return_value = "RUN"
    assert ds.get_acquisition_state() == AcquisitionState.RUNNING


def test_fetch_waveform_computes_times_and_voltages(ds: RigolDS1104Z, ds_visa: MagicMock) -> None:
    preamble = ",".join(["0", "2", "1200", "1", str(1e-9), str(-1e-6), "0", str(0.01), "10", "127"])
    ds_visa.query.return_value = preamble
    ds_visa.query_binary_values.return_value = [137, 127]
    ds_visa.read_raw.side_effect = TimeoutError

    waveform = ds.fetch_waveform(channel=CHANNEL)

    assert waveform.times == [-1000, -999]
    assert waveform.voltages == pytest.approx([0.0, -0.1])
    ds_visa.write.assert_any_call(":WAVeform:SOURce CHANnel1")
    ds_visa.query_binary_values.assert_called_once_with(":WAVeform:DATA?", datatype="B", container=list)


def test_measure_returns_value(ds: RigolDS1104Z, ds_visa: MagicMock) -> None:
    ds_visa.query.return_value = "8.888889e-03"
    assert ds.measure(ScopeMeasurementType.VPP, channel=CHANNEL) == pytest.approx(8.888889e-03)
    ds_visa.query.assert_called_once_with(":MEASure:ITEM? VPP,CHANnel1")


def test_measure_returns_nan_on_sentinel(ds: RigolDS1104Z, ds_visa: MagicMock) -> None:
    ds_visa.query.return_value = "9.9e+37"
    assert math.isnan(ds.measure(ScopeMeasurementType.FREQUENCY, channel=CHANNEL))


def test_measure_duty_cycle_uses_positive_duty_item(ds: RigolDS1104Z, ds_visa: MagicMock) -> None:
    ds_visa.query.return_value = "5.000000e+01"
    ds.measure(ScopeMeasurementType.DUTY_CYCLE, channel=CHANNEL)
    ds_visa.query.assert_called_once_with(":MEASure:ITEM? PDUTy,CHANnel1")


def test_set_trigger_source_uses_edge_subsystem_by_default(ds: RigolDS1104Z, ds_visa: MagicMock) -> None:
    ds.set_trigger_source(channel=2)
    ds_visa.write.assert_called_once_with(":TRIGger:EDGe:SOURce CHANnel2")


def test_set_trigger_type_switches_source_subsystem(ds: RigolDS1104Z, ds_visa: MagicMock) -> None:
    ds.set_trigger_source(channel=2)
    ds_visa.write.reset_mock()

    ds.set_trigger_type(TriggerType.PULSE)

    assert ds_visa.write.call_args_list[0][0][0] == ":TRIGger:MODE PULSe"
    assert ds_visa.write.call_args_list[1][0][0] == ":TRIGger:PULSe:SOURce CHANnel2"


def test_set_trigger_level_uses_cached_type(ds: RigolDS1104Z, ds_visa: MagicMock) -> None:
    ds.set_trigger_type(TriggerType.PULSE)
    ds_visa.write.reset_mock()

    ds.set_trigger_level(0.5)
    ds_visa.write.assert_called_once_with(":TRIGger:PULSe:LEVel 5.000000E-01")


def test_set_trigger_slope(ds: RigolDS1104Z, ds_visa: MagicMock) -> None:
    ds.set_trigger_slope(TriggerSlope.EITHER)
    ds_visa.write.assert_called_once_with(":TRIGger:EDGe:SLOPe RFALl")


def test_set_trigger_mode(ds: RigolDS1104Z, ds_visa: MagicMock) -> None:
    ds.set_trigger_mode(TriggerMode.NORMAL)
    ds_visa.write.assert_called_once_with(":TRIGger:SWEep NORMal")


def test_force_trigger(ds: RigolDS1104Z, ds_visa: MagicMock) -> None:
    ds.force_trigger()
    ds_visa.write.assert_called_once_with(":TFORce")


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("TD", TriggerStatus.TRIGGERED),
        ("WAIT", TriggerStatus.ARMED),
        ("RUN", TriggerStatus.READY),
        ("AUTO", TriggerStatus.AUTO),
        ("STOP", TriggerStatus.READY),
        ("SOMETHING_UNKNOWN", TriggerStatus.ARMED),
    ],
)
def test_get_trigger_status_maps_replies(
    ds: RigolDS1104Z, ds_visa: MagicMock, reply: str, expected: TriggerStatus
) -> None:
    ds_visa.query.return_value = reply
    assert ds.get_trigger_status() == expected


def test_save_screenshot_writes_bytes_to_file(ds: RigolDS1104Z, ds_visa: MagicMock, tmp_path) -> None:
    ds_visa.query_binary_values.return_value = [0x42, 0x4D, 0x00]
    ds_visa.read_raw.side_effect = TimeoutError
    filepath = tmp_path / "screenshot.bmp"

    data = ds.save_screenshot(str(filepath))

    assert data == b"\x42\x4d\x00"
    assert filepath.read_bytes() == data
    ds_visa.query_binary_values.assert_called_once_with(":DISPlay:DATA?", datatype="B", container=list)


def test_save_screenshot_to_instrument_raises(ds: RigolDS1104Z, ds_visa: MagicMock) -> None:
    with pytest.raises(NotImplementedError, match="instrument storage"):
        ds.save_screenshot("ignored.bmp", to_instrument=True)
    ds_visa.query_binary_values.assert_not_called()


def test_save_settings_writes_bytes_to_file(ds: RigolDS1104Z, ds_visa: MagicMock, tmp_path) -> None:
    ds_visa.query_binary_values.return_value = [1, 2, 3]
    ds_visa.read_raw.side_effect = TimeoutError
    filepath = tmp_path / "setup.bin"

    data = ds.save_settings(str(filepath))

    assert data == b"\x01\x02\x03"
    assert filepath.read_bytes() == data
    ds_visa.query_binary_values.assert_called_once_with(":SYSTem:SETup?", datatype="B", container=list)


def test_load_settings_round_trips_saved_blob(ds: RigolDS1104Z, ds_visa: MagicMock, tmp_path) -> None:
    filepath = tmp_path / "setup.bin"
    filepath.write_bytes(b"\x01\x02\x03")

    ds.load_settings(str(filepath))

    ds_visa.write_raw.assert_called_once_with(b":SYSTem:SETup #9000000003\x01\x02\x03\r\n")


def test_load_settings_from_instrument_raises(ds: RigolDS1104Z, ds_visa: MagicMock) -> None:
    with pytest.raises(NotImplementedError, match="instrument storage"):
        ds.load_settings("ignored.bin", from_instrument=True)
    ds_visa.write_raw.assert_not_called()
