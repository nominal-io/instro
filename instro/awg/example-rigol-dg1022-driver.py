"""

Python Interchangeable Virtual Instrument Library

Copyright (c) 2012-2017 Alex Forencich

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.

"""

from . import ivi

# Exceptions
class FrequencyListUnknownException(ivi.IviException): pass

# Parameter Values
Scaling = set(['linear', 'logarithmic'])
ExternalCoupling = set(['ac', 'dc'])
Source = set(['internal', 'external'])
Polarity = set(['normal', 'inverse'])
Slope = set(['positive', 'negative'])
LFGeneratorWaveform = set(['sine', 'square', 'triangle', 'ramp_up', 'ramp_down'])
SweepMode = set(['none', 'frequency_sweep', 'power_sweep', 'frequency_step', 'power_step', 'list'])
TriggerSource = set(['immediate', 'external', 'software'])
IQSource = set(['digital_modulation_base', 'cdma_base', 'tdma_base', 'arb_generator', 'external'])
DigitalModulationBaseDataSource = set(['external', 'prbs', 'bit_sequence'])
DigitalModulationBasePRBSType = set(['prbs9', 'prbs11', 'prbs15', 'prbs16', 'prbs20', 'prbs21', 'prbs23'])
ClockType = set(['bit', 'symbol'])

class Base(ivi.IviContainer):
    "Base IVI methods for all RF signal generators"
    
    def __init__(self, *args, **kwargs):
        super(Base, self).__init__(*args, **kwargs)
        
        cls = 'IviRFSigGen'
        grp = 'Base'
        ivi.add_group_capability(self, cls+grp)
        
        self._rf_frequency = 1e8
        self._rf_level = 0.0
        self._rf_output_enabled = False
        self._alc_enabled = False
        
        self._add_property('rf.frequency',
                        self._get_rf_frequency,
                        self._set_rf_frequency)
        self._add_property('rf.level',
                        self._get_rf_level,
                        self._set_rf_level)
        self._add_property('rf.output_enabled',
                        self._get_rf_output_enabled,
                        self._set_rf_output_enabled)
        self._add_method('rf.configure',
                        self._rf_configure)
        self._add_method('rf.disable_all_modulation',
                        self._rf_disable_all_modulation)
        self._add_method('rf.is_settled',
                        self._rf_is_settled)
        self._add_method('rf.wait_until_settled',
                        self._rf_wait_until_settled)
        self._add_property('alc.enabled',
                        self._get_alc_enabled,
                        self._set_alc_enabled)
    
    def _get_rf_frequency(self):
        return self._rf_frequency
    
    def _set_rf_frequency(self, value):
        value = float(value)
        self._rf_frequency = value
    
    def _get_rf_level(self):
        return self._rf_level
    
    def _set_rf_level(self, value):
        value = float(value)
        self._rf_level = value
    
    def _get_rf_output_enabled(self):
        return self._rf_output_enabled
    
    def _set_rf_output_enabled(self, value):
        value = bool(value)
        self._rf_output_enabled = value
    
    def _get_alc_enabled(self):
        return self._alc_enabled
    
    def _set_alc_enabled(self, value):
        value = bool(value)
        self._alc_enabled = value
    
    def _rf_configure(self, frequency, level):
        self._set_rf_frequency(frequency)
        self._set_rf_level(level)
    
    def _rf_disable_all_modulation(self):
        try:
            self._set_analog_modulation_am_enabled(False)
        except:
            pass
        try:
            self._set_analog_modulation_fm_enabled(False)
        except:
            pass
        try:
            self._set_analog_modulation_pm_enabled(False)
        except:
            pass
        try:
            self._set_pulse_modulation_enabled(False)
        except:
            pass
    
    def _rf_is_settled(self):
        return True
    
    def _rf_wait_until_settled(self, maximum_time):
        t = 0
        while not self._rf_is_settled() and t < maximum_time:
            time.sleep(0.01)
            t = t + 0.01
    
    
class ModulateAM(ivi.IviContainer):
    "Extension IVI methods for generators supporting amplitude modulation"
    
    def __init__(self, *args, **kwargs):
        super(ModulateAM, self).__init__(*args, **kwargs)
        
        cls = 'IviRFSigGen'
        grp = 'ModulateAM'
        ivi.add_group_capability(self, cls+grp)
        
        self._analog_modulation_am_enabled = False
        self._analog_modulation_am_source = ""
        self._analog_modulation_am_scaling = 'linear'
        self._analog_modulation_am_external_coupling = 'dc'
        self._analog_modulation_am_nominal_voltage = 0.0
        self._analog_modulation_am_depth = 0.0
        
        self._add_property('analog_modulation.am.enabled',
                        self._get_analog_modulation_am_enabled,
                        self._set_analog_modulation_am_enabled)
        self._add_property('analog_modulation.am.source',
                        self._get_analog_modulation_am_source,
                        self._set_analog_modulation_am_source)
        self._add_property('analog_modulation.am.scaling',
                        self._get_analog_modulation_am_scaling,
                        self._set_analog_modulation_am_scaling)
        self._add_property('analog_modulation.am.external_coupling',
                        self._get_analog_modulation_am_external_coupling,
                        self._set_analog_modulation_am_external_coupling)
        self._add_property('analog_modulation.am.nominal_voltage',
                        self._get_analog_modulation_am_nominal_voltage,
                        self._set_analog_modulation_am_nominal_voltage)
        self._add_property('analog_modulation.am.depth',
                        self._get_analog_modulation_am_depth,
                        self._set_analog_modulation_am_depth)
        self._add_method('analog_modulation.am.configure',
                        self._analog_modulation_am_configure)
    
    def _get_analog_modulation_am_enabled(self):
        return self._analog_modulation_am_enabled
    
    def _set_analog_modulation_am_enabled(self, value):
        value = bool(value)
        self._analog_modulation_am_enabled = value
    
    def _get_analog_modulation_am_source(self):
        return self._analog_modulation_am_source
    
    def _set_analog_modulation_am_source(self, value):
        if value not in Source:
            raise ivi.ValueNotSupportedException()
        self._analog_modulation_am_source = value
    
    def _get_analog_modulation_am_scaling(self):
        return self._analog_modulation_am_scaling
    
    def _set_analog_modulation_am_scaling(self, value):
        value = int(value)
        self._analog_modulation_am_scaling = value
    
    def _get_analog_modulation_am_external_coupling(self):
        return self._analog_modulation_am_external_coupling
    
    def _set_analog_modulation_am_external_coupling(self, value):
        if value not in ExternalCoupling:
            raise ivi.ValueNotSupportedException()
        self._analog_modulation_am_external_coupling = value
    
    def _get_analog_modulation_am_nominal_voltage(self):
        return self._analog_modulation_am_nominal_voltage
    
    def _set_analog_modulation_am_nominal_voltage(self, value):
        value = float(value)
        self._analog_modulation_am_nominal_voltage = value
    
    def _get_analog_modulation_am_depth(self):
        return self._analog_modulation_am_depth
    
    def _set_analog_modulation_am_depth(self, value):
        value = float(value)
        self._analog_modulation_am_depth = value
    
    def _analog_modulation_am_configure(self, source, scaling, depth):
        self._set_analog_modulation_am_source(source)
        self._set_analog_modulation_am_scaling(scaling)
        self._set_analog_modulation_am_depth(depth)
    
    
class ModulateFM(ivi.IviContainer):
    "Extension IVI methods for generators supporting frequency modulation"
    
    def __init__(self, *args, **kwargs):
        super(ModulateFM, self).__init__(*args, **kwargs)
        
        cls = 'IviRFSigGen'
        grp = 'ModulateFM'
        ivi.add_group_capability(self, cls+grp)
        
        self._analog_modulation_fm_enabled = False
        self._analog_modulation_fm_source = ""
        self._analog_modulation_fm_external_coupling = 'dc'
        self._analog_modulation_fm_nominal_voltage = 0.0
        self._analog_modulation_fm_deviation = 0.0
        
        self._add_property('analog_modulation.fm.enabled',
                        self._get_analog_modulation_fm_enabled,
                        self._set_analog_modulation_fm_enabled)
        self._add_property('analog_modulation.fm.source',
                        self._get_analog_modulation_fm_source,
                        self._set_analog_modulation_fm_source)
        self._add_property('analog_modulation.fm.external_coupling',
                        self._get_analog_modulation_fm_external_coupling,
                        self._set_analog_modulation_fm_external_coupling)
        self._add_property('analog_modulation.fm.nominal_voltage',
                        self._get_analog_modulation_fm_nominal_voltage,
                        self._set_analog_modulation_fm_nominal_voltage)
        self._add_property('analog_modulation.fm.deviation',
                        self._get_analog_modulation_fm_deviation,
                        self._set_analog_modulation_fm_deviation)
        self._add_method('analog_modulation.fm.configure',
                        self._analog_modulation_fm_configure)
    
    def _get_analog_modulation_fm_enabled(self):
        return self._analog_modulation_fm_enabled
    
    def _set_analog_modulation_fm_enabled(self, value):
        value = bool(value)
        self._analog_modulation_fm_enabled = value
    
    def _get_analog_modulation_fm_source(self):
        return self._analog_modulation_fm_source
    
    def _set_analog_modulation_fm_source(self, value):
        if value not in Source:
            raise ivi.ValueNotSupportedException()
        self._analog_modulation_fm_source = value
    
    def _get_analog_modulation_fm_external_coupling(self):
        return self._analog_modulation_fm_external_coupling
    
    def _set_analog_modulation_fm_external_coupling(self, value):
        if value not in ExternalCoupling:
            raise ivi.ValueNotSupportedException()
        self._analog_modulation_fm_external_coupling = value
    
    def _get_analog_modulation_fm_nominal_voltage(self):
        return self._analog_modulation_fm_nominal_voltage
    
    def _set_analog_modulation_fm_nominal_voltage(self, value):
        value = float(value)
        self._analog_modulation_fm_nominal_voltage = value
    
    def _get_analog_modulation_fm_deviation(self):
        return self._analog_modulation_fm_deviation
    
    def _set_analog_modulation_fm_deviation(self, value):
        value = float(value)
        self._analog_modulation_fm_deviation = value
    
    def _analog_modulation_fm_configure(self, source, deviation):
        self._set_analog_modulation_fm_source(source)
        self._set_analog_modulation_fm_deviation(deviation)
    
    
class ModulatePM(ivi.IviContainer):
    "Extension IVI methods for generators supporting phase modulation"
    
    def __init__(self, *args, **kwargs):
        super(ModulatePM, self).__init__(*args, **kwargs)
        
        cls = 'IviRFSigGen'
        grp = 'ModulatePM'
        ivi.add_group_capability(self, cls+grp)
        
        self._analog_modulation_pm_enabled = False
        self._analog_modulation_pm_source = ""
        self._analog_modulation_pm_external_coupling = 'dc'
        self._analog_modulation_pm_nominal_voltage = 0.0
        self._analog_modulation_pm_deviation = 0.0
        
        self._add_property('analog_modulation.pm.enabled',
                        self._get_analog_modulation_pm_enabled,
                        self._set_analog_modulation_pm_enabled)
        self._add_property('analog_modulation.pm.source',
                        self._get_analog_modulation_pm_source,
                        self._set_analog_modulation_pm_source)
        self._add_property('analog_modulation.pm.external_coupling',
                        self._get_analog_modulation_pm_external_coupling,
                        self._set_analog_modulation_pm_external_coupling)
        self._add_property('analog_modulation.pm.nominal_voltage',
                        self._get_analog_modulation_pm_nominal_voltage,
                        self._set_analog_modulation_pm_nominal_voltage)
        self._add_property('analog_modulation.pm.deviation',
                        self._get_analog_modulation_pm_deviation,
                        self._set_analog_modulation_pm_deviation)
        self._add_method('analog_modulation.pm.configure',
                        self._analog_modulation_pm_configure)
    
    def _get_analog_modulation_pm_enabled(self):
        return self._analog_modulation_pm_enabled
    
    def _set_analog_modulation_pm_enabled(self, value):
        value = bool(value)
        self._analog_modulation_pm_enabled = value
    
    def _get_analog_modulation_pm_source(self):
        return self._analog_modulation_pm_source
    
    def _set_analog_modulation_pm_source(self, value):
        if value not in Source:
            raise ivi.ValueNotSupportedException()
        self._analog_modulation_pm_source = value
    
    def _get_analog_modulation_pm_external_coupling(self):
        return self._analog_modulation_pm_external_coupling
    
    def _set_analog_modulation_pm_external_coupling(self, value):
        if value not in ExternalCoupling:
            raise ivi.ValueNotSupportedException()
        self._analog_modulation_pm_external_coupling = value
    
    def _get_analog_modulation_pm_nominal_voltage(self):
        return self._analog_modulation_pm_nominal_voltage
    
    def _set_analog_modulation_pm_nominal_voltage(self, value):
        value = float(value)
        self._analog_modulation_pm_nominal_voltage = value
    
    def _get_analog_modulation_pm_deviation(self):
        return self._analog_modulation_pm_deviation
    
    def _set_analog_modulation_pm_deviation(self, value):
        value = float(value)
        self._analog_modulation_pm_deviation = value
    
    def _analog_modulation_pm_configure(self, source, deviation):
        self._set_analog_modulation_pm_source(source)
        self._set_analog_modulation_pm_deviation(deviation)
    
    
class AnalogModulationSource(ivi.IviContainer):
    "Extension IVI methods for generators supporting analog modulation"
    
    def __init__(self, *args, **kwargs):
        super(AnalogModulationSource, self).__init__(*args, **kwargs)
        
        cls = 'IviRFSigGen'
        grp = 'AnalogModulationSource'
        ivi.add_group_capability(self, cls+grp)
        
        self._analog_modulation_source_count = 0
        self._analog_modulation_source_name = list()
    
    def _get_analog_modulation_source_count(self):
        return self._analog_modulation_source_count
    
    def _get_analog_modulation_source_name(self, index):
        if index < 0 or index >= self._analog_modulation_source_count: raise Exception('Channel out of range')
        return self._analog_modulation_source_name[index]
    
    
class ModulatePulse(ivi.IviContainer):
    "Extension IVI methods for generators supporting pulse modulation"
    
    def __init__(self, *args, **kwargs):
        super(ModulatePulse, self).__init__(*args, **kwargs)
        
        cls = 'IviRFSigGen'
        grp = 'ModulatePulse'
        ivi.add_group_capability(self, cls+grp)
        
        self._pulse_modulation_enabled = False
        self._pulse_modulation_source = ""
        self._pulse_modulation_external_polarity = 'normal'
        
        self._add_property('pulse_modulation.enabled',
                        self._get_pulse_modulation_enabled,
                        self._set_pulse_modulation_enabled)
        self._add_property('pulse_modulation.source',
                        self._get_pulse_modulation_source,
                        self._set_pulse_modulation_source)
        self._add_property('pulse_modulation.external_polarity',
                        self._get_pulse_modulation_external_polarity,
                        self._set_pulse_modulation_external_polarity)
    
    def _get_pulse_modulation_enabled(self):
        return self._pulse_modulation_enabled
    
    def _set_pulse_modulation_enabled(self, value):
        value = bool(value)
        self._pulse_modulation_enabled = value
    
    def _get_pulse_modulation_source(self):
        return self._pulse_modulation_source
    
    def _set_pulse_modulation_source(self, value):
        if value not in Source:
            raise ivi.ValueNotSupportedException()
        self._pulse_modulation_source = value
    
    def _get_pulse_modulation_external_polarity(self):
        return self._pulse_modulation_external_polarity
    
    def _set_pulse_modulation_external_polarity(self, value):
        if value not in Polarity:
            raise ivi.ValueNotSupportedException()
        self._pulse_modulation_external_polarity = value
    
    
class LFGenerator(ivi.IviContainer):
    "Extension IVI methods for generators with internal analog modulation sources"
    
    def __init__(self, *args, **kwargs):
        super(LFGenerator, self).__init__(*args, **kwargs)
        
        cls = 'IviRFSigGen'
        grp = 'LFGenerator'
        ivi.add_group_capability(self, cls+grp)
        
        self._lf_generator_active_lf_generator = ""
        self._lf_generator_count = 0
        self._lf_generator_name = list()
        self._lf_generator_frequency = 0.0
        self._lf_generator_waveform = 'sine'
        
        self._add_property('lf_generator.active_lf_generator',
                        self._get_lf_generator_active_lf_generator,
                        self._set_lf_generator_active_lf_generator)
        self._add_property('lf_generator.frequency',
                        self._get_lf_generator_frequency,
                        self._set_lf_generator_frequency)
        self._add_property('lf_generator.waveform',
                        self._get_lf_generator_waveform,
                        self._set_lf_generator_waveform)
        self._add_method('lf_generator.configure',
                        self._lf_generator_configure)
    
    def _get_lf_generator_active_lf_generator(self):
        return self._lf_generator_active_lf_generator
    
    def _set_lf_generator_active_lf_generator(self, value):
        value = str(value)
        self._lf_generator_active_lf_generator = value
    
    def _get_lf_generator_count(self):
        return self._lf_generator_count
    
    def _get_lf_generator_name(self, index):
        if index < 0 or index >= self._lf_generator_count: raise Exception('Channel out of range')
        return self._lf_generator_name[index]
    
    def _get_lf_generator_frequency(self):
        return self._lf_generator_frequency
    
    def _set_lf_generator_frequency(self, value):
        value = float(value)
        self._lf_generator_frequency = value
    
    def _get_lf_generator_waveform(self):
        return self._lf_generator_waveform
    
    def _set_lf_generator_waveform(self, value):
        if value not in LFGeneratorWaveform:
            raise ivi.ValueNotSupportedException()
        self._lf_generator_waveform = value
    
    def _lf_generator_configure(self, frequency, waveform):
        self._set_lf_generator_frequency(frequency)
        self._set_lf_generator_waveform(waveform)
    
    
class LFGeneratorOutput(ivi.IviContainer):
    "Extension IVI methods for generators with internal analog modulation sources"
    
    def __init__(self, *args, **kwargs):
        super(LFGeneratorOutput, self).__init__(*args, **kwargs)
        
        cls = 'IviRFSigGen'
        grp = 'LFGeneratorOutput'
        ivi.add_group_capability(self, cls+grp)
        
        self._lf_generator_output_amplitude = 0.0
        self._lf_generator_output_enabled = False
        
        self._add_property('lf_generator.output.enabled',
                        self._get_lf_generator_output_enabled,
                        self._set_lf_generator_output_enabled)
        self._add_property('lf_generator.output.amplitude',
                        self._get_lf_generator_output_amplitude,
                        self._set_lf_generator_output_amplitude)
        self._add_method('lf_generator.output.configure',
                        self._lf_generator_output_configure)
    
    def _get_lf_generator_output_amplitude(self):
        return self._lf_generator_output_amplitude
    
    def _set_lf_generator_output_amplitude(self, value):
        value = float(value)
        self._lf_generator_output_amplitude = value
    
    def _get_lf_generator_output_enabled(self):
        return self._lf_generator_output_enabled
    
    def _set_lf_generator_output_enabled(self, value):
        value = bool(value)
        self._lf_generator_output_enabled = value
    
    def _lf_generator_output_configure(self, amplitude, enabled):
        self._set_lf_generator_output_amplitude(amplitude)
        self._set_lf_generator_output_enabled(enabled)
    
    
class PulseGenerator(ivi.IviContainer):
    "Extension IVI methods for generators with internal pulse modulation sources"
    
    def __init__(self, *args, **kwargs):
        super(PulseGenerator, self).__init__(*args, **kwargs)
        
        cls = 'IviRFSigGen'
        grp = 'PulseGenerator'
        ivi.add_group_capability(self, cls+grp)
        
        self._pulse_generator_internal_trigger_period = 0.0
        self._pulse_generator_width = 0.0
        self._pulse_generator_gating_enabled = False
        self._pulse_generator_trigger_source = ""
        self._pulse_generator_external_trigger_slope = 0.0
        self._pulse_generator_external_trigger_delay = 0.0
        
        self._add_property('pulse_generator.internal_trigger_period',
                        self._get_pulse_generator_internal_trigger_period,
                        self._set_pulse_generator_internal_trigger_period)
        self._add_property('pulse_generator.width',
                        self._get_pulse_generator_width,
                        self._set_pulse_generator_width)
        self._add_property('pulse_generator.gating_enabled',
                        self._get_pulse_generator_gating_enabled,
                        self._set_pulse_generator_gating_enabled)
        self._add_property('pulse_generator.trigger_source',
                        self._get_pulse_generator_trigger_source,
                        self._set_pulse_generator_trigger_source)
        self._add_property('pulse_generator.external_trigger_slope',
                        self._get_pulse_generator_external_trigger_slope,
                        self._set_pulse_generator_external_trigger_slope)
        self._add_property('pulse_generator.external_trigger_delay',
                        self._get_pulse_generator_external_trigger_delay,
                        self._set_pulse_generator_external_trigger_delay)
        self._add_method('pulse_generator.configure_external_trigger',
                        self._pulse_generator_configure_external_trigger)
        self._add_method('pulse_generator.configure',
                        self._pulse_generator_configure)
    
    def _get_pulse_generator_internal_trigger_period(self):
        return self._pulse_generator_internal_trigger_period
    
    def _set_pulse_generator_internal_trigger_period(self, value):
        value = float(value)
        self._pulse_generator_internal_trigger_period = value
    
    def _get_pulse_generator_width(self):
        return self._pulse_generator_width
    
    def _set_pulse_generator_width(self, value):
        value = float(value)
        self._pulse_generator_width = value
    
    def _get_pulse_generator_gating_enabled(self):
        return self._pulse_generator_gating_enabled
    
    def _set_pulse_generator_gating_enabled(self, value):
        value = bool(value)
        self._pulse_generator_gating_enabled = value
    
    def _get_pulse_generator_trigger_source(self):
        return self._pulse_generator_trigger_source
    
    def _set_pulse_generator_trigger_source(self, value):
        if value not in Source:
            raise ivi.ValueNotSupportedException()
        self._pulse_generator_trigger_source = value
    
    def _get_pulse_generator_external_trigger_slope(self):
        return self._pulse_generator_external_trigger_slope
    
    def _set_pulse_generator_external_trigger_slope(self, value):
        value = int(value)
        self._pulse_generator_external_trigger_slope = value
    
    def _get_pulse_generator_external_trigger_delay(self):
        return self._pulse_generator_external_trigger_delay
    
    def _set_pulse_generator_external_trigger_delay(self, value):
        value = float(value)
        self._pulse_generator_external_trigger_delay = value
    
    def _pulse_generator_configure_external_trigger(self, slope, delay):
        self._set_pulse_generator_external_trigger_slope(slope)
        self._set_pulse_generator_external_trigger_delay(delay)
    
    def _pulse_generator_configure(self, trigger_source, pulse_width, gating_enabled):
        self._set_pulse_generator_trigger_source(trigger_source)
        self._set_pulse_generator_width(pulse_width)
        self._set_pulse_generator_gating_enabled(gating_enabled)
    
    
class PulseDoubleGenerator(ivi.IviContainer):
    "Extension IVI methods for generators with internal double pulse modulation sources"
    
    def __init__(self, *args, **kwargs):
        super(PulseDoubleGenerator, self).__init__(*args, **kwargs)
        
        cls = 'IviRFSigGen'
        grp = 'PulseDoubleGenerator'
        ivi.add_group_capability(self, cls+grp)
        
        self._pulse_generator_double_pulse_enabled = False
        self._pulse_generator_double_pulse_delay = 0.0
        
        self._add_property('pulse_generator.double_pulse.enabled',
                        self._get_pulse_generator_double_pulse_enabled,
                        self._set_pulse_generator_double_pulse_enabled)
        self._add_property('pulse_generator.double_pulse.delay',
                        self._get_pulse_generator_double_pulse_delay,
                        self._set_pulse_generator_double_pulse_delay)
        self._add_method('pulse_generator.double_pulse.configure',
                        self._pulse_generator_double_pulse_configure)
    
    def _get_pulse_generator_double_pulse_enabled(self):
        return self._pulse_generator_double_pulse_enabled
    
    def _set_pulse_generator_double_pulse_enabled(self, value):
        value = bool(value)
        self._pulse_generator_double_pulse_enabled = value
    
    def _get_pulse_generator_double_pulse_delay(self):
        return self._pulse_generator_double_pulse_delay
    
    def _set_pulse_generator_double_pulse_delay(self, value):
        value = float(value)
        self._pulse_generator_double_pulse_delay = value
    
    def _pulse_generator_double_pulse_configure(self, enabled, delay):
        self._set_pulse_generator_double_pulse_enabled(enabled)
        self._set_pulse_generator_double_pulse_delay(delay)
    
    
class PulseGeneratorOutput(ivi.IviContainer):
    "Extension IVI methods for generators with internal pulse modulation sources"
    
    def __init__(self, *args, **kwargs):
        super(PulseGeneratorOutput, self).__init__(*args, **kwargs)
        
        cls = 'IviRFSigGen'
        grp = 'PulseGeneratorOutput'
        ivi.add_group_capability(self, cls+grp)
        
        self._pulse_generator_output_polarity = 'normal'
        self._pulse_generator_output_enabled = False
        
        self._add_property('pulse_generator.output.polarity',
                        self._get_pulse_generator_output_polarity,
                        self._set_pulse_generator_output_polarity)
        self._add_property('pulse_generator.output.enabled',
                        self._get_pulse_generator_output_enabled,
                        self._set_pulse_generator_output_enabled)
        self._add_method('pulse_generator.output.configure',
                        self._pulse_generator_output_configure)
    
    def _get_pulse_generator_output_polarity(self):
        return self._pulse_generator_output_polarity
    
    def _set_pulse_generator_output_polarity(self, value):
        if value not in Polarity:
            raise ivi.ValueNotSupportedException()
        self._pulse_generator_output_polarity = value
    
    def _get_pulse_generator_output_enabled(self):
        return self._pulse_generator_output_enabled
    
    def _set_pulse_generator_output_enabled(self, value):
        value = bool(value)
        self._pulse_generator_output_enabled = value
    
    def _pulse_generator_output_configure(self, polarity, enabled):
        self._set_pulse_generator_output_polarity(polarity)
        self._set_pulse_generator_output_enabled(enabled)
    
    
class Sweep(ivi.IviContainer):
    "Extension IVI methods for generators that support sweeping"
    
    def __init__(self, *args, **kwargs):
        super(Sweep, self).__init__(*args, **kwargs)
        
        cls = 'IviRFSigGen'
        grp = 'Sweep'
        ivi.add_group_capability(self, cls+grp)
        
        self._sweep_mode = 'none'
        self._sweep_trigger_source = ""
        
        self._add_property('sweep.mode',
                        self._get_sweep_mode,
                        self._set_sweep_mode)
        self._add_property('sweep.trigger_source',
                        self._get_sweep_trigger_source,
                        self._set_sweep_trigger_source)
        self._add_method('sweep.configure',
                        self._sweep_configure)
    
    def _get_sweep_mode(self):
        return self._sweep_mode
    
    def _set_sweep_mode(self, value):
        if value not in SweepMode:
            raise ivi.ValueNotSupportedException()
        self._sweep_mode = value
    
    def _get_sweep_trigger_source(self):
        return self._sweep_trigger_source
    
    def _set_sweep_trigger_source(self, value):
        if value not in Source:
            raise ivi.ValueNotSupportedException()
        self._sweep_trigger_source = value
    
    def _sweep_configure(self, mode, trigger_source):
        self._set_sweep_mode(mode)
        self._set_sweep_trigger_source(trigger_source)
    
    
class FrequencySweep(ivi.IviContainer):
    "Extension IVI methods for generators that support frequency sweeping"
    
    def __init__(self, *args, **kwargs):
        super(FrequencySweep, self).__init__(*args, **kwargs)
        
        cls = 'IviRFSigGen'
        grp = 'FrequencySweep'
        ivi.add_group_capability(self, cls+grp)
        
        self._sweep_frequency_sweep_start = 0.0
        self._sweep_frequency_sweep_stop = 0.0
        self._sweep_frequency_sweep_time = 0.0
        
        self._add_property('sweep.frequency_sweep.start',
                        self._get_sweep_frequency_sweep_start,
                        self._set_sweep_frequency_sweep_start)
        self._add_property('sweep.frequency_sweep.stop',
                        self._get_sweep_frequency_sweep_stop,
                        self._set_sweep_frequency_sweep_stop)
        self._add_property('sweep.frequency_sweep.time',
                        self._get_sweep_frequency_sweep_time,
                        self._set_sweep_frequency_sweep_time)
        self._add_method('sweep.frequency_sweep.configure_start_stop',
                        self._sweep_frequency_sweep_configure_start_stop)
        self._add_method('sweep.frequency_sweep.configure_center_span',
                        self._sweep_frequency_sweep_configure_center_span)
    
    def _get_sweep_frequency_sweep_start(self):
        return self._sweep_frequency_sweep_start
    
    def _set_sweep_frequency_sweep_start(self, value):
        value = float(value)
        self._sweep_frequency_sweep_start = value
    
    def _get_sweep_frequency_sweep_stop(self):
        return self._sweep_frequency_sweep_stop
    
    def _set_sweep_frequency_sweep_stop(self, value):
        value = float(value)
        self._sweep_frequency_sweep_stop = value
    
    def _get_sweep_frequency_sweep_time(self):
        return self._sweep_frequency_sweep_time
    
    def _set_sweep_frequency_sweep_time(self, value):
        value = float(value)
        self._sweep_frequency_sweep_time = value
    
    def _sweep_frequency_sweep_configure_start_stop(self, start, stop):
        self._set_sweep_frequency_sweep_start(start)
        self._set_sweep_frequency_sweep_stop(stop)
    
    def _sweep_frequency_sweep_configure_center_span(self, center, span):
        self._set_sweep_frequency_sweep_start(center - span/2)
        self._set_sweep_frequency_sweep_stop(center + span/2)
    
    
class PowerSweep(ivi.IviContainer):
    "Extension IVI methods for generators that support power sweeping"
    
    def __init__(self, *args, **kwargs):
        super(PowerSweep, self).__init__(*args, **kwargs)
        
        cls = 'IviRFSigGen'
        grp = 'PowerSweep'
        ivi.add_group_capability(self, cls+grp)
        
        self._sweep_power_sweep_start = 0.0
        self._sweep_power_sweep_stop = 0.0
        self._sweep_power_sweep_time = 0.0
        
        self._add_property('sweep.power_sweep.start',
                        self._get_sweep_power_sweep_start,
                        self._set_sweep_power_sweep_start)
        self._add_property('sweep.power_sweep.stop',
                        self._get_sweep_power_sweep_stop,
                        self._set_sweep_power_sweep_stop)
        self._add_property('sweep.power_sweep.time',
                        self._get_sweep_power_sweep_time,
                        self._set_sweep_power_sweep_time)
        self._add_method('sweep.power_sweep.configure_start_stop',
                        self._sweep_power_sweep_configure_start_stop)
    
    def _get_sweep_power_sweep_start(self):
        return self._sweep_power_sweep_start
    
    def _set_sweep_power_sweep_start(self, value):
        value = float(value)
        self._sweep_power_sweep_start = value
    
    def _get_sweep_power_sweep_stop(self):
        return self._sweep_power_sweep_stop
    
    def _set_sweep_power_sweep_stop(self, value):
        value = float(value)
        self._sweep_power_sweep_stop = value
    
    def _get_sweep_power_sweep_time(self):
        return self._sweep_power_sweep_time
    
    def _set_sweep_power_sweep_time(self, value):
        value = float(value)
        self._sweep_power_sweep_time = value
    
    def _sweep_power_sweep_configure_start_stop(self, start, stop):
        self._set_sweep_power_sweep_start(start)
        self._set_sweep_power_sweep_stop(stop)
    
    
class FrequencyStep(ivi.IviContainer):
    "Extension IVI methods for generators that support frequency sweeping in steps"
    
    def __init__(self, *args, **kwargs):
        super(FrequencyStep, self).__init__(*args, **kwargs)
        
        cls = 'IviRFSigGen'
        grp = 'FrequencyStep'
        ivi.add_group_capability(self, cls+grp)
        
        self._sweep_frequency_step_start = 0.0
        self._sweep_frequency_step_stop = 0.0
        self._sweep_frequency_step_scaling = 'linear'
        self._sweep_frequency_step_size = 0.0
        self._sweep_frequency_step_single_step_enabled = False
        self._sweep_frequency_step_dwell = 0.0
        
        self._add_property('sweep.frequency_step.start',
                        self._get_sweep_frequency_step_start,
                        self._set_sweep_frequency_step_start)
        self._add_property('sweep.frequency_step.stop',
                        self._get_sweep_frequency_step_stop,
                        self._set_sweep_frequency_step_stop)
        self._add_property('sweep.frequency_step.scaling',
                        self._get_sweep_frequency_step_scaling,
                        self._set_sweep_frequency_step_scaling)
        self._add_property('sweep.frequency_step.size',
                        self._get_sweep_frequency_step_size,
                        self._set_sweep_frequency_step_size)
        self._add_property('sweep.frequency_step.single_step_enabled',
                        self._get_sweep_frequency_step_single_step_enabled,
                        self._set_sweep_frequency_step_single_step_enabled)
        self._add_property('sweep.frequency_step.dwell',
                        self._get_sweep_frequency_step_dwell,
                        self._set_sweep_frequency_step_dwell)
        self._add_method('sweep.frequency_step.configure_start_stop',
                        self._sweep_frequency_step_configure_start_stop)
        self._add_method('sweep.frequency_step.configure_dwell',
                        self._sweep_frequency_step_configure_dwell)
        self._add_method('sweep.frequency_step.reset',
                        self._sweep_frequency_step_reset)
    
    def _get_sweep_frequency_step_start(self):
        return self._sweep_frequency_step_start
    
    def _set_sweep_frequency_step_start(self, value):
        value = float(value)
        self._sweep_frequency_step_start = value
    
    def _get_sweep_frequency_step_stop(self):
        return self._sweep_frequency_step_stop
    
    def _set_sweep_frequency_step_stop(self, value):
        value = float(value)
        self._sweep_frequency_step_stop = value
    
    def _get_sweep_frequency_step_scaling(self):
        return self._sweep_frequency_step_scaling
    
    def _set_sweep_frequency_step_scaling(self, value):
        if value not in Scaling:
            raise ivi.ValueNotSupportedException()
        self._sweep_frequency_step_scaling = value
    
    def _get_sweep_frequency_step_size(self):
        return self._sweep_frequency_step_size
    
    def _set_sweep_frequency_step_size(self, value):
        value = float(value)
        self._sweep_frequency_step_size = value
    
    def _get_sweep_frequency_step_single_step_enabled(self):
        return self._sweep_frequency_step_single_step_enabled
    
    def _set_sweep_frequency_step_single_step_enabled(self, value):
        value = bool(value)
        self._sweep_frequency_step_single_step_enabled = value
    
    def _get_sweep_frequency_step_dwell(self):
        return self._sweep_frequency_step_dwell
    
    def _set_sweep_frequency_step_dwell(self, value):
        value = float(value)
        self._sweep_frequency_step_dwell = value
    
    def _sweep_frequency_step_configure_start_stop(self, start, stop, scaling, size):
        self._set_sweep_frequency_step_start(start)
        self._set_sweep_frequency_step_stop(stop)
        self._set_sweep_frequency_step_scaling(scaling)
        self._set_sweep_frequency_step_size(size)
    
    def _sweep_frequency_step_configure_dwell(self, single_step_enabled, dwell):
        self._set_sweep_frequency_step_single_step_enabled(single_step_enabled)
        self._set_sweep_frequency_step_dwell(dwell)
    
    def _sweep_frequency_step_reset(self):
        pass
    
    
class PowerStep(ivi.IviContainer):
    "Extension IVI methods for generators that support power sweeping in steps"
    
    def __init__(self, *args, **kwargs):
        super(PowerStep, self).__init__(*args, **kwargs)
        
        cls = 'IviRFSigGen'
        grp = 'PowerStep'
        ivi.add_group_capability(self, cls+grp)
        
        self._sweep_power_step_start = 0.0
        self._sweep_power_step_stop = 0.0
        self._sweep_power_step_size = 0.0
        self._sweep_power_step_single_step_enabled = False
        self._sweep_power_step_dwell = 0.0
        
        self._add_property('sweep.power_step.start',
                        self._get_sweep_power_step_start,
                        self._set_sweep_power_step_start)
        self._add_property('sweep.power_step.stop',
                        self._get_sweep_power_step_stop,
                        self._set_sweep_power_step_stop)
        self._add_property('sweep.power_step.size',
                        self._get_sweep_power_step_size,
                        self._set_sweep_power_step_size)
        self._add_property('sweep.power_step.single_step_enabled',
                        self._get_sweep_power_step_single_step_enabled,
                        self._set_sweep_power_step_single_step_enabled)
        self._add_property('sweep.power_step.dwell',
                        self._get_sweep_power_step_dwell,
                        self._set_sweep_power_step_dwell)
        self._add_method('sweep.power_step.configure_start_stop',
                        self._sweep_power_step_configure_start_stop)
        self._add_method('sweep.power_step.configure_dwell',
                        self._sweep_power_step_configure_dwell)
        self._add_method('sweep.power_step.reset',
                        self._sweep_power_step_reset)
    
    def _get_sweep_power_step_start(self):
        return self._sweep_power_step_start
    
    def _set_sweep_power_step_start(self, value):
        value = float(value)
        self._sweep_power_step_start = value
    
    def _get_sweep_power_step_stop(self):
        return self._sweep_power_step_stop
    
    def _set_sweep_power_step_stop(self, value):
        value = float(value)
        self._sweep_power_step_stop = value
    
    def _get_sweep_power_step_size(self):
        return self._sweep_power_step_size
    
    def _set_sweep_power_step_size(self, value):
        value = float(value)
        self._sweep_power_step_size = value
    
    def _get_sweep_power_step_single_step_enabled(self):
        return self._sweep_power_step_single_step_enabled
    
    def _set_sweep_power_step_single_step_enabled(self, value):
        value = bool(value)
        self._sweep_power_step_single_step_enabled = value
    
    def _get_sweep_power_step_dwell(self):
        return self._sweep_power_step_dwell
    
    def _set_sweep_power_step_dwell(self, value):
        value = float(value)
        self._sweep_power_step_dwell = value
    
    def _sweep_power_step_configure_start_stop(self, start, stop, size):
        self._set_sweep_power_step_start(start)
        self._set_sweep_power_step_stop(stop)
        self._set_sweep_power_step_size(size)
    
    def _sweep_power_step_configure_dwell(self, single_step_enabled, dwell):
        self._set_sweep_power_step_single_step_enabled(single_step_enabled)
        self._set_sweep_power_step_dwell(dwell)
    
    def _sweep_power_step_reset(self):
        pass
    
    
class List(ivi.IviContainer):
    "Extension IVI methods for generators that support frequency and power selection a list"
    
    def __init__(self, *args, **kwargs):
        super(List, self).__init__(*args, **kwargs)
        
        cls = 'IviRFSigGen'
        grp = 'List'
        ivi.add_group_capability(self, cls+grp)
        
        self._sweep_list_selected_list = ''
        self._sweep_list_single_step_enabled = False
        self._sweep_list_dwell = 0.0
        
        self._add_property('sweep.list.selected_list',
                        self._get_sweep_list_selected_list,
                        self._set_sweep_list_selected_list)
        self._add_property('sweep.list.single_step_enabled',
                        self._get_sweep_list_single_step_enabled,
                        self._set_sweep_list_single_step_enabled)
        self._add_property('sweep.list.dwell',
                        self._get_sweep_list_dwell,
                        self._set_sweep_list_dwell)
        self._add_method('sweep.list.create_frequency',
                        self._sweep_list_create_frequency)
        self._add_method('sweep.list.create_power',
                        self._sweep_list_create_power)
        self._add_method('sweep.list.create_frequency_power',
                        self._sweep_list_create_frequency_power)
        self._add_method('sweep.list.clear_all',
                        self._sweep_list_clear_all)
        self._add_method('sweep.list.configure_dwell',
                        self._sweep_list_configure_dwell)
        self._add_method('sweep.list.reset',
                        self._sweep_list_reset)
    
    def _get_sweep_list_selected_list(self):
        return self._sweep_list_selected_list
    
    def _set_sweep_list_selected_list(self, value):
        value = str(value)
        self._sweep_list_selected_list = value
    
    def _get_sweep_list_single_step_enabled(self):
        return self._sweep_list_single_step_enabled
    
    def _set_sweep_list_single_step_enabled(self, value):
        value = bool(value)
        self._sweep_list_single_step_enabled = value
    
    def _get_sweep_list_dwell(self):
        return self._sweep_list_dwell
    
    def _set_sweep_list_dwell(self, value):
        value = float(value)
        self._sweep_list_dwell = value
    
    def _sweep_list_create_frequency(self, name, frequency):
        pass
    
    def _sweep_list_create_power(self, name, power):
        pass
    
    def _sweep_list_create_frequency_power(self, name, frequency, power):
        pass
    
    def _sweep_list_clear_all(self):
        pass
    
    def _sweep_list_configure_dwell(self, single_step_enabled, dwell):
        self._set_sweep_list_single_step_enabled(single_step_enabled)
        self._set_sweep_list_dwell(dwell)
    
    def _sweep_list_reset(self):
        pass
    
    
class ALC(ivi.IviContainer):
    "Extension IVI methods for generators that support automatic level control"
    
    def __init__(self, *args, **kwargs):
        super(ALC, self).__init__(*args, **kwargs)
        
        cls = 'IviRFSigGen'
        grp = 'ALC'
        ivi.add_group_capability(self, cls+grp)
        
        self._alc_source = 'internal'
        self._alc_bandwidth = 0.0
        
        self._add_property('alc.source',
                        self._get_alc_source,
                        self._set_alc_source)
        self._add_property('alc.bandwidth',
                        self._get_alc_bandwidth,
                        self._set_alc_bandwidth)
        self._add_method('alc.configure',
                        self._alc_configure)
    
    def _get_alc_source(self):
        return self._alc_source
    
    def _set_alc_source(self, value):
        if value not in Source:
            raise ivi.ValueNotSupportedException()
        self._alc_source = value
    
    def _get_alc_bandwidth(self):
        return self._alc_bandwidth
    
    def _set_alc_bandwidth(self, bandwidth):
        value = float(value)
        self._alc_bandwidth = value
    
    def _alc_configure(self, source, bandwidth):
        self._set_alc_source = source
        self._set_alc_bandwidth = bandwidth
    
    
class ReferenceOscillator(ivi.IviContainer):
    "Extension IVI methods for generators that support a configurable frequency reference"
    
    def __init__(self, *args, **kwargs):
        super(ReferenceOscillator, self).__init__(*args, **kwargs)
        
        cls = 'IviRFSigGen'
        grp = 'ReferenceOscillator'
        ivi.add_group_capability(self, cls+grp)
        
        self._reference_oscillator_source = 'internal'
        self._reference_oscillator_external_frequency = 0
        
        self._add_property('reference_oscillator.source',
                        self._get_reference_oscillator_source,
                        self._set_reference_oscillator_source)
        self._add_property('reference_oscillator.external_frequency',
                        self._get_reference_oscillator_external_frequency,
                        self._set_reference_oscillator_external_frequency)
        self._add_method('reference_oscillator.configure',
                        self._reference_oscillator_configure)
    
    def _get_reference_oscillator_source(self):
        return self._reference_oscillator_source
    
    def _set_reference_oscillator_source(self, value):
        if value not in Source:
            raise ivi.ValueNotSupportedException()
        self._reference_oscillator_source = value
    
    def _get_reference_oscillator_external_frequency(self):
        return self._reference_oscillator_external_frequency
    
    def _set_reference_oscillator_external_frequency(self, external_frequency):
        value = float(value)
        self._reference_oscillator_external_frequency = value
    
    def _reference_oscillator_configure(self, source, external_frequency):
        self._set_reference_oscillator_source = source
        self._set_reference_oscillator_external_frequency = external_frequency
    
    
class SoftwareTrigger(ivi.IviContainer):
    "Extension IVI methods for generators that support software triggering"
    
    def __init__(self, *args, **kwargs):
        super(SoftwareTrigger, self).__init__(*args, **kwargs)
        
        cls = 'IviRFSigGen'
        grp = 'SoftwareTrigger'
        ivi.add_group_capability(self, cls+grp)

        self._add_method('send_software_trigger',
                        self._send_software_trigger,
                        """
                        This function sends a software-generated trigger to the instrument. It is
                        only applicable for instruments using interfaces or protocols which
                        support an explicit trigger function. For example, with GPIB this function
                        could send a group execute trigger to the instrument. Other
                        implementations might send a ``*TRG`` command.

                        Since instruments interpret a software-generated trigger in a wide variety
                        of ways, the precise response of the instrument to this trigger is not
                        defined. Note that SCPI details a possible implementation.

                        This function should not use resources which are potentially shared by
                        other devices (for example, the VXI trigger lines). Use of such shared
                        resources may have undesirable effects on other devices.

                        This function should not check the instrument status. Typically, the
                        end-user calls this function only in a sequence of calls to other
                        low-level driver functions. The sequence performs one operation. The
                        end-user uses the low-level functions to optimize one or more aspects of
                        interaction with the instrument. To check the instrument status, call the
                        appropriate error query function at the conclusion of the sequence.

                        The trigger source attribute must accept Software Trigger as a valid
                        setting for this function to work. If the trigger source is not set to
                        Software Trigger, this function does nothing and returns the error Trigger
                        Not Software.
                        """)

    def _send_software_trigger(self):
        pass
    
    
class ModulateIQ(ivi.IviContainer):
    "Extension IVI methods for generators supporting IQ (vector) modulation"
    
    def __init__(self, *args, **kwargs):
        super(ModulateIQ, self).__init__(*args, **kwargs)
        
        cls = 'IviRFSigGen'
        grp = 'ModulateIQ'
        ivi.add_group_capability(self, cls+grp)
        
        self._iq_enabled = False
        self._iq_source = 'digital_modulation_base'
        self._iq_nominal_voltage = 0.0
        self._iq_swap_enabled = False
        
        self._add_property('iq.enabled',
                        self._get_iq_enabled,
                        self._set_iq_enabled)
        self._add_property('iq.source',
                        self._get_iq_source,
                        self._set_iq_source)
        self._add_property('iq.nominal_voltage',
                        self._get_iq_nominal_voltage,
                        self._set_iq_nominal_voltage)
        self._add_property('iq.swap_enabled',
                        self._get_iq_swap_enabled,
                        self._set_iq_swap_enabled)
        self._add_method('iq.configure',
                        self._iq_configure)
        self._add_method('iq.calibrate',
                        self._iq_calibrate)
    
    def _get_iq_enabled(self):
        return self._iq_enabled
    
    def _set_iq_enabled(self, value):
        value = bool(value)
        self._iq_enabled = value
    
    def _get_iq_source(self):
        return self._iq_source
    
    def _set_iq_source(self, value):
        if value not in IQSource:
            raise ivi.ValueNotSupportedException()
        self._iq_source = value
    
    def _get_iq_nominal_voltage(self):
        return self._iq_nominal_voltage
    
    def _set_iq_nominal_voltage(self, value):
        value = float(value)
        self._iq_nominal_voltage = value
    
    def _get_iq_swap_enabled(self):
        return self._iq_swap_enabled
    
    def _set_iq_swap_enabled(self, value):
        value = bool(value)
        self._iq_swap_enabled = value
    
    def _iq_configure(self, source, swap_enabled):
        self._set_iq_source(source)
        self._set_iq_swap_enabled(swap_enabled)
    
    def _iq_calibrate(self):
        pass
    
    
class IQImpairment(ivi.IviContainer):
    "Extension IVI methods for generators supporting IQ modulation simulation or correction of IQ impairment"
    
    def __init__(self, *args, **kwargs):
        super(IQImpairment, self).__init__(*args, **kwargs)
        
        cls = 'IviRFSigGen'
        grp = 'IQImpairment'
        ivi.add_group_capability(self, cls+grp)
        
        self._iq_impairment_enabled = False
        self._iq_impairment_i_offset = 0.0
        self._iq_impairment_q_offset = 0.0
        self._iq_impairment_iq_ratio = 0.0
        self._iq_impairment_iq_skew = 0.0
        
        self._add_property('iq.impairment.enabled',
                        self._get_iq_impairment_enabled,
                        self._set_iq_impairment_enabled)
        self._add_property('iq.impairment.i_offset',
                        self._get_iq_impairment_i_offset,
                        self._set_iq_impairment_i_offset)
        self._add_property('iq.impairment.q_offset',
                        self._get_iq_impairment_q_offset,
                        self._set_iq_impairment_q_offset)
        self._add_property('iq.impairment.iq_ratio',
                        self._get_iq_impairment_iq_ratio,
                        self._set_iq_impairment_iq_ratio)
        self._add_property('iq.impairment.iq_skew',
                        self._get_iq_impairment_iq_skew,
                        self._set_iq_impairment_iq_skew)
        self._add_method('iq.impairment.configure',
                        self._iq_impairment_configure)
    
    def _get_iq_impairment_enabled(self):
        return self._iq_impairment_enabled
    
    def _set_iq_impairment_enabled(self, value):
        value = bool(value)
        self._iq_impairment_enabled = value
    
    def _get_iq_impairment_i_offset(self):
        return self._iq_impairment_i_offset
    
    def _set_iq_impairment_i_offset(self, value):
        value = float(value)
        self._iq_impairment_i_offset = value
    
    def _get_iq_impairment_q_offset(self):
        return self._iq_impairment_q_offset
    
    def _set_iq_impairment_q_offset(self, value):
        value = float(value)
        self._iq_impairment_q_offset = value
    
    def _get_iq_impairment_iq_ratio(self):
        return self._iq_impairment_iq_ratio
    
    def _set_iq_impairment_iq_ratio(self, value):
        value = float(value)
        self._iq_impairment_iq_ratio = value
    
    def _get_iq_impairment_iq_skew(self):
        return self._iq_impairment_iq_skew
    
    def _set_iq_impairment_iq_skew(self, value):
        value = float(value)
        self._iq_impairment_iq_skew = value
    
    def _iq_impairment_configure(self, i_offset, q_offset, iq_ratio, iq_skew):
        self._set_iq_impairment_i_offset(i_offset)
        self._set_iq_impairment_q_offset(q_offset)
        self._set_iq_impairment_iq_ratio(iq_ratio)
        self._set_iq_impairment_iq_skew(iq_skew)
    
    
class ArbGenerator(ivi.IviContainer):
    "Extension IVI methods for generators with internal arbitrary waveform generators"
    
    def __init__(self, *args, **kwargs):
        super(ArbGenerator, self).__init__(*args, **kwargs)
        
        cls = 'IviRFSigGen'
        grp = 'ArbGenerator'
        ivi.add_group_capability(self, cls+grp)
        
        self._digital_modulation_arb_selected_waveform = ''
        self._digital_modulation_arb_clock_frequency = 0.0
        self._digital_modulation_arb_filter_frequency = 0.0
        self._digital_modulation_arb_max_number_waveforms = 0
        self._digital_modulation_arb_waveform_quantum = 0
        self._digital_modulation_arb_waveform_size_min = 0
        self._digital_modulation_arb_waveform_size_max = 0
        self._digital_modulation_arb_trigger_source = 'immediate'
        self._digital_modulation_arb_external_trigger_slope = 'positive'
        
        self._add_property('digital_modulation.arb.selected_waveform',
                        self._get_digital_modulation_arb_selected_waveform,
                        self._set_digital_modulation_arb_selected_waveform)
        self._add_property('digital_modulation.arb.clock_frequency',
                        self._get_digital_modulation_arb_clock_frequency,
                        self._set_digital_modulation_arb_clock_frequency)
        self._add_property('digital_modulation.arb.filter_frequency',
                        self._get_digital_modulation_arb_filter_frequency,
                        self._set_digital_modulation_arb_filter_frequency)
        self._add_property('digital_modulation.arb.max_number_waveforms',
                        self._get_digital_modulation_arb_max_number_waveforms)
        self._add_property('digital_modulation.arb.waveform_quantum',
                        self._get_digital_modulation_arb_waveform_quantum)
        self._add_property('digital_modulation.arb.waveform_size_min',
                        self._get_digital_modulation_arb_waveform_size_min)
        self._add_property('digital_modulation.arb.waveform_size_max',
                        self._get_digital_modulation_arb_waveform_size_max)
        self._add_property('digital_modulation.arb.trigger_source',
                        self._get_digital_modulation_arb_trigger_source,
                        self._set_digital_modulation_arb_trigger_source)
        self._add_property('digital_modulation.arb.external_trigger_slope',
                        self._get_digital_modulation_arb_external_trigger_slope,
                        self._set_digital_modulation_arb_external_trigger_slope)
        self._add_method('digital_modulation.arb.configure',
                        self._digital_modulation_arb_configure)
        self._add_method('digital_modulation.arb.write_waveform',
                        self._digital_modulation_arb_write_waveform)
        self._add_method('digital_modulation.arb.clear_all_waveforms',
                        self._digital_modulation_arb_clear_all_waveforms)
    
    def _get_digital_modulation_arb_selected_waveform(self):
        return self._digital_modulation_arb_selected_waveform
    
    def _set_digital_modulation_arb_selected_waveform(self, value):
        value = str(value)
        self._digital_modulation_arb_selected_waveform = value
    
    def _get_digital_modulation_arb_clock_frequency(self):
        return self._digital_modulation_arb_clock_frequency
    
    def _set_digital_modulation_arb_clock_frequency(self, value):
        value = float(value)
        self._digital_modulation_arb_clock_frequency = value
    
    def _get_digital_modulation_arb_filter_frequency(self):
        return self._digital_modulation_arb_filter_frequency
    
    def _set_digital_modulation_arb_filter_frequency(self, value):
        value = float(value)
        self._digital_modulation_arb_filter_frequency = value
    
    def _get_digital_modulation_arb_max_number_waveforms(self):
        return self._digital_modulation_arb_max_number_waveforms
    
    def _get_digital_modulation_arb_waveform_quantum(self):
        return self._digital_modulation_arb_waveform_quantum
    
    def _get_digital_modulation_arb_waveform_size_min(self):
        return self._digital_modulation_arb_waveform_size_min
    
    def _get_digital_modulation_arb_waveform_size_max(self):
        return self._digital_modulation_arb_waveform_size_max
    
    def _get_digital_modulation_arb_trigger_source(self):
        return self._digital_modulation_arb_trigger_source
    
    def _set_digital_modulation_arb_trigger_source(self, value):
        if value not in TriggerSource:
            raise ivi.ValueNotSupportedException()
        self._digital_modulation_arb_trigger_source = value
    
    def _get_digital_modulation_arb_external_trigger_slope(self):
        return self._digital_modulation_arb_external_trigger_slope
    
    def _set_digital_modulation_arb_external_trigger_slope(self, value):
        if value not in Slope:
            raise ivi.ValueNotSupportedException()
        self._digital_modulation_arb_external_trigger_slope = value
    
    def _digital_modulation_arb_configure(self, clock_frequency, filter_frequency):
        self._set_digital_modulation_arb_clock_frequency(clock_frequency)
        self._set_digital_modulation_arb_filter_frequency(filter_frequency)
    
    def _digital_modulation_arb_write_waveform(self, name, idata, qdata, more_data_pending):
        pass
    
    def _digital_modulation_arb_clear_all_waveforms(self):
        pass
    
    
class DigitalModulationBase(ivi.IviContainer):
    "Extension IVI methods for generators supporting generation of standard wireless communication signals"
    
    def __init__(self, *args, **kwargs):
        super(DigitalModulationBase, self).__init__(*args, **kwargs)
        
        cls = 'IviRFSigGen'
        grp = 'DigitalModulationBase'
        ivi.add_group_capability(self, cls+grp)
        
        self._digital_modulation_base_standard_names = list()
        self._digital_modulation_base_selected_standard = ''
        self._digital_modulation_base_data_source = 'prbs'
        self._digital_modulation_base_prbs_type = 'prbs9'
        self._digital_modulation_base_selected_bit_sequence = ''
        self._digital_modulation_base_clock_source = 'internal'
        self._digital_modulation_base_external_clock_type = 'bit'
        
        self._add_property('digital_modulation.base.standard_names',
                        self._get_digital_modulation_base_standard_names)
        self._add_property('digital_modulation.base.selected_standard',
                        self._get_digital_modulation_base_selected_standard,
                        self._set_digital_modulation_base_selected_standard)
        self._add_property('digital_modulation.base.data_source',
                        self._get_digital_modulation_base_data_source,
                        self._set_digital_modulation_base_data_source)
        self._add_property('digital_modulation.base.prbs_type',
                        self._get_digital_modulation_base_prbs_type,
                        self._set_digital_modulation_base_prbs_type)
        self._add_property('digital_modulation.base.selected_bit_sequence',
                        self._get_digital_modulation_base_selected_bit_sequence,
                        self._set_digital_modulation_base_selected_bit_sequence)
        self._add_property('digital_modulation.base.clock_source',
                        self._get_digital_modulation_base_clock_source,
                        self._set_digital_modulation_base_clock_source)
        self._add_property('digital_modulation.base.external_clock_type',
                        self._get_digital_modulation_base_external_clock_type,
                        self._set_digital_modulation_base_external_clock_type)
        self._add_method('digital_modulation.base.configure_clock_source',
                        self._digital_modulation_base_configure_clock_source)
        self._add_method('digital_modulation.base.create_bit_sequence',
                        self._digital_modulation_base_create_bit_sequence)
        self._add_method('digital_modulation.base.clear_all_bit_sequences',
                        self._digital_modulation_base_clear_all_bit_sequences)
    
    def _get_digital_modulation_base_standard_names(self):
        return self._digital_modulation_base_standard_names
    
    def _get_digital_modulation_base_selected_standard(self):
        return self._digital_modulation_base_selected_standard
    
    def _set_digital_modulation_base_selected_standard(self, value):
        value = str(value)
        self._digital_modulation_base_selected_standard = value
    
    def _get_digital_modulation_base_data_source(self):
        return self._digital_modulation_base_data_source
    
    def _set_digital_modulation_base_data_source(self, value):
        if value not in DigitalModulationBaseDataSource:
            raise ivi.ValueNotSupportedException()
        self._digital_modulation_base_data_source = value
    
    def _get_digital_modulation_base_prbs_type(self):
        return self._digital_modulation_base_prbs_type
    
    def _set_digital_modulation_base_prbs_type(self, value):
        if value not in DigitalModulationBasePRBSType:
            raise ivi.ValueNotSupportedException()
        self._digital_modulation_base_prbs_type = value
    
    def _get_digital_modulation_base_selected_bit_sequence(self):
        return self._digital_modulation_base_selected_bit_sequence
    
    def _set_digital_modulation_base_selected_bit_sequence(self, value):
        value = str(value)
        self._digital_modulation_base_selected_bit_sequence = value
    
    def _get_digital_modulation_base_clock_source(self):
        return self._digital_modulation_base_clock_source
    
    def _set_digital_modulation_base_clock_source(self, value):
        if value not in Source:
            raise ivi.ValueNotSupportedException()
        self._digital_modulation_base_clock_source = value
    
    def _get_digital_modulation_base_external_clock_type(self):
        return self._digital_modulation_base_external_clock_type
    
    def _set_digital_modulation_base_external_clock_type(self, value):
        if value not in ClockType:
            raise ivi.ValueNotSupportedException()
        self._digital_modulation_base_external_clock_type = value
    
    def _digital_modulation_base_configure_clock_source(self, source, type):
        self._set_digital_modulation_base_clock_source(source)
        self._set_digital_modulation_base_external_clock_type(type)
    
    def _digital_modulation_base_create_bit_sequence(self, name, bit_count, sequence, more_data_pending):
        pass
    
    def _digital_modulation_base_clear_all_bit_sequences(self):
        pass
    
    
class CDMABase(ivi.IviContainer):
    "Extension IVI methods for generators supporting generation of CDMA wireless communication signals"
    
    def __init__(self, *args, **kwargs):
        super(CDMABase, self).__init__(*args, **kwargs)
        
        cls = 'IviRFSigGen'
        grp = 'CDMABase'
        ivi.add_group_capability(self, cls+grp)
        
        self._digital_modulation_cdma_standard_names = list()
        self._digital_modulation_cdma_selected_standard = ''
        self._digital_modulation_cdma_trigger_source = 'immediate'
        self._digital_modulation_cdma_external_trigger_slope = 'positive'
        self._digital_modulation_cdma_test_model_names = list()
        self._digital_modulation_cdma_selected_test_model = ''
        self._digital_modulation_cdma_clock_source = 'internal'
        
        self._add_property('digital_modulation.cdma.standard_names',
                        self._get_digital_modulation_cdma_standard_names)
        self._add_property('digital_modulation.cdma.selected_standard',
                        self._get_digital_modulation_cdma_selected_standard,
                        self._set_digital_modulation_cdma_selected_standard)
        self._add_property('digital_modulation.cdma.trigger_source',
                        self._get_digital_modulation_cdma_trigger_source,
                        self._set_digital_modulation_cdma_trigger_source)
        self._add_property('digital_modulation.cdma.external_trigger_slope',
                        self._get_digital_modulation_cdma_external_trigger_slope,
                        self._set_digital_modulation_cdma_external_trigger_slope)
        self._add_property('digital_modulation.cdma.test_model_names',
                        self._get_digital_modulation_cdma_test_model_names)
        self._add_property('digital_modulation.cdma.selected_test_model',
                        self._get_digital_modulation_cdma_selected_test_model,
                        self._set_digital_modulation_cdma_selected_test_model)
        self._add_property('digital_modulation.cdma.clock_source',
                        self._get_digital_modulation_cdma_clock_source,
                        self._set_digital_modulation_cdma_clock_source)
    
    def _get_digital_modulation_cdma_standard_names(self):
        return self._digital_modulation_cdma_standard_names
    
    def _get_digital_modulation_cdma_selected_standard(self):
        return self._digital_modulation_cdma_selected_standard
    
    def _set_digital_modulation_cdma_selected_standard(self, value):
        value = str(value)
        self._digital_modulation_cdma_selected_standard = value
    
    def _get_digital_modulation_cdma_trigger_source(self):
        return self._digital_modulation_cdma_trigger_source
    
    def _set_digital_modulation_cdma_trigger_source(self, value):
        if value not in TriggerSource:
            raise ivi.ValueNotSupportedException()
        self._digital_modulation_cdma_trigger_source = value
    
    def _get_digital_modulation_cdma_external_trigger_slope(self):
        return self._digital_modulation_cdma_external_trigger_slope
    
    def _set_digital_modulation_cdma_external_trigger_slope(self, value):
        if value not in Slope:
            raise ivi.ValueNotSupportedException()
        self._digital_modulation_cdma_external_trigger_slope = value
    
    def _get_digital_modulation_cdma_test_model_names(self):
        return self._digital_modulation_cdma_test_model_names
    
    def _get_digital_modulation_cdma_selected_test_model(self):
        return self._digital_modulation_cdma_selected_test_model
    
    def _set_digital_modulation_cdma_selected_test_model(self, value):
        value = str(value)
        self._digital_modulation_cdma_selected_test_model = value
    
    def _get_digital_modulation_cdma_clock_source(self):
        return self._digital_modulation_cdma_clock_source
    
    def _set_digital_modulation_cdma_clock_source(self, value):
        if value not in Source:
            raise ivi.ValueNotSupportedException()
        self._digital_modulation_cdma_clock_source = value
    
    
class TDMABase(ivi.IviContainer):
    "Extension IVI methods for generators supporting generation of TDMA wireless communication signals"
    
    def __init__(self, *args, **kwargs):
        super(TDMABase, self).__init__(*args, **kwargs)
        
        cls = 'IviRFSigGen'
        grp = 'TDMABase'
        ivi.add_group_capability(self, cls+grp)
        
        self._digital_modulation_tdma_standard_names = list()
        self._digital_modulation_tdma_selected_standard = ''
        self._digital_modulation_tdma_trigger_source = 'immediate'
        self._digital_modulation_tdma_external_trigger_slope = 'positive'
        self._digital_modulation_tdma_frame_names = list()
        self._digital_modulation_tdma_selected_frame = ''
        self._digital_modulation_tdma_clock_source = 'internal'
        self._digital_modulation_tdma_external_clock_type = 'bit'
        
        self._add_property('digital_modulation.tdma.standard_names',
                        self._get_digital_modulation_tdma_standard_names)
        self._add_property('digital_modulation.tdma.selected_standard',
                        self._get_digital_modulation_tdma_selected_standard,
                        self._set_digital_modulation_tdma_selected_standard)
        self._add_property('digital_modulation.tdma.trigger_source',
                        self._get_digital_modulation_tdma_trigger_source,
                        self._set_digital_modulation_tdma_trigger_source)
        self._add_property('digital_modulation.tdma.external_trigger_slope',
                        self._get_digital_modulation_tdma_external_trigger_slope,
                        self._set_digital_modulation_tdma_external_trigger_slope)
        self._add_property('digital_modulation.tdma.frame_names',
                        self._get_digital_modulation_tdma_frame_names)
        self._add_property('digital_modulation.tdma.selected_frame',
                        self._get_digital_modulation_tdma_selected_frame,
                        self._set_digital_modulation_tdma_selected_frame)
        self._add_property('digital_modulation.tdma.clock_source',
                        self._get_digital_modulation_tdma_clock_source,
                        self._set_digital_modulation_tdma_clock_source)
        self._add_property('digital_modulation.tdma.external_clock_type',
                        self._get_digital_modulation_tdma_external_clock_type,
                        self._set_digital_modulation_tdma_external_clock_type)
        self._add_method('digital_modulation.tdma.configure_clock_source',
                        self._digital_modulation_tdma_configure_clock_source)
    
    def _get_digital_modulation_tdma_standard_names(self):
        return self._digital_modulation_tdma_standard_names
    
    def _get_digital_modulation_tdma_selected_standard(self):
        return self._digital_modulation_tdma_selected_standard
    
    def _set_digital_modulation_tdma_selected_standard(self, value):
        value = str(value)
        self._digital_modulation_tdma_selected_standard = value
    
    def _get_digital_modulation_tdma_trigger_source(self):
        return self._digital_modulation_tdma_trigger_source
    
    def _set_digital_modulation_tdma_trigger_source(self, value):
        if value not in TriggerSource:
            raise ivi.ValueNotSupportedException()
        self._digital_modulation_tdma_trigger_source = value
    
    def _get_digital_modulation_tdma_external_trigger_slope(self):
        return self._digital_modulation_tdma_external_trigger_slope
    
    def _set_digital_modulation_tdma_external_trigger_slope(self, value):
        if value not in Slope:
            raise ivi.ValueNotSupportedException()
        self._digital_modulation_tdma_external_trigger_slope = value
    
    def _get_digital_modulation_tdma_frame_names(self):
        return self._digital_modulation_tdma_frame_names
    
    def _get_digital_modulation_tdma_selected_frame(self):
        return self._digital_modulation_tdma_selected_frame
    
    def _set_digital_modulation_tdma_selected_frame(self, value):
        value = str(value)
        self._digital_modulation_tdma_selected_frame = value
    
    def _get_digital_modulation_tdma_clock_source(self):
        return self._digital_modulation_tdma_clock_source
    
    def _set_digital_modulation_tdma_clock_source(self, value):
        if value not in Source:
            raise ivi.ValueNotSupportedException()
        self._digital_modulation_tdma_clock_source = value
    
    def _get_digital_modulation_tdma_external_clock_type(self):
        return self._digital_modulation_tdma_external_clock_type
    
    def _set_digital_modulation_tdma_external_clock_type(self, value):
        if value not in ClockType:
            raise ivi.ValueNotSupportedException()
        self._digital_modulation_tdma_external_clock_type = value
    
    def _digital_modulation_tdma_configure_clock_source(self, source, type):
        self._set_digital_modulation_tdma_clock_source(source)
        self._set_digital_modulation_tdma_external_clock_type(type)
    
    
