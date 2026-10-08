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

Scans VISA resources and serial ports, queries each instrument for its identity, and prints a summary table.

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

The command groups results into up to four tables.

**Recognized devices** — instruments with a known driver. The table shows the VISA resource address, instrument category, and the `instro` driver class to use. A serial instrument also shows the serial settings it answered with.

**Serial ports not identified** — serial ports that did not answer `*IDN?` at the default serial settings (9600 baud, 8N1, no flow control), with the reason. A serial port carries no identity of its own, so a port that does not answer is listed here with a note to configure its serial settings manually.

**Unrecognized devices** — instruments that responded to `*IDN?` but did not match any known driver. The raw IDN response is shown so you can identify the device.

**Errors** — non-serial resources that were listed but could not be identified, with an actionable hint where one is known.

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

`instro discover` is a renderer over `instro.lib.discover.discover`, which your own scripts and test fixtures can call directly:

```python
from instro.lib.discover import discover

report = discover(extra_resources=["TCPIP0::10.0.0.5::5025::SOCKET"])  # LAN sockets VISA doesn't enumerate

for info in report.instruments:
    print(info.resource, info.category, info.driver_name)

for error in report.errors:
    print(error.resource, error.hint or error.message)
```

`discover` returns a `DiscoveryReport` with `instruments`, `unrecognized`, `errors`, `skipped`, and the OS `serial_ports`; `report.by_category("psu")` filters the instruments. Its keyword arguments cover the backend, extra resources, ports to exclude, the serial probe policy and settings, the per-probe timeout, which discovery `sources` run (only `visa` exists today), and an `on_event` progress callback; see the [API reference](/sdk/library/discover.md).

Each `DiscoveredInstrument` builds what you need next, so a script never re-derives the driver from the resource string:

```python
from instro.psu import InstroPSU

psu_info = report.by_category("psu")[0]
psu = InstroPSU(name="psu", driver=psu_info.make_driver(), num_channels=psu_info.num_channels)
driver_block = psu_info.config_block()  # the ``driver`` block of a JSON config, for InstroPSU(config=...)
```

`visa_config()` and `driver_class()` give you the pieces when you want to construct the driver yourself. The layers under `discover` are public too: `enumerate_candidates()` lists what could be probed without touching anything, `identify(resource)` runs a single `*IDN?` probe, `match_idn(idn)` maps an identity string you already have to a driver, and `scan_visa_resources()` remains as a VISA-only wrapper with serial probing disabled.
