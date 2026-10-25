"""Mocked-transport tests for Tektronix4SeriesMSO: shared-transport ownership and where the scope view diverges from the 2 Series driver."""

from __future__ import annotations

import contextlib
import struct
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from instro.lib.transports.visa import VisaConfig
from instro.scope import ScopeDriverBase
from instro.scope.drivers import Tektronix4SeriesMSO
from instro.scope.drivers.tektronix_4series import _parse_frame_timestamps_ns, _split_curve_blocks
from instro.scope.types import Coupling

VISA_RESOURCE = "<visa_resource>"  # placeholder; these mocked tests only assert it is forwarded to VisaDriver
NO_EVENTS = '0,"No events to report; queue empty"'


@pytest.fixture
def visa_cls() -> Iterator[MagicMock]:
    with patch("instro.scope.drivers.tektronix_4series.VisaDriver", autospec=True) as driver_cls:
        yield driver_cls


@pytest.fixture
def visa(visa_cls: MagicMock) -> MagicMock:
    visa = visa_cls.return_value
    visa.query.return_value = NO_EVENTS
    visa.query_raw.return_value = NO_EVENTS.encode()
    return visa


@pytest.fixture
def device(visa_cls: MagicMock, visa: MagicMock) -> Tektronix4SeriesMSO:
    return Tektronix4SeriesMSO(VISA_RESOURCE)


@pytest.fixture
def scope(device: Tektronix4SeriesMSO) -> ScopeDriverBase:
    return device.scope


def _writes(visa: MagicMock) -> list[str]:
    return [c.args[0] for c in visa.write.call_args_list]


def test_init_passes_resource_or_config_to_visa(visa_cls: MagicMock) -> None:
    Tektronix4SeriesMSO(VISA_RESOURCE)
    visa_cls.assert_called_once_with(VISA_RESOURCE)
    config = VisaConfig(visa_resource="GPIB0::1::INSTR")
    Tektronix4SeriesMSO(config)
    visa_cls.assert_called_with(config)


def test_first_view_open_holds_transport_and_pins_response_format(device: Tektronix4SeriesMSO, visa: MagicMock) -> None:
    device.scope.open()
    visa.open.assert_called_once_with(device)
    writes = [c.args[0] for c in visa.write.call_args_list]
    assert writes == ["*CLS", "HEADer OFF", "VERBose ON"]


def test_transport_opens_once_and_closes_with_the_last_view(device: Tektronix4SeriesMSO, visa: MagicMock) -> None:
    device.scope.open()
    device.awg.open()
    visa.open.assert_called_once_with(device)
    assert visa.write.call_count == 3  # setup ran for the first view only

    device.scope.close()
    visa.close.assert_not_called()
    device.awg.close()
    visa.close.assert_called_once_with(device)


def test_failed_first_open_releases_the_transport_so_retry_redoes_setup(
    device: Tektronix4SeriesMSO, visa: MagicMock
) -> None:
    visa.query_raw.return_value = b'221,"Settings conflict;"'
    with pytest.raises(RuntimeError):
        device.scope.open()
    visa.close.assert_called_once_with(device)

    visa.query_raw.return_value = NO_EVENTS.encode()
    device.scope.open()
    assert [c.args[0] for c in visa.write.call_args_list].count("HEADer OFF") == 2


@pytest.mark.parametrize("events", ["0", "1", NO_EVENTS, '1,"No events to report; new events pending *ESR?"'])
def test_check_errors_passes_on_empty_queue(scope: ScopeDriverBase, visa: MagicMock, events: str) -> None:
    visa.query_raw.return_value = events.encode()
    scope.check_errors()


def test_check_errors_reads_esr_before_allev_and_raises(scope: ScopeDriverBase, visa: MagicMock) -> None:
    visa.query.return_value = "32"
    visa.query_raw.return_value = b'221,"Settings conflict; CH1:SCAle";420,"Query UNTERMINATED;"'
    with pytest.raises(RuntimeError, match="SCPI errors.*221"):
        scope.check_errors()
    visa.query.assert_called_once_with("*ESR?")
    visa.query_raw.assert_called_once_with("ALLEv?")


def test_check_errors_tolerates_power_on_and_other_informational_events(
    scope: ScopeDriverBase, visa: MagicMock
) -> None:
    visa.query_raw.return_value = b'401,"Power on";468,"Knob/Keypad value changed"'
    scope.check_errors()
    visa.query_raw.return_value = b'420,"Query UNTERMINATED;"'
    with pytest.raises(RuntimeError, match="420"):
        scope.check_errors()


def test_check_errors_tolerates_warnings_and_non_ascii_text(scope: ScopeDriverBase, visa: MagicMock) -> None:
    visa.query_raw.return_value = (
        '547,"Measurement warning, Clipping positive/negative";551,"Offset 5 \u00b5V"'.encode()
    )
    scope.check_errors()


def test_check_errors_tolerates_measurement_errors_but_raises_the_rest_of_the_22xx_block(
    scope: ScopeDriverBase, visa: MagicMock
) -> None:
    visa.query_raw.return_value = (
        b'2200,"Measurement error, Measurement system error; Low amplitude",540,"Measurement warning; Empty input"'
    )
    scope.check_errors()
    for code in (2233, 2244, 2500):
        visa.query_raw.return_value = f'{code},"Some other error"'.encode()
        with pytest.raises(RuntimeError, match=str(code)):
            scope.check_errors()


def test_get_coupling_rejects_dc_reject(scope: ScopeDriverBase, visa: MagicMock) -> None:
    visa.query.return_value = "DCR"
    with pytest.raises(ValueError, match="DCR"):
        scope.get_coupling(1)
    visa.query.return_value = "AC"
    assert scope.get_coupling(1) is Coupling.AC


def test_fetch_waveform_rewrites_stale_data_stop_and_references_trigger(
    scope: ScopeDriverBase, visa: MagicMock
) -> None:
    responses = {
        "HORizontal:ROLL?": "0",
        "HORizontal:RECOrdlength?": "1000",
        "WFMOutpre:XINcr?": "4e-9",
        "WFMOutpre:XZEro?": "1e-10",
        "WFMOutpre:PT_Off?": "2",
        "WFMOutpre:YMUlt?": "0.5",
        "WFMOutpre:YOFf?": "0",
        "WFMOutpre:YZEro?": "1",
    }
    visa.query.side_effect = lambda cmd: responses.get(cmd, NO_EVENTS)
    visa.query_binary_values.return_value = [10, 11, 12]

    waveform = scope.fetch_waveform(2)

    writes = [c.args[0] for c in visa.write.call_args_list]
    assert writes[-2:] == ["DATa:STARt 1", "DATa:STOP 1000"]
    assert writes[0] == "DATa:SOUrce CH2"
    assert waveform.times == [-8, -4, 0]
    assert waveform.voltages == pytest.approx([6.0, 6.5, 7.0])


def test_load_settings_waits_for_recall_before_deleting_temp_file(
    scope: ScopeDriverBase, visa: MagicMock, tmp_path
) -> None:
    setup = tmp_path / "setup.set"
    setup.write_bytes(b"blob")
    scope.load_settings(str(setup))

    recall = next(
        i for i, c in enumerate(visa.method_calls) if c.args[:1] == ('RECAll:SETUp "C:/nominal_temp_setup.set"',)
    )
    opc = next(i for i, c in enumerate(visa.method_calls) if c.args[:1] == ("*OPC?",))
    delete = next(
        i for i, c in enumerate(visa.method_calls) if c.args[:1] == ('FILESystem:DELete "C:/nominal_temp_setup.set"',)
    )
    assert recall < opc < delete


def test_save_settings_to_instrument_waits_for_completion(scope: ScopeDriverBase, visa: MagicMock) -> None:
    scope.save_settings("C:/saved.set", to_instrument=True)
    assert [c[0] for c in visa.method_calls if c[0] in {"write", "query"}] == ["write", "query"]
    visa.write.assert_called_once_with('SAVe:SETUp "C:/saved.set"')
    visa.query.assert_called_once_with("*OPC?")


def test_load_settings_uploads_file_as_definite_length_block(
    scope: ScopeDriverBase, visa: MagicMock, tmp_path: Path
) -> None:
    setup = tmp_path / "setup.set"
    setup.write_bytes(b"x" * 12)
    scope.load_settings(str(setup))
    visa.write_raw.assert_called_once_with(b'FILESystem:WRITEFile "C:/nominal_temp_setup.set",#212' + b"x" * 12)


def test_failed_connect_does_not_strand_the_view_so_retry_reopens(device: Tektronix4SeriesMSO, visa: MagicMock) -> None:
    visa.open.side_effect = [OSError("unreachable"), None]
    with pytest.raises(OSError):
        device.scope.open()
    device.scope.open()
    assert visa.open.call_count == 2
    assert "HEADer OFF" in _writes(visa)


def test_roll_mode_fetch_anchors_the_newest_sample_at_zero(scope: ScopeDriverBase, visa: MagicMock) -> None:
    responses = {
        "HORizontal:ROLL?": "1",
        "HORizontal:RECOrdlength?": "4",
        "WFMOutpre:XINcr?": "1e-3",
        "WFMOutpre:XZEro?": "0",
        "WFMOutpre:PT_Off?": "-30473",
        "WFMOutpre:YMUlt?": "1",
        "WFMOutpre:YOFf?": "0",
        "WFMOutpre:YZEro?": "0",
    }
    visa.query.side_effect = lambda cmd: responses.get(cmd, NO_EVENTS)
    visa.query_binary_values.return_value = [1, 2, 3, 4]

    waveform = scope.fetch_waveform(1)

    assert waveform.times == [-3_000_000, -2_000_000, -1_000_000, 0]


def test_fetch_holds_the_visa_lock_from_setup_through_the_data_read(scope: ScopeDriverBase, visa: MagicMock) -> None:
    held = 0

    @contextlib.contextmanager
    def lock() -> Iterator[None]:
        nonlocal held
        held += 1
        try:
            yield
        finally:
            held -= 1

    seen: list[int] = []

    def query(cmd: str) -> str:
        seen.append(held)
        return (
            "4" if cmd == "HORizontal:RECOrdlength?" else ("0" if cmd == "HORizontal:FASTframe:STATE?" else NO_EVENTS)
        )

    visa.lock.side_effect = lock
    visa.query.side_effect = lambda cmd: "1" if cmd.startswith("WFM") else query(cmd)
    visa.query_binary_values.side_effect = lambda *a, **k: seen.append(held) or [1, 2, 3, 4]

    scope.fetch_waveform(1)

    assert seen and all(depth > 0 for depth in seen)


def test_fetch_waveform_reads_a_fastframe_burst_onto_one_timeline_ending_at_the_last_trigger(
    scope: ScopeDriverBase, visa: MagicMock
) -> None:
    def block(*codes: int) -> bytes:
        return b"#16" + struct.pack(f">{len(codes)}h", *codes)

    responses = {
        "HORizontal:FASTframe:STATE?": "1",
        "HORizontal:RECOrdlength?": "3",
        "HORizontal:FASTframe:TIMEStamp:ALL?": '"1: 20.10.2026.12:10:19.020000000000","2: 20.10.2026.12:10:19.023000000000"',
        "HORizontal:FASTframe:XZEro:ALL?": '"1: 0.0E+0","2: 0.0E+0"',
        "WFMOutpre:XINcr?": "1e-9",
        "WFMOutpre:PT_Off?": "1",
        "WFMOutpre:YMUlt?": "1",
        "WFMOutpre:YOFf?": "0",
        "WFMOutpre:YZEro?": "0",
    }
    visa.query.side_effect = lambda cmd: responses.get(cmd, NO_EVENTS)
    visa.query_raw.side_effect = (
        lambda cmd: block(1, 2, 3) + b";" + block(4, 5, 6) + b"\n" if cmd == "CURVe?" else NO_EVENTS.encode()
    )

    waveform = scope.fetch_waveform(1)

    assert waveform.voltages == [1, 2, 3, 4, 5, 6]
    assert waveform.times == [-3_000_001, -3_000_000, -2_999_999, -1, 0, 1]
    assert _writes(visa)[-2:] == ["DATa:FRAMESTARt 1", "DATa:FRAMESTOP 2"]


def test_fastframe_is_read_but_never_configured(device: Tektronix4SeriesMSO, visa: MagicMock) -> None:
    device.scope.open()
    device.scope.single()
    device.scope.run()
    assert not any("FASTframe" in w for w in _writes(visa))


def test_split_curve_blocks_is_length_framed_not_separator_framed() -> None:
    first = b"\n;\n;"  # payload bytes that look like separators must not split a frame
    reply = b"#14" + first + b";#12" + b"ab" + b"\n"
    assert _split_curve_blocks(reply) == [first, b"ab"]


def test_frame_timestamps_keep_nanosecond_deltas_across_midnight() -> None:
    times = _parse_frame_timestamps_ns('"1: 20.10.2026.23:59:59.998999999999","2: 21.10.2026.00:00:00.001999999999"')
    assert times[1] - times[0] == 3_000_000


def test_fetch_waveform_rejects_frame_and_timestamp_count_mismatch(scope: ScopeDriverBase, visa: MagicMock) -> None:
    responses = {
        "HORizontal:FASTframe:STATE?": "1",
        "HORizontal:RECOrdlength?": "1",
        "HORizontal:FASTframe:TIMEStamp:ALL?": '"1: 20.10.2026.12:10:19.020000000000","2: 20.10.2026.12:10:19.023000000000"',
        "HORizontal:FASTframe:XZEro:ALL?": '"1: 0.0E+0","2: 0.0E+0"',
    }
    visa.query.side_effect = lambda cmd: responses.get(cmd, "1" if cmd.startswith("WFMOutpre") else NO_EVENTS)
    visa.query_raw.side_effect = lambda cmd: b"#12\x00\x01" if cmd == "CURVe?" else NO_EVENTS.encode()
    with pytest.raises(RuntimeError, match="1 frames, 2 timestamps"):
        scope.fetch_waveform(1)
