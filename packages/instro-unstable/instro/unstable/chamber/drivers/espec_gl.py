"""ESPEC GL-series environmental chamber controller driver (line-based ASCII, no error queue)."""

from __future__ import annotations

import enum

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
        raise NotImplementedError

    def open(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def _monitor(self, command: str) -> str:
        raise NotImplementedError

    def _command(self, command: str) -> str:
        raise NotImplementedError

    @publish_measurement
    def identify(self) -> Measurement:
        raise NotImplementedError

    @publish_measurement
    def get_temperature(self) -> Measurement:
        raise NotImplementedError

    @publish_measurement
    def get_temperature_setpoint(self) -> Measurement:
        raise NotImplementedError

    @publish_command
    def set_temperature_setpoint(self, celsius: float) -> Command:
        raise NotImplementedError

    @publish_measurement
    def get_humidity(self) -> Measurement:
        raise NotImplementedError

    @publish_command
    def set_humidity_setpoint(self, percent_rh: float) -> Command:
        raise NotImplementedError

    @publish_measurement
    def get_operation_mode(self) -> Measurement:
        raise NotImplementedError

    @publish_command
    def set_operation_mode(self, mode: OperationMode) -> Command:
        raise NotImplementedError
