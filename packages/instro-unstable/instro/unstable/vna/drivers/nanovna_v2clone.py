import time

import numpy as np
import serial

from instro.lib import InstroError
from instro.unstable.vna.vna import VNADriverBase


class NanoVNAv2Clone(VNADriverBase):
    def __init__(self, port: str = "/dev/ttyACM0", baudrate: int = 9600, timeout: float = 3.0) -> None:
        """Initializes serial connection to the text-based NanoVNA firmware."""
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.ser: serial.Serial | None = None
        self._nports = 2
        self.open()

    def _require_open(self) -> serial.Serial:
        if self.ser is None:
            raise InstroError("NanoVNA serial port is not open")
        return self.ser

    def open(self) -> None:
        """Opens the serial port interface."""
        self.ser = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            timeout=self.timeout,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
        )
        time.sleep(0.5)  # Allow connection to settle
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()
        self._is_open = True

    def close(self) -> None:
        """Closes the serial port interface safely."""
        if self.ser and self.ser.is_open:
            self.ser.close()
        self._is_open = False

    def _send_command(self, cmd_string: str) -> None:
        """Sends an ASCII text command and flushes buffers."""
        ser = self._require_open()
        full_cmd = f"{cmd_string}\r\n".encode("ascii")
        ser.write(full_cmd)
        ser.flush()
        time.sleep(0.1)  # Small processing delay for the VNA micro-controller

    def _read_lines(self) -> list[str]:
        """Reads back incoming lines until the trailing prompt or timeout occurs."""
        ser = self._require_open()
        lines: list[str] = []
        start_time = time.time()
        while (time.time() - start_time) < self.timeout:
            if ser.in_waiting > 0:
                line = ser.readline().decode("ascii", errors="ignore").strip()
                if not line:
                    continue
                # If we encounter the command prompt character, parsing is complete
                if "ch0>" in line or "ch1>" in line or ">" in line:
                    break
                lines.append(line)
        return lines

    def get_freq_start(self, ch: int | None = None) -> float:
        return float(self.get_f()[0])

    def get_freq_stop(self, ch: int | None = None) -> float:
        return float(self.get_f()[-1])

    def get_freq_npoints(self, ch: int | None = None) -> int:
        return len(self.get_f())

    def get_f(self) -> np.ndarray:
        """Queries the device for the active sweep frequencies."""
        self._require_open().reset_input_buffer()
        self._send_command("frequencies")
        raw_lines = self._read_lines()

        freqs = []
        for line in raw_lines:
            try:
                # Filter out echoed echo text lines or system strings
                if "frequencies" in line:
                    continue
                val = float(line.strip())
                freqs.append(val)
            except ValueError:
                continue

        if not freqs:
            raise InstroError("NanoVNA returned no frequency data (check connection and port)")
        return np.array(freqs)

    def get_nports(self, ch: int | None = None) -> int:
        """Get the number of ports of the VNA."""
        return self._nports  # NanoVNA v1 has 2 ports

    def get_channel_data(self, array_index: int = 0) -> np.ndarray:
        """Fetches raw S-parameter values (array 0 = S11, array 1 = S21)."""
        self._require_open().reset_input_buffer()
        self._send_command(f"data {array_index}")
        raw_lines = self._read_lines()

        complex_data = []
        for line in raw_lines:
            try:
                if f"data {array_index}" in line:
                    continue
                # V1 formats raw real/imaginary values split by whitespace
                parts = line.strip().split()
                if len(parts) >= 2:
                    real = float(parts[0])
                    imag = float(parts[1])
                    complex_data.append(complex(real, imag))
            except ValueError:
                continue

        return np.array(complex_data)

    def get_smat(self, m: int, n: int, ch: int | None = None) -> np.ndarray:
        """Get one S-parameter; the NanoVNA measures only S11/S21, so other terms are zero-filled."""
        if (m, n) == (0, 0):
            return self.get_channel_data(array_index=0)
        elif (m, n) == (1, 0):
            return self.get_channel_data(array_index=1)
        else:
            # 1.5-port instrument: S12/S22 aren't measurable; zero-fill to keep a standard 2-port network shape
            return np.zeros(self.get_freq_npoints())
