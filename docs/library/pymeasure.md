---
myst:
  html_meta:
    description: "Publish pymeasure instrument commands and readings through any instro publisher without rewriting drivers"
---

# Pymeasure Instruments

{.lead}
`instro-pymeasure` publishes the commands you send to a [pymeasure](https://pymeasure.readthedocs.io) instrument, and the values you read from it, through any instro publisher. Your drivers don't change.

There are two ways to use it. [Attach to code you already have](#publish-from-existing-code), or [write new code around `PymeasureInstrument`](#write-new-instro-code), which adds tags and background polling. Either way, setting a property publishes a `Command` on `<name>.<channel>.cmd`, and reading one publishes a `Measurement` on `<name>.<channel>`. Property access inside driver methods, such as `apply_voltage()`, is published too.

Each property keeps its attribute path as its channel name. A [channel map](#channel-names) gives properties custom names or leaves some out. The [example notebook](/examples/notebooks/pymeasure_publishing.md) runs each of these against simulated instruments, so you can try them without hardware. [Failures and cleanup](#failures-and-cleanup) describes what happens when a read, a set or a publisher raises.

## Install

```bash
uv add "instro[pymeasure]"
```

## Publish from existing code

`attach()` adds publishing to an instrument you already use, with any list of instro [publishers](publishers.md). In a pymeasure `Procedure`, attach in `startup()` and close the recorder in `shutdown()`.

```python
from pymeasure.instruments.keithley import Keithley2400

from instro.lib import NominalCorePublisher
from instro.pymeasure import attach

keithley = Keithley2400("GPIB::24")
recorder = attach(keithley, "smu", [NominalCorePublisher(dataset_rid="<dataset rid>")])

keithley.apply_voltage()       # existing code, unchanged
keithley.source_voltage = 1.0  # publishes smu.source_voltage.cmd
print(keithley.current)        # publishes smu.current

recorder.close()  # flushes and closes the publishers; the instrument stays connected
```

## Write new instro code

`PymeasureInstrument` is an instro [`Instrument`](/library/concepts.md), so it also has tags, the background daemon and `get_channel()`.

```python
from pymeasure.instruments.keysight import KeysightE36312A

from instro.pymeasure import PymeasureInstrument

with PymeasureInstrument(
    name="psu",
    instrument=KeysightE36312A("USB0::0x2A8D::0x1102::MY12345678::INSTR"),
    dataset_rid="<dataset rid>",
    poll=["ch_1.voltage", "ch_1.current"],  # read on every background_interval
) as psu:
    psu.device.ch_1.voltage_setpoint = 5  # .device is the pymeasure instrument
    psu.start()
    print(psu.get_channel("ch1.current", wait_for_new_samples=True).latest)
```

`close()` closes the instrument's connection. It doesn't call the driver's `shutdown()`, which on many drivers turns outputs off, so call `psu.device.shutdown()` first if you want that. Pass `close_device=False` to keep the connection open.

## Channel names

Every property the driver defines is published under its attribute path.

| pymeasure | Published as |
|---|---|
| `supply.ch_1.voltage_setpoint = 5` | `psu.ch1.voltage_setpoint.cmd` = `5.0` |
| `supply.ch_1.current` | `psu.ch1.current` |
| `supply.ch_1.output_enabled` (bool) | `psu.ch1.output_enabled` = `1.0` or `0.0` |
| a string or mapped value | a string channel |
| a list of up to 16 values (`"1,2,3"`) | `….0`, `….1`, `….2` |

instro skips properties inherited from pymeasure's base classes (`id`, `status`, `next_error`) and arrays longer than 16 values, such as traces. It logs a warning the first time it skips an array.

To choose the names, generate a channel map, edit it, and pass it as `channel_map=`:

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

Change an entry's `channel` to rename it, or set it to `null` to stop publishing it. Properties missing from the map aren't published, unless you set `"include_unmapped": true`. instro rejects a map when you attach if it names a property the instrument doesn't have, gives two properties one name, or uses a name ending in `.cmd`.

Some drivers add channels in `__init__` instead of with `ChannelCreator`. For those, add `--resource <VISA resource>` to the command, or call `ChannelMap.generate()` with the connected instrument. The notebook below builds a map in Python. See [CLI](/cli.md#instro-pymeasure) for the commands.

## Failures and cleanup

- A set or read that raises publishes nothing.
- A publisher that raises is logged. The set or read still completes, because the instrument has already acted.
- `close()` or `detach()` stops background polling and restores the original classes.

See the [API reference](/ref/library/pymeasure.md) for every option.
