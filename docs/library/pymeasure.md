---
myst:
  html_meta:
    description: "Publish pymeasure instrument commands and readings to Nominal without rewriting drivers"
---

# pymeasure instruments

{.lead}
Publish pymeasure instrument commands and readings to Nominal without rewriting drivers

[`instro-pymeasure`](https://github.com/nominal-io/instro/tree/main/packages/instro-pymeasure) wraps any
[pymeasure](https://pymeasure.readthedocs.io) instrument so that its traffic goes through instro publishers:

- Every property **set**, such as `supply.ch_1.voltage_setpoint = 5`, publishes a `Command` on `<name>.<channel>.cmd`.
- Every property **read**, such as `supply.ch_1.current`, publishes a `Measurement` on `<name>.<channel>`.

Property access inside the driver's own methods (`apply_voltage()`, `ramp_to_current()`, …) is published too.

## Install

```bash
uv add "instro[pymeasure]"
```

## Existing pymeasure code

`attach()` adds publishing to an instrument your code already uses. The rest of your code doesn't change:

```python
from pymeasure.instruments.keithley import Keithley2400

from instro.lib.publishers import NominalCorePublisher
from instro.pymeasure import attach

keithley = Keithley2400("GPIB::24")
recorder = attach(keithley, "smu", [NominalCorePublisher(dataset_rid="ri.catalog....")])

keithley.apply_voltage()             # existing code, unchanged
keithley.source_voltage = 1.0        # publishes smu.source_voltage.cmd
print(keithley.current)              # publishes smu.current

recorder.close()                     # flushes and closes the publishers; the instrument stays connected
```

In a pymeasure `Procedure`, attach in `startup()` and close the recorder in `shutdown()`.

## instro-first code

`PymeasureInstrument` is an instro [`Instrument`](/library/concepts.md), with publishers, tags, `dataset_rid=`,
the background daemon and `get_channel()`:

- **The pymeasure object** is available as `.device`. Pass `device_attr="smu"` to also reach it as `.smu`.
- **`poll`** lists properties the daemon reads on every `background_interval`.
- **`close()`** also closes the instrument's connection, unless you pass `close_device=False`. It doesn't call the
  driver's `shutdown()`, which on many drivers turns outputs off. Call `psu.device.shutdown()` first if you want that.

```python
from pymeasure.instruments.keysight import KeysightE36312A

from instro.pymeasure import PymeasureInstrument

with PymeasureInstrument(
    name="psu",
    instrument=KeysightE36312A("USB0::0x2A8D::0x1102::MY12345678::INSTR"),
    dataset_rid="ri.catalog....",
    poll=["ch_1.voltage", "ch_1.current"],
) as psu:
    psu.device.ch_1.voltage_setpoint = 5
    psu.start()
    print(psu.get_channel("ch1.current", wait_for_new_samples=True).latest)
```

## Channel names

Without a channel map, every property the driver defines is published under its attribute path.

| pymeasure | Published as |
|---|---|
| `supply.ch_1.voltage_setpoint = 5` | `psu.ch1.voltage_setpoint.cmd` = `5.0` |
| `supply.ch_1.current` | `psu.ch1.current` |
| `supply.ch_1.output_enabled` (bool) | `psu.ch1.output_enabled` = `1.0` / `0.0` |
| a string or mapped value | a string channel |
| a list of up to 16 values (`"1,2,3"`) | `….0`, `….1`, `….2` |

Some properties are skipped:

- Housekeeping properties inherited from pymeasure's base classes (`id`, `status`, `next_error`, …).
- Longer arrays such as traces and buffers. A warning is logged once for each.

To choose your own names, generate a channel map, edit it, and pass it in. In Python or a notebook:

```python
from instro.pymeasure import ChannelMap

channel_map = ChannelMap.generate(supply)                 # every property under its default name
channel_map.select("ch_1.*").rename({"ch_1.voltage_setpoint": "out1.v_set"})
pd.DataFrame(channel_map.preview(supply, name="psu"))     # command and measurement channel per property
channel_map.save("psu_map.json", supply)                  # commit it; ChannelMap.load() reads it back
```

Or from a terminal:

```bash
instro pymeasure map keysight.KeysightE36312A -o psu_map.json
```

```json
{
  "format": "instro-pymeasure-channel-map/1",
  "include_unmapped": false,
  "channels": {
    "ch_1.voltage_setpoint": {"channel": "out1.v_set", "kind": "control", "get": "VOLT? (@{ch})", "set": "VOLT %g, (@{ch})"},
    "ch_2.voltage_setpoint": {"channel": null, "kind": "control", "get": "VOLT? (@{ch})", "set": "VOLT %g, (@{ch})"}
  }
}
```

```python
attach(supply, "psu", publishers, channel_map="psu_map.json")
```

- **Rename an entry** by changing its `channel`.
- **Stop publishing an entry** by setting `channel` to `null`.
- **Entries missing from the map** aren't published, unless you set `"include_unmapped": true`.
- **Mistakes are rejected when you attach:** a path the instrument doesn't have (for example, renamed by a pymeasure upgrade), two properties sharing a name, or a name ending in `.cmd`.

Some drivers add channels in `__init__` rather than with `ChannelCreator`. For those, pass `--resource <VISA resource>`
to inspect a connected instance. `ChannelMap.generate(instrument)` does the same from Python.
See [CLI](/cli.md#instro-pymeasure) for the commands.

## How it works

`attach()` swaps the instrument's class (and each channel's) for a generated subclass. In that subclass, every
property delegates to the original pymeasure property and then publishes what it read or wrote.

- **Behaviour:** `isinstance` still holds, and `DynamicProperty` and validators behave as before.
- **Errors:** a set or read that raises publishes nothing. A publisher that raises is logged, and the set or read
  still completes, since the instrument has already acted.
- **Threads:** a lock serializes the instrument's property access and method calls, so background polling
  doesn't interleave with your code on the same connection.
- **Undoing it:** `close()` (or `detach()`) stops background polling and restores the original classes.

The example notebook
[`examples/pymeasure/pymeasure_publishing.md`](https://github.com/nominal-io/instro/blob/main/examples/pymeasure/pymeasure_publishing.md)
walks through all of this against a simulated supply.

See the [API reference](/ref/library/pymeasure.md).
