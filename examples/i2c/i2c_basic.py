"""Example: I2C basic example.

Requires the Aardvark vendor package: install with ``uv sync --extra i2c``
(or ``pip install 'instro[i2c]'``).
"""

import time

from instro.i2c import I2CInterface
from instro.i2c.drivers.totalphase import Aardvark
from instro.i2c.types import SystemDefinition
from instro.lib.publishers import NominalCorePublisher

RESOURCE_ID = "2239-764425"

DATASET_RID = "<dataset_rid>"  # Replace with your dataset RID.


def my_system_definition() -> SystemDefinition:
    """Defines the peripherals on the I2C bus.

    Typically, you'd want to put this definition in a seperate file from your main app.
    """
    from instro.i2c.types import (
        DataFormat,
        FieldDef,
        RegisterDef,
        RegisterDevice,
    )

    # The following represents a PCA9506 GPIO exapander with
    # some LEDs wired to the IO lines on Port 4

    # LEDs
    port4_pin_0 = FieldDef(name="led_1", lsb=0, width_bits=1)
    port4_pin_1 = FieldDef(name="led_2", lsb=1, width_bits=1)
    port4_pin_2 = FieldDef(name="led_3", lsb=2, width_bits=1)
    port4_pin_3 = FieldDef(name="led_4", lsb=3, width_bits=1)
    port4_pin_4 = FieldDef(name="led_5", lsb=4, width_bits=1)

    port4_output_state = RegisterDef(
        alias="LED_OUTPUT_STATE",
        register=0x0C,
        default_value=0x00,
        format=DataFormat(transfer_bits=8),
        endianness="big",
        fields={
            port4_pin_0.name: port4_pin_0,
            port4_pin_1.name: port4_pin_1,
            port4_pin_2.name: port4_pin_2,
            port4_pin_3.name: port4_pin_3,
            port4_pin_4.name: port4_pin_4,
        },
    )

    port4_direction = RegisterDef(
        alias="LED_DIRECTION",
        register=0x1C,
        default_value=0x00,
        format=DataFormat(transfer_bits=8),
        endianness="big",
        fields={
            port4_pin_0.name: port4_pin_0,
            port4_pin_1.name: port4_pin_1,
            port4_pin_2.name: port4_pin_2,
            port4_pin_3.name: port4_pin_3,
            port4_pin_4.name: port4_pin_4,
        },
    )

    gpio = RegisterDevice(
        name="power_gpio",
        address=0x21,
        registers={
            port4_direction.alias: port4_direction,
            port4_output_state.alias: port4_output_state,
        },
    )

    return SystemDefinition(
        devices={
            gpio.name: gpio,
        }
    )


# This is the main app. It turns on and off an LED that's connected to a GPIO expander.

# A system_definition is passed in which describes the
# peripherals on the I2C bus.

system_definition = my_system_definition()

i2c = I2CInterface(
    name="myI2C",
    driver=Aardvark(serial_number=RESOURCE_ID),
    system_definition=system_definition,
)
i2c.add_publisher(NominalCorePublisher(dataset_rid=DATASET_RID))

with i2c:
    i2c.write("power_gpio", "LED_DIRECTION", 0x00)
    i2c.write("power_gpio", "LED_OUTPUT_STATE", 0xFF)

    state = True
    for _ in range(10):
        state = not state
        for i in range(1, 6):
            i2c.write("power_gpio", "LED_OUTPUT_STATE", int(state), f"led_{i}")
            time.sleep(0.25)
