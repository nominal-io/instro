"""Example: stream every channel from a local ArduPilot SITL instance to Nominal.

Run:
    sim_vehicle.py -v ArduCopter                 # terminal 1: SITL, with MAVProxy output on UDP 14550
    uv run python packages/instro-unstable/instro/unstable/mavstream/examples/mavstream_sitl.py  # terminal 2
"""

import time

from instro.unstable.mavstream import InstroMavStream

from instro.lib.publishers import NominalCorePublisher

ADDRESS = "<ADD TCP OR UDP ADDRESS>"
DATASET_RID = "<ADD DATASET RID>"


def main() -> None:
    mav = InstroMavStream("sitl", address=ADDRESS, publishers=[NominalCorePublisher(DATASET_RID)])

    # No add_channel call, so the instrument streams every channel
    mav.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        mav.close()


if __name__ == "__main__":
    main()
