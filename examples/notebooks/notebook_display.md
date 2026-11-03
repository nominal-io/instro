---
jupytext:
  text_representation:
    format_name: myst
kernelspec:
  display_name: Python 3 (ipykernel)
  language: python
  name: python3
---

# Notebook display for instruments and publishers

Watch a bench power supply from a notebook: its settings, the latest value of every channel, and what each publisher has done with the data. Instruments and publishers display as cards, described in the [notebook display guide](https://github.com/nominal-io/instro/blob/main/docs/library/notebook-display.md).

The example runs without hardware or Nominal credentials. It uses a `VirtualPSU`, a local file publisher and an offline stand-in for a Nominal dataset. To show a real dataset, set `DATASET_RID` in the first cell.

From a clone of the repository, `just notebook` opens this file in JupyterLab: right-click `notebook_display.md` and choose **Open With → Jupytext Notebook**.

```{code-cell} ipython3
import tempfile
import time

from instro.lib import set_display_options
from instro.lib.publishers import FilePublisher, QueuedPublisher
from instro.lib.types import BackgroundDaemonConfig
from instro.psu import InstroPSU
from instro.psu.drivers import VirtualPSU

# A dataset RID to write to with your "default" Nominal profile, or None for an offline stand-in.
DATASET_RID = None

# The file publisher writes JSON Lines here.
capture_dir = tempfile.mkdtemp(prefix="instro-notebook-")
```

## An instrument card

An instrument at the end of a cell displays as a card. Before `start()`, the card shows the driver, the channel count, the background daemon's settings, the publishers and the default tags. Building it never talks to the hardware.

```{code-cell} ipython3
psu = InstroPSU(
    name="bench_psu",
    driver=VirtualPSU(num_channels=2),
    num_channels=2,
    publishers=[QueuedPublisher(FilePublisher(capture_dir, format="jsonl"))],
    background_config=BackgroundDaemonConfig(interval=0.1),
    # Default tags, added to every published value.
    test_stand="rig-3",
    dut_serial="SN-0042",
)
psu.open()
psu
```

Outside a notebook, `repr()` gives a one-line summary.

```{code-cell} ipython3
print(repr(psu))
```

## While it runs

Once the background daemon runs, the card has a **running** badge and the last loop time. The **Channels** table lists every channel published so far, with its latest value, rate and sample count. Settings you send, such as the voltage, appear as `.cmd` channels.

```{code-cell} ipython3
# Each setter publishes a .cmd channel.
psu.set_current_limit(0.5, channel=1)
psu.set_voltage(3.3, channel=1)
psu.output_enable(True, channel=1)
# Poll the supply every 0.1 s, then let a few readings arrive.
psu.start()
time.sleep(1.5)
psu
```

## Publisher cards

Each publisher has its own card. `NominalCorePublisher` shows the dataset with an **Open in Nominal** link, the RID with a copy button, and where the credentials came from. It never shows an API key.


```{code-cell} ipython3
from unittest.mock import MagicMock, create_autospec

from nominal.core import Dataset
from nominal_streaming import NominalDatasetStream

from instro.lib.publishers import NominalCorePublisher

if DATASET_RID:
    nominal = NominalCorePublisher(DATASET_RID)
else:
    # An offline stand-in for a Nominal dataset, so the card renders without a network.
    stand_in = create_autospec(Dataset, instance=True)
    stand_in.rid = "ri.catalog.main.dataset.00000000-0000-0000-0000-000000000000"
    stand_in.name = "bench_psu run (offline stand-in)"
    stream = create_autospec(NominalDatasetStream, instance=True)
    stream._impl = MagicMock()
    stand_in.get_write_stream.return_value = stream
    type(stand_in).nominal_url = property(lambda _: "https://app.gov.nominal.io/")
    nominal = NominalCorePublisher(dataset=stand_in)

nominal
```

```{code-cell} ipython3
psu.publishers[0].publisher  # the FilePublisher inside the QueuedPublisher
```

## A failing publisher

A publisher that raises doesn't stop the instrument. The instrument counts the error, sends the data to its other publishers, and logs the first error with its traceback. The publisher below fails on every other call: one error is logged, and the card gets an error badge.

```{code-cell} ipython3
# A publisher whose upload fails on every second call.
class FlakyPublisher:
    def __init__(self):
        self.calls = 0

    def publish(self, data, **kwargs):
        self.calls += 1
        if self.calls % 2 == 0:
            raise ConnectionError("upload endpoint unreachable")

    def close(self):
        pass

    # How the publisher is described in the instrument's card.
    def _display_summary(self):
        return "flaky test publisher"


psu.add_publisher(FlakyPublisher())
time.sleep(1.0)
psu
```

`publisher_stats` and `channel_stats` return the counters from the card as snapshots.

```{code-cell} ipython3
for stats in psu.publisher_stats:
    print(f"{stats.publisher:24} sent={stats.sent:<5} errors={stats.errors:<4} last_error={stats.last_error}")
```

```{code-cell} ipython3
for state in psu.channel_stats:
    print(f"{state.channel:32} {state.kind:11} value={state.value!s:<10} count={state.count:<5} {state.rate(5.0):5.1f}/s")
```

## Display options

`set_display_options` changes every card, as a call or for the length of a `with` block.

```{code-cell} ipython3
with set_display_options(time_precision="ms", time_format="readable", timezone="local"):
    display(psu)
```

```{code-cell} ipython3
with set_display_options(style="text"):
    display(psu)  # the plain repr
```

## A live view

A card is a snapshot taken when the cell runs. To watch it change, refresh a display handle.

```{code-cell} ipython3
# Step the voltage for five seconds and redraw the card after each step.
handle = display(psu, display_id=True)
for step in range(10):
    psu.set_voltage(1.0 + 0.5 * step, channel=1)
    time.sleep(0.5)
    handle.update(psu)
```

## Clean up

```{code-cell} ipython3
# Stops the daemon and closes the supply and every publisher.
psu.close()
psu
```
