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

An instrument's card shows its driver, background daemon, publishers, default tags and, once `start()` has been called, the latest value of every channel. A card shows only what `instro` already holds in memory, so displaying an instrument never talks to the hardware.

Each publisher has a card too. A {py:class}`~instro.lib.publishers.nominal_core.NominalCorePublisher` card links its dataset in Nominal.

## Display options

{py:func}`instro.lib.set_display_options` changes every card, as a call or for the length of a `with` block:

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

A custom {py:class}`~instro.lib.Instrument` subclass gets a default card automatically. To customize the card, override `_display_fields()` and return `(label, html)` pairs built from in-memory state only. Start from `super()._display_fields()` to keep the rows of the class you extend:

```python
from instro.lib import _display
from instro.psu import InstroPSU


class BenchPSU(InstroPSU):
    def _display_fields(self):
        return [*super()._display_fields(), ("Location", _display.escape("bench 3"))]
```

To replace the whole card, override `_repr_html_()`.

An instrument's card describes each publisher in one line. A publisher without `_display_summary()` shows its class name. Define `_display_summary()`, returning one line of HTML, to describe it there; a subclass of a built-in publisher can extend the built-in line with `super()._display_summary()`.
