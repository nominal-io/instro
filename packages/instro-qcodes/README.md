# instro-qcodes

Publish [QCoDeS](https://microsoft.github.io/Qcodes/) instrument commands and readings through any [instro](https://github.com/nominal-io/instro) publisher. Your drivers don't change.

This package is pre-1.0: its API may change between minor releases.

Setting a parameter publishes a command on `<name>.<channel>.cmd`. Getting one publishes a measurement on `<name>.<channel>`.

```bash
pip install "instro[qcodes]"
```

```python
from qcodes.instrument_drivers.mock_instruments import DummyInstrument

from instro.lib import NominalCorePublisher
from instro.qcodes import attach

dac = DummyInstrument("dac_driver", gates=["g1", "g2"])
recorder = attach(dac, "dac", [NominalCorePublisher(dataset_rid="<dataset rid>")])

dac.g1(1.5)      # publishes dac.g1.cmd
print(dac.g1())  # publishes dac.g1

recorder.close()
```

See the [guide](https://instro.nominal.io/library/qcodes) for `QcodesInstrument`, channel maps and polling, and the [example notebook](https://github.com/nominal-io/instro/blob/main/examples/notebooks/qcodes_publishing.md) for a version that runs without hardware.
