# instro-pymeasure

Publish [pymeasure](https://pymeasure.readthedocs.io) instrument traffic to [Nominal](https://nominal.io) through
[instro](https://github.com/nominal-io/instro) publishers, without rewriting drivers.

When you set a property on a wrapped instrument, the package publishes a command on `<name>.<channel>.cmd`.
When you read one, it publishes a measurement on `<name>.<channel>`.

```bash
pip install "instro[pymeasure]"
```

## Existing pymeasure code

```python
from pymeasure.instruments.keithley import Keithley2400
from instro.lib.publishers import NominalCorePublisher
from instro.pymeasure import attach

keithley = Keithley2400("GPIB::24")
recorder = attach(keithley, "smu", [NominalCorePublisher(dataset_rid="ri.catalog....")])
keithley.source_voltage = 1.0   # publishes smu.source_voltage.cmd
print(keithley.current)         # publishes smu.current
recorder.close()
```

## instro-first code

```python
from instro.pymeasure import PymeasureInstrument

with PymeasureInstrument("psu", KeysightE36312A(resource), dataset_rid="ri.catalog....",
                         poll=["ch_1.voltage", "ch_1.current"]) as psu:
    psu.device.ch_1.voltage_setpoint = 5
    psu.start()
```

## Channel names

```bash
instro pymeasure map keysight.KeysightE36312A -o psu_map.json   # generate once, edit, then:
```

```python
attach(supply, "psu", publishers, channel_map="psu_map.json")
```

See the [guide](https://instro.nominal.io/library/pymeasure) and the
[example notebook](https://github.com/nominal-io/instro/blob/main/examples/notebooks/pymeasure_publishing.md).
