# DAQ (Data Acquisition)

Data acquisition systems.

## Instrument and Abstract Driver

```{eval-rst}
.. autosummary::
   :toctree: generated
   :nosignatures:

   ~instro.daq.InstroDAQ
   ~instro.daq.DAQDriverBase
   ~instro.daq.drivers.HWTimestamper
```

## Vendor Drivers

| Vendor | Model | Description |
|--------|-------|-------------|
| Keysight | {py:class}`34980A <instro.daq.drivers.keysight_34980a.Keysight34980A>` | {pysummary}`instro.daq.drivers.keysight_34980a.Keysight34980A` |
| National Instruments | {py:class}`NI-DAQmx <instro.daq.drivers.ni.nidaq.NIDAQDriver>` | {pysummary}`instro.daq.drivers.ni.nidaq.NIDAQDriver` |
| LabJack | {py:class}`T-series <instro.daq.drivers.labjack.t_series.LabJackTSeriesDriver>` | {pysummary}`instro.daq.drivers.labjack.t_series.LabJackTSeriesDriver` |
| Measurement Computing | {py:class}`USB-series <instro.daq.drivers.mcc.mccdaq.MCCDriver>` | {pysummary}`instro.daq.drivers.mcc.mccdaq.MCCDriver` |
| Dewesoft | {py:class}`DewesoftX (unstable) <instro.unstable.daq.drivers.dewesoftx.DewesoftX>` | {pysummary}`instro.unstable.daq.drivers.dewesoftx.DewesoftX` |

<!-- The vendor table is laid out by hand for its Vendor/Model columns; its
descriptions still come from the docstrings via {pysummary}. autosummary needs to
see these classes to generate their pages, and it scans source text, so a
never-built `only` block is enough. Sphinx collects toctrees inside `only`
regardless, so the pages still appear in the sidebar. -->

```{eval-rst}
.. only:: autosummary_stubs

   .. autosummary::
      :toctree: generated

      instro.daq.drivers.keysight_34980a.Keysight34980A
      instro.daq.drivers.ni.nidaq.NIDAQDriver
      instro.daq.drivers.labjack.t_series.LabJackTSeriesDriver
      instro.daq.drivers.mcc.mccdaq.MCCDriver
      instro.unstable.daq.drivers.dewesoftx.DewesoftX
```

## Types & Configuration

```{eval-rst}
.. autosummary::
   :toctree: generated
   :nosignatures:

   ~instro.daq.types.DAQVendor
   ~instro.daq.types.ChannelType
   ~instro.daq.types.Logic
   ~instro.daq.types.Direction
   ~instro.daq.types.TerminalConfig
   ~instro.daq.types.CJCSource
   ~instro.daq.types.HWTimingConfig
   ~instro.daq.types.DAQChannel
   ~instro.daq.types.AnalogChannel
   ~instro.daq.types.AnalogVoltageChannel
   ~instro.daq.types.AnalogCurrentChannel
   ~instro.daq.types.AnalogThermocoupleChannel
   ~instro.daq.types.DigitalPortWidth
   ~instro.daq.types.DigitalChannel
   ~instro.daq.types.DigitalPortChannel
   ~instro.daq.types.DigitalLineChannel
   ~instro.daq.types.RelayChannel
```

## Scaling

```{eval-rst}
.. autosummary::
   :toctree: generated
   :nosignatures:

   ~instro.daq.scaling.Scaler
   ~instro.daq.scaling.LinearScaler
   ~instro.daq.scaling.ReverseLinearScaler
   ~instro.daq.scaling.ScalerPipeline
```

### Thermocouple Scaling

```{eval-rst}
.. autosummary::
   :toctree: generated
   :nosignatures:

   ~instro.daq.scaling.thermocouple.ThermocoupleSensor
   ~instro.daq.scaling.thermocouple.InverseThermocoupleSensor
   ~instro.daq.scaling.thermocouple.TC_TYPE
   ~instro.daq.scaling.thermocouple.TC_UNIT
   ~instro.daq.scaling.thermocouple.kelvin_to_unit
   ~instro.daq.scaling.thermocouple.unit_to_kelvin
```

---

Errors raised by these methods are documented in [Exceptions](../reference/exceptions.md).
