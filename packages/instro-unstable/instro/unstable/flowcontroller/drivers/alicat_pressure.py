"""Alicat gauge-pressure controller driver.

Measures pressure and setpoint only. Minimal data frame.
"""

from instro.lib.transports.visa import SerialConfig, TerminatorConfig, VisaConfig, VisaDriver
from instro.unstable.flowcontroller import FlowControllerDriverBase
from instro.unstable.flowcontroller.types import PRESSURE_KEY, PressureData


def _default_alicat_config(visa_resource: str) -> VisaConfig:
    """Build default VisaConfig for Alicat RS-232 at 19200 baud."""
    return VisaConfig(
        visa_resource=visa_resource,
        serial_config=SerialConfig(baud_rate=19200),
        terminator=TerminatorConfig(read="\r", write="\r"),
    )


class AlicatPressureController(FlowControllerDriverBase):
    """Alicat gauge-pressure controller (P-series, etc).

    Measures pressure and setpoint only.
    Does not measure flow (mass or volumetric).
    """

    unit_id: str
    _visa: VisaDriver

    def __init__(self, visa_resource: str | VisaConfig, device_id: str = "A") -> None:
        self.unit_id = device_id
        if isinstance(visa_resource, str):
            visa_resource = _default_alicat_config(visa_resource)
        self._visa = VisaDriver(visa_resource)

    def open(self) -> None:
        """Open the VISA transport."""
        self._visa.open()

    def close(self) -> None:
        """Close the VISA transport."""
        self._visa.close()

    def _query_checked(self, command: str) -> str:
        """Query the device and raise RuntimeError if the response is '?'."""
        response = self._visa.query(command)
        if response == "?":
            raise RuntimeError(f"Error running command {command}, device returned ?")
        return response

    def get_flow_data(self) -> PressureData:
        """Poll the device for a single measurement frame."""
        response = self._query_checked(self.unit_id)
        fields = response.split()
        if len(fields) < 3:
            raise RuntimeError(f"AlicatPressureController: short response from {self.unit_id!r}: {response!r}")
        if fields[0].upper() != self.unit_id.upper():
            raise RuntimeError(
                f"AlicatPressureController: response ID {fields[0]!r} does not match device ID {self.unit_id!r}"
            )
        return PressureData(
            pressure=float(fields[1]),
            setpoint=float(fields[2]),
        )

    def set_setpoint(self, setpt: float) -> float:
        """Command a float setpoint in the device's configured engineering units."""
        response = self._query_checked(f"{self.unit_id}s{setpt}")
        fields = response.split()
        if len(fields) < 3:
            raise RuntimeError(f"AlicatPressureController: short response from {self.unit_id!r}: {response!r}")
        return float(fields[2])

    def select_working_fluid(self, fluid_name: str) -> str:
        """Pressure controllers do not support gas/fluid selection; raises NotImplementedError."""
        raise NotImplementedError(f"{self.__class__.__name__} does not support working fluid selection")

    @property
    def setpoint(self) -> float:
        """Current setpoint in the device's configured engineering units."""
        response = self._query_checked(self.unit_id)
        fields = response.split()
        if len(fields) < 3:
            raise RuntimeError(f"AlicatPressureController: short response from {self.unit_id!r}: {response!r}")
        return float(fields[2])

    @property
    def pressure(self) -> float:
        """Current pressure reading in the device's configured engineering units."""
        response = self._query_checked(self.unit_id)
        fields = response.split()
        if len(fields) < 2:
            raise RuntimeError(f"AlicatPressureController: short response from {self.unit_id!r}: {response!r}")
        return float(fields[1])

    @property
    def process_value(self) -> float:
        """Current process value (pressure for pressure-only controllers)."""
        return self.pressure

    @property
    def process_value_source(self) -> str:
        """Pressure controllers use pressure as the process value."""
        return PRESSURE_KEY
