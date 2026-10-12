"""ESPEC GL-series environmental chamber controller driver (line-based ASCII, no error queue)."""

from __future__ import annotations

import enum
import time

from instro.lib.instrument import Instrument, publish_command, publish_measurement
from instro.lib.publishers import Publisher
from instro.lib.transports.visa import VisaConfig, VisaDriver
from instro.lib.types import Command, Measurement


class OperationMode(enum.Enum):
    OFF = "OFF"
    STANDBY = "STANDBY"
    CONSTANT = "CONSTANT"
    RUN = "RUN"  # readable, not settable


class EspecGL(Instrument):
    """ESPEC GL-series chamber controller (Constant No.1 only, no error queue)."""

    def __init__(
        self,
        visa_resource: str | VisaConfig,
        name: str = "chamber",
        publishers: list[Publisher] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(name=name, publishers=publishers, **kwargs)
        self._visa = VisaDriver(visa_resource)

    def open(self) -> None:
        self._visa.open()

    def close(self) -> None:
        self._visa.close()
        super().close()

    def _monitor(self, command: str) -> str:
        reply = self._visa.query(command)
        if reply.startswith("NA"):
            raise RuntimeError(f"ESPEC GL reported error: {reply}")
        return reply

    def _command(self, command: str) -> str:
        reply = self._visa.query(command)
        if not reply.startswith("OK"):
            raise RuntimeError(f"ESPEC GL reported error: {reply}")
        return reply

    @publish_measurement
    def identify(self) -> Measurement:
        text = f"{self._monitor('ROM?')} / {self._monitor('TYPE?')}"
        return self._package_measurement("identity", text, time.time_ns())

    @publish_measurement
    def get_temperature(self) -> Measurement:
        value = float(self._monitor("TEMP?").split(",")[0])
        return self._package_measurement("temperature", value, time.time_ns())

    @publish_measurement
    def get_temperature_setpoint(self) -> Measurement:
        value = float(self._monitor("TEMP?").split(",")[1])
        return self._package_measurement("temperature_setpoint", value, time.time_ns())

    @publish_command
    def set_temperature_setpoint(self, celsius: float) -> Command:
        self._command(f"TEMP,S{celsius:.1f}")
        return self._package_command("temperature_setpoint.cmd", celsius, time.time_ns())

    @publish_measurement
    def get_humidity(self) -> Measurement:
        value = float(self._monitor("HUMI?").split(",")[0])
        return self._package_measurement("humidity", value, time.time_ns())

    @publish_command
    def set_humidity_setpoint(self, percent_rh: float) -> Command:
        self._command(f"HUMI,S{percent_rh:.0f}")
        return self._package_command("humidity_setpoint.cmd", percent_rh, time.time_ns())

    @publish_measurement
    def get_operation_mode(self) -> Measurement:
        mode = OperationMode(self._monitor("MODE?").strip().upper())
        return self._package_measurement("operation_mode", mode.value, time.time_ns())

    @publish_command
    def set_operation_mode(self, mode: OperationMode) -> Command:
        """Set the chamber operation mode. ``OperationMode.RUN`` is device-rejected; not driver-blocked."""
        self._command(f"MODE,{mode.value}")
        return self._package_command("operation_mode.cmd", mode.value, time.time_ns())
