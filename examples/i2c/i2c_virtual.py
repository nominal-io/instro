"""Example: Read and write a virtual I2C bus, with no hardware or SDK.

VirtualI2C simulates the bus in-process, so this runs as-is. Swap in a real
adapter driver (Aardvark, ...) and the rest is unchanged.
"""

from instro.i2c import I2CInterface
from instro.i2c.drivers import VirtualI2C
from instro.i2c.types import FieldDef, RegisterDef, RegisterDevice, SystemDefinition

leds = {f"led_{i}": FieldDef(name=f"led_{i}", lsb=i - 1) for i in range(1, 6)}
output = RegisterDef(alias="LED_OUTPUT_STATE", register=0x0C, default_value=0x00, fields=leds)
gpio = RegisterDevice(name="power_gpio", address=0x21, registers={output.alias: output})
system = SystemDefinition(devices={gpio.name: gpio})

driver = VirtualI2C({0x21: {0x0C: b"\x1f"}})  # optional: which addresses answer, and their registers

with I2CInterface(name="myI2C", driver=driver, system_definition=system) as i2c:
    print(f"Output state: {i2c.read('power_gpio', 'LED_OUTPUT_STATE').latest:.0f}")
    i2c.write("power_gpio", "LED_OUTPUT_STATE", 0, field="led_3")
    print(f"LED 3: {i2c.read('power_gpio', 'LED_OUTPUT_STATE', field='led_3').latest:.0f}")
    i2c.reset_reg("power_gpio", "LED_OUTPUT_STATE")
    print(f"After reset: {i2c.read('power_gpio', 'LED_OUTPUT_STATE').latest:.0f}")
