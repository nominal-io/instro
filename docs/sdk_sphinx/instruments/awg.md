# Arbitrary Waveform Generator (AWG)

Arbitrary waveform generators.

## Instrument and Abstract Driver

```{eval-rst}
.. autosummary::
   :toctree: generated
   :nosignatures:

   ~instro.awg.InstroAWG
   ~instro.awg.AWGDriverBase
```

## Vendor Drivers

| Vendor | Model | Description |
|--------|-------|-------------|
| Keysight | {py:class}`33521B <instro.awg.drivers.keysight_33521b.Keysight33521B>` | {pysummary}`instro.awg.drivers.keysight_33521b.Keysight33521B` |
| Rigol | {py:class}`DG1022Z <instro.awg.drivers.rigol_dg1022z.RigolDG1022Z>` | {pysummary}`instro.awg.drivers.rigol_dg1022z.RigolDG1022Z` |

<!-- The vendor table is laid out by hand for its Vendor/Model columns; its
descriptions still come from the docstrings via {pysummary}. autosummary needs to
see these classes to generate their pages, and it scans source text, so a
never-built `only` block is enough. Sphinx collects toctrees inside `only`
regardless, so the pages still appear in the sidebar. -->

```{eval-rst}
.. only:: autosummary_stubs

   .. autosummary::
      :toctree: generated

      instro.awg.drivers.keysight_33521b.Keysight33521B
      instro.awg.drivers.rigol_dg1022z.RigolDG1022Z
```

## Types & Configuration

```{eval-rst}
.. autosummary::
   :toctree: generated
   :nosignatures:

   ~instro.awg.types.Sine
   ~instro.awg.types.Square
   ~instro.awg.types.Sawtooth
   ~instro.awg.types.Triangle
   ~instro.awg.types.Pulse
   ~instro.awg.types.Arbitrary
   ~instro.awg.types.StaticValue
   ~instro.awg.types.AmplitudeMeasurementUnit
   ~instro.awg.types.ModulationType
   ~instro.awg.types.BurstType
   ~instro.awg.types.BurstTriggerSource
   ~instro.awg.types.GatePolarity
   ~instro.awg.types.SweepType
   ~instro.awg.types.SweepTriggerSource
   ~instro.awg.types.convert_amplitude
```

---

Errors raised by these methods are documented in [Exceptions](../reference/exceptions.md).
