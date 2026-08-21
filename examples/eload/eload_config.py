"""Example: build an InstroELoad from a JSON config file.

Edit eload_config_bk85xxb.json (visa_resource, dataset_rid) for your bench
setup before running:
    uv run python examples/eload/eload_config.py
"""

import time
from pathlib import Path

from instro.eload import InstroELoad

CONFIG_PATH = Path(__file__).parent / "eload_config_bk85xxb.json"


def main() -> None:
    # The config's `load` block pre-arms the mode, level, range, and slew rate
    # on open(); enabling the input stays an explicit runtime call.
    with InstroELoad(config=CONFIG_PATH) as eload:
        eload.output_enable(True)
        time.sleep(0.5)

        voltage = eload.get_voltage()
        current = eload.get_current()
        print(f"{eload.name}: {voltage.latest:.3f}V, {current.latest:.3f}A")

        eload.output_enable(False)


if __name__ == "__main__":
    main()
