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

The example uses a `VirtualPSU`, so it needs no hardware. With a `default` Nominal [profile](https://docs.nominal.io/core/sdk/python-client/authentication), it streams the supply's channels to a Nominal dataset. Without one, it uses an offline stand-in for the dataset and uploads nothing.

From a clone of the repository, `just notebook` opens this file in JupyterLab: right-click `notebook_display.md` and choose **Open With → Jupytext Notebook**.

```{code-cell} ipython3
import tempfile
import time

from instro.lib import set_display_options
from instro.lib.publishers import FilePublisher, QueuedPublisher
from instro.lib.types import BackgroundDaemonConfig
from instro.psu import InstroPSU
from instro.psu.drivers import VirtualPSU

# A dataset RID to stream to with your "default" Nominal profile. With None, the example finds or
# creates its own dataset in that profile's workspace.
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

Each publisher has its own card. This one is the file publisher inside the `QueuedPublisher`.

```{code-cell} ipython3
psu.publishers[0].publisher
```

## Stream to Nominal

A `NominalCorePublisher` streams every value the instrument publishes to a Nominal dataset. Add it to the running instrument with `add_publisher`, and the instrument's card lists it with a link to the dataset and a count of what it has sent. A publisher only gets values published after you add it, so the current limit set above isn't in the dataset.

```{code-cell} ipython3
:tags: [hide-input]

# Find the dataset to stream to: DATASET_RID, or this example's dataset in your "default"
# profile's workspace, created on the first run. Without a profile, use an offline stand-in
# so the notebook still runs. The docs page collapses this cell.
from unittest.mock import MagicMock, create_autospec

from nominal.config import NominalConfig
from nominal.core import Dataset, NominalClient
from nominal.exceptions import NominalConfigError
from nominal_streaming import NominalDatasetStream

EXAMPLE_PROPERTIES = {"example": "instro-notebook-display"}

try:
    NominalConfig.from_yaml().get_profile("default")
    client = NominalClient.from_profile("default")
except (FileNotFoundError, NominalConfigError):
    client = None

if client is None:
    dataset = create_autospec(Dataset, instance=True)
    dataset.rid = "ri.catalog.main.dataset.00000000-0000-0000-0000-000000000000"
    dataset.name = "bench_psu run (offline stand-in)"
    stream = create_autospec(NominalDatasetStream, instance=True)
    stream._impl = MagicMock()
    dataset.get_write_stream.return_value = stream
    type(dataset).nominal_url = property(lambda _: "https://app.gov.nominal.io/")
    print("No 'default' Nominal profile: using an offline stand-in, so nothing is uploaded.")
elif DATASET_RID:
    dataset = client.get_dataset(DATASET_RID)
else:
    found = client.search_datasets(properties=EXAMPLE_PROPERTIES)
    dataset = found[0] if found else client.create_dataset(
        "instro notebook display example",
        description="Channels streamed by instro's notebook display example.",
        properties=EXAMPLE_PROPERTIES,
    )
```

```{code-cell} ipython3
from instro.lib.publishers import NominalCorePublisher

# Credentials come from the dataset; pass dataset_rid= and profile= instead to look it up here.
nominal = NominalCorePublisher(dataset=dataset)
psu.add_publisher(nominal)
time.sleep(2.0)
psu
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

Closing the instrument stops the daemon, flushes the stream to Nominal, and closes every publisher. The Nominal publisher's card then links to the dataset with everything this notebook sent.

```{code-cell} ipython3
psu.close()
nominal
```
