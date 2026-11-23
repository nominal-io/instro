---
myst:
  html_meta:
    description: "Publish QCoDeS instrument commands and readings through any instro publisher without rewriting drivers"
---

# QCoDeS Instruments

{.lead}
Publish the commands you send to a [QCoDeS](https://microsoft.github.io/Qcodes/) instrument, and the values you read from it, through any instro publisher. Your drivers don't change.

You can use it in two ways:

- [`attach()`](#publish-from-existing-code) adds publishing to QCoDeS code you already have.
- [`QcodesInstrument`](#write-new-instro-code) wraps an instrument in new instro code, with tags and background polling.

Setting a parameter publishes a `Command` on `<name>.<channel>.cmd`, and getting one publishes a `Measurement` on `<name>.<channel>`. That includes parameter access inside driver methods. Channels take the parameter's path unless you [set your own](#channel-names). The [example notebook](/examples/notebooks/qcodes_publishing.md) runs all of this against QCoDeS's mock instruments.

## Install

```bash
uv add "instro[qcodes]"
```

## Publish from existing code

`attach()` adds publishing to an instrument you already use, with any list of instro [publishers](publishers.md). In a QCoDeS measurement script, attach before the measurement starts and close the recorder when it ends.

```python
from qcodes.instrument_drivers.Keithley import Keithley2450

from instro.lib import NominalCorePublisher
from instro.qcodes import attach

smu = Keithley2450("smu_driver", "USB0::0x05E6::0x2450::04567890::INSTR")
recorder = attach(smu, "smu", [NominalCorePublisher(dataset_rid="<dataset rid>")])

smu.source.voltage(1.0)  # existing code, publishes smu.source.voltage.cmd
print(smu.source.current())  # publishes smu.source.current

recorder.close()  # flushes and closes the publishers; the instrument stays connected
```

## Write new instro code

`QcodesInstrument` is an instro [`Instrument`](/library/concepts.md), so it also has tags, the background daemon and `get_channel()`.

```python
from qcodes.instrument_drivers.Keithley import Keithley2450

from instro.qcodes import QcodesInstrument

with QcodesInstrument(
    name="smu",
    instrument=Keithley2450("smu_driver", "USB0::0x05E6::0x2450::04567890::INSTR"),
    dataset_rid="<dataset rid>",
    poll=["source.current"],  # read on every background_interval
) as smu:
    smu.device.source.voltage(1.0)  # .device is the QCoDeS instrument
    smu.start()
    print(smu.get_channel("source.current", wait_for_new_samples=True).latest)
```

`close()` closes the QCoDeS instrument, which releases its connection and its QCoDeS name. It doesn't turn outputs off, so do that first if you want it. Pass `close_device=False` to keep the instrument open.

## Channel names

Every parameter the driver registers is published under its path from the instrument. Submodules and channels add a segment, and so do members of a channel list.

| QCoDeS | Published as |
|---|---|
| `smu.source.voltage(1.0)` | `smu.source.voltage.cmd` = `1.0` |
| `smu.source.current()` | `smu.source.current` |
| `smu.output_enabled(True)` | `smu.output_enabled.cmd` = `1.0` |
| a parameter with `val_mapping`, set to `"on"` | `….cmd` = `"on"`, the value you passed |
| a list of up to 16 values | `….0`, `….1`, `….2` |

The value you pass to `set` is the command, and the value `get` returns is the measurement, both before QCoDeS maps them to the instrument's wire format. A parameter without a get command returns the value you last set, and reading it publishes that value.

instro skips the root instrument's `IDN` and VISA `timeout` parameters, and arrays longer than 16 values, such as traces. It logs a warning the first time it skips an array.

To choose the names, generate a channel map, edit it, and pass it as `channel_map=`:

```bash
instro qcodes map Keithley.Keithley2450 --resource "USB0::0x05E6::0x2450::04567890::INSTR" -o smu_map.json
```

```json
{
  "format": "instro-qcodes-channel-map/1",
  "include_unmapped": false,
  "channels": {
    "source.voltage": {"channel": "out.v_set", "kind": "control", "unit": "V", "label": "Voltage"},
    "source.current": {"channel": null, "kind": "control", "unit": "A", "label": "Current"}
  }
}
```

```python
attach(smu, "smu", publishers, channel_map="smu_map.json")
```

Change an entry's `channel` to rename it, or set it to `null` to stop publishing it. `kind`, `unit` and `label` come from the driver and are for reference. Parameters missing from the map aren't published, unless you set `"include_unmapped": true`. instro rejects a map when you attach if it names a parameter the instrument doesn't have, gives two parameters one name, or uses a name ending in `.cmd`.

QCoDeS drivers register parameters when you construct them, so the command connects to the instrument. Pass `--resource` for the address, and `--arg NAME=VALUE` for any other constructor argument. In Python, call `ChannelMap.generate()` with the connected instrument. See [CLI](/cli.md#instro-qcodes) for the commands.

## Failures and cleanup

- A set or get that raises publishes nothing.
- A publisher that raises is logged. The set or get still completes, because the instrument has already acted.
- `close()` or `detach()` stops background polling and puts back the original `get` and `set` of every parameter.

See the [API reference](/ref/library/qcodes.md) for every option.
