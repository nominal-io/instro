# Flow Controller

## Interface

::: instro.flowcontroller.InstroFlowController

## Driver Interface

::: instro.flowcontroller.FlowControllerDriverBase

## Measurement keys

`FlowControllerDriverBase` defines string constants for the keys returned by `get_flow_data()` and used by the single-value properties:

| Constant | Key | Description |
|---|---|---|
| `SETPOINT` | `"setpoint"` | Commanded flow setpoint |
| `MASS_FLOW` | `"mass_flow"` | Measured mass flow |
| `VOL_FLOW` | `"vol_flow"` | Measured volumetric flow |
| `PRESSURE` | `"pressure"` | Absolute pressure |
| `TEMPERATURE` | `"temperature"` | Gas temperature |

## Vendor Drivers

### Alicat MC-series

::: instro.flowcontroller.drivers.AlicatMC
    options:
      heading_level: 4
