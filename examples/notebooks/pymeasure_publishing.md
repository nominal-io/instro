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

Publish the commands you send to a [pymeasure](https://pymeasure.readthedocs.io) instrument, and the values you read from it, through any instro publisher. The examples use pymeasure's simulated instruments, so they need no hardware, and write CSV files to temporary folders. The [guide](/library/pymeasure.md) covers the options.

```bash
pip install "instro[pymeasure]"
```

From a clone of the repository, `just notebook` opens this file in JupyterLab: open `pymeasure_publishing.md` with **Open With → Jupytext Notebook**.

## Wrap an instrument

`PymeasureInstrument` makes a pymeasure instrument an instro instrument. Setting a property publishes a command on `<name>.<property>.cmd`, and reading one publishes a measurement on `<name>.<property>`.

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

`attach()` adds publishing to an instrument your code already uses. The instrument stays connected after you close the recorder.

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

After `start()`, the background daemon reads each property in `poll` every `background_interval` seconds and publishes it. `get_channel()` returns the latest values.

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

This example runs the `KeysightE36312A` driver against pymeasure's `ProtocolAdapter`, which replies to each SCPI command from a list. On a bench, pass the VISA resource instead: `KeysightE36312A(adapter="USB0::0x2A8D::0x1102::MY12345678::INSTR")`.

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

The attribute `ch_1` becomes `ch1` in the channel name, and booleans publish as `1.0` and `0.0`. Setting `voltage_setpoint` publishes `psu.ch1.voltage_setpoint.cmd`, and reading it back publishes `psu.ch1.voltage_setpoint`, so the value you asked for stays apart from the value the instrument reports. The [guide](/library/pymeasure.md#channel-names) lists the properties instro skips.

## Choose channel names

A `ChannelMap` names each property's channel, or leaves the property unpublished. Generate one, edit it, save it next to your code, and pass it as `channel_map=`. `preview()` shows the command and measurement channel of each property.

```{code-cell} ipython3
from instro.pymeasure import ChannelMap

channel_map = ChannelMap.generate(target=KeysightE36312A)
pd.DataFrame(channel_map.preview(target=KeysightE36312A, name="psu")).head(5)
```

`KeysightE36312A` declares its channels on the class, so the class is enough. For a driver that adds channels when it connects, pass the connected instrument. `select()` and `exclude()` take glob patterns, and `rename()` sets names. Anything that matches nothing raises `KeyError`.

```{code-cell} ipython3
channel_map.select("ch_1.*")  # glob patterns
channel_map.rename(names={"ch_1.voltage_setpoint": "out1.v_set", "ch_1.current": "out1.i"})
map_path = f"{tempfile.mkdtemp()}/psu_map.json"  # in your project, save it next to your code
channel_map.save(path=map_path, target=KeysightE36312A)  # adds kind, SCPI commands and docs for hand edits

preview = pd.DataFrame(channel_map.preview(target=KeysightE36312A, name="psu"))
preview.dropna(subset=["command", "measurement"], how="all")
```

Edit the saved file by hand, or generate it from the terminal with `instro pymeasure map keysight.KeysightE36312A -o psu_map.json`. Pass the file or the `ChannelMap` as `channel_map=` to `PymeasureInstrument` or `attach()`. The [guide](/library/pymeasure.md#channel-names) describes the format.

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

Attach in `startup()` and close the recorder in `shutdown()`. pymeasure still writes its results file.

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

`publishers=` takes any instro [publisher](/library/publishers.md). To publish to a Nominal dataset, use a `NominalCorePublisher`, or pass `dataset_rid=` to use your default Nominal profile:

```python
from instro.lib import NominalCorePublisher

recorder = attach(instrument=fake, name="bench", publishers=[NominalCorePublisher(dataset_rid="<dataset rid>")])
# or, with your default profile
recorder = attach(instrument=fake, name="bench", dataset_rid="<dataset rid>")
```
