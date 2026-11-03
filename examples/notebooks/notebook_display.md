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

This notebook shows the features from [#709](https://github.com/nominal-io/instro/issues/709): notebook cards ([#711](https://github.com/nominal-io/instro/pull/711)) and publisher stats ([#712](https://github.com/nominal-io/instro/pull/712)). The cards were inspired by [nominal-io/nominal-client#1260](https://github.com/nominal-io/nominal-client/pull/1260).

It needs no hardware and no Nominal credentials: it uses a `VirtualPSU`, a local file publisher, and an offline stand-in for a Nominal dataset. To see a real dataset card instead, set `DATASET_RID` below.

Open it from the repo root with `just notebook`, then right-click `notebook_display.md` and choose **Open With → Jupytext Notebook**. `just test-notebooks` runs it from top to bottom.

```{code-cell} ipython3
import tempfile
import time

from instro.lib import set_display_options
from instro.lib.publishers import FilePublisher, QueuedPublisher
from instro.lib.types import BackgroundDaemonConfig
from instro.psu import InstroPSU
from instro.psu.drivers import VirtualPSU

# Set to a dataset RID to use a real dataset with your "default" Nominal profile.
DATASET_RID = None

capture_dir = tempfile.mkdtemp(prefix="instro-notebook-")
```

## An instrument card

An instrument at the end of a cell displays as a card. Before `start()`, it shows the driver, the channel count, the background daemon's settings, the publishers and the default tags. Building the card never talks to the hardware.

```{code-cell} ipython3
psu = InstroPSU(
    name="bench_psu",
    driver=VirtualPSU(num_channels=2),
    num_channels=2,
    publishers=[QueuedPublisher(FilePublisher(capture_dir, format="jsonl"))],
    background_config=BackgroundDaemonConfig(interval=0.1),
    operator="alex",
    test_stand="rig-3",
)
psu.open()
psu
```

`repr()` gives a one-line summary, which you see outside a notebook:

```{code-cell} ipython3
print(repr(psu))
```

## While it runs

Set a voltage, enable the output, and start the background daemon. The card now has a **running** badge and the last loop time. The **Channels** table lists every channel published so far, commands (`.cmd`) included, each with its latest value, rate and sample count.

```{code-cell} ipython3
psu.set_current_limit(0.5, channel=1)
psu.set_voltage(3.3, channel=1)
psu.output_enable(True, channel=1)
psu.start()
time.sleep(1.5)
psu
```

## Publisher cards

Each publisher has its own card. `NominalCorePublisher` shows the dataset with an **Open in Nominal** link, the RID with a copy button, and where the credentials came from. It never shows an API key.

Without `DATASET_RID`, the next cell builds the publisher around an offline stand-in dataset, so its card renders without a network.

```{code-cell} ipython3
from unittest.mock import MagicMock, create_autospec

from nominal.core import Dataset
from nominal_streaming import NominalDatasetStream

from instro.lib.publishers import NominalCorePublisher

if DATASET_RID:
    nominal = NominalCorePublisher(DATASET_RID)
else:
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

A publisher that raises no longer breaks the instrument. The instrument logs the first failure with its traceback, counts each one, and keeps sending to the other publishers. Here a publisher fails on every other call. Only one error is logged, and the card gets an error badge.

```{code-cell} ipython3
class FlakyPublisher:
    def __init__(self):
        self.calls = 0

    def publish(self, data, **kwargs):
        self.calls += 1
        if self.calls % 2 == 0:
            raise ConnectionError("upload endpoint unreachable")

    def close(self):
        pass

    def _display_summary(self):
        return "flaky test publisher"


psu.add_publisher(FlakyPublisher())
time.sleep(1.0)
psu
```

The same counters are available from code:

```{code-cell} ipython3
for stats in psu.publisher_stats:
    print(f"{stats.publisher:24} sent={stats.sent:<5} errors={stats.errors:<4} last_error={stats.last_error}")
```

```{code-cell} ipython3
for state in psu.channel_stats:
    print(f"{state.channel:32} {state.kind:11} value={state.value!s:<10} count={state.count:<5} {state.rate(5.0):5.1f}/s")
```

## Display options

`set_display_options` changes every card, as a call or for the length of a `with` block. These options match nominal-client's.

```{code-cell} ipython3
with set_display_options(time_precision="ms", time_format="readable", timezone="local"):
    display(psu)
```

```{code-cell} ipython3
with set_display_options(style="text"):
    display(psu)  # the plain repr
```

## A live view

A card is a snapshot taken when the cell runs. To watch it update, refresh a display handle. This loop runs for five seconds and changes the voltage as it goes. A built-in `watch()` is a possible follow-up from #709.

```{code-cell} ipython3
handle = display(psu, display_id=True)
for step in range(10):
    psu.set_voltage(1.0 + 0.5 * step, channel=1)
    time.sleep(0.5)
    handle.update(psu)
```

## Clean up

```{code-cell} ipython3
psu.close()
psu
```
