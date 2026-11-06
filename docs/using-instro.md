---
orphan: true
myst:
  html_meta:
    description: "Using instro with some more detail"
---

# Using instro

{.lead}
Using instro with some more detail

This page discusses details about using `instro`.

## Imports

Import each name from the shallowest module that has it. For almost every name, that is one of three namespaces:

| Namespace | What it holds | Example |
| --- | --- | --- |
| `instro` | Instrument classes, abstract driver bases, and each category's types and config models | `from instro import InstroPSU, LoadMode, MeasurementFunction` |
| `instro.drivers` | Every concrete driver, including the drivers in vendor packages | `from instro.drivers import BK9115, NIDAQDriver, VirtualDMM` |
| `instro.lib` | Shared building blocks: publishers, transports, measurement types, exceptions | `from instro.lib import NominalCorePublisher, VisaConfig` |

```python
from instro import InstroDMM, InstroELoad, InstroPSU, LoadMode, MeasurementFunction
from instro.drivers import VirtualDMM, VirtualELoad, VirtualPSU
from instro.lib import BackgroundDaemonConfig, NominalCorePublisher
```

Code that imports from the deeper module paths (`instro.psu.drivers`, `instro.lib.publishers`, ...) keeps working unchanged, but write new code against the namespaces above.

A driver from a vendor package (`NIDAQDriver`, `LabJackTSeriesDriver`, `MCCDriver`, `Aardvark`) loads when you first import it. If its package is missing, the `ImportError` names the extra to install, for example `pip install 'instro[nidaq]'`. Community drivers have their own flat namespace, `instro.contrib.drivers`.

Two names are not in `instro` because they mean different things in different protocols, so their shallowest module is the category package: import Modbus `TimingConfig` from `instro.modbus`, and the I2C `RegisterDef` from `instro.i2c` (`instro.RegisterDef` is the Modbus one).

`import instro` is cheap: each name loads on first use. In IPython, `dir(instro)` and tab completion list every name before anything loads.

## Instruments

### Two ways to get data

You can acquire measurements from an instrument in two ways.  The difference is who triggers the reads and how often.

|              | Background acquisition | On-demand acquisition |
| ------------ | ---------------------- | --------------------- |
| **How**      | Call `start()`. A background daemon reads from the instrument at a fixed, configurable rate. Pull the latest N values with `get_channel()` when you need them. |   Call read methods (`read_analog()`, `get_voltage()`, …) only when you want a measurement.  |
| **Best for** | Apps where data should always be available and publishing. | Apps that need measurements only at specific times; lower system and bus load. DMMs are a good example. |

## open()/close() or `with` block

Instruments can also be open/closed automatically using a `with` block.

::::{tab-set}

:::{tab-item} Explicit open() / close()

Best when the instrument's lifetime spans many functions, or when you want to interleave setup and teardown across event handlers, fixtures, or REPL sessions.

```python
from instro import InstroPSU
from instro.drivers import SimulatedPSU

psu = InstroPSU(
    name="bench_psu",
    driver=SimulatedPSU("TCPIP0::127.0.0.1::5025::SOCKET"),
    num_channels=2,
)
psu.open()

psu.set_voltage(5.0, channel=1)
psu.set_current_limit(1.0, channel=1)
psu.output_enable(True, channel=1)

voltage = psu.get_voltage(channel=1)
print(f"V: {voltage.latest:.3f} V")

psu.output_enable(False, channel=1)
psu.close()
```
:::

:::{tab-item} with block

Best for scripts and tests where the instrument is used inside a single scope. `close()` runs automatically on the way out, even if an exception is raised.

```python
from instro import InstroPSU
from instro.drivers import SimulatedPSU

with InstroPSU(
    name="bench_psu",
    driver=SimulatedPSU("TCPIP0::127.0.0.1::5025::SOCKET"),
    num_channels=2,
) as psu:
    psu.set_voltage(5.0, channel=1)
    psu.set_current_limit(1.0, channel=1)
    psu.output_enable(True, channel=1)

    voltage = psu.get_voltage(channel=1)
    print(f"V: {voltage.latest:.3f} V")
```
:::
::::
