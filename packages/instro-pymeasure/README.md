# instro-pymeasure

Publish [pymeasure](https://pymeasure.readthedocs.io) instrument commands and readings through any [instro](https://github.com/nominal-io/instro) publisher. Your drivers don't change.

Setting a property publishes a command on `<name>.<channel>.cmd`. Reading one publishes a measurement on `<name>.<channel>`.

```bash
pip install "instro[pymeasure]"
```

```python
from pymeasure.instruments.keithley import Keithley2400

from instro.lib import NominalCorePublisher
from instro.pymeasure import attach

keithley = Keithley2400("GPIB::24")
recorder = attach(keithley, "smu", [NominalCorePublisher(dataset_rid="<dataset rid>")])

keithley.source_voltage = 1.0  # publishes smu.source_voltage.cmd
print(keithley.current)        # publishes smu.current

recorder.close()
```

See the [guide](https://instro.nominal.io/library/pymeasure) for `PymeasureInstrument`, channel maps and polling, and the [example notebook](https://github.com/nominal-io/instro/blob/main/examples/notebooks/pymeasure_publishing.md) for a version that runs without hardware.
