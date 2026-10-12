# I2C

I2C bus communication devices.

## Instrument and Abstract Driver

```{eval-rst}
.. autosummary::
   :toctree: generated
   :nosignatures:

   ~instro.i2c.I2CInterface
   ~instro.i2c.I2CDriverBase
```

## Vendor Drivers

| Vendor | Model | Description |
|--------|-------|-------------|
| Total Phase | {py:class}`Aardvark <instro.i2c.drivers.totalphase.aardvark.Aardvark>` | {pysummary}`instro.i2c.drivers.totalphase.aardvark.Aardvark` |

<!-- The vendor table is laid out by hand for its Vendor/Model columns; its
descriptions still come from the docstrings via {pysummary}. autosummary needs to
see these classes to generate their pages, and it scans source text, so a
never-built `only` block is enough. Sphinx collects toctrees inside `only`
regardless, so the pages still appear in the sidebar. -->

```{eval-rst}
.. only:: autosummary_stubs

   .. autosummary::
      :toctree: generated

      instro.i2c.drivers.totalphase.aardvark.Aardvark
```

## Types & Configuration

```{eval-rst}
.. autosummary::
   :toctree: generated
   :nosignatures:

   ~instro.i2c.types.SystemDefinition
   ~instro.i2c.types.RegisterDevice
   ~instro.i2c.types.CommandDevice
   ~instro.i2c.types.RegisterDef
   ~instro.i2c.types.CommandDef
   ~instro.i2c.types.FieldDef
   ~instro.i2c.types.DataFormat
   ~instro.i2c.types.ScalingFunction
   ~instro.i2c.types.LinearScaling
   ~instro.i2c.types.CustomScaling
```

---

Errors raised by these methods are documented in [Exceptions](../library/exceptions.md).
