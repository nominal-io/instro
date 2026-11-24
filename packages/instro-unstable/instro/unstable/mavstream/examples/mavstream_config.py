"""Example: build an InstroMavStream from a JSON config file and stream the configured channels to Nominal.

Run:
    mavproxy.py --master tcp:127.0.0.1:5760 --out udp:127.0.0.1:14551   # terminal 1: forward SITL to UDP 14551
    uv run python packages/instro-unstable/instro/unstable/mavstream/examples/mavstream_config.py  # terminal 2

Edit the dataset_rid in mavstream_config_sitl.json first.
"""

import time
from pathlib import Path

from instro.unstable.mavstream import InstroMavStream

CONFIG_PATH = Path(__file__).parent / "mavstream_config_sitl.json"


def main() -> None:
    # autostart opens the connection and starts the stream
    mav = InstroMavStream(config=CONFIG_PATH, autostart=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        mav.close()


if __name__ == "__main__":
    main()
