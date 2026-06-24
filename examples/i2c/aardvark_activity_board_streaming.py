"""Example: stream dense I2C time-series from a Total Phase Aardvark to Nominal Core.

Drives the PCA9554 I/O expander on a Total Phase I2C/SPI Activity Board with a
continuous sine waveform (0-255) and reads it back on every background-daemon
tick, publishing both the commanded and read-back values to a Nominal Core
dataset. The daemon runs at a fixed rate, so the result is a dense, non-sparse
time series: in Core you should see the commanded (``pca9554.output.cmd``) and
read-back (``pca9554.output``) channels trace the same clean sine, confirming the
I2C write/read roundtrip is faithful.

The dataset RID is read from the ``NOMINAL_DATASET_RID`` environment variable, so
no RID is hard-coded here. Nominal credentials come from the on-disk ``default``
profile (see https://docs.nominal.io/core/sdk/python-client/authentication).

Requires the Aardvark vendor package: install with ``uv sync --extra i2c``.

Run:
    NOMINAL_DATASET_RID=<rid> uv run --extra i2c python examples/i2c/aardvark_activity_board_streaming.py
"""

import math
import os
import time

from instro.i2c import I2CInterface, RegisterDevice, SystemDefinition
from instro.i2c.drivers.totalphase import Aardvark
from instro.i2c.types import DataFormat, RegisterDef
from instro.lib.publishers import NominalCorePublisher

SERIAL_NUMBER: str | None = None  # None selects the first available adapter.
BITRATE_KHZ = 100
SAMPLE_RATE_HZ = 20  # Background-daemon publish rate; high enough for dense plots.
WAVEFORM_HZ = 0.2  # Sine period ~5 s, so a run shows several clean cycles.
DURATION_S = 60  # How long to stream before stopping.

PCA9554_ADDRESS = 0x38
PCA9554_OUTPUT_REG = 0x01
PCA9554_CONFIG_REG = 0x03


def activity_board_system() -> SystemDefinition:
    """SystemDefinition for the PCA9554 I/O expander on the Activity Board."""
    pca9554 = RegisterDevice(
        name="pca9554",
        address=PCA9554_ADDRESS,
        addr_width_bytes=1,
        registers={
            "config": RegisterDef(
                alias="config", register=PCA9554_CONFIG_REG, default_value=0xFF, format=DataFormat(transfer_bits=8)
            ),
            "output": RegisterDef(
                alias="output", register=PCA9554_OUTPUT_REG, default_value=0xFF, format=DataFormat(transfer_bits=8)
            ),
        },
    )
    return SystemDefinition(devices={pca9554.name: pca9554})


def stream_tick(i2c: I2CInterface) -> None:
    """One acquisition tick: command a sine sample, write it, read it back (both publish)."""
    value = round(127.5 + 127.5 * math.sin(2 * math.pi * WAVEFORM_HZ * time.time()))
    value = max(0, min(255, value))
    i2c.write("pca9554", "output", value)  # publishes pca9554.output.cmd
    i2c.read("pca9554", "output")  # publishes pca9554.output (read-back)


def main() -> None:
    dataset_rid = os.environ.get("NOMINAL_DATASET_RID")
    if not dataset_rid:
        raise SystemExit("Set NOMINAL_DATASET_RID to the target Nominal dataset RID before running.")

    i2c = I2CInterface(
        name="aardvark_demo",
        driver=Aardvark(serial_number=SERIAL_NUMBER),
        system_definition=activity_board_system(),
        publishers=[NominalCorePublisher(dataset_rid=dataset_rid)],
    )
    i2c.background_interval = 1.0 / SAMPLE_RATE_HZ
    i2c.add_background_daemon_function(stream_tick, i2c)

    with i2c:
        # Bus-level config (bitrate/pull-ups/target power) lives on the driver,
        # not the HAL; the Activity Board is powered from the Aardvark.
        driver: Aardvark = i2c._driver  # type: ignore[assignment]
        driver.set_bitrate(BITRATE_KHZ)
        driver.set_pullups(True)
        driver.set_power_enable(True)
        time.sleep(0.2)  # let the board power up
        i2c.write("pca9554", "config", 0x00)  # all lines outputs so the LEDs animate

        print(
            f"Streaming PCA9554 sine to dataset {dataset_rid} at {SAMPLE_RATE_HZ} Hz for {DURATION_S}s (Ctrl-C to stop)..."
        )
        try:
            i2c.start()
            time.sleep(DURATION_S)
        except KeyboardInterrupt:
            print("Interrupted; stopping.")
        finally:
            i2c.stop()  # stop the daemon before touching the bus again
            i2c.write("pca9554", "output", 0xFF)  # LEDs to default state
            i2c.write("pca9554", "config", 0xFF)  # restore power-on default (all inputs)
            driver.set_power_enable(False)
    print("Done.")


if __name__ == "__main__":
    main()
