<Warning>This Instrument category is new and is currently available only in the Unstable package</Warning>

# Flow Controller


## Interface

::: instro.unstable.flowcontroller.InstroFlowController

## Driver Interface

::: instro.unstable.flowcontroller.FlowControllerDriverBase

## Measurement keys

`FlowControllerDriverBase` defines string constants for the keys returned by `get_flow_data()` and used by the single-value properties:

| Constant | Key | Description |
|---|---|---|
| `SETPOINT_KEY` | `"setpoint"` | Commanded flow setpoint |
| `MASS_FLOW_KEY` | `"mass_flow"` | Measured mass flow |
| `VOLUMETRIC_FLOW_KEY` | `"vol_flow"` | Measured volumetric flow |
| `PRESSURE_KEY` | `"pressure"` | Absolute pressure |
| `TEMPERATURE_KEY` | `"temperature"` | Gas temperature |

## Vendor Drivers

### Alicat MC-series

::: instro.unstable.flowcontroller.drivers.AlicatMC
    options:
      heading_level: 4
