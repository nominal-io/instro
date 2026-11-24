"""Example: stream every channel from a local ArduPilot SITL instance to Nominal.

Run:
    sim_vehicle.py -v ArduCopter                 # terminal 1: SITL, with MAVProxy output on UDP 14550
    uv run python packages/instro-unstable/instro/unstable/mavstream/examples/mavstream_sitl.py  # terminal 2
"""

import time

from instro.lib.publishers import NominalCorePublisher
from instro.unstable.mavstream import InstroMavStream

ADDRESS = "udp:127.0.0.1:14551"
DATASET_RID = "ri.catalog.cerulean-staging.dataset.90315db1-049a-49e8-9e02-38c3be36b802"


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
