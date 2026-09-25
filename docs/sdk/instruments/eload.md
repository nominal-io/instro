# Electronic Load

Electronic loads.

## Instrument and Abstract Driver

```{eval-rst}
.. autosummary::
   :toctree: generated
   :nosignatures:

   ~instro.eload.InstroELoad
   ~instro.eload.ELoadDriverBase
```

## Vendor Drivers

| Vendor | Model | Description |
|--------|-------|-------------|
| B&K Precision | {py:class}`85XXB <instro.eload.drivers.bk_85xxb.BK85XXB>` | {pysummary}`instro.eload.drivers.bk_85xxb.BK85XXB` |

<!-- The vendor table is laid out by hand for its Vendor/Model columns; its
descriptions still come from the docstrings via {pysummary}. autosummary needs to
see these classes to generate their pages, and it scans source text, so a
never-built `only` block is enough. Sphinx collects toctrees inside `only`
regardless, so the pages still appear in the sidebar. -->

```{eval-rst}
.. only:: autosummary_stubs

   .. autosummary::
      :toctree: generated

      instro.eload.drivers.bk_85xxb.BK85XXB
```

## Types & Configuration

```{eval-rst}
.. autosummary::
   :toctree: generated
   :nosignatures:

   ~instro.eload.types.LoadMode
   ~instro.eload.types.SlewRateDirection
   ~instro.eload.config.ELoadConfig
   ~instro.eload.config.LoadConfig
   ~instro.eload.config.SlewRateConfig
   ~instro.eload.config.VisaDriverConfig
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
