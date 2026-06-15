"""Example: Test rack end-to-end demo.

Demonstrates publishing measurements/commands to a dataset (Nominal Core publisher).

"""

import time

from instro.daq import InstroDAQ
from instro.daq.drivers import Keysight34980A
from instro.daq.types import Direction
from instro.eload import InstroELoad
from instro.eload.drivers.bk_85xxb import BK85XXB
from instro.eload.types import LoadMode
from instro.lib.transports import SerialConfig, VisaConfig
from instro.psu import InstroPSU
from instro.psu.drivers import BK9115

# Demo assumes a power supply output is connected to the inputs of an electronic load.
# Demo assumes there is a DAQ monitoring this line.

DATASET_RID = "<dataset_rid>"  # Replace with your dataset RID.


def main():
    # ====== SETUP =========
    # Define instruments
    daq = InstroDAQ(
        name="myDAQ",
        driver=Keysight34980A("USB0::0x0957::0x0507::MY44001757::INSTR"),
        dataset_rid=DATASET_RID,
    )
    psu = InstroPSU(
        name="myPSU",
        driver=BK9115("USB0::0xFFFF::0x9115::800422020766920015::INSTR"),
        num_channels=2,
        dataset_rid=DATASET_RID,
    )
    eload = InstroELoad(
        name="myELoad",
        driver=BK85XXB(
            VisaConfig(
                visa_resource="ASRL19::INSTR",
                serial_config=SerialConfig(baud_rate=9600),
            )
        ),
        dataset_rid=DATASET_RID,
    )

    # The context manager opens each instrument on entry and closes them on exit,
    # including if the block raises.
    with daq, psu, eload:
        try:
            # Configure DAQ
            daq.configure_analog_channel(direction=Direction.INPUT, physical_channel="1010", alias="psu_v")
            daq.configure_ai_sample_rate(sample_rate=1000)

            # Start the DAQ to monitor the power supply/eload prior to using it
            daq.start()

            time.sleep(2)  # Let's chill...for fun. Grab a drink

            # Ensure powersupply is in a known state
            psu.output_enable(False, channel=1)
            psu.set_voltage(0, channel=1)
            psu.set_current_limit(1.5, channel=1)

            time.sleep(2)  # More fun. More drinks.

            # Ensure eload is in a known state
            eload.output_enable(False)
            eload.set_mode(LoadMode.CR)  # Constant Resistance
            eload.set_level(10)  # Ohms

            # Start the power supply and eload monitoring
            psu.background_interval = 0.1
            eload.background_interval = 0.1
            psu.start()
            eload.start()

            time.sleep(2)  # More time to drink and chill

            # Power up the power supply
            psu.output_enable(True, channel=1)

            time.sleep(2)  # Drink up, it's time to do work.

            # ========= THE TEST =========
            # Ramp up the PSU voltage
            eload.output_enable(True)
            for voltage in range(10):
                psu.set_voltage(voltage, channel=1)
                time.sleep(1)

            # Now keep the psu the same level and ramp up the eload resistance
            for resistance in range(10, 100, 10):
                eload.set_level(resistance)
                time.sleep(1)
            # ========== END TEST =======

        finally:
            # Put the test stand into a safe state before the instruments close.
            eload.output_enable(False)
            psu.output_enable(False, channel=1)

            time.sleep(3)  # give space so that shutdown is observed


if __name__ == "__main__":
    main()
