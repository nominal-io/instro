"""Software tests for the Keysight 3000T X-Series scope driver (unstable)."""

import math
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from instro.lib.transports.visa import VisaConfig
from instro.scope.types import AcquisitionMode, AcquisitionState, Coupling, ScopeMeasurementType, TriggerStatus
from instro.unstable.scope.drivers import Keysight3000TX

VISA_RESOURCE = "TCPIP0::192.168.1.50::INSTR"


@contextmanager
def _temp_timeout_cm() -> Iterator[None]:
    yield


@pytest.fixture
def visa_cls() -> Iterator[MagicMock]:
    with patch("instro.unstable.scope.drivers.keysight_3000tx.VisaDriver", autospec=True) as driver_cls:
        yield driver_cls


@pytest.fixture
def visa(visa_cls: MagicMock) -> MagicMock:
    inst = visa_cls.return_value
    inst.query.return_value = '+0,"No error"'
    inst.temporary_timeout.return_value = _temp_timeout_cm()
    return inst


@pytest.fixture
def scope(visa_cls: MagicMock, visa: MagicMock) -> Keysight3000TX:
    return Keysight3000TX(VISA_RESOURCE)


def _writes(visa: MagicMock) -> list[str]:
    return [c.args[0] for c in visa.write.call_args_list]


def test_init_passes_resource_to_visa(visa_cls: MagicMock) -> None:
    Keysight3000TX(VISA_RESOURCE)
    visa_cls.assert_called_once_with(VISA_RESOURCE)


def test_init_accepts_prebuilt_visa_config(visa_cls: MagicMock) -> None:
    config = VisaConfig(visa_resource=VISA_RESOURCE)
    Keysight3000TX(config)
    visa_cls.assert_called_once_with(config)


def test_open_close_delegate(scope: Keysight3000TX, visa: MagicMock) -> None:
    scope.open()
    scope.close()
    visa.open.assert_called_once_with()
    visa.close.assert_called_once_with()


@pytest.mark.parametrize("response", ['+0,"No error"', '0,"No error"'])
def test_check_errors_accepts_both_zero_forms(scope: Keysight3000TX, visa: MagicMock, response: str) -> None:
    visa.query.return_value = response
    scope.check_errors()
    visa.query.assert_called_once_with(":SYSTem:ERRor?")


def test_check_errors_raises_on_nonzero(scope: Keysight3000TX, visa: MagicMock) -> None:
    visa.query.return_value = '-113,"Undefined header"'
    with pytest.raises(RuntimeError, match="Keysight SCPI error -113: Undefined header"):
        scope.check_errors()


def test_vertical_settings_write_scpi(scope: Keysight3000TX, visa: MagicMock) -> None:
    scope.set_vertical_scale(0.5, channel=2)
    scope.set_vertical_offset(-0.25, channel=2)
    scope.set_coupling(Coupling.AC, channel=2)
    scope.set_probe_attenuation(10, channel=2)
    scope.set_channel_display(True, channel=2)
    assert _writes(visa) == [
        ":CHANnel2:SCALe 0.5",
        ":CHANnel2:OFFSet -0.25",
        ":CHANnel2:COUPling AC",
        ":CHANnel2:PROBe 10",
        ":CHANnel2:DISPlay 1",
    ]


def test_get_coupling_parses_token(scope: Keysight3000TX, visa: MagicMock) -> None:
    visa.query.return_value = "AC\n"
    assert scope.get_coupling(channel=1) is Coupling.AC
    visa.query.assert_called_once_with(":CHANnel1:COUPling?")


def test_get_sample_rate_queries_srate(scope: Keysight3000TX, visa: MagicMock) -> None:
    visa.query.return_value = "+2.50000E+09"
    assert scope.get_sample_rate() == pytest.approx(2.5e9)
    visa.query.assert_called_once_with(":ACQuire:SRATe?")


def test_acquisition_mode_envelope_rejected(scope: Keysight3000TX) -> None:
    with pytest.raises(NotImplementedError):
        scope.set_acquisition_mode(AcquisitionMode.ENVELOPE)


@pytest.mark.parametrize(
    ("response", "expected"),
    [("NORM", AcquisitionMode.NORMAL), ("AVER", AcquisitionMode.AVERAGE), ("HRES", AcquisitionMode.HIGH_RESOLUTION)],
)
def test_get_acquisition_mode_parses_short_form(
    scope: Keysight3000TX, visa: MagicMock, response: str, expected: AcquisitionMode
) -> None:
    visa.query.return_value = response + "\n"
    assert scope.get_acquisition_mode() is expected


def test_acquisition_state_and_trigger_status_use_condition_bits(scope: Keysight3000TX, visa: MagicMock) -> None:
    visa.query.return_value = "+40"  # 0x28: WAIT TRIG (0x20) | RUN (0x08)
    assert scope.get_acquisition_state() is AcquisitionState.RUNNING
    assert scope.get_trigger_status() is TriggerStatus.ARMED
    visa.query.return_value = "+0"
    assert scope.get_acquisition_state() is AcquisitionState.STOPPED
    assert scope.get_trigger_status() is TriggerStatus.TRIGGERED


def test_set_trigger_level_uses_cached_source(scope: Keysight3000TX, visa: MagicMock) -> None:
    scope.set_trigger_level(0.5)
    scope.set_trigger_source(3)
    scope.set_trigger_level(1.2)
    assert _writes(visa) == [
        ":TRIGger:EDGE:LEVel 0.5",
        ":TRIGger:EDGE:SOURce CHANnel3",
        ":TRIGger:EDGE:LEVel 1.2,CHANnel3",
    ]


def test_digitize_uses_cached_source_and_temp_timeout(scope: Keysight3000TX, visa: MagicMock) -> None:
    scope.set_trigger_source(2)
    visa.reset_mock()

    scope.digitize(timeout=1.5)

    visa.write.assert_called_once_with(":DIGitize CHANnel2")
    visa.temporary_timeout.assert_called_once_with(1500)
    visa.query.assert_called_once_with("*OPC?")


def test_digitize_raises_timeout_and_clears(scope: Keysight3000TX, visa: MagicMock) -> None:
    visa.query.side_effect = TimeoutError("VI_ERROR_TMO")
    with pytest.raises(TimeoutError, match="did not complete"):
        scope.digitize(timeout=0.1)
    visa.clear.assert_called_once()


def test_fetch_waveform_sets_unsigned_word_and_scales_points(scope: Keysight3000TX, visa: MagicMock) -> None:
    visa.query.side_effect = [
        '+0,"No error"',  # check_errors after setup
        "+1,+0,+4,+1,+2.0E-09,-4.0E-09,+0,+1.0E-03,+0.0E+00,+32768",
    ]
    visa.query_binary_values.return_value = [32768, 33768, 31768, 32768]

    waveform = scope.fetch_waveform(channel=3)

    assert _writes(visa) == [
        ":WAVeform:SOURce CHANnel3",
        ":WAVeform:FORMat WORD",
        ":WAVeform:UNSigned 1",
        ":WAVeform:BYTeorder LSBFirst",
        ":WAVeform:POINts:MODE NORMal",
        ":WAVeform:POINts 1000",
    ]
    visa.query_binary_values.assert_called_once_with(
        ":WAVeform:DATA?", datatype="H", is_big_endian=False, container=list
    )
    assert waveform.times == [-4, -2, 0, 2]
    assert waveform.voltages == pytest.approx([0.0, 1.0, -1.0, 0.0])


def test_fetch_waveform_raises_before_data_query_on_error(scope: Keysight3000TX, visa: MagicMock) -> None:
    visa.query.return_value = '-222,"Data out of range"'
    with pytest.raises(RuntimeError, match="-222"):
        scope.fetch_waveform(channel=1)
    visa.query_binary_values.assert_not_called()


def test_measure_vrms_installs_then_queries(scope: Keysight3000TX, visa: MagicMock) -> None:
    visa.query.side_effect = ['+0,"No error"', "+1.23400E+00"]
    assert scope.measure(ScopeMeasurementType.VRMS, channel=1) == pytest.approx(1.234)
    assert _writes(visa) == [":MEASure:VRMS DISPlay,DC,CHANnel1"]
    assert visa.query.call_args_list[-1].args == (":MEASure:VRMS? DISPlay,DC,CHANnel1",)


@pytest.mark.parametrize("sentinel", ["+9.9E+37", "9.91e37"])
def test_measure_returns_nan_on_sentinel(scope: Keysight3000TX, visa: MagicMock, sentinel: str) -> None:
    visa.query.side_effect = ['+0,"No error"', sentinel]
    assert math.isnan(scope.measure(ScopeMeasurementType.VPP, channel=1))


def test_save_screenshot_to_instrument_sets_png_format(scope: Keysight3000TX, visa: MagicMock) -> None:
    assert scope.save_screenshot("\\usb\\shot.png", to_instrument=True) == b""
    assert _writes(visa) == [":SAVE:IMAGe:FORMat PNG", ':SAVE:IMAGe "\\usb\\shot.png"']
    visa.query.assert_called_once_with(":SYSTem:ERRor?")


def test_save_screenshot_to_host_disables_inksaver_and_writes_file(
    scope: Keysight3000TX, visa: MagicMock, tmp_path: Path
) -> None:
    visa.query_binary_values.return_value = [0x89, 0x50, 0x4E, 0x47]
    out = tmp_path / "shot.png"

    data = scope.save_screenshot(str(out))

    assert _writes(visa) == [":HARDcopy:INKSaver OFF"]
    visa.query_binary_values.assert_called_once_with(":DISPlay:DATA? PNG,COLor", datatype="B", container=list)
    assert data == b"\x89PNG"
    assert out.read_bytes() == b"\x89PNG"


def test_save_settings_to_host_reads_learn_string(scope: Keysight3000TX, visa: MagicMock, tmp_path: Path) -> None:
    visa.query_binary_values.return_value = [1, 2, 3]
    out = tmp_path / "setup.bin"

    assert scope.save_settings(str(out)) == b"\x01\x02\x03"

    visa.write.assert_not_called()
    visa.query_binary_values.assert_called_once_with(":SYSTem:SETup?", datatype="B", container=list)
    assert out.read_bytes() == b"\x01\x02\x03"


def test_load_settings_from_host_sends_definite_length_block(
    scope: Keysight3000TX, visa: MagicMock, tmp_path: Path
) -> None:
    src = tmp_path / "setup.bin"
    src.write_bytes(b"\x01\x02\x03")

    scope.load_settings(str(src))

    visa.write_raw.assert_called_once_with(b":SYSTem:SETup #13\x01\x02\x03")


def test_load_settings_from_instrument_recalls_by_name(scope: Keysight3000TX, visa: MagicMock) -> None:
    scope.load_settings("bench.scp", from_instrument=True)
    visa.write.assert_called_once_with(':RECall:SETup "bench.scp"')
