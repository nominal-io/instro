"""Example: I2C bus from a JSON config.

``sensor_bus.json`` (next to this script) describes the adapter, a PCA9506 GPIO
expander, and an LTC2309 ADC. The config replaces the programmatic
``SystemDefinition`` used in ``i2c_basic.py`` and ``i2c_command.py``.

Requires the Aardvark vendor package: install with ``uv sync --extra i2c``
(or ``pip install 'instro[i2c]'``).
"""

import time
from pathlib import Path

from instro.i2c import I2CInterface
from instro.i2c.types import CustomScaling
from instro.lib.publishers import NominalCorePublisher

DATASET_RID = "<dataset_rid>"  # Replace with your dataset RID.

# The driver comes from the config's `connection` block. Pass driver=Aardvark(...)
# to override it, e.g. to share one config across benches with different adapters.
i2c = I2CInterface(config=Path(__file__).with_name("sensor_bus.json"))
i2c.add_publisher(NominalCorePublisher(dataset_rid=DATASET_RID))

# Scaling that JSON can't express is attached at runtime, after loading the config.
i2c.set_scaling("VOLTAGE_ADC", CustomScaling(to_physical_fn=lambda raw: raw / 4095 * 5 * 7.2))

with i2c:
    i2c.write("power_gpio", "LED_DIRECTION", 0x00)

    # Registers and batch commands marked `poll: true` are read in the background
    # at `timing.poll_interval`.
    i2c.start()

    state = True
    for _ in range(10):
        state = not state
        for n in range(1, 6):
            i2c.write("power_gpio", "LED_OUTPUT_STATE", int(state), f"led_{n}")
            time.sleep(0.25)

    print(i2c.query("VOLTAGE_ADC", "ch1"))
    print(i2c.get_channel("VOLTAGE_ADC.ch0"))
