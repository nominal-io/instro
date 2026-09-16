# Software Defined Radio (SDR)

Software defined radios.

```{warning}
This Instrument category is new and is currently available only in the Unstable package.
```

## Instrument and Abstract Driver

```{eval-rst}
.. autosummary::
   :toctree: generated
   :nosignatures:

   ~instro.unstable.sdr.InstroSDR
   ~instro.unstable.sdr.SDRDriverBase
```

## Vendor Drivers

| Vendor | Model | Description |
|--------|-------|-------------|
| Great Scott Gadgets | {py:class}`HackRF One <instro.unstable.sdr.drivers.hackrf_one.HackRFOne>` | {pysummary}`instro.unstable.sdr.drivers.hackrf_one.HackRFOne` |

<!-- The vendor table is laid out by hand for its Vendor/Model columns; its
descriptions still come from the docstrings via {pysummary}. autosummary needs to
see these classes to generate their pages, and it scans source text, so a
never-built `only` block is enough. Sphinx collects toctrees inside `only`
regardless, so the pages still appear in the sidebar. -->

```{eval-rst}
.. only:: autosummary_stubs

   .. autosummary::
      :toctree: generated

      instro.unstable.sdr.drivers.hackrf_one.HackRFOne
```

## Types & Configuration

```{eval-rst}
.. autosummary::
   :toctree: generated
   :nosignatures:

   ~instro.unstable.sdr.sdr.IQCapture
   ~instro.unstable.sdr.types.Direction
```

---

Errors raised by these methods are documented in [Exceptions](../library/exceptions.md).
