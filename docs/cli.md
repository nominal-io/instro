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

**Serial ports not identified** — serial ports the OS reports that discovery could not match to an instrument, with the reason. A serial port carries no identity of its own, so each USB serial adapter is probed once at the default serial settings (9600 baud, 8N1, no flow control). A port that does not answer is listed here with a note to configure its serial settings manually; discovery does not sweep baud rates or framings. Motherboard ports are skipped by default because each costs a timeout and may front non-SCPI equipment. The programmatic API lets you change the probed serial settings and the policy, exclude ports, and add addresses; the matching CLI flags are tracked in [#614](https://github.com/nominal-io/instro/issues/614).

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

report = discover(
    extra_resources=["TCPIP0::10.0.0.5::5025::SOCKET"],  # LAN sockets VISA doesn't enumerate
    exclude=["/dev/ttyS0"],                                # never probe this port
    probe_serial="usb",                                    # "usb" (default), "all", or "none"
    timeout=2.0,
)

for info in report.instruments:
    print(info.resource, info.category, info.driver_name, info.serial_config)

for unrecognized in report.unrecognized:
    print(unrecognized.resource, unrecognized.idn)

for error in report.errors:
    print(error.resource, error.hint or error.message)

for skipped in report.skipped:
    print(skipped.resource, skipped.reason)
```

`discover` also takes `backend` (what `--backend` sets), `serial_config` (a `SerialConfig` every serial port is probed with, defaulting to 9600 baud, 8N1), and `on_event`, a callback that receives a `DiscoveryEvent` before each probe and after each outcome, for progress output. A serial port that does not answer at the probed settings appears in `errors` with a hint to set its `serial_config` manually. It returns a `DiscoveryReport` with `instruments` (`DiscoveredInstrument`), `unrecognized`, `errors`, `skipped`, and the OS `serial_ports`; `report.by_category("psu")` filters the instruments.

Each `DiscoveredInstrument` can build what you need next, so a script never re-derives the driver from the resource string:

```python
from instro.psu import InstroPSU

psu_info = report.by_category("psu")[0]

# a ready-to-open driver (VisaConfig with the backend the scan was asked for)
psu = InstroPSU(name="psu", driver=psu_info.make_driver(), num_channels=psu_info.num_channels)

# or the ``driver`` block of a JSON config, for InstroPSU(config=...)
driver_block = psu_info.config_block()
```

`visa_config()` returns the `VisaConfig` on its own, and `driver_class()` the driver class, when you want to construct the driver yourself.

The layers under `discover` are public too: `enumerate_candidates()` lists what could be probed without touching anything, `identify(resource)` runs a single `*IDN?` probe (at the default or given serial settings on a serial port), and `match_idn(idn)` maps an identity string you already have to a driver. `scan_visa_resources()` remains as a VISA-only wrapper with serial probing disabled.

### Non-SCPI instruments

Discovery runs the *sources* you ask for. The default, `visa`, is the SCPI-over-VISA scan described above, serial ports included. Instruments reached through a vendor SDK rather than VISA (the NI, LabJack and MCC DAQs) get their own source names that you opt in to, with `discover(sources=["visa", "nidaq"])` or `sources=["all"]`; those sources ship with their packages. A record from such a source carries the SDK's identity string in `idn`, the vendor as `transport`, and a `driver_name` from `DAQ_VENDOR_REGISTRY`, so `driver_class()` and `make_driver()` work the same way: `make_driver()` passes `resource` straight to the driver constructor, which is the `device_id` the DAQ drivers take. `visa_config()` and `config_block()` raise `ValueError` for them, since there is no VISA connection or JSON schema to build.

The Keysight 34980A is a VISA instrument and is matched by `*IDN?` like everything else. Pass `providers=[...]` to `discover` to run a specific set of providers instead of named sources.
