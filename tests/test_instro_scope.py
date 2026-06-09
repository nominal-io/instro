"""Tests for the oscilloscope driver shape.

Covers vendor drivers (Keysight1200X, Tektronix2SeriesMSO, SiglentSDS1000XE)
owning a VisaDriver, and InstroScope delegating to its driver.
"""

import math
from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest

from instro.lib.transports.visa import SerialConfig, VisaConfig
from instro.unstable.scope import (
    AcquisitionMode,
    AcquisitionState,
    Coupling,
    InstroScope,
    ScopeMeasurementType,
    TriggerMode,
    TriggerSlope,
    TriggerStatus,
    TriggerType,
)
from instro.unstable.scope.driver import ScopeDriverBase
from instro.unstable.scope.drivers.keysight import Keysight1200X
from instro.unstable.scope.drivers.siglent import SiglentSDS1000XE
from instro.unstable.scope.drivers.tektronix import Tektronix2SeriesMSO


def _make_temp_timeout_cm() -> MagicMock:
    """Build a MagicMock that supports `with self._visa.temporary_timeout(ms):`."""
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=None)
    cm.__exit__ = MagicMock(return_value=False)
    return cm


# --- Keysight1200X unit tests ---


@pytest.fixture
def keysight_visa_cls() -> Iterator[MagicMock]:
    with patch("instro.unstable.scope.drivers.keysight.keysight_1200x.VisaDriver", autospec=True) as driver_cls:
        yield driver_cls


@pytest.fixture
def keysight_visa(keysight_visa_cls: MagicMock) -> MagicMock:
    visa = keysight_visa_cls.return_value
    visa.query.return_value = '0,"No error"'
    visa.temporary_timeout.return_value = _make_temp_timeout_cm()
    return visa


@pytest.fixture
def keysight(keysight_visa_cls: MagicMock, keysight_visa: MagicMock) -> Keysight1200X:
    return Keysight1200X("USB0::10893::923::CN64191203::INSTR")


def test_keysight_init_passes_resource_to_visa(keysight_visa_cls: MagicMock) -> None:
    Keysight1200X("USB0::10893::923::CN64191203::INSTR")
    keysight_visa_cls.assert_called_once_with("USB0::10893::923::CN64191203::INSTR")


def test_keysight_init_accepts_prebuilt_visa_config(keysight_visa_cls: MagicMock) -> None:
    config = VisaConfig(visa_resource="ASRL3::INSTR", serial_config=SerialConfig(baud_rate=19_200))
    Keysight1200X(config)
    keysight_visa_cls.assert_called_once_with(config)


def test_keysight_open_close_delegate(keysight: Keysight1200X, keysight_visa: MagicMock) -> None:
    keysight.open()
    keysight.close()
    keysight_visa.open.assert_called_once()
    keysight_visa.close.assert_called_once()


def test_keysight_check_errors_passes_on_zero(keysight: Keysight1200X, keysight_visa: MagicMock) -> None:
    keysight_visa.query.return_value = '0,"No error"'
    keysight.check_errors()


def test_keysight_check_errors_raises_on_nonzero(keysight: Keysight1200X, keysight_visa: MagicMock) -> None:
    keysight_visa.query.return_value = '-113,"Undefined header"'
    with pytest.raises(RuntimeError, match="Keysight SCPI error -113"):
        keysight.check_errors()


def test_keysight_set_vertical_scale_writes_scpi(keysight: Keysight1200X, keysight_visa: MagicMock) -> None:
    keysight.set_vertical_scale(0.5, channel=2)
    keysight_visa.write.assert_called_once_with(":CHANnel2:SCALe 0.5")


def test_keysight_set_coupling_writes_mapped_scpi(keysight: Keysight1200X, keysight_visa: MagicMock) -> None:
    keysight.set_coupling(Coupling.AC, channel=1)
    keysight_visa.write.assert_called_once_with(":CHANnel1:COUPling AC")


def test_keysight_set_trigger_source_caches_channel(keysight: Keysight1200X, keysight_visa: MagicMock) -> None:
    keysight.set_trigger_source(3)
    assert keysight._trigger_source == 3
    keysight_visa.write.assert_called_once_with(":TRIGger:EDGE:SOURce CHANnel3")


def test_keysight_set_trigger_level_uses_cached_source(keysight: Keysight1200X, keysight_visa: MagicMock) -> None:
    keysight.set_trigger_source(2)
    keysight_visa.reset_mock()
    keysight.set_trigger_level(0.75)
    keysight_visa.write.assert_called_once_with(":TRIGger:EDGE:LEVel 0.75,CHANnel2")


def test_keysight_set_trigger_level_without_source_omits_channel(
    keysight: Keysight1200X, keysight_visa: MagicMock
) -> None:
    keysight.set_trigger_level(0.5)
    keysight_visa.write.assert_called_once_with(":TRIGger:EDGE:LEVel 0.5")


def test_keysight_acquisition_mode_envelope_rejected(keysight: Keysight1200X) -> None:
    with pytest.raises(NotImplementedError, match="ENVELOPE"):
        keysight.set_acquisition_mode(AcquisitionMode.ENVELOPE)


def test_keysight_digitize_uses_cached_source_and_temp_timeout(
    keysight: Keysight1200X, keysight_visa: MagicMock
) -> None:
    keysight.set_trigger_source(3)
    keysight_visa.reset_mock()

    keysight.digitize(timeout=1.5)

    keysight_visa.write.assert_called_once_with(":DIGitize CHANnel3")
    keysight_visa.temporary_timeout.assert_called_once_with(1500)
    keysight_visa.query.assert_called_once_with("*OPC?")


def test_keysight_digitize_default_source_is_1(keysight: Keysight1200X, keysight_visa: MagicMock) -> None:
    keysight.digitize(timeout=0.5)
    keysight_visa.write.assert_called_once_with(":DIGitize CHANnel1")


def test_keysight_digitize_raises_timeout_and_clears(keysight: Keysight1200X, keysight_visa: MagicMock) -> None:
    keysight_visa.query.side_effect = TimeoutError("VI_ERROR_TMO")

    with pytest.raises(TimeoutError, match="did not complete"):
        keysight.digitize(timeout=0.1)

    keysight_visa.clear.assert_called_once()


def test_keysight_fetch_waveform_uses_query_binary_values(keysight: Keysight1200X, keysight_visa: MagicMock) -> None:
    # First three queries are :SYSTem:ERRor? (error check), then :WAVeform:PREamble?
    keysight_visa.query.side_effect = [
        '0,"No error"',  # check_errors after setup
        "+0,+0,4,+0,1.0E-9,0.0,0,1.0E-3,0,32768",  # PREamble
    ]
    keysight_visa.query_binary_values.return_value = [32768, 32769, 32770, 32771]

    waveform = keysight.fetch_waveform(channel=1)

    keysight_visa.query_binary_values.assert_called_once_with(
        ":WAVeform:DATA?", datatype="H", is_big_endian=False, container=list
    )
    assert len(waveform.voltages) == 4
    assert len(waveform.times) == 4


def test_keysight_measure_vpp_installs_then_queries(keysight: Keysight1200X, keysight_visa: MagicMock) -> None:
    keysight_visa.query.side_effect = ['0,"No error"', "1.234"]
    result = keysight.measure(ScopeMeasurementType.VPP, channel=1)
    assert result == pytest.approx(1.234)
    writes = [c.args[0] for c in keysight_visa.write.call_args_list]
    assert writes == [":MEASure:VPP CHANnel1"]
    queries = [c.args[0] for c in keysight_visa.query.call_args_list]
    assert ":MEASure:VPP? CHANnel1" in queries


def test_keysight_measure_returns_nan_on_sentinel(keysight: Keysight1200X, keysight_visa: MagicMock) -> None:
    # check_errors gets '0,"No error"', then the measurement query returns sentinel.
    keysight_visa.query.side_effect = ['0,"No error"', "9.91e37"]
    result = keysight.measure(ScopeMeasurementType.VPP, channel=1)
    assert math.isnan(result)


# --- Tektronix2SeriesMSO unit tests ---


@pytest.fixture
def tektronix_visa_cls() -> Iterator[MagicMock]:
    with patch("instro.unstable.scope.drivers.tektronix.tektronix_2series.VisaDriver", autospec=True) as driver_cls:
        yield driver_cls


@pytest.fixture
def tektronix_visa(tektronix_visa_cls: MagicMock) -> MagicMock:
    visa = tektronix_visa_cls.return_value
    visa.query.return_value = "1"  # default: no events for ALLEv?
    visa.temporary_timeout.return_value = _make_temp_timeout_cm()
    return visa


@pytest.fixture
def tektronix(tektronix_visa_cls: MagicMock, tektronix_visa: MagicMock) -> Tektronix2SeriesMSO:
    return Tektronix2SeriesMSO("TCPIP0::scope.local::INSTR")


def test_tektronix_init_passes_resource_to_visa(tektronix_visa_cls: MagicMock) -> None:
    Tektronix2SeriesMSO("TCPIP0::scope.local::INSTR")
    tektronix_visa_cls.assert_called_once_with("TCPIP0::scope.local::INSTR")


def test_tektronix_init_accepts_prebuilt_visa_config(tektronix_visa_cls: MagicMock) -> None:
    config = VisaConfig(visa_resource="GPIB0::1::INSTR")
    Tektronix2SeriesMSO(config)
    tektronix_visa_cls.assert_called_once_with(config)


def test_tektronix_check_errors_passes_when_idle(tektronix: Tektronix2SeriesMSO, tektronix_visa: MagicMock) -> None:
    tektronix_visa.query.return_value = "1"
    tektronix.check_errors()


def test_tektronix_check_errors_raises_on_error_codes(
    tektronix: Tektronix2SeriesMSO, tektronix_visa: MagicMock
) -> None:
    tektronix_visa.query.return_value = '100,"Command error"'
    with pytest.raises(RuntimeError, match="Tektronix SCPI errors"):
        tektronix.check_errors()


def test_tektronix_set_trigger_source_caches_channel(tektronix: Tektronix2SeriesMSO, tektronix_visa: MagicMock) -> None:
    tektronix.set_trigger_source(4)
    assert tektronix._trigger_source == 4
    tektronix_visa.write.assert_called_once_with("TRIGger:A:EDGE:SOUrce CH4")


def test_tektronix_set_trigger_level_uses_cached_source(
    tektronix: Tektronix2SeriesMSO, tektronix_visa: MagicMock
) -> None:
    tektronix.set_trigger_source(3)
    tektronix_visa.reset_mock()
    tektronix.set_trigger_level(1.25)
    tektronix_visa.write.assert_called_once_with("TRIGger:A:LEVel:CH3 1.25")


def test_tektronix_set_trigger_level_defaults_to_ch1(tektronix: Tektronix2SeriesMSO, tektronix_visa: MagicMock) -> None:
    tektronix.set_trigger_level(0.0)
    tektronix_visa.write.assert_called_once_with("TRIGger:A:LEVel:CH1 0.0")


def test_tektronix_setup_measurement_uses_addmeas_and_caches_slot(
    tektronix: Tektronix2SeriesMSO, tektronix_visa: MagicMock
) -> None:
    # Queries: LIST? (empty), LIST? (one new slot), ALLEv?, polling.
    tektronix_visa.query.side_effect = ["", "MEAS1", "1", "2.5"]
    tektronix.setup_measurement(ScopeMeasurementType.VPP, channel=2)

    assert tektronix._measurement_slots[(ScopeMeasurementType.VPP, 2)] == "MEAS1"
    writes = [c.args[0] for c in tektronix_visa.write.call_args_list]
    # The atomic add carries the type with it — no separate :TYPe write.
    assert "MEASUrement:ADDMEAS PK2Pk" in writes
    assert "MEASUrement:MEAS1:SOUrce1 CH2" in writes
    assert not any("ADDNew" in w for w in writes)
    assert not any(":TYPe " in w for w in writes)


def test_tektronix_setup_measurement_discovers_new_slot_via_list_diff(
    tektronix: Tektronix2SeriesMSO, tektronix_visa: MagicMock
) -> None:
    # Prior measurements exist on the scope (e.g. from a previous Python session).
    # The new slot's name (MEAS3) is whatever the scope auto-assigns.
    tektronix_visa.query.side_effect = [
        "MEAS1,MEAS2",  # LIST? before
        "MEAS1,MEAS2,MEAS3",  # LIST? after — diff yields MEAS3
        "1",  # ALLEv?
        "5.0",  # polling
    ]
    tektronix.setup_measurement(ScopeMeasurementType.VRMS, channel=1)
    assert tektronix._measurement_slots[(ScopeMeasurementType.VRMS, 1)] == "MEAS3"


def test_tektronix_setup_measurement_raises_if_no_new_slot(
    tektronix: Tektronix2SeriesMSO, tektronix_visa: MagicMock
) -> None:
    # Same LIST? before and after — ADDMEAS failed silently for some reason.
    tektronix_visa.query.side_effect = ["MEAS1", "MEAS1"]
    with pytest.raises(RuntimeError, match="Expected one new measurement"):
        tektronix.setup_measurement(ScopeMeasurementType.VPP, channel=1)


def test_tektronix_setup_measurement_reuses_existing_slot(
    tektronix: Tektronix2SeriesMSO, tektronix_visa: MagicMock
) -> None:
    tektronix_visa.query.side_effect = ["", "MEAS1", "1", "1.0"]
    tektronix.setup_measurement(ScopeMeasurementType.VRMS, channel=1)
    initial_write_count = tektronix_visa.write.call_count
    initial_query_count = tektronix_visa.query.call_count

    # Second setup with same key is a no-op — no SCPI calls.
    tektronix.setup_measurement(ScopeMeasurementType.VRMS, channel=1)
    assert tektronix_visa.write.call_count == initial_write_count
    assert tektronix_visa.query.call_count == initial_query_count


def test_tektronix_measure_reads_from_slot(tektronix: Tektronix2SeriesMSO, tektronix_visa: MagicMock) -> None:
    # Queries: LIST? before, LIST? after, ALLEv?, polling, measurement read.
    tektronix_visa.query.side_effect = ["", "MEAS1", "1", "2.5", "2.5"]
    result = tektronix.measure(ScopeMeasurementType.VRMS, channel=1)
    assert result == pytest.approx(2.5)


def test_tektronix_setup_measurement_polls_until_non_sentinel(
    tektronix: Tektronix2SeriesMSO, tektronix_visa: MagicMock
) -> None:
    # LIST? before, LIST? after, ALLEv?, then 3 polling queries
    # (sentinel, sentinel, real) — 6 total.
    tektronix_visa.query.side_effect = ["", "MEAS1", "1", "9.91e37", "9.91e37", "1.23"]
    tektronix.setup_measurement(ScopeMeasurementType.VPP, channel=1)
    assert tektronix_visa.query.call_count == 6


def test_tektronix_wait_for_measurement_ready_returns_on_timeout(
    tektronix: Tektronix2SeriesMSO, tektronix_visa: MagicMock
) -> None:
    # Scope keeps returning sentinel — the helper must exit on timeout
    # rather than hang forever.
    tektronix_visa.query.return_value = "9.91e37"
    tektronix._wait_for_measurement_ready("MEAS1", timeout=0.05)


def test_tektronix_clear_measurements_deletes_all_slots(
    tektronix: Tektronix2SeriesMSO, tektronix_visa: MagicMock
) -> None:
    tektronix_visa.query.return_value = "MEAS1,MEAS2,MEAS3"
    tektronix._measurement_slots[(ScopeMeasurementType.VPP, 1)] = "MEAS1"

    tektronix.clear_measurements()

    writes = [c.args[0] for c in tektronix_visa.write.call_args_list]
    assert 'MEASUrement:DELete "MEAS1"' in writes
    assert 'MEASUrement:DELete "MEAS2"' in writes
    assert 'MEASUrement:DELete "MEAS3"' in writes
    assert tektronix._measurement_slots == {}


def test_tektronix_clear_measurements_with_no_slots_is_noop(
    tektronix: Tektronix2SeriesMSO, tektronix_visa: MagicMock
) -> None:
    tektronix_visa.query.return_value = ""
    tektronix.clear_measurements()
    assert not tektronix_visa.write.called


def test_tektronix_measure_returns_nan_on_sentinel(tektronix: Tektronix2SeriesMSO, tektronix_visa: MagicMock) -> None:
    # Pre-populate the slot so setup_measurement is a no-op (skips polling).
    tektronix._measurement_slots[(ScopeMeasurementType.VRMS, 1)] = "MEAS1"
    tektronix_visa.query.return_value = "9.91e37"
    result = tektronix.measure(ScopeMeasurementType.VRMS, channel=1)
    assert math.isnan(result)


def test_tektronix_measure_negative_sentinel_also_maps_to_nan(
    tektronix: Tektronix2SeriesMSO, tektronix_visa: MagicMock
) -> None:
    # Some scopes emit -9.91e37 for "below range" — should also map to NaN.
    tektronix._measurement_slots[(ScopeMeasurementType.VRMS, 1)] = "MEAS1"
    tektronix_visa.query.return_value = "-9.91e37"
    result = tektronix.measure(ScopeMeasurementType.VRMS, channel=1)
    assert math.isnan(result)


def test_tektronix_digitize_raises_timeout_and_clears(
    tektronix: Tektronix2SeriesMSO, tektronix_visa: MagicMock
) -> None:
    tektronix_visa.query.side_effect = TimeoutError("VI_ERROR_TMO")

    with pytest.raises(TimeoutError, match="did not complete"):
        tektronix.digitize(timeout=0.1)

    tektronix_visa.clear.assert_called_once()


# --- SiglentSDS1000XE unit tests ---


@pytest.fixture
def siglent_visa_cls() -> Iterator[MagicMock]:
    with patch("instro.unstable.scope.drivers.siglent.siglent_sds1000x_e.VisaDriver", autospec=True) as driver_cls:
        yield driver_cls


@pytest.fixture
def siglent_visa(siglent_visa_cls: MagicMock) -> MagicMock:
    visa = siglent_visa_cls.return_value
    visa.query.return_value = "0"  # default: CMR?/EXR? report no error
    visa.temporary_timeout.return_value = _make_temp_timeout_cm()
    return visa


@pytest.fixture
def siglent(siglent_visa_cls: MagicMock, siglent_visa: MagicMock) -> SiglentSDS1000XE:
    return SiglentSDS1000XE("TCPIP0::192.168.1.10::INSTR")


def test_siglent_init_passes_resource_to_visa(siglent_visa_cls: MagicMock) -> None:
    SiglentSDS1000XE("TCPIP0::192.168.1.10::INSTR")
    siglent_visa_cls.assert_called_once_with("TCPIP0::192.168.1.10::INSTR")


def test_siglent_init_accepts_prebuilt_visa_config(siglent_visa_cls: MagicMock) -> None:
    config = VisaConfig(visa_resource="ASRL3::INSTR", serial_config=SerialConfig(baud_rate=115_200))
    SiglentSDS1000XE(config)
    siglent_visa_cls.assert_called_once_with(config)


def test_siglent_open_disables_headers_and_clears(siglent: SiglentSDS1000XE, siglent_visa: MagicMock) -> None:
    siglent.open()
    siglent_visa.open.assert_called_once()
    writes = [c.args[0] for c in siglent_visa.write.call_args_list]
    assert writes == ["CHDR OFF", "*CLS"]


def test_siglent_close_delegates(siglent: SiglentSDS1000XE, siglent_visa: MagicMock) -> None:
    siglent.close()
    siglent_visa.close.assert_called_once()


def test_siglent_check_errors_passes_on_zero(siglent: SiglentSDS1000XE, siglent_visa: MagicMock) -> None:
    siglent_visa.query.return_value = "0"
    siglent.check_errors()
    queries = [c.args[0] for c in siglent_visa.query.call_args_list]
    assert queries == ["CMR?", "EXR?"]


def test_siglent_check_errors_raises_on_command_error(siglent: SiglentSDS1000XE, siglent_visa: MagicMock) -> None:
    siglent_visa.query.return_value = "1"
    with pytest.raises(RuntimeError, match="Siglent command error 1"):
        siglent.check_errors()


def test_siglent_check_errors_raises_on_execution_error(siglent: SiglentSDS1000XE, siglent_visa: MagicMock) -> None:
    # CMR? clean, EXR? reports an execution fault.
    siglent_visa.query.side_effect = ["0", "26"]
    with pytest.raises(RuntimeError, match="Siglent execution error 26"):
        siglent.check_errors()


def test_siglent_set_vertical_scale_writes_scpi(siglent: SiglentSDS1000XE, siglent_visa: MagicMock) -> None:
    siglent.set_vertical_scale(0.05, channel=2)
    siglent_visa.write.assert_called_once_with("C2:VDIV 5.0000E-02")


def test_siglent_get_vertical_scale_parses_float(siglent: SiglentSDS1000XE, siglent_visa: MagicMock) -> None:
    siglent_visa.query.return_value = "2.00E-01"
    assert siglent.get_vertical_scale(1) == pytest.approx(0.2)
    siglent_visa.query.assert_called_once_with("C1:VDIV?")


def test_siglent_set_coupling_maps_to_impedance_token(siglent: SiglentSDS1000XE, siglent_visa: MagicMock) -> None:
    siglent.set_coupling(Coupling.AC, channel=1)
    siglent_visa.write.assert_called_once_with("C1:CPL A1M")


def test_siglent_get_coupling_parses_token(siglent: SiglentSDS1000XE, siglent_visa: MagicMock) -> None:
    siglent_visa.query.return_value = "D1M"
    assert siglent.get_coupling(3) == Coupling.DC


def test_siglent_set_probe_attenuation_writes_scpi(siglent: SiglentSDS1000XE, siglent_visa: MagicMock) -> None:
    siglent.set_probe_attenuation(10, channel=4)
    siglent_visa.write.assert_called_once_with("C4:ATTN 10")


def test_siglent_set_horizontal_scale_writes_scpi(siglent: SiglentSDS1000XE, siglent_visa: MagicMock) -> None:
    siglent.set_horizontal_scale(5e-4)
    siglent_visa.write.assert_called_once_with("TDIV 5.0000E-04")


def test_siglent_get_sample_rate_parses_si_suffix(siglent: SiglentSDS1000XE, siglent_visa: MagicMock) -> None:
    siglent_visa.query.return_value = "1.00GSa/s"
    assert siglent.get_sample_rate() == pytest.approx(1e9)


def test_siglent_get_sample_rate_parses_bare_float(siglent: SiglentSDS1000XE, siglent_visa: MagicMock) -> None:
    siglent_visa.query.return_value = "5.00E+08"
    assert siglent.get_sample_rate() == pytest.approx(5e8)


def test_siglent_set_acquisition_mode_writes_scpi(siglent: SiglentSDS1000XE, siglent_visa: MagicMock) -> None:
    siglent.set_acquisition_mode(AcquisitionMode.HIGH_RESOLUTION)
    siglent_visa.write.assert_called_once_with("ACQW HIGH_RES")


def test_siglent_set_acquisition_mode_average_includes_count(
    siglent: SiglentSDS1000XE, siglent_visa: MagicMock
) -> None:
    # AVERAGE is silently ignored by the scope without an inline count.
    siglent.set_acquisition_mode(AcquisitionMode.AVERAGE)
    siglent_visa.write.assert_called_once_with("ACQW AVERAGE,16")


def test_siglent_set_average_count_writes_and_feeds_average_mode(
    siglent: SiglentSDS1000XE, siglent_visa: MagicMock
) -> None:
    siglent.set_average_count(64)
    siglent_visa.write.assert_called_once_with("AVGA 64")
    siglent_visa.reset_mock()
    # The cached count is reused as the inline AVERAGE count.
    siglent.set_acquisition_mode(AcquisitionMode.AVERAGE)
    siglent_visa.write.assert_called_once_with("ACQW AVERAGE,64")


def test_siglent_acquisition_mode_envelope_rejected(siglent: SiglentSDS1000XE) -> None:
    with pytest.raises(NotImplementedError, match="ENVELOPE"):
        siglent.set_acquisition_mode(AcquisitionMode.ENVELOPE)


def test_siglent_get_acquisition_mode_parses_average_with_count(
    siglent: SiglentSDS1000XE, siglent_visa: MagicMock
) -> None:
    siglent_visa.query.return_value = "AVERAGE,16"
    assert siglent.get_acquisition_mode() == AcquisitionMode.AVERAGE


def test_siglent_run_sets_auto_without_arm(siglent: SiglentSDS1000XE, siglent_visa: MagicMock) -> None:
    # ARM would force single-shot mode, so run() must NOT send it.
    siglent.run()
    writes = [c.args[0] for c in siglent_visa.write.call_args_list]
    assert writes == ["TRMD AUTO"]


def test_siglent_single_arms_single_shot(siglent: SiglentSDS1000XE, siglent_visa: MagicMock) -> None:
    siglent.single()
    writes = [c.args[0] for c in siglent_visa.write.call_args_list]
    assert writes == ["TRMD SINGLE", "ARM"]


def test_siglent_digitize_returns_when_inr_bit_set(siglent: SiglentSDS1000XE, siglent_visa: MagicMock) -> None:
    siglent_visa.query.return_value = "1"  # INR? bit 0 set immediately
    siglent.digitize(timeout=1.0)
    writes = [c.args[0] for c in siglent_visa.write.call_args_list]
    assert writes == ["TRMD SINGLE", "ARM"]
    siglent_visa.query.assert_called_with("INR?")


def test_siglent_digitize_times_out_and_stops(siglent: SiglentSDS1000XE, siglent_visa: MagicMock) -> None:
    with pytest.raises(TimeoutError, match="did not complete"):
        siglent.digitize(timeout=0.0)
    assert siglent_visa.write.call_args_list[-1].args[0] == "STOP"


def test_siglent_get_acquisition_state_stopped(siglent: SiglentSDS1000XE, siglent_visa: MagicMock) -> None:
    siglent_visa.query.return_value = "Stop"
    assert siglent.get_acquisition_state() == AcquisitionState.STOPPED


def test_siglent_get_acquisition_state_running(siglent: SiglentSDS1000XE, siglent_visa: MagicMock) -> None:
    siglent_visa.query.return_value = "Trig'd"
    assert siglent.get_acquisition_state() == AcquisitionState.RUNNING


def test_siglent_fetch_waveform_converts_codes(siglent: SiglentSDS1000XE, siglent_visa: MagicMock) -> None:
    # CMR?, EXR? (check_errors), then VDIV?, OFST?, TDIV?, TRDL?, SARA?.
    siglent_visa.query.side_effect = ["0", "0", "0.5", "-0.5", "5.00E-09", "-5.00E-09", "1.00GSa/s"]
    siglent_visa.query_binary_values.return_value = [2, 4, -3]

    waveform = siglent.fetch_waveform(channel=1)

    siglent_visa.query_binary_values.assert_called_once_with("C1:WF? DAT2", datatype="b", container=list)
    # voltage = code * vdiv/25 - offset; code=2 -> 2*0.02 + 0.5 = 0.54
    assert waveform.voltages[0] == pytest.approx(0.54)
    assert len(waveform.times) == 3
    # 1 GSa/s -> 1 ns interval
    assert waveform.times[1] - waveform.times[0] == 1


def test_siglent_measure_vpp_queries_pava(siglent: SiglentSDS1000XE, siglent_visa: MagicMock) -> None:
    siglent_visa.query.return_value = "PKPK,1.23E+00V"
    result = siglent.measure(ScopeMeasurementType.VPP, channel=1)
    assert result == pytest.approx(1.23)
    siglent_visa.query.assert_called_once_with("C1:PAVA? PKPK")


def test_siglent_measure_returns_nan_on_non_numeric(siglent: SiglentSDS1000XE, siglent_visa: MagicMock) -> None:
    siglent_visa.query.return_value = "PKPK,****"
    assert math.isnan(siglent.measure(ScopeMeasurementType.VPP, channel=1))


def test_siglent_measure_returns_nan_on_sentinel(siglent: SiglentSDS1000XE, siglent_visa: MagicMock) -> None:
    siglent_visa.query.return_value = "FREQ,9.91E+37Hz"
    assert math.isnan(siglent.measure(ScopeMeasurementType.FREQUENCY, channel=1))


def test_siglent_set_trigger_source_emits_trse_and_caches(siglent: SiglentSDS1000XE, siglent_visa: MagicMock) -> None:
    siglent.set_trigger_source(3)
    assert siglent._trigger_source == 3
    siglent_visa.write.assert_called_once_with("TRSE EDGE,SR,C3,HT,OFF")


def test_siglent_set_trigger_type_pulse_maps_to_glit(siglent: SiglentSDS1000XE, siglent_visa: MagicMock) -> None:
    siglent.set_trigger_source(2)
    siglent_visa.reset_mock()
    siglent.set_trigger_type(TriggerType.PULSE)
    siglent_visa.write.assert_called_once_with("TRSE GLIT,SR,C2,HT,OFF")


def test_siglent_set_trigger_level_uses_cached_source(siglent: SiglentSDS1000XE, siglent_visa: MagicMock) -> None:
    siglent.set_trigger_source(2)
    siglent_visa.reset_mock()
    siglent.set_trigger_level(0.75)
    siglent_visa.write.assert_called_once_with("C2:TRLV 7.5000E-01")


def test_siglent_set_trigger_level_defaults_to_ch1(siglent: SiglentSDS1000XE, siglent_visa: MagicMock) -> None:
    siglent.set_trigger_level(0.0)
    siglent_visa.write.assert_called_once_with("C1:TRLV 0.0000E+00")


def test_siglent_set_trigger_slope_uses_cached_source(siglent: SiglentSDS1000XE, siglent_visa: MagicMock) -> None:
    siglent.set_trigger_source(4)
    siglent_visa.reset_mock()
    siglent.set_trigger_slope(TriggerSlope.FALLING)
    siglent_visa.write.assert_called_once_with("C4:TRSL NEG")


def test_siglent_set_trigger_mode_writes_scpi(siglent: SiglentSDS1000XE, siglent_visa: MagicMock) -> None:
    siglent.set_trigger_mode(TriggerMode.NORMAL)
    siglent_visa.write.assert_called_once_with("TRMD NORM")


def test_siglent_force_trigger_writes_frtr(siglent: SiglentSDS1000XE, siglent_visa: MagicMock) -> None:
    siglent.force_trigger()
    siglent_visa.write.assert_called_once_with("FRTR")


def test_siglent_get_trigger_status_maps_token(siglent: SiglentSDS1000XE, siglent_visa: MagicMock) -> None:
    siglent_visa.query.return_value = "Trig'd"
    assert siglent.get_trigger_status() == TriggerStatus.TRIGGERED


def test_siglent_save_screenshot_to_instrument_unsupported(siglent: SiglentSDS1000XE) -> None:
    with pytest.raises(NotImplementedError, match="host-transfer only"):
        siglent.save_screenshot("scope.png", to_instrument=True)


def test_siglent_save_settings_to_instrument_writes_stpn(siglent: SiglentSDS1000XE, siglent_visa: MagicMock) -> None:
    result = siglent.save_settings("setup.set", to_instrument=True)
    siglent_visa.write.assert_called_once_with("STPN DISK,UDSK,FILE,'setup.set'")
    assert result == b""


def test_siglent_load_settings_from_instrument_writes_rcpn(siglent: SiglentSDS1000XE, siglent_visa: MagicMock) -> None:
    siglent.load_settings("setup.set", from_instrument=True)
    siglent_visa.write.assert_called_once_with("RCPN DISK,UDSK,FILE,'setup.set'")


def _setup_escape_hatch(visa_mock: MagicMock, raw_reply: bytes) -> MagicMock:
    """Wire the pyvisa-resource escape hatch on the mocked transport for binary-read tests."""
    inst = MagicMock()
    inst.read_termination = "\n"
    inst.read_raw.return_value = raw_reply
    visa_mock._inst = inst
    visa_mock.lock.return_value = _make_temp_timeout_cm()
    return inst


def test_siglent_save_screenshot_host_reads_full_bmp(
    siglent: SiglentSDS1000XE, siglent_visa: MagicMock, tmp_path
) -> None:
    # 'BM' + 4-byte little-endian size (10) + 4 payload bytes, with a stray trailing LF to drop.
    bmp = b"BM" + (10).to_bytes(4, "little") + b"WXYZ"
    inst = _setup_escape_hatch(siglent_visa, bmp + b"\n")
    path = tmp_path / "shot.bmp"
    data = siglent.save_screenshot(str(path))
    assert "SCDP" in [c.args[0] for c in siglent_visa.write.call_args_list]
    inst.read_raw.assert_called_once()
    assert data == bmp  # sliced to the declared BMP size, trailing LF dropped
    assert path.read_bytes() == bmp


def test_siglent_save_settings_host_reads_pnsu_block(
    siglent: SiglentSDS1000XE, siglent_visa: MagicMock, tmp_path
) -> None:
    # Host path reads the full message via the escape hatch and parses the IEEE #9<len> block.
    payload = b"hi"
    block = b"#9" + b"%09d" % len(payload) + payload + b"\n"
    inst = _setup_escape_hatch(siglent_visa, block)
    path = tmp_path / "setup.bin"
    result = siglent.save_settings(str(path), to_instrument=False)
    assert "PNSU?" in [c.args[0] for c in siglent_visa.write.call_args_list]
    inst.read_raw.assert_called_once()
    assert result == payload
    assert path.read_bytes() == payload


def test_siglent_load_settings_host_reconstructs_block_header(
    siglent: SiglentSDS1000XE, siglent_visa: MagicMock, tmp_path
) -> None:
    path = tmp_path / "setup.bin"
    path.write_bytes(b"hello")
    siglent.load_settings(str(path), from_instrument=False)
    siglent_visa.write_raw.assert_called_once_with(b"PNSU #9000000005hello\n")


# --- InstroScope composition tests ---


class _StubScopeDriver(ScopeDriverBase):
    """Minimal ScopeDriverBase implementation for testing InstroScope behavior."""

    def __init__(self) -> None:
        self.opened = False
        self.closed = False
        self.errors_checked = 0
        self.calls: list[tuple] = []

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.closed = True

    def check_errors(self) -> None:
        self.errors_checked += 1

    def set_vertical_scale(self, volts_per_div: float, channel: int) -> None:
        self.calls.append(("set_vertical_scale", volts_per_div, channel))

    def get_vertical_scale(self, channel: int) -> float:
        return 0.5

    def set_vertical_offset(self, offset: float, channel: int) -> None:
        self.calls.append(("set_vertical_offset", offset, channel))

    def get_vertical_offset(self, channel: int) -> float:
        return 0.0

    def set_coupling(self, coupling, channel: int) -> None:
        self.calls.append(("set_coupling", coupling, channel))

    def get_coupling(self, channel: int):
        return Coupling.DC

    def set_probe_attenuation(self, factor: float, channel: int) -> None:
        self.calls.append(("set_probe_attenuation", factor, channel))

    def get_probe_attenuation(self, channel: int) -> float:
        return 1.0

    def set_horizontal_scale(self, seconds_per_div: float) -> None:
        self.calls.append(("set_horizontal_scale", seconds_per_div))

    def get_horizontal_scale(self) -> float:
        return 0.001

    def get_sample_rate(self) -> float:
        return 1e9

    def set_acquisition_mode(self, mode) -> None:
        self.calls.append(("set_acquisition_mode", mode))

    def get_acquisition_mode(self):
        return AcquisitionMode.NORMAL

    def set_average_count(self, count: int) -> None:
        self.calls.append(("set_average_count", count))

    def get_average_count(self) -> int:
        return 1

    def run(self) -> None:
        self.calls.append(("run",))

    def stop(self) -> None:
        self.calls.append(("stop",))

    def single(self) -> None:
        self.calls.append(("single",))

    def digitize(self, timeout: float) -> None:
        self.calls.append(("digitize", timeout))

    def get_acquisition_state(self):
        from instro.unstable.scope.types import AcquisitionState

        return AcquisitionState.STOPPED

    def fetch_waveform(self, channel: int):
        from instro.unstable.scope.types import WaveformData

        return WaveformData(times=[0, 1, 2], voltages=[0.1, 0.2, 0.3])

    def measure(self, measurement_type, channel: int) -> float:
        return 1.23

    def set_trigger_source(self, channel: int) -> None:
        self.calls.append(("set_trigger_source", channel))

    def set_trigger_type(self, trigger_type) -> None:
        self.calls.append(("set_trigger_type", trigger_type))

    def set_trigger_level(self, level: float) -> None:
        self.calls.append(("set_trigger_level", level))

    def set_trigger_slope(self, slope) -> None:
        self.calls.append(("set_trigger_slope", slope))

    def set_trigger_mode(self, mode) -> None:
        self.calls.append(("set_trigger_mode", mode))

    def force_trigger(self) -> None:
        self.calls.append(("force_trigger",))

    def get_trigger_status(self):
        from instro.unstable.scope.types import TriggerStatus

        return TriggerStatus.TRIGGERED

    def save_screenshot(self, filepath: str, to_instrument: bool = False) -> bytes:
        return b""

    def save_settings(self, name: str, to_instrument: bool = False) -> bytes:
        return b""

    def load_settings(self, name: str, from_instrument: bool = False) -> None:
        pass


@pytest.fixture
def stub_driver() -> _StubScopeDriver:
    return _StubScopeDriver()


def test_instro_scope_stores_driver(stub_driver: _StubScopeDriver) -> None:
    scope = InstroScope(name="ut", driver=stub_driver, num_channels=2)
    assert scope._driver is stub_driver


def test_instro_scope_open_close_delegate(stub_driver: _StubScopeDriver) -> None:
    scope = InstroScope(name="ut", driver=stub_driver, num_channels=2)
    scope.open()
    assert stub_driver.opened
    scope.close()
    assert stub_driver.closed


def test_instro_scope_set_vertical_scale_delegates_and_updates_config(
    stub_driver: _StubScopeDriver,
) -> None:
    scope = InstroScope(name="ut", driver=stub_driver, num_channels=2)
    scope.set_vertical_scale(0.25, channel=1)
    assert ("set_vertical_scale", 0.25, 1) in stub_driver.calls
    assert scope._config.channels[1].vertical_scale == 0.25


def test_instro_scope_set_trigger_source_delegates(stub_driver: _StubScopeDriver) -> None:
    scope = InstroScope(name="ut", driver=stub_driver, num_channels=2)
    scope.set_trigger_source(channel=2)
    assert ("set_trigger_source", 2) in stub_driver.calls
    assert scope._config.trigger.source == 2


def test_instro_scope_check_errors_calls_driver(stub_driver: _StubScopeDriver) -> None:
    scope = InstroScope(name="ut", driver=stub_driver, num_channels=2)
    scope.set_vertical_scale(1.0, channel=1)
    # Each public method invokes _check_errors() once
    assert stub_driver.errors_checked == 1


def test_instro_scope_initializes_channel_configs(stub_driver: _StubScopeDriver) -> None:
    scope = InstroScope(name="ut", driver=stub_driver, num_channels=4)
    assert set(scope._config.channels.keys()) == {1, 2, 3, 4}
