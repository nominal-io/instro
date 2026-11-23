---
jupytext:
  text_representation:
    format_name: myst
kernelspec:
  display_name: Python 3 (ipykernel)
  language: python
  name: python3
---

# Publish QCoDeS instruments with instro

Publish the commands you send to a [QCoDeS](https://microsoft.github.io/Qcodes/) instrument, and the values you read from it, through any instro publisher. The examples use QCoDeS's mock instruments, so they need no hardware, and write CSV files to temporary folders. The [guide](/library/qcodes.md) covers the options.

```bash
pip install "instro[qcodes]"
```

From a clone of the repository, `just notebook` opens this file in JupyterLab: open `qcodes_publishing.md` with **Open With → Jupytext Notebook**.

## Wrap an instrument

`QcodesInstrument` makes a QCoDeS instrument an instro instrument. Setting a parameter publishes a command on `<name>.<parameter>.cmd`, and getting one publishes a measurement on `<name>.<parameter>`.

```{code-cell} ipython3
import tempfile

import pandas as pd
from qcodes.instrument import Instrument
from qcodes.instrument_drivers.mock_instruments import DummyInstrument

from instro.lib import FilePublisher
from instro.qcodes import QcodesInstrument

Instrument.close_all()  # QCoDeS instrument names are global; this lets you re-run the cells
publisher = FilePublisher(directory=tempfile.mkdtemp(), format="csv")

with QcodesInstrument(
    name="dac", instrument=DummyInstrument("dac_driver", gates=["g1", "g2"]), publishers=[publisher]
) as dac:
    dac.device.g1(1.5)  # dac.device is the QCoDeS instrument
    dac.device.g1()

pd.read_csv(publisher.file_path)[["channel", "value"]]
```

## Attach to existing QCoDeS code

`attach()` adds publishing to an instrument your code already uses. The instrument stays open after you close the recorder.

```{code-cell} ipython3
from instro.qcodes import attach

Instrument.close_all()
dac = DummyInstrument("dac_driver", gates=["g1", "g2"])  # existing code
publisher = FilePublisher(directory=tempfile.mkdtemp(), format="csv")
recorder = attach(instrument=dac, name="dac", publishers=[publisher])  # added

for volts in [1.0, 2.0, 3.0]:  # existing code, unchanged
    dac.g1(volts)
    print(dac.g1())

recorder.close()  # added: flushes and closes the publisher

pd.read_csv(publisher.file_path)[["channel", "value"]]
```

## Poll in the background

After `start()`, the background daemon reads each parameter in `poll` every `background_interval` seconds and publishes it. `get_channel()` returns the latest values.

```{code-cell} ipython3
from instro.lib import BackgroundDaemonConfig

Instrument.close_all()
with QcodesInstrument(
    name="dac",
    instrument=DummyInstrument("dac_driver", gates=["g1"]),
    publishers=[FilePublisher(directory=tempfile.mkdtemp(), format="csv")],
    poll=["g1"],
    background_config=BackgroundDaemonConfig(interval=0.1),
) as dac:
    dac.start()
    for _ in range(3):
        print(dac.get_channel(channel_name="g1", wait_for_new_samples=True).latest)
```

## Instruments with channels

A QCoDeS channel adds a segment to the channel name. `DummyChannelInstrument` has channels `A` and `B`, each with a `temperature` parameter.

```{code-cell} ipython3
from qcodes.instrument_drivers.mock_instruments import DummyChannelInstrument

Instrument.close_all()
publisher = FilePublisher(directory=tempfile.mkdtemp(), format="csv")

with QcodesInstrument(
    name="chamber",
    instrument=DummyChannelInstrument("chamber_driver", channel_names=["A", "B"]),
    publishers=[publisher],
) as chamber:
    chamber.device.A.temperature(21.5)  # command
    chamber.device.A.temperature()  # measurement
    chamber.device.B.temperature()

pd.read_csv(publisher.file_path)[["channel", "value"]]
```

Values that are not a number or a string are skipped. That includes arrays longer than 16 values, such as traces. instro logs a warning the first time it skips one. The [guide](/library/qcodes.md#channel-names) lists what is published.

## Choose channel names

A `ChannelMap` names each parameter's channel, or leaves the parameter unpublished. Generate one, edit it, save it next to your code, and pass it as `channel_map=`. `preview()` shows the command and measurement channel of each parameter.

QCoDeS drivers register their parameters when you construct them, so generate the map from the connected instrument.

```{code-cell} ipython3
from instro.qcodes import ChannelMap

Instrument.close_all()
dac = DummyInstrument("dac_driver", gates=["g1", "g2", "g3"])

channel_map = ChannelMap.generate(target=dac)
pd.DataFrame(channel_map.preview(target=dac, name="dac"))
```

`select()` and `exclude()` take glob patterns, and `rename()` sets names. Anything that matches nothing raises `KeyError`.

```{code-cell} ipython3
channel_map.select("g1", "g2")  # glob patterns
channel_map.rename(names={"g1": "out1.v"})
map_path = f"{tempfile.mkdtemp()}/dac_map.json"  # in your project, save it next to your code
channel_map.save(path=map_path, target=dac)  # adds kind, unit and label for hand edits

preview = pd.DataFrame(channel_map.preview(target=dac, name="dac"))
preview.dropna(subset=["command", "measurement"], how="all")
```

Edit the saved file by hand, or generate it from the terminal with `instro qcodes map`. Pass the file or the `ChannelMap` as `channel_map=` to `QcodesInstrument` or `attach()`. The [guide](/library/qcodes.md#channel-names) describes the format.

```{code-cell} ipython3
publisher = FilePublisher(directory=tempfile.mkdtemp(), format="csv")

with QcodesInstrument(name="dac", instrument=dac, publishers=[publisher], channel_map=map_path) as dac_wrapper:
    dac_wrapper.device.g1(2.5)
    dac_wrapper.device.g2(1.0)
    dac_wrapper.device.g3(0.5)  # sent to the instrument, not published

pd.read_csv(publisher.file_path)[["channel", "value"]]
```

## Choose a publisher

`publishers=` takes any instro [publisher](/library/publishers.md). To publish to a Nominal dataset, use a `NominalCorePublisher`, or pass `dataset_rid=` to use your default Nominal profile:

```python
from instro.lib import NominalCorePublisher

recorder = attach(instrument=dac, name="dac", publishers=[NominalCorePublisher(dataset_rid="<dataset rid>")])
# or, with your default profile
recorder = attach(instrument=dac, name="dac", dataset_rid="<dataset rid>")
```
