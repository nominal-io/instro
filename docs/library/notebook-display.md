---
myst:
  html_meta:
    description: "See instruments and publishers as cards in Jupyter, VS Code and Colab notebooks"
---

# Notebook display

{.lead}
Instruments and publishers display as cards in Jupyter, VS Code and Colab notebooks. A card needs no setup and never talks to the hardware.

## Overview

In Jupyter, VS Code and Colab, an instrument or publisher at the end of a cell displays as a card. Outside a notebook, `repr()` gives a one-line summary.

```python
from instro.psu import InstroPSU
from instro.psu.drivers import VirtualPSU

psu = InstroPSU(name="bench", driver=VirtualPSU(), num_channels=2, dataset_rid="<dataset RID>")
psu.start()
psu  # shows the card
```

An instrument's card shows:

- its driver, and category details such as the channel count
- the background daemon: whether it runs, its interval, and the last loop time
- its publishers, each with its sent and error counts and a link to the Nominal dataset it writes to (see [publisher errors and stats](/library/publishers.md#publisher-errors-and-stats))
- every channel published so far, commands included, with its latest value, rate, sample count and time
- its default tags

A card shows only what `instro` already holds in memory, so displaying an instrument is safe while the background daemon runs.

Each publisher has a card too. A {py:class}`~instro.lib.publishers.nominal_core.NominalCorePublisher` card shows the dataset with an **Open in Nominal** link, its RID with a copy button, where the credentials came from, and the stream settings. It never shows an API key.

The [notebook display example](/examples/notebooks/notebook_display.md) shows each card and needs no hardware or Nominal credentials. From a clone of the repository, `just notebook` opens it in JupyterLab: open `notebook_display.md` with **Open With → Jupytext Notebook**.

## Display options

{py:func}`~instro.lib._display.set_display_options` changes every card, as a call or for the length of a `with` block:

```python
from instro.lib import set_display_options

set_display_options(timezone="America/Los_Angeles", time_precision="ms")

with set_display_options(show_link=False):
    psu  # no request to resolve the Nominal workspace
```

| Option | Default | Effect |
| --- | --- | --- |
| `style` | `"html"` | `"text"` keeps the plain `repr` |
| `timezone` | `"UTC"` | `"local"` or an IANA name |
| `time_format` | `"iso"` | `"readable"`: `2026-11-02 09:52:57 MST` |
| `time_precision` | `"s"` | `"ms"`, `"us"` or `"ns"`. Hovering shows the full value. |
| `show_link` | `True` | Link datasets to Nominal. The first link for a client makes one request to resolve its workspace. |
| `collapse_threshold` | `10` | Sections with more items start collapsed |
| `max_items` | `50` | Items listed per section |

## Custom instruments and publishers

A custom {py:class}`~instro.lib.Instrument` subclass gets a card without extra code. To add rows, override `_display_fields()` and return `(label, html)` pairs built from in-memory state only.

A custom publisher appears in an instrument's card under its class name. Define `_display_summary()`, returning one line of HTML, to describe it there.
