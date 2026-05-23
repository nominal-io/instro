"""In-process simulated PSU SCPI server (SCPI/VISA transport)."""

import logging
import random
import socket
import time
from typing import Any, Callable, cast

# TODO
# Create some error conditions for when Ohms law is violated, e.g. OVP, UVL, OCP, etc.

logger = logging.getLogger(__name__)


def add_noise(value: float, percent: float) -> float:
    std_dev = value * percent / 3
    return random.gauss(value, std_dev)


class SimulatedPSU:
    id = "NOMINAL,SIMULATED_PSU,1.0"

    error_codes: dict[int, str] = {
        0: "No error",
        -100: "Command error",
        -101: "Invalid Character",
        -102: "Syntax error",
        -104: "Data type error",
        -109: "Missing parameter",
        -112: "Program word too long",
        -131: "Invalid Suffix",
        -222: "Data out of range",
        -241: "Hardware Missing",
        -350: "Execution error",
        +300: "Execution error",
        +301: "PV above OVP",
        +302: "PV below UVL",
        +304: "OVP below PV",
        +306: "UVL above PV",
        +307: "On during fault",
        +320: "Fault shutdown",
        +321: "AC fault shutdown",
        +322: "OVP shutdown",
        +323: "UVL shutdown",
        +324: "Foldback shutdown",
        +325: "OTP shutdown",
        +326: "Output-Off shutdown",
        +327: "Enable Open shutdown",
        +340: "Internal message fault",
        +341: "Input overflow",
        +342: "Internal overflow",
        +343: "Internal timeout",
        +344: "Internal checksum",
        +345: "Internal checksum error",
        +399: "Unknown Error",
    }

    def __init__(self):
        self._voltage_ch1: float = 0
        self._voltage_ch2: float = 0
        self._current_ch1: float = 0
        self._current_ch2: float = 0
        self._voltage_limit_ch1: float = 0
        self._voltage_limit_ch2: float = 0
        self._current_limit_ch1: float = 0
        self._current_limit_ch2: float = 0
        self._enable_ch1: bool = False
        self._enable_ch2: bool = False
        self._error: int = 0
        self._load: float = 1000

    def _parse_scpi_command(self, cmd: str) -> tuple[str, list[str]]:
        command, *args = cmd.split()

        return command, args

    def process_scpi_command(self, cmd: str) -> Any:
        actions = {
            "VOLT": self.set_voltage_limit,
            "CURR": self.set_current_limit,
            "MEAS:VOLT?": self.get_voltage,
            "MEAS:CURR?": self.get_current,
            "OUTP:STAT": self.set_output_enable,
            "OUTP:STAT?": self.get_output_enable,
            "SYSTEM:ERROR?": self.get_error,
            "*IDN?": self._get_id,
        }

        command, args = self._parse_scpi_command(cmd)

        if command in actions:
            logger.info(f"Processing command: {command} with args: {args}")
            func = actions[command]
            # Use typing.cast to help mypy know the type is Callable
            typed_func = cast(Callable[..., Any], func)
            return typed_func(*args)
        else:
            logger.error(f"Unknown command: {command, args}")
            self._error = 200

    def _get_id(self) -> str:
        time.sleep(0.135)
        return SimulatedPSU.id

    def set_voltage_limit(self, voltage: str, channel: str = "1"):
        time.sleep(0.085)  # Simulate hardware response delay
        if channel == "1":
            self._voltage_limit_ch1 = float(voltage)
        else:
            self._voltage_limit_ch2 = float(voltage)
        logger.info(f"Voltage limit for ch{channel} set to {self._voltage_limit_ch1}")
        logger.info(f"Voltage limit for ch{channel} set to {self._voltage_limit_ch2}")
        self._update()

    def set_current_limit(self, current: str, channel: str = "1"):
        time.sleep(0.085)  # Simulate hardware response delay
        current_float = float(current)
        if channel == "1":
            self._current_limit_ch1 = current_float
        else:
            self._current_limit_ch2 = current_float
        logger.info(f"Current limit for ch1 set to {self._current_limit_ch1}")
        logger.info(f"Current limit for ch2 set to {self._current_limit_ch2}")
        self._update()

    def get_voltage(self, channel: str = "1") -> float:
        time.sleep(0.085)  # Simulate measurement delay
        self._update()
        if channel == "1":
            logger.info(f"Voltage measured for ch1: {self._voltage_ch1}")
            return self._voltage_ch1
        else:
            logger.info(f"Voltage measured for ch2: {self._voltage_ch2}")
            return self._voltage_ch2

    def get_current(self, channel: str = "1") -> float:
        time.sleep(0.085)  # Simulate measurement delay
        self._update()
        if channel == "1":
            logger.info(f"Current measured for ch1: {self._current_ch1}")
            return self._current_ch1
        else:
            logger.info(f"Current measured for ch2: {self._current_ch2}")
            return self._current_ch2

    def set_output_enable(self, cmd: str, channel: str = "1"):
        time.sleep(0.085)  # Simulate relay switching delay
        if cmd == "ON":
            if channel == "1":
                self._enable_ch1 = True
            else:
                self._enable_ch2 = True
            logger.info(f"Output channel {channel} enabled")
        elif cmd == "OFF":
            if channel == "1":
                self._enable_ch1 = False
            else:
                self._enable_ch2 = False
            logger.info(f"Output channel {channel} disabled")
        else:
            self._error = -101

        self._update()

    def get_output_enable(self, channel: str = "1") -> str:
        time.sleep(0.085)
        if channel == "1":
            if self._enable_ch1:
                return "ON"
            else:
                return "OFF"
        else:
            if self._enable_ch2:
                return "ON"
            else:
                return "OFF"

    def get_error(self) -> str:
        time.sleep(0.015)
        error_msg = SimulatedPSU.error_codes[self._error]
        return f"{self._error}, {error_msg}"

    def _update(self):
        logger.info("Updating simulated PSU")
        logger.info(f"Channel 1 enabled: {self._enable_ch1}")
        logger.info(f"Channel 2 enabled: {self._enable_ch2}")
        if self._enable_ch1:
            self._voltage_ch1 = add_noise(self._voltage_limit_ch1, 0.005)
            self._current_ch1 = add_noise(self._voltage_ch1 / self._load, 0.005)
        else:
            self._voltage_ch1 = 0
            self._current_ch1 = 0

        if self._enable_ch2:
            self._voltage_ch2 = add_noise(self._voltage_limit_ch2, 0.005)
            self._current_ch2 = add_noise(self._voltage_ch2 / self._load, 0.005)
        else:
            self._voltage_ch2 = 0
            self._current_ch2 = 0

        # TODO add voltage/current Overvolt and overcurrent checking/protection
        # TODO check if the load is zero (aka a short circuit) which would trip OCP
        self._current_ch2 = add_noise(self._voltage_ch2 / self._load, 0.005)

        logger.info(f"Updated voltage: {self._voltage_ch1}, current: {self._current_ch1}, load: {self._load}")
        logger.info(f"Updated voltage: {self._voltage_ch2}, current: {self._current_ch2}, load: {self._load}")

    @property
    def sim_load(self) -> float:
        return self._load

    @sim_load.setter
    def sim_load(self, load: float):
        self._load = load
        logger.debug(f"Load set to {self._load}")
        self._update()


def main():
    psu = SimulatedPSU()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 5025))
    server.listen(1)
    print("Virtual SCPI device running on port 5025...")

    try:
        while True:
            conn, addr = server.accept()
            print(f"Connection from {addr}")
            with conn:
                while True:
                    data = conn.recv(1024)
                    if not data:
                        break
                    commands = data.decode().split("\n")
                    for cmd in commands:
                        cmd = cmd.strip()
                        if not cmd:
                            continue
                        response = psu.process_scpi_command(cmd)
                        if response is not None:
                            conn.sendall((str(response) + "\n").encode())
    except KeyboardInterrupt:
        print("Shutting down VISA sim server...")
    finally:
        server.close()
        print("Server closed.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    main()
