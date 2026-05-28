"""A minimal custom `Instrument`, a simulated temperature controller whose reading lags the commanded setpoint."""

import random
import time

from instro.lib import Command, Instrument, Measurement
from instro.lib.instrument import publish_command, publish_measurement


class SimpleTempController(Instrument):
    def __init__(self, name: str, **kwargs):
        super().__init__(name, **kwargs)
        self._temperature_c = 20.0  # current temperature (starts at room temp)
        self._setpoint_c = 20.0  # commanded target
        self.background_interval = 0.5
        self.add_background_daemon_function(self.read_temperature)

    def open(self) -> None:
        # Establish your device connection here (open a socket, VISA session, etc.).
        pass

    def close(self) -> None:
        # `super().close()` stops the daemon and closes attached publishers.
        # Tear down your device connection after that.
        super().close()

    @publish_measurement
    def read_temperature(self, **kwargs) -> Measurement:
        # Each tick: move 20% of the way toward the setpoint, plus a little noise.
        # That lag is the "physics", enough to make the command visibly do work.
        drift = (self._setpoint_c - self._temperature_c) * 0.2
        self._temperature_c += drift + random.uniform(-0.1, 0.1)
        return self._package_measurement("temperature_c", self._temperature_c, time.time_ns(), **kwargs)

    @publish_command
    def set_target_temperature(self, value: float, **kwargs) -> Command:
        self._setpoint_c = value
        return self._package_command("setpoint_c.cmd", value, time.time_ns(), **kwargs)


def main() -> None:
    # `with` calls open() on entry and close() on exit. close() stops the daemon
    # and closes publishers even if an exception escapes the block.
    with SimpleTempController(name="controller") as controller:
        controller.start()

        for target in (25, 50, 75):
            controller.set_target_temperature(target)
            time.sleep(5)


if __name__ == "__main__":
    main()
