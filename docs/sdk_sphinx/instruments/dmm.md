# DMM (Digital Multimeter)

Digital multimeters.

## Instrument and Abstract Driver

```{eval-rst}
.. autosummary::
   :toctree: generated
   :nosignatures:

   ~instro.dmm.InstroDMM
   ~instro.dmm.DMMDriverBase
```

## Vendor Drivers

| Vendor | Model | Description |
|--------|-------|-------------|
| Keithley | {py:class}`2400 <instro.dmm.drivers.keithley_2400.Keithley2400>` | {pysummary}`instro.dmm.drivers.keithley_2400.Keithley2400` |
| Agilent | {py:class}`34401A <instro.dmm.drivers.agilent_a34401a.Agilent34401A>` | {pysummary}`instro.dmm.drivers.agilent_a34401a.Agilent34401A` |
| Keysight | {py:class}`34461A <instro.dmm.drivers.keysight_34461a.Keysight34461A>` | {pysummary}`instro.dmm.drivers.keysight_34461a.Keysight34461A` |

<!-- The vendor table is laid out by hand for its Vendor/Model columns; its
descriptions still come from the docstrings via {pysummary}. autosummary needs to
see these classes to generate their pages, and it scans source text, so a
never-built `only` block is enough. Sphinx collects toctrees inside `only`
regardless, so the pages still appear in the sidebar. -->

```{eval-rst}
.. only:: autosummary_stubs

   .. autosummary::
      :toctree: generated

      instro.dmm.drivers.keithley_2400.Keithley2400
      instro.dmm.drivers.agilent_a34401a.Agilent34401A
      instro.dmm.drivers.keysight_34461a.Keysight34461A
```

## Simulated Driver

```{eval-rst}
.. autosummary::
   :toctree: generated
   :nosignatures:

   ~instro.dmm.drivers.simulated.SimulatedDMM
```

## Types & Configuration

```{eval-rst}
.. autosummary::
   :toctree: generated
   :nosignatures:

   ~instro.dmm.types.MeasurementFunction
   ~instro.dmm.types.RangeMode
   ~instro.dmm.types.DMMMeasurementConfig
   ~instro.dmm.config.DMMConfig
   ~instro.dmm.config.MeasurementConfig
   ~instro.dmm.config.VisaDriverConfig
```

### Shared configuration

| Class | Description |
|-------|-------------|
| {py:class}`~instro.lib.types.DeviceInfo` | {pysummary}`instro.lib.types.DeviceInfo` |
| {py:class}`~instro.lib.config.TimingConfig` | {pysummary}`instro.lib.config.TimingConfig` |
| {py:class}`~instro.lib.config.NominalCorePublisherConfig` | {pysummary}`instro.lib.config.NominalCorePublisherConfig` |
| {py:class}`~instro.lib.config.FilePublisherConfig` | {pysummary}`instro.lib.config.FilePublisherConfig` |

---

Errors raised by these methods are documented in [Exceptions](../reference/exceptions.md).
