"""Alicat liquid-flow controller driver.

Measures volumetric flow and pressure. Independent implementation (does not subclass AlicatMC).
"""

from dataclasses import dataclass

from instro.lib.transports.visa import SerialConfig, TerminatorConfig, VisaConfig, VisaDriver
from instro.unstable.flowcontroller import FlowControllerDriverBase
from instro.unstable.flowcontroller.types import VOLUMETRIC_FLOW_KEY, LiquidFlowData


def _default_alicat_config(visa_resource: str) -> VisaConfig:
    """Build default VisaConfig for Alicat RS-232 at 19200 baud."""
    return VisaConfig(
        visa_resource=visa_resource,
        serial_config=SerialConfig(baud_rate=19200),
        terminator=TerminatorConfig(read="\r", write="\r"),
    )


@dataclass
class AlicatLiquidFlowSample:
    """Single parsed measurement frame from an Alicat liquid-flow controller."""

    pressure: float
    temperature: float
    vol_flow: float
    setpoint: float

    def to_flow_data(self) -> LiquidFlowData:
        """Serialize to the LiquidFlowData dict."""
        return LiquidFlowData(
            pressure=self.pressure,
            temperature=self.temperature,
            vol_flow=self.vol_flow,
            setpoint=self.setpoint,
        )


class AlicatLiquidFlowController(FlowControllerDriverBase):
    """Alicat liquid-flow controller (L-series, etc).

    Measures volumetric flow, pressure, and setpoint.
    Does not measure mass flow or gas type.
    Communicates in RS-232 polling mode.
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

    def get_flow_data(self) -> LiquidFlowData:
        """Poll the device for a single measurement frame."""
        return self._get_flow_data().to_flow_data()

    def _get_flow_data(self) -> AlicatLiquidFlowSample:
        """Poll the device for a single measurement frame, returning liquid-specific format."""
        response = self._query_checked(self.unit_id)
        return self._parse_flowdata(response)

    def set_setpoint(self, setpt: float) -> float:
        """Command a float setpoint in the device's configured engineering units."""
        response = self._query_checked(f"{self.unit_id}s{setpt}")
        return self._parse_flowdata(response).setpoint

    def select_working_fluid(self, fluid_name: str) -> str:
        """Alicat liquid controllers do not support fluid selection; raises NotImplementedError."""
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support working fluid selection (got: {fluid_name})"
        )

    @property
    def setpoint(self) -> float:
        """Current setpoint in the device's configured engineering units."""
        return self._get_flow_data().setpoint

    @property
    def volumetric_flow(self) -> float:
        """Current volumetric flow reading in the device's configured engineering units."""
        return self._get_flow_data().vol_flow

    @property
    def pressure(self) -> float:
        """Current pressure reading in the device's configured engineering units."""
        return self._get_flow_data().pressure

    @property
    def process_value(self) -> float:
        """Current process value (volumetric flow for liquid-flow controllers)."""
        return self._get_flow_data().vol_flow

    @property
    def process_value_source(self) -> str:
        """Liquid-flow controllers use volumetric flow as the process value."""
        return VOLUMETRIC_FLOW_KEY

    def _parse_flowdata(self, response: str) -> AlicatLiquidFlowSample:
        """Parse one device ASCII response into an AlicatLiquidFlowSample."""
        # order: Unit[0], Pressure[1], Temp[2], Vol Flow[3], Setpoint[4]
        fields = response.split()
        if len(fields) < 5:
            raise RuntimeError(f"AlicatLiquidFlowController: short response from {self.unit_id!r}: {response!r}")
        if fields[0].upper() != self.unit_id.upper():
            raise RuntimeError(
                f"AlicatLiquidFlowController: response ID {fields[0]!r} does not match device ID {self.unit_id!r}"
            )
        return AlicatLiquidFlowSample(
            pressure=float(fields[1]),
            temperature=float(fields[2]),
            vol_flow=float(fields[3]),
            setpoint=float(fields[4]),
        )
