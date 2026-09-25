# Scope (Oscilloscope)

Oscilloscopes.

## Instrument and Abstract Driver

```{eval-rst}
.. autosummary::
   :toctree: generated
   :nosignatures:

   ~instro.scope.InstroScope
   ~instro.scope.ScopeDriverBase
```

## Vendor Drivers

| Vendor | Model | Description |
|--------|-------|-------------|
| Keysight | {py:class}`1200X <instro.scope.drivers.keysight_1200x.Keysight1200X>` | {pysummary}`instro.scope.drivers.keysight_1200x.Keysight1200X` |
| Siglent | {py:class}`SDS1000X-E <instro.scope.drivers.siglent_sds1000x_e.SiglentSDS1000XE>` | {pysummary}`instro.scope.drivers.siglent_sds1000x_e.SiglentSDS1000XE` |
| Tektronix | {py:class}`2 Series MSO <instro.scope.drivers.tektronix_2series.Tektronix2SeriesMSO>` | {pysummary}`instro.scope.drivers.tektronix_2series.Tektronix2SeriesMSO` |

<!-- The vendor table is laid out by hand for its Vendor/Model columns; its
descriptions still come from the docstrings via {pysummary}. autosummary needs to
see these classes to generate their pages, and it scans source text, so a
never-built `only` block is enough. Sphinx collects toctrees inside `only`
regardless, so the pages still appear in the sidebar. -->

```{eval-rst}
.. only:: autosummary_stubs

   .. autosummary::
      :toctree: generated

      instro.scope.drivers.keysight_1200x.Keysight1200X
      instro.scope.drivers.siglent_sds1000x_e.SiglentSDS1000XE
      instro.scope.drivers.tektronix_2series.Tektronix2SeriesMSO
```

## Types & Configuration

```{eval-rst}
.. autosummary::
   :toctree: generated
   :nosignatures:

   ~instro.scope.types.Coupling
   ~instro.scope.types.AcquisitionMode
   ~instro.scope.types.TriggerType
   ~instro.scope.types.TriggerSlope
   ~instro.scope.types.TriggerMode
   ~instro.scope.types.TriggerStatus
   ~instro.scope.types.AcquisitionState
   ~instro.scope.types.ScopeMeasurementType
   ~instro.scope.types.WaveformData
   ~instro.scope.types.ChannelState
   ~instro.scope.types.TriggerState
   ~instro.scope.types.ScopeState
   ~instro.scope.config.ScopeConfig
   ~instro.scope.config.AcquisitionConfig
   ~instro.scope.config.ChannelConfig
   ~instro.scope.config.TriggerConfig
   ~instro.scope.config.VisaDriverConfig
   ~instro.scope.config.resolve_scope_from_config
```

### Shared configuration

| Class | Description |
|-------|-------------|
| {py:class}`~instro.lib.types.DeviceInfo` | {pysummary}`instro.lib.types.DeviceInfo` |
| {py:class}`~instro.lib.config.TimingConfig` | {pysummary}`instro.lib.config.TimingConfig` |
| {py:class}`~instro.lib.config.NominalCorePublisherConfig` | {pysummary}`instro.lib.config.NominalCorePublisherConfig` |
| {py:class}`~instro.lib.config.FilePublisherConfig` | {pysummary}`instro.lib.config.FilePublisherConfig` |

---

Errors raised by these methods are documented in [Exceptions](../library/exceptions.md).
