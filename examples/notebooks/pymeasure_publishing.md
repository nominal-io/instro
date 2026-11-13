---
jupytext:
  text_representation:
    format_name: myst
kernelspec:
  display_name: Python 3 (ipykernel)
  language: python
  name: python3
---

# Publish pymeasure instruments with instro

{.lead}
`instro-pymeasure` publishes the commands you send to a [pymeasure](https://pymeasure.readthedocs.io) instrument,
and the values you read from it, through any instro publisher, such as Nominal or a local file. It works with any
pymeasure driver.

```bash
pip install "instro[pymeasure]"
```

The first examples use `SwissArmyFake`, a simulated instrument that ships with pymeasure. They write CSV files to
temporary folders, so you can see each published value. On a bench, construct your instrument's pymeasure driver
with its VISA resource instead.

From a clone of the repository, `just notebook` opens this file in JupyterLab: right-click `pymeasure_publishing.md` and choose
**Open With → Jupytext Notebook**.

## Wrap an instrument

`PymeasureInstrument` turns a pymeasure instrument into an instro instrument. Setting a property publishes a
command, and reading one publishes a measurement. The channel is the instrument name followed by the property name,
and command channels end in `.cmd`.

```{code-cell} ipython3
import tempfile

import pandas as pd
from pymeasure.instruments.fakes import SwissArmyFake

from instro.lib import FilePublisher
from instro.pymeasure import PymeasureInstrument

publisher = FilePublisher(directory=tempfile.mkdtemp(), format="csv")

with PymeasureInstrument(name="bench", instrument=SwissArmyFake(wait=0), publishers=[publisher]) as bench:
    bench.device.output_voltage = 5.0  # bench.device is the pymeasure instrument
    bench.device.voltage

pd.read_csv(publisher.file_path)[["channel", "value"]]
```

## Attach to existing pymeasure code

`attach()` adds publishing to code that already uses a pymeasure instrument, without changing that code. The
instrument keeps its class, and it stays connected after you close the recorder.

```{code-cell} ipython3
from instro.pymeasure import attach

fake = SwissArmyFake(wait=0)  # existing code
publisher = FilePublisher(directory=tempfile.mkdtemp(), format="csv")
recorder = attach(instrument=fake, name="bench", publishers=[publisher])  # added

for volts in [1.0, 2.0, 3.0]:  # existing code, unchanged
    fake.output_voltage = volts
    print(fake.voltage)

recorder.close()  # added: flushes and closes the publisher

pd.read_csv(publisher.file_path)[["channel", "value"]]
```

## Poll in the background

`poll` lists properties that the instro background daemon reads every `background_interval` seconds, once you call
`start()`. Each read is published, and `get_channel()` returns the latest values.

```{code-cell} ipython3
from instro.lib import BackgroundDaemonConfig

with PymeasureInstrument(
    name="bench",
    instrument=SwissArmyFake(wait=0),
    publishers=[FilePublisher(directory=tempfile.mkdtemp(), format="csv")],
    poll=["wave"],  # SwissArmyFake.wave is a sine of elapsed time
    background_config=BackgroundDaemonConfig(interval=0.1),
) as bench:
    bench.start()
    for _ in range(3):
        print(bench.get_channel(channel_name="wave", wait_for_new_samples=True).latest)
```

## Drivers with channels and SCPI commands

pymeasure drivers declare their properties as a *control* (read and write), a *measurement* (read only) or a
*setting* (write only). Each one maps to SCPI commands. Multi-output instruments group them into channels, such as
`ch_1`.

This example uses the `KeysightE36312A` driver with pymeasure's `ProtocolAdapter`, which replies to each SCPI
command from a list, so you can see the exchange without hardware. On a bench, pass the resource instead:
`KeysightE36312A(adapter="USB0::0x2A8D::0x1102::MY12345678::INSTR")`.

```{code-cell} ipython3
from pymeasure.adapters import ProtocolAdapter
from pymeasure.instruments.keysight import KeysightE36312A

adapter = ProtocolAdapter(
    comm_pairs=[
        ("OUTPut 1, (@1)", None),  # (command sent, instrument reply)
        ("VOLT 2.5, (@1)", None),
        ("VOLT? (@1)", "2.5"),
        ("MEAS:CURRent? (@1)", "0.25"),
    ]
)
publisher = FilePublisher(directory=tempfile.mkdtemp(), format="csv")

with PymeasureInstrument(name="psu", instrument=KeysightE36312A(adapter=adapter), publishers=[publisher]) as psu:
    psu.device.ch_1.output_enabled = True
    psu.device.ch_1.voltage_setpoint = 2.5  # control, set: command
    psu.device.ch_1.voltage_setpoint  # control, read back: measurement
    psu.device.ch_1.current  # measurement

pd.read_csv(publisher.file_path)[["channel", "value"]]
```

The channel attribute `ch_1` becomes `ch1` in the channel name, and booleans are published as `1.0` and `0.0`.
Setting the `voltage_setpoint` control publishes `psu.ch1.voltage_setpoint.cmd`, and reading it back publishes
`psu.ch1.voltage_setpoint`. That keeps the value you asked for apart from the value the instrument reports.

Property access inside a driver's own methods, such as `Keithley2400.apply_voltage()`, is published the same way.
Two kinds of property aren't published:

- Properties inherited from pymeasure's base classes, such as `id`, `status` and `next_error`.
- Values longer than 16 elements, such as traces and buffers. instro logs a warning the first time it skips one.

## Choose channel names

A `ChannelMap` sets the channel name of each property, or leaves the property unpublished. You build it once, check
it, save it next to your code, and pass it as `channel_map=`.

`ChannelMap.generate()` lists every property under its default name. `preview()` shows the command channel and the
measurement channel each property publishes.

```{code-cell} ipython3
from instro.pymeasure import ChannelMap

channel_map = ChannelMap.generate(target=KeysightE36312A)
pd.DataFrame(channel_map.preview(target=KeysightE36312A, name="psu")).head(5)
```

`KeysightE36312A` declares its channels on the class, so the class is enough here. Some drivers add channels when
they connect. For those, pass the connected instrument instead of the class.

`select()` keeps the properties that match glob patterns, `exclude()` drops them, and `rename()` sets names. A
pattern or property that matches nothing raises `KeyError`.

```{code-cell} ipython3
channel_map.select("ch_1.*")  # glob patterns
channel_map.rename(names={"ch_1.voltage_setpoint": "out1.v_set", "ch_1.current": "out1.i"})
map_path = f"{tempfile.mkdtemp()}/psu_map.json"  # in your project, save it next to your code
channel_map.save(path=map_path, target=KeysightE36312A)  # adds kind, SCPI commands and docs for hand edits

preview = pd.DataFrame(channel_map.preview(target=KeysightE36312A, name="psu"))
preview.dropna(subset=["command", "measurement"], how="all")
```

In the saved file, each entry's `channel` is its name, and `null` means not published. You can edit it by hand, or
generate it from the terminal:

```bash
instro pymeasure map keysight.KeysightE36312A -o psu_map.json
```

Pass the file, or the `ChannelMap`, as `channel_map=` to `PymeasureInstrument` or `attach()`. instro checks it
against the instrument when it attaches, and rejects properties the instrument doesn't have, two properties with
one name, and names ending in `.cmd`.

```{code-cell} ipython3
adapter = ProtocolAdapter(
    comm_pairs=[("VOLT 2.5, (@1)", None), ("MEAS:CURRent? (@1)", "0.25"), ("VOLT 1, (@2)", None)]
)
publisher = FilePublisher(directory=tempfile.mkdtemp(), format="csv")

with PymeasureInstrument(
    name="psu",
    instrument=KeysightE36312A(adapter=adapter),
    publishers=[publisher],
    channel_map=map_path,
) as psu:
    psu.device.ch_1.voltage_setpoint = 2.5
    psu.device.ch_1.current
    psu.device.ch_2.voltage_setpoint = 1  # sent to the instrument, not published

pd.read_csv(publisher.file_path)[["channel", "value"]]
```

## Publish from a pymeasure procedure

In a pymeasure `Procedure`, attach in `startup()` and close the recorder in `shutdown()`. pymeasure still writes its
results file.

```{code-cell} ipython3
from pymeasure.experiment import FloatParameter, Procedure, Results, Worker, unique_filename

publisher = FilePublisher(directory=tempfile.mkdtemp(), format="csv")


class VoltageSweep(Procedure):
    max_voltage = FloatParameter("Max voltage", units="V", default=5)
    DATA_COLUMNS = ["Output voltage (V)", "Voltage (V)"]

    def startup(self):
        self.instrument = SwissArmyFake(wait=0)
        self.recorder = attach(instrument=self.instrument, name="sweep", publishers=[publisher])

    def execute(self):
        for step in range(6):
            self.instrument.output_voltage = self.max_voltage * step / 5
            self.emit(
                "results",
                {"Output voltage (V)": self.instrument.output_voltage, "Voltage (V)": self.instrument.voltage},
            )

    def shutdown(self):
        self.recorder.close()


results = Results(procedure=VoltageSweep(), data_filename=unique_filename(directory=tempfile.mkdtemp()))
worker = Worker(results=results)
worker.start()
worker.join(timeout=30)

pd.read_csv(publisher.file_path).groupby("channel").value.count()
```

## Choose a publisher

The examples above use a `FilePublisher`, but `publishers=` takes any instro publisher. For example, to publish to a
Nominal dataset, use a `NominalCorePublisher`, or pass `dataset_rid=` to use your default Nominal credential:

```python
from instro.lib import NominalCorePublisher

recorder = attach(instrument=fake, name="bench", publishers=[NominalCorePublisher(dataset_rid="<dataset rid>")])
# or
recorder = attach(instrument=fake, name="bench", dataset_rid="<dataset rid>")
```

See [Publishers](https://dev.nominal.io/instro/library/publishers/) for the publisher options, and
[pymeasure instruments](https://dev.nominal.io/instro/library/pymeasure/) for the guide.
