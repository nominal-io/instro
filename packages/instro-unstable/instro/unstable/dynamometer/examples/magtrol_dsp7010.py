"""Example: read a Magtrol DSP7010, apply a light torque load, then release it.

Connect the controller over USB (it shows up as a COM port) or GPIB, with a motor running
on channel 1. Alarm settings are left as configured on the controller.

Run:
    uv run python packages/instro-unstable/instro/unstable/dynamometer/examples/magtrol_dsp7010.py ASRL3::INSTR
"""

import sys
import time

from instro.unstable.dynamometer.drivers import MagtrolDSP7010, TorqueUnit

RESOURCE = sys.argv[1] if len(sys.argv) > 1 else "ASRL3::INSTR"  # GPIB default is "GPIB0::9::INSTR"
CHANNEL = 1
LOAD_NM = 0.5

dyno = MagtrolDSP7010(RESOURCE)
dyno.open()
try:
    print(dyno.identify())

    dyno.set_torque_units(CHANNEL, TorqueUnit.N_M)

    dyno.set_torque(CHANNEL, LOAD_NM)  # brake on
    try:
        for _ in range(10):
            reading = dyno.read_data(CHANNEL).channel_data
            data = {key.rsplit(".", 1)[-1]: values[0] for key, values in reading.items()}
            print(
                f"speed={data['speed']:.0f} rpm  torque={data['torque']:.3f} Nm  "
                f"power={data['power']:.1f} W  alarm={data['alarm']:.0f}"
            )
            time.sleep(0.5)
    finally:
        dyno.brake_off(CHANNEL)

    status = dyno.get_status().channel_data
    print("limit alarms:", [key for key, values in status.items() if ".alarm." in key and values[0]] or "none")
finally:
    dyno.close()
