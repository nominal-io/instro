"""Rigol DG1022 signal generator driver."""

from instro.lib.transports.visa import VisaConfig, VisaDriver
from instro.awg.awg import AWGDriverBase

MODEL_NAME = "Rigol DG1022"


class RigolDG1022(SigGenDriverBase):
    """Rigol DG1022 two-channel function/arbitrary waveform generator."""

    def __init__(self, visa_resource: str | VisaConfig) -> None:
        self._visa = VisaDriver(visa_resource)

    @classmethod
    def match_idn(cls, idn: str) -> bool:
        upper = idn.upper()
        return "RIGOL" in upper and "DG1022" in upper

    def open(self) -> None:
        self._visa.open()

    def close(self) -> None:
        self._visa.close()

    def _ch(self, channel: Channel) -> str:
        return ":CH2" if channel == Channel.CH2 else ""

    def _send(self, cmd: str) -> None:
        with self._visa.lock():
            self._visa.write(cmd)
            self._check_errors_locked()

    def _query(self, cmd: str) -> str:
        with self._visa.lock():
            result = self._visa.query(cmd)
            self._check_errors_locked()
        return result

    def _check_errors_locked(self) -> None:
        err = self._visa.query("SYST:ERR?")
        code = err.split(",")[0].strip()
        if code != "0":
            raise RuntimeError(f"{MODEL_NAME} error: {err.strip()}")

    def check_errors(self) -> None:
        with self._visa.lock():
            self._check_errors_locked()

    # --- Output ---

    def set_output_enable(self, channel: Channel, enable: bool) -> None:
        state = "ON" if enable else "OFF"
        self._send(f"OUTP{self._ch(channel)} {state}")

    def set_output_load(self, channel: Channel, ohms: float | None) -> None:
        value = "INF" if ohms is None else f"{ohms:g}"
        self._send(f"OUTP:LOAD{self._ch(channel)} {value}")

    def set_output_polarity(self, channel: Channel, polarity: OutputPolarity) -> None:
        self._send(f"OUTP:POL{self._ch(channel)} {polarity.value}")

    def set_sync_output_enable(self, enable: bool) -> None:
        self._send(f"OUTP:SYNC {'ON' if enable else 'OFF'}")

    # --- APPLy shortcut ---

    def apply_waveform(
        self,
        channel: Channel,
        waveform: WaveformType,
        frequency: float,
        amplitude: float,
        offset: float,
    ) -> None:
        # :CH2 suffix goes after the waveform keyword for APPLy
        if channel == Channel.CH1:
            self._send(f"APPL:{waveform.value} {frequency:g},{amplitude:g},{offset:g}")
        else:
            self._send(f"APPL:{waveform.value}:CH2 {frequency:g},{amplitude:g},{offset:g}")

    # --- Discrete waveform params ---

    def set_function(self, channel: Channel, waveform: WaveformType) -> None:
        self._send(f"FUNC{self._ch(channel)} {waveform.value}")

    def set_frequency(self, channel: Channel, frequency_hz: float) -> None:
        self._send(f"FREQ{self._ch(channel)} {frequency_hz:g}")

    def set_amplitude(self, channel: Channel, amplitude: float) -> None:
        self._send(f"VOLT{self._ch(channel)} {amplitude:g}")

    def set_voltage_unit(self, channel: Channel, unit: VoltageUnit) -> None:
        self._send(f"VOLT:UNIT{self._ch(channel)} {unit.value}")

    def set_offset(self, channel: Channel, offset_v: float) -> None:
        self._send(f"VOLT:OFFS{self._ch(channel)} {offset_v:g}")

    def set_high_level(self, channel: Channel, volts: float) -> None:
        self._send(f"VOLT:HIGH{self._ch(channel)} {volts:g}")

    def set_low_level(self, channel: Channel, volts: float) -> None:
        self._send(f"VOLT:LOW{self._ch(channel)} {volts:g}")

    def set_phase(self, channel: Channel, phase_deg: float) -> None:
        self._send(f"PHAS{self._ch(channel)} {phase_deg:g}")

    def align_phase(self) -> None:
        self._send("PHAS:ALIGN")

    # --- Waveform-specific ---

    def set_square_duty_cycle(self, channel: Channel, duty_pct: float) -> None:
        self._send(f"FUNC:SQU:DCYC{self._ch(channel)} {duty_pct:g}")

    def set_ramp_symmetry(self, channel: Channel, symmetry_pct: float) -> None:
        self._send(f"FUNC:RAMP:SYMM{self._ch(channel)} {symmetry_pct:g}")

    def set_pulse_period(self, channel: Channel, period_s: float) -> None:
        self._send(f"PULS:PER{self._ch(channel)} {period_s:g}")

    def set_pulse_width(self, channel: Channel, width_s: float) -> None:
        self._send(f"PULS:WIDT{self._ch(channel)} {width_s:g}")

    def set_pulse_duty_cycle(self, channel: Channel, duty_pct: float) -> None:
        self._send(f"PULS:DCYC{self._ch(channel)} {duty_pct:g}")

    # --- Modulation (CH1 only) ---

    def set_am_state(self, enable: bool) -> None:
        self._send(f"AM:STAT {'ON' if enable else 'OFF'}")

    def set_am_source(self, source: ModSource) -> None:
        self._send(f"AM:SOUR {source.value}")

    def set_am_internal_function(self, waveform: ModWaveform) -> None:
        self._send(f"AM:INT:FUNC {waveform.value}")

    def set_am_internal_frequency(self, frequency_hz: float) -> None:
        self._send(f"AM:INT:FREQ {frequency_hz:g}")

    def set_am_depth(self, depth_pct: float) -> None:
        self._send(f"AM:DEPT {depth_pct:g}")

    def set_fm_state(self, enable: bool) -> None:
        self._send(f"FM:STAT {'ON' if enable else 'OFF'}")

    def set_fm_source(self, source: ModSource) -> None:
        self._send(f"FM:SOUR {source.value}")

    def set_fm_internal_function(self, waveform: ModWaveform) -> None:
        self._send(f"FM:INT:FUNC {waveform.value}")

    def set_fm_internal_frequency(self, frequency_hz: float) -> None:
        self._send(f"FM:INT:FREQ {frequency_hz:g}")

    def set_fm_deviation(self, deviation_hz: float) -> None:
        self._send(f"FM:DEV {deviation_hz:g}")

    def set_pm_state(self, enable: bool) -> None:
        self._send(f"PM:STAT {'ON' if enable else 'OFF'}")

    def set_pm_source(self, source: ModSource) -> None:
        self._send(f"PM:SOUR {source.value}")

    def set_pm_internal_function(self, waveform: ModWaveform) -> None:
        self._send(f"PM:INT:FUNC {waveform.value}")

    def set_pm_internal_frequency(self, frequency_hz: float) -> None:
        self._send(f"PM:INT:FREQ {frequency_hz:g}")

    def set_pm_deviation(self, deviation_deg: float) -> None:
        self._send(f"PM:DEV {deviation_deg:g}")

    def set_fsk_state(self, enable: bool) -> None:
        self._send(f"FSK:STAT {'ON' if enable else 'OFF'}")

    def set_fsk_source(self, source: ModSource) -> None:
        self._send(f"FSK:SOUR {source.value}")

    def set_fsk_hop_frequency(self, frequency_hz: float) -> None:
        self._send(f"FSK:FREQ {frequency_hz:g}")

    def set_fsk_rate(self, rate_hz: float) -> None:
        self._send(f"FSK:INT:RATE {rate_hz:g}")

    # --- Sweep (CH1 only) ---

    def set_sweep_state(self, enable: bool) -> None:
        self._send(f"SWE:STAT {'ON' if enable else 'OFF'}")

    def set_sweep_spacing(self, spacing: SweepSpacing) -> None:
        self._send(f"SWE:SPAC {spacing.value}")

    def set_sweep_time(self, time_s: float) -> None:
        self._send(f"SWE:TIME {time_s:g}")

    def set_sweep_start_freq(self, frequency_hz: float) -> None:
        self._send(f"FREQ:STAR {frequency_hz:g}")

    def set_sweep_stop_freq(self, frequency_hz: float) -> None:
        self._send(f"FREQ:STOP {frequency_hz:g}")

    def set_sweep_center_freq(self, frequency_hz: float) -> None:
        self._send(f"FREQ:CENT {frequency_hz:g}")

    def set_sweep_span_freq(self, span_hz: float) -> None:
        self._send(f"FREQ:SPAN {span_hz:g}")

    # --- Trigger ---

    def set_trigger_source(self, source: TriggerSource) -> None:
        # TriggerSource.INTERNAL = "IMM" (not "INT")
        self._send(f"TRIG:SOUR {source.value}")

    def set_trigger_slope(self, slope: TriggerSlope) -> None:
        self._send(f"TRIG:SLOP {slope.value}")

    def set_trigger_delay(self, delay_s: float) -> None:
        self._send(f"TRIG:DEL {delay_s:g}")

    def set_trigger_output_enable(self, enable: bool) -> None:
        self._send(f"OUTP:TRIG {'ON' if enable else 'OFF'}")

    def set_trigger_output_slope(self, slope: TriggerSlope) -> None:
        self._send(f"OUTP:TRIG:SLOP {slope.value}")

    # --- Burst (CH1 only) ---

    def set_burst_state(self, enable: bool) -> None:
        self._send(f"BURS:STAT {'ON' if enable else 'OFF'}")

    def set_burst_mode(self, mode: BurstMode) -> None:
        self._send(f"BURS:MODE {mode.value}")

    def set_burst_cycles(self, cycles: int | float) -> None:
        value = "INF" if cycles == float("inf") else f"{int(cycles)}"
        self._send(f"BURS:NCYC {value}")

    def set_burst_period(self, period_s: float) -> None:
        self._send(f"BURS:INT:PER {period_s:g}")

    def set_burst_phase(self, phase_deg: float) -> None:
        self._send(f"BURS:PHAS {phase_deg:g}")

    def set_burst_gate_polarity(self, polarity: OutputPolarity) -> None:
        self._send(f"BURS:GATE:POL {polarity.value}")

    # --- Arb waveform ---

    def upload_arb_waveform_float(self, data: list[float]) -> None:
        values = ",".join(f"{v:g}" for v in data)
        self._send(f"DATA VOLATILE,{values}")

    def upload_arb_waveform_dac(self, data: list[int]) -> None:
        values = ",".join(str(v) for v in data)
        self._send(f"DATA:DAC VOLATILE,{values}")

    def set_arb_user(self, channel: Channel, name: str) -> None:
        self._send(f"FUNC:USER{self._ch(channel)} {name}")

    def copy_arb_to_nonvolatile(self, dest_name: str) -> None:
        self._send(f"DATA:COPY {dest_name},VOLATILE")

    # --- Coupling / copy ---

    def set_coupling_state(self, enable: bool) -> None:
        self._send(f"COUP:STAT {'ON' if enable else 'OFF'}")

    def set_coupling_base_channel(self, channel: Channel) -> None:
        # Colon before CH keyword per DG1022 convention
        self._send(f"COUP:BASE:CH{channel.value}")

    def set_coupling_phase_deviation(self, deviation_deg: float) -> None:
        self._send(f"COUP:PHAS:DEV {deviation_deg:g}")

    def set_coupling_freq_deviation(self, deviation_hz: float) -> None:
        self._send(f"COUP:FREQ:DEV {deviation_hz:g}")

    def copy_channel(self, source: Channel, dest: Channel) -> None:
        # Uses > literal: COUP:CHANNC 1>2
        self._send(f"COUP:CHANNC {source.value}>{dest.value}")

    # --- System ---

    def set_clock_source(self, source: ClockSource) -> None:
        self._send(f"ROSC:SOUR {source.value}")
