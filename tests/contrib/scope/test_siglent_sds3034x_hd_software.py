"""Wire-level regressions for the SDS3034X HD."""

import math
import struct
from unittest.mock import MagicMock, call, patch

import pytest

from instro.contrib.drivers import SiglentSDS3034XHD
from instro.lib.transports.visa import VisaConfig
from instro.scope import AcquisitionMode, InstroScope, ScopeMeasurementType, TriggerType


@pytest.fixture
def transport():
    with patch("instro.contrib.scope.drivers.siglent_sds3034x_hd.VisaDriver", autospec=True) as factory:
        config = VisaConfig("TCPIP::scope::INSTR")
        driver = SiglentSDS3034XHD(config)
        factory.assert_called_once_with(config)
        visa = factory.return_value
        visa._inst = MagicMock(resource_name=config.visa_resource, read_termination="\n")
        yield driver, visa


def block(payload: bytes, suffix: bytes = b"\n") -> bytes:
    return f"#9{len(payload):09d}".encode() + payload + suffix


@pytest.fixture
def waveform_descriptor(transport):
    _, visa = transport
    desc = bytearray(346)
    desc[:8] = b"WAVEDESC"
    struct.pack_into("<hh", desc, 32, 1, 0)
    struct.pack_into("<i", desc, 116, 5)
    struct.pack_into("<fff", desc, 156, 0.125, -0.25, 1024)
    struct.pack_into("<fd", desc, 176, 0.5e-9, 1.25e-9)
    struct.pack_into("<h", desc, 324, 0)  # 200 ps/div, ten divisions
    struct.pack_into("<f", desc, 328, 10.0)
    struct.pack_into("<h", desc, 344, 2)
    visa.query.side_effect = {
        ":TRIG:STAT?": "Stop",
        ":ACQ:SEQ?": "OFF",
        ":CHAN3:SWIT?": "ON",
        ":SYST:ERR?": '0,"No error"',
        ":WAV:MAXP?": "3",
    }.__getitem__
    return desc


def test_waveform_scales_signed_words_and_joins_transfer_windows(transport, waveform_descriptor):
    driver, visa = transport
    visa.query_raw.side_effect = [
        block(waveform_descriptor),
        block(struct.pack("<3h", -1024, 10, 1024), b"\n\n"),
        block(struct.pack("<2h", 512, -2048), b"\n\n"),
    ]
    waveform = driver.fetch_waveform(3)
    assert waveform.voltages == pytest.approx([1.25, 2.51220703125, 3.75, 3.125, 0])
    assert waveform.times == [0, 1, 1, 2, 2]
    visa.write.assert_has_calls([call(":WAV:SOUR C3"), call(":WAV:WIDT WORD"), call(":WAV:BYT LSB")])
    assert visa.write.call_args_list[-4:] == [
        call(":WAV:POIN 3"),
        call(":WAV:STAR 0"),
        call(":WAV:POIN 2"),
        call(":WAV:STAR 3"),
    ]


@pytest.mark.parametrize(
    ("location", "fmt", "value", "error"),
    [
        (32, "<h", 0, "WORD WAVEDESC"),
        (344, "<h", 1, "channel 3"),
        (164, "<f", 0, "scaling"),
        (176, "<f", -1e-9, "scaling"),
        (180, "<d", math.nan, "scaling"),
    ],
)
def test_invalid_descriptor_fails_before_reading_samples(transport, waveform_descriptor, location, fmt, value, error):
    driver, visa = transport
    struct.pack_into(fmt, waveform_descriptor, location, value)
    visa.query_raw.return_value = block(waveform_descriptor)
    with pytest.raises(ValueError, match=error):
        driver.fetch_waveform(3)
    visa.query_raw.assert_called_once_with(":WAV:PRE?")


def test_truncated_waveform_window(transport, waveform_descriptor):
    driver, visa = transport
    visa.query_raw.side_effect = [block(waveform_descriptor), block(bytes(6)), block(bytes(2))]
    with pytest.raises(ValueError, match="Expected 2 waveform samples at 3, received 2 bytes"):
        driver.fetch_waveform(3)


def test_single_then_hal_measure_preserves_the_only_trigger(transport):
    driver, visa = transport
    scope = InstroScope("bench", driver=driver, num_channels=4, publishers=None)
    visa.query.side_effect = {
        ":TRIG:MODE?": "NORMal",
        ":TRIG:STAT?": "Stop",
        ":SYST:ERR?": '0,"No error"',
        "*OPC?": "1",
        "C3:PAVA? FREQ": "C3:PAVA FREQ,1.00E+03Hz",
    }.__getitem__
    scope.open()
    try:
        scope.single()
        assert scope.measure(ScopeMeasurementType.FREQUENCY, channel=3).latest == 1000
        visa.write.assert_called_once_with(":TRIG:MODE SING")
    finally:
        scope.close()


def test_transfer_failure_recovers_text_termination(transport, tmp_path):
    driver, visa = transport

    def timeout(_command):
        assert visa._inst.read_termination is None
        raise TimeoutError("partial transfer")

    visa.query_raw.side_effect = timeout
    with pytest.raises(TimeoutError, match="partial transfer"):
        driver.save_settings(str(tmp_path / "setup.xml"))
    assert visa._inst.read_termination == "\n"
    visa.clear.assert_called_once()
    assert not (tmp_path / "setup.xml").exists()


def test_setup_binary_roundtrip_and_truncated_reply(transport, tmp_path):
    driver, visa = transport
    path = tmp_path / "setup.xml"
    contents = b"<setup>\n</setup>\n"
    visa.query_raw.return_value = b"PNSU " + block(contents, suffix=b"g>\n>\n\n")
    assert driver.save_settings(str(path)) == contents
    driver.load_settings(str(path))
    visa.write_raw.assert_called_once_with(b"PNSU " + block(contents))
    visa.query_raw.return_value = b"#9000000200truncated\n"
    with pytest.raises(ValueError, match="binary block"):
        driver.save_settings(str(path))
    assert path.read_bytes() == contents


def test_instrument_file_operations(transport):
    driver, visa = transport
    assert driver.save_screenshot("local/trace.png", to_instrument=True) == b""
    assert driver.save_settings("local/test.xml", to_instrument=True) == b""
    driver.load_settings("local/test.xml", from_instrument=True)
    assert visa.write.call_args_list == [
        call(':SAVE:IMAG "local/trace.png",PNG,OFF'),
        call(':SAVE:SET EXT,"local/test.xml"'),
        call(':REC:SET EXT,"local/test.xml"'),
    ]
    visa.query.assert_called_once_with("*OPC?")


def test_average_count_staging_and_error_queue(transport):
    driver, visa = transport
    visa.query.side_effect = [
        "NORMal",
        "NORMal",
        '0,"No error"',
        "-113,Undefined header",
        "-200,Execution error",
        '0,"No error"',
    ]
    driver.set_average_count(32)
    visa.write.assert_not_called()
    driver.set_acquisition_mode(AcquisitionMode.AVERAGE)
    visa.write.assert_called_once_with(":ACQ:TYPE AVER,32")
    with pytest.raises(RuntimeError, match="Undefined header.*Execution error"):
        driver.check_errors()


def test_external_average_mode_uses_hardware_count(transport):
    driver, visa = transport
    visa.query.side_effect = ["NORMal", "AVERage,16", "AVGA 16"]
    driver.set_average_count(32)
    assert driver.get_average_count() == 16
    visa.write.assert_not_called()


def test_reselecting_high_resolution_preserves_enhanced_bits(transport):
    driver, visa = transport
    visa.query.return_value = "ERES,2.0"
    driver.set_acquisition_mode(AcquisitionMode.HIGH_RESOLUTION)
    visa.write.assert_not_called()


@pytest.mark.parametrize("front_panel_source", ["C3", "C4"])
def test_trigger_type_carries_only_the_source_still_selected_on_the_scope(transport, front_panel_source):
    driver, visa = transport
    visa.query.side_effect = {
        ":TRIG:TYPE?": "EDGE",
        ":TRIG:EDGE:SOUR?": front_panel_source,
        ":SYST:ERR?": '0,"No error"',
    }.__getitem__
    driver.set_trigger_source(3)
    driver.set_trigger_type(TriggerType.PULSE)
    expected = [call(":TRIG:EDGE:SOUR C3"), call(":TRIG:TYPE PULSE")]
    if front_panel_source == "C3":
        expected.append(call(":TRIG:PULSE:SOUR C3"))
    assert visa.write.call_args_list == expected


def test_unavailable_measurement(transport):
    driver, visa = transport
    visa.query.return_value = "C4:PAVA PER,****"
    assert math.isnan(driver.measure(ScopeMeasurementType.PERIOD, 4))
    visa.query.assert_called_once_with("C4:PAVA? PER")
