---
myst:
  html_meta:
    description: "Command-line tools for working with instro"
---

# CLI

{.lead}
Command-line tools for working with instro

`instro` ships a command-line interface for inspecting and discovering instruments on your bench.

## Running the CLI

The CLI comes with the `instro` package (see [Installation](/installation.md)).
In a project that has `instro` installed, run:

```bash
uv run instro discover
```

For regular use, put `instro` on your PATH instead so you can call it from anywhere:

```bash
uv tool install instro
instro discover
```
## Usage
### instro --version

Prints version information.

```bash
instro --version # prints version information
```

### instro discover

Scans all available VISA resources and serial ports, queries each instrument for its identity, and prints a summary table.

To force a specific VISA backend, pass `--backend`:

```bash
instro discover --backend=@py
instro discover --backend "@ivi"
```

:::{note}
The `@ivi` backend finds an available IVI-compliant backend on your system e.g. NI-VISA or Keysight IO. `@ivi` is more reliable for USB and GPIB instruments. The `@py` backend (`pyvisa-py`) is a pure-Python fallback that works without NI-VISA installed but may not detect all instruments.
:::

### Output

Before scanning, the command prints which VISA backend is active (`@ivi`, or `@py` when no IVI VISA is installed). On the `@py` backend it also lists any interfaces the scan cannot cover — for example `GPIB: unavailable — gpib_ctypes is installed but could not locate the gpib library (install NI-488.2 or linux-gpib)` — so you know when an empty result reflects a missing library rather than an empty bench. These notes are informational; the scan still runs and the command exits 0.

The command groups results into up to three tables.

**Recognized devices** — instruments with a known driver. The table shows the VISA resource address, instrument category, and the `instro` driver class to use.

**Serial devices** — serial ports detected by the OS. These are listed separately because serial instruments require manual configuration; `instro discover` cannot query them automatically.

**Unrecognized devices** — instruments that responded to `*IDN?` but did not match any known driver. The raw IDN response is shown so you can identify the device.

If no devices are found at all, the command prints a single error panel; on the `@py` backend the panel repeats any unavailable interfaces.

### Using a recognized device

Pass the resource address from the **Recognized devices** table directly to the driver's constructor:

```python
from instro.psu.drivers.bk_9115 import BK9115
from instro.psu import InstroPSU

psu = InstroPSU(name="psu", driver=BK9115("USB0::0x15EF::0x0099::INSTR"), num_channels=1)
psu.open()
```

### Unrecognized devices

If your instrument appears in the **Unrecognized devices** table, `instro` does not yet have a driver for it. See [Custom instruments](/library/custom-instruments.md) for how to author one, or [instro-contrib](/library/contrib.md) to check for community-contributed drivers.

### Discovering programmatically

`instro discover` is a thin wrapper around `scan_visa_resources`, which is also available directly from `instro.lib` for use in your own scripts:

```python
from instro.lib import scan_visa_resources

result = scan_visa_resources()

for info in result.instruments:
    print(info.resource, info.category, info.driver_name)

for unrecognized in result.unrecognized:
    print(unrecognized.resource, unrecognized.idn)

for error in result.errors:
    print(error.resource, error.hint or error.message)
```

`scan_visa_resources` accepts the same `backend` the CLI's `--backend` flag does, plus an optional `timeout` (seconds per instrument query, default `2`) that the CLI doesn't currently expose. It returns a `VisaScanResult` with `instruments` (`DiscoveredInstrument`), `unrecognized` (`VisaUnrecognizedInstrument`), and `errors` (`VisaScanError`) — the same data the CLI renders into the tables above.

Each `DiscoveredInstrument` can build what you need next, so a script never re-derives the driver from the resource string:

```python
from instro.psu import InstroPSU

psu_info = next(i for i in result.instruments if i.category == "psu")

# a ready-to-open driver (VisaConfig with the backend the scan was asked for)
psu = InstroPSU(name="psu", driver=psu_info.make_driver(), num_channels=psu_info.num_channels)

# or the ``driver`` block of a JSON config, for InstroPSU(config=...)
driver_block = psu_info.config_block()
```

`visa_config()` returns the `VisaConfig` on its own, and `driver_class()` the driver class, when you want to construct the driver yourself.
