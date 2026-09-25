# PSU (Power Supply Unit)

Programmable power supplies.

## Instrument and Abstract Driver

```{eval-rst}
.. autosummary::
   :toctree: generated
   :nosignatures:

   ~instro.psu.InstroPSU
   ~instro.psu.PSUDriverBase
```

## Vendor Drivers

| Vendor | Model | Description |
|--------|-------|-------------|
| B&K Precision | {py:class}`9115 <instro.psu.drivers.bk_9115.BK9115>` | {pysummary}`instro.psu.drivers.bk_9115.BK9115` |
| B&K Precision | {py:class}`914X <instro.psu.drivers.bk_914x.BK914X>` | {pysummary}`instro.psu.drivers.bk_914x.BK914X` |
| Keysight | {py:class}`E36100 <instro.psu.drivers.keysight_e36100.KeysightE36100>` | {pysummary}`instro.psu.drivers.keysight_e36100.KeysightE36100` |
| Rigol | {py:class}`DP800 <instro.psu.drivers.rigol_dp800.RigolDP800>` | {pysummary}`instro.psu.drivers.rigol_dp800.RigolDP800` |
| Siglent | {py:class}`SPD3303 <instro.psu.drivers.siglent_spd3303.SiglentSPD3303>` | {pysummary}`instro.psu.drivers.siglent_spd3303.SiglentSPD3303` |
| TDK Lambda | {py:class}`Genesys <instro.psu.drivers.tdk_lambda_genesys.TDKLambdaGenesys>` | {pysummary}`instro.psu.drivers.tdk_lambda_genesys.TDKLambdaGenesys` |

<!-- The vendor table is laid out by hand for its Vendor/Model columns; its
descriptions still come from the docstrings via {pysummary}. autosummary needs to
see these classes to generate their pages, and it scans source text, so a
never-built `only` block is enough. Sphinx collects toctrees inside `only`
regardless, so the pages still appear in the sidebar. -->

```{eval-rst}
.. only:: autosummary_stubs

   .. autosummary::
      :toctree: generated

      instro.psu.drivers.bk_9115.BK9115
      instro.psu.drivers.bk_914x.BK914X
      instro.psu.drivers.keysight_e36100.KeysightE36100
      instro.psu.drivers.rigol_dp800.RigolDP800
      instro.psu.drivers.siglent_spd3303.SiglentSPD3303
      instro.psu.drivers.tdk_lambda_genesys.TDKLambdaGenesys
```

## Simulated Driver

```{eval-rst}
.. autosummary::
   :toctree: generated
   :nosignatures:

   ~instro.psu.drivers.simulated.SimulatedPSU
```

---

Errors raised by these methods are documented in [Exceptions](../library/exceptions.md).
