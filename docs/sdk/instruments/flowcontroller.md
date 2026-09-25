# Flow Controller

Flow controllers.

```{warning}
This Instrument category is new and is currently available only in the Unstable package.
```

## Instrument and Abstract Driver

```{eval-rst}
.. autosummary::
   :toctree: generated
   :nosignatures:

   ~instro.unstable.flowcontroller.InstroFlowController
   ~instro.unstable.flowcontroller.FlowControllerDriverBase
```

## Vendor Drivers

| Vendor | Model | Description |
|--------|-------|-------------|
| Alicat | {py:class}`MC-series <instro.unstable.flowcontroller.drivers.AlicatMC>` | {pysummary}`instro.unstable.flowcontroller.drivers.AlicatMC` |

<!-- The vendor table is laid out by hand for its Vendor/Model columns; its
descriptions still come from the docstrings via {pysummary}. autosummary needs to
see these classes to generate their pages, and it scans source text, so a
never-built `only` block is enough. Sphinx collects toctrees inside `only`
regardless, so the pages still appear in the sidebar. -->

```{eval-rst}
.. only:: autosummary_stubs

   .. autosummary::
      :toctree: generated

      instro.unstable.flowcontroller.drivers.AlicatMC
```
## Measurement keys

`FlowControllerDriverBase` defines string constants for the keys returned by `get_flow_data()` and used by the single-value properties:

| Constant | Key | Description |
|---|---|---|
| `SETPOINT_KEY` | `"setpoint"` | Commanded flow setpoint |
| `MASS_FLOW_KEY` | `"mass_flow"` | Measured mass flow |
| `VOLUMETRIC_FLOW_KEY` | `"vol_flow"` | Measured volumetric flow |
| `PRESSURE_KEY` | `"pressure"` | Absolute pressure |
| `TEMPERATURE_KEY` | `"temperature"` | Gas temperature |
