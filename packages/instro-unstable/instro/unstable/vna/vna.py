import abc
import logging
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Callable

import numpy as np
import skrf  # type: ignore[import-untyped]

from instro.lib import Instrument
from instro.lib.instrument import publish_command, publish_measurement
from instro.lib.publishers import Publisher
from instro.lib.types import Command, Measurement
from instro.unstable.vna.storage import DiskStorage, Storage
from instro.unstable.vna.types import NetworkFileFormat, SweepType

logger = logging.getLogger(__name__)


class VNADriverBase(abc.ABC):
    """Base class for VNA drivers; channels are flat — ``ch`` is a plain argument on all relevant methods."""

    # TODO: have a clever way to pass `ch` everwhere without seeing it all the time
    # maybe a `channalize` decorator
    @abc.abstractmethod
    def open(self) -> None:
        """Open the underlying transport."""

    @abc.abstractmethod
    def close(self) -> None:
        """Close the underlying transport. Idempotent."""

    @abc.abstractmethod
    def get_freq_start(self, ch: int | None = None) -> float:
        """Get the start frequency of the VNA in Hz."""

    def set_freq_start(self, freq: float, ch: int | None = None) -> float:
        """Set the start frequency of the VNA in Hz."""
        raise NotImplementedError("set_freq_start is not supported by this driver")

    @abc.abstractmethod
    def get_freq_stop(self, ch: int | None = None) -> float:
        """Get the stop frequency of the VNA in Hz."""

    def set_freq_stop(self, freq: float, ch: int | None = None) -> float:
        """Set the stop frequency of the VNA in Hz."""
        raise NotImplementedError("set_freq_stop is not supported by this driver")

    def get_freq_span(self, ch: int | None = None) -> float:
        """Get the frequency span of the VNA in Hz."""
        raise NotImplementedError("get_freq_span is not supported by this driver")

    def set_freq_span(self, freq: float, ch: int | None = None) -> float:
        """Set the frequency span of the VNA in Hz."""
        raise NotImplementedError("set_freq_span is not supported by this driver")

    def get_freq_center(self, ch: int | None = None) -> float:
        """Get the center frequency of the VNA in Hz."""
        raise NotImplementedError("get_freq_center is not supported by this driver")

    def set_freq_center(self, freq: float, ch: int | None = None) -> float:
        """Set the center frequency of the VNA in Hz."""
        raise NotImplementedError("set_freq_center is not supported by this driver")

    @abc.abstractmethod
    def get_freq_npoints(self, ch: int | None = None) -> int:
        """Get the number of frequency points of the VNA sweep."""

    def set_freq_npoints(self, npoints: int, ch: int | None = None) -> int:
        """Set the number of frequency points of the VNA sweep."""
        raise NotImplementedError("set_freq_npoints is not supported by this driver")

    @abc.abstractmethod
    def get_nports(self, ch: int | None = None) -> int:
        """Get the number of ports of the VNA."""

    @abc.abstractmethod
    def get_smat(
        self,
        m: int,
        n: int,
        ch: int | None = None,
    ) -> np.ndarray:
        """Get one S-parameter (0-based row m, column n) as a complex array."""

    def get_frequency(
        self,
        ch: int | None = None,
        unit: str = "hz",
        sweep_type: SweepType | str = SweepType.LIN,
    ) -> skrf.Frequency:
        """Get the frequency of the VNA."""
        # skrf's unit setter doesn't validate; check here so a typo fails at the call site
        if unit.lower() not in skrf.Frequency.unit_dict:
            raise ValueError(f"invalid frequency unit {unit!r}; expected one of {list(skrf.Frequency.unit_dict)}")
        sweep_type = SweepType(sweep_type)
        if sweep_type == SweepType.LIN:
            # drivers report Hz; construct in Hz, then set the display unit (does not rescale)
            frequency = skrf.Frequency(
                start=self.get_freq_start(ch=ch),
                stop=self.get_freq_stop(ch=ch),
                npoints=self.get_freq_npoints(ch=ch),
                unit="hz",
            )
            frequency.unit = unit
        else:
            raise NotImplementedError
        return frequency

    def set_frequency(
        self,
        freq: skrf.Frequency,
        ch: int | None = None,
    ):
        """Set the frequency of the VNA."""
        self.set_freq_start(freq.start, ch=ch)
        self.set_freq_stop(freq.stop, ch=ch)
        self.set_freq_npoints(freq.npoints, ch=ch)

    @property
    def frequency(self):
        return self.get_frequency()

    @frequency.setter
    def frequency(self, freq):
        return self.set_frequency(freq)

    def get_network(
        self,
        ports: Sequence | None = None,
        ch: int | None = None,
        **kw,
    ) -> skrf.Network:
        """Get an ``skrf.Network`` of the measured S-parameters; ``ports=None`` uses all instrument ports."""
        frequency = self.get_frequency(ch=ch)
        if ports is None:
            ports = range(self.get_nports(ch=ch))

        # iterate over ports and populate the s-parameter matrix
        s = np.zeros((len(frequency.f), len(ports), len(ports)), dtype=complex)
        for i, m in enumerate(ports):
            for j, n in enumerate(ports):
                s[:, i, j] = self.get_smat(m, n, ch=ch)
        network = skrf.Network(frequency=frequency, s=s, **kw)
        return network

    def get_s(self, m: int, n: int, ch: int | None = None, **kw) -> skrf.Network:
        """Get a single S-parameter (0-based row m, column n) as a one-port network."""
        frequency = self.get_frequency(ch=ch)
        s = self.get_smat(m, n, ch=ch)
        network = skrf.Network(frequency=frequency, s=s[:, np.newaxis, np.newaxis], **kw)
        return network

    @property
    def s11(self):
        return self.get_s(m=0, n=0)

    @property
    def s22(self):
        return self.get_s(m=1, n=1)

    @property
    def s21(self):
        return self.get_s(m=1, n=0)

    @property
    def s12(self):
        return self.get_s(m=0, n=1)


class InstroVNA(Instrument):
    def __init__(
        self,
        name: str,
        driver: VNADriverBase,
        publishers: list[Publisher] | None = None,
        storage: Storage | None = None,
        **kwargs,
    ):
        """High-level VNA wrapper around a vendor driver; see ``examples/vna/`` for usage."""
        super().__init__(name, publishers=publishers, **kwargs)
        self._driver = driver
        self._resource_lock = threading.Lock()
        self._storage = storage if storage is not None else DiskStorage()

    def open(self) -> None:
        """Open the underlying driver."""
        logger.info("Opening VNA '%s'", self.name)
        self._driver.open()

    def close(self) -> None:
        """Close the underlying driver and stop the daemon."""
        logger.info("Closing VNA '%s'", self.name)
        super().close()
        self._driver.close()

    # this is general and should be inherited
    @publish_measurement
    def _execute_measurement(
        self,
        driver_method: Callable,
        driver_kwargs: dict[str, Any] | None = None,
        channel: int = 1,
        *args,
        **kwargs,
    ) -> Measurement | None:
        """Execute a driver measurement method and return a Measurement for the read value."""
        with self._resource_lock:
            data = driver_method(**(driver_kwargs or {}))
            timestamp = time.time_ns()

        channel_name = f"ch{driver_method.__name__}"

        return self._package_measurement(channel=channel_name, data=data, timestamp=timestamp, **kwargs)

    @publish_command
    def _execute_command(
        self,
        driver_method: Callable,
        value: Any,
        channel: int | None = None,
        **kwargs,
    ) -> Command:
        """Execute a driver command method and return a Command for the published value."""
        with self._resource_lock:
            driver_method(value, ch=channel)
            timestamp = time.time_ns()

        channel_name = f"ch{driver_method.__name__}.cmd"
        return self._package_command(channel=channel_name, data=value, timestamp=timestamp, **kwargs)

    @property
    def driver(self) -> VNADriverBase:
        """The underlying vendor driver."""
        return self._driver

    def get_freq_start(self, ch: int | None = None, **kwargs) -> Measurement | None:
        """Read the start frequency (Hz) and publish it as a Measurement."""
        return self._execute_measurement(driver_method=self._driver.get_freq_start, driver_kwargs={"ch": ch}, **kwargs)

    def get_freq_stop(self, ch: int | None = None, **kwargs) -> Measurement | None:
        """Read the stop frequency (Hz) and publish it as a Measurement."""
        return self._execute_measurement(driver_method=self._driver.get_freq_stop, driver_kwargs={"ch": ch}, **kwargs)

    def get_freq_span(self, ch: int | None = None, **kwargs) -> Measurement | None:
        """Read the frequency span (Hz) and publish it as a Measurement."""
        return self._execute_measurement(driver_method=self._driver.get_freq_span, driver_kwargs={"ch": ch}, **kwargs)

    def get_freq_center(self, ch: int | None = None, **kwargs) -> Measurement | None:
        """Read the center frequency (Hz) and publish it as a Measurement."""
        return self._execute_measurement(driver_method=self._driver.get_freq_center, driver_kwargs={"ch": ch}, **kwargs)

    def get_freq_npoints(self, ch: int | None = None, **kwargs) -> Measurement | None:
        """Read the number of sweep points and publish it as a Measurement."""
        return self._execute_measurement(
            driver_method=self._driver.get_freq_npoints, driver_kwargs={"ch": ch}, **kwargs
        )

    def get_nports(self, ch: int | None = None, **kwargs) -> Measurement | None:
        """Read the number of ports and publish it as a Measurement."""
        return self._execute_measurement(driver_method=self._driver.get_nports, driver_kwargs={"ch": ch}, **kwargs)

    def set_freq_start(self, freq: float, ch: int | None = None, **kwargs) -> Command:
        """Set the start frequency (Hz) and publish it as a Command."""
        return self._execute_command(driver_method=self._driver.set_freq_start, value=freq, channel=ch, **kwargs)

    def set_freq_stop(self, freq: float, ch: int | None = None, **kwargs) -> Command:
        """Set the stop frequency (Hz) and publish it as a Command."""
        return self._execute_command(driver_method=self._driver.set_freq_stop, value=freq, channel=ch, **kwargs)

    def set_freq_span(self, freq: float, ch: int | None = None, **kwargs) -> Command:
        """Set the frequency span (Hz) and publish it as a Command."""
        return self._execute_command(driver_method=self._driver.set_freq_span, value=freq, channel=ch, **kwargs)

    def set_freq_center(self, freq: float, ch: int | None = None, **kwargs) -> Command:
        """Set the center frequency (Hz) and publish it as a Command."""
        return self._execute_command(driver_method=self._driver.set_freq_center, value=freq, channel=ch, **kwargs)

    def set_freq_npoints(self, npoints: int, ch: int | None = None, **kwargs) -> Command:
        """Set the number of sweep points and publish it as a Command."""
        return self._execute_command(driver_method=self._driver.set_freq_npoints, value=npoints, channel=ch, **kwargs)

    def get_frequency(
        self,
        ch: int | None = None,
        unit: str = "hz",
        sweep_type: SweepType | str = SweepType.LIN,
    ) -> skrf.Frequency:
        """Get the sweep axis as an ``skrf.Frequency`` under the resource lock."""
        with self._resource_lock:
            return self._driver.get_frequency(ch=ch, unit=unit, sweep_type=sweep_type)

    def set_frequency(self, freq: skrf.Frequency, ch: int | None = None) -> None:
        """Program the sweep axis from an ``skrf.Frequency`` under the resource lock."""
        with self._resource_lock:
            self._driver.set_frequency(freq, ch=ch)

    def get_network(self, ports: Sequence[int] | None = None, ch: int | None = None, **kw) -> skrf.Network:
        """Get an ``skrf.Network`` of the measured S-parameters under the resource lock."""
        with self._resource_lock:
            return self._driver.get_network(ports=ports, ch=ch, **kw)

    def get_s(self, m: int, n: int, ch: int | None = None, **kw) -> skrf.Network:
        """Get a single S-parameter (0-based row m, column n) as a one-port network under the resource lock."""
        with self._resource_lock:
            return self._driver.get_s(m, n, ch=ch, **kw)

    def save_network(
        self,
        name: str | None = None,
        ports: Sequence[int] | None = None,
        ch: int | None = None,
        format: NetworkFileFormat = NetworkFileFormat.SNP,
        **kw,
    ) -> Path:
        """Measure a network, write it to storage, and return the saved file's path."""
        with self._resource_lock:
            timestamp = time.time_ns()
            network = self._driver.get_network(ports=ports, ch=ch, **kw)
        if name is None:
            name = f"{self.name}_network_{timestamp}"

        if format == NetworkFileFormat.SNP:
            path = self._storage.get_path_for_filename(f"{name}.s{network.nports}p")
            network.write_touchstone(path)

        else:
            raise NotImplementedError("See NetworkFileFormat for possible formats")
        return path

    # TODO: publishing network data as Measurements awaits non-timeseries payload support in instro.lib
