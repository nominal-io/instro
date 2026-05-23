"""DAQ scalers: convert raw readings to physical units with attached units strings."""

from abc import ABC, abstractmethod


class Scaler(ABC):
    """Convert a raw DAQ reading to a physical value and report the resulting units.

    Scalers compose via ``ScalerPipeline`` (e.g. voltage-divider → thermocouple).
    The pipeline's reported ``units`` come from the last stage.
    """

    @abstractmethod
    def scale(self, raw: float) -> float:
        """Convert a raw DAQ reading to a physical value in :attr:`units`."""

    @property
    @abstractmethod
    def units(self) -> str:
        """Physical units of the scaled output (e.g. ``"V"``, ``"degC"``, ``"psi"``)."""


class LinearScaler(Scaler):
    """``output = raw * gain + offset``."""

    def __init__(self, gain: float, offset: float, units: str) -> None:
        self._gain = gain
        self._offset = offset
        self._units = units

    def scale(self, raw: float) -> float:
        return raw * self._gain + self._offset

    @property
    def units(self) -> str:
        return self._units


class ReverseLinearScaler(Scaler):
    """``output = (raw - offset) / gain`` — inverse of ``LinearScaler``, e.g. to back out a measurement amplifier."""

    def __init__(self, gain: float, offset: float, units: str) -> None:
        self._gain = gain
        self._offset = offset
        self._units = units

    def scale(self, raw: float) -> float:
        return (raw - self._offset) / self._gain

    @property
    def units(self) -> str:
        return self._units


class DevicePassthrough(Scaler):
    """Pass through raw values unchanged; attaches a units string."""

    def __init__(self, units: str) -> None:
        self._units = units

    def scale(self, raw: float) -> float:
        return raw

    @property
    def units(self) -> str:
        return self._units


class ScalerPipeline(Scaler):
    """Apply scaler stages sequentially (e.g. voltage divider → thermocouple). Units come from the last stage."""

    def __init__(self, stage: Scaler, *stages: Scaler):
        self._stages = [stage, *stages]

    def scale(self, raw: float) -> float:
        for stage in self._stages:
            raw = stage.scale(raw)
        return raw

    @property
    def units(self) -> str:
        return self._stages[-1].units
