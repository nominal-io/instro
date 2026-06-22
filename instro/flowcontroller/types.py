"""Flow-controller shared types."""

from dataclasses import dataclass, field


@dataclass
class FlowData:
    """Snapshot of all live measurements from one flow controller poll."""

    setpoint: float
    mass_flow: float
    vol_flow: float
    pressure: float
    temperature: float
    gas: str
    status_flags: set[str] = field(default_factory=set)
