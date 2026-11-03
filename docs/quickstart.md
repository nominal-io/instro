---
myst:
  html_meta:
    description: "Drive a simulated power supply end-to-end in about five minutes. No hardware required"
---

# Quickstart

{.lead}
Drive a simulated power supply end-to-end in about five minutes. No hardware required

## Step-by-step

`instro` ships with a built-in SCPI simulator, so you can try the library without owning any hardware.

::::{container} steps

:::{container} step

**Install instro**

```bash
pip install instro
```
:::

:::{container} step

**Start the simulated PSU**

Open a separate terminal and start the bundled simulator.

```bash
python -m instro.psu.scpi_sim_server
```
:::

:::{container} step

**Construct an InstroPSU**

Create an `InstroPSU` driven by the `SimulatedPSU` driver. The driver owns the VISA transport.

```python
from instro.psu import InstroPSU
from instro.psu.drivers import SimulatedPSU
from instro.lib.publishers import FilePublisher

file_pub = FilePublisher(format="jsonl", directory="/tmp/instro/")
psu = InstroPSU(
    name="bench_psu",
    driver=SimulatedPSU("TCPIP0::127.0.0.1::5025::SOCKET"),
    publishers=[file_pub],
    num_channels=2,
)
psu.open()
```
:::

:::{container} step

**Configure Instrument**

`apply()` sets the current limit, then the voltage, then enables the output.

```python
psu.set_overvoltage_protection_level(5.5, channel=1)
psu.set_overvoltage_protection_enabled(True, channel=1)
psu.apply(current_limit=1.0, voltage=5.0, enable=True, channel=1)
```
:::

:::{container} step

**Make Measurements**

Measurement getters return a [Measurement](/library/library.md#measurements-and-commands) object. Use `.latest` to grab the current value. Every measurement is timestamped, tagged, and streamed to the [Publishers](/library/publishers.md).

```python
voltage = psu.get_voltage(channel=1)
current = psu.get_current(channel=1)
print(f"V: {voltage.latest:.3f} V")
print(f"I: {current.latest:.3f} A")
```
:::

:::{container} step

**Close Up Shop**

When you're done, disable the output and close the connection. `close()` stops the background daemon and flushes the publishers.

```python
psu.output_enable(False, channel=1)
psu.close()
```
:::

:::{container} step

**Read the Data**

All `Commands` and `Measurements` are logged to the `publishers`.

```python
print(file_pub.file_path.read_text())  # commands and measurements written to the publisher
```
:::
::::

::::{grid} 1 1 3 3
:gutter: 2

:::{grid-item-card} {octicon}`download` Installation
:link: /installation
:link-type: doc

:::

:::{grid-item-card} {octicon}`code` Examples
:link: /examples/index
:link-type: doc

:::

:::{grid-item-card} {octicon}`cpu` Supported Instruments
:link: /instruments
:link-type: doc

:::
::::
