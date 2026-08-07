"""EA Elektro-Automatik PSB 10000-series bidirectional supply.

``EAPSB10000Visa`` owns the one VISA session and vends the two quadrant drivers: ``.source`` for
``InstroPSU`` and ``.sink`` for ``InstroELoad``. Command set per EA ModBus & SCPI guide REV 24.
"""

from __future__ import annotations

import logging
import string
import threading
from functools import cached_property

from instro.eload import ELoadDriverBase
from instro.eload.types import LoadMode, SlewRateDirection
from instro.lib.exceptions import FeatureNotSupportedError
from instro.lib.transports.visa import VisaConfig, VisaDriver
from instro.psu import PSUDriverBase

logger = logging.getLogger(__name__)

FRIENDLY_NAME = "EA PSB 10000-series"

_SINK_CMD: dict[LoadMode, str] = {
    LoadMode.CC: "SINK:CURR",
    LoadMode.CP: "SINK:POW",
    LoadMode.CR: "SINK:RES",
}


class EAPSB10000Visa:
    """One PSB 10000: owns the VISA session, the remote lock, the error queue, and the operation mode.

    Ownership is deferred-teardown via ``TransportBase``: the first quadrant to open takes the
    device's remote lock, and the last one to close releases it.
    """

    def __init__(self, visa_resource: str | VisaConfig) -> None:
        """Construct from a VISA resource string (uses defaults) or a full ``VisaConfig``."""
        self._visa = VisaDriver(visa_resource)

    @cached_property
    def source(self) -> PSUDriverBase:
        """The source quadrant, for ``InstroPSU``."""
        return EAPSB10000VisaSource(self)

    @cached_property
    def sink(self) -> ELoadDriverBase:
        """The sink quadrant, for ``InstroELoad``."""
        return EAPSB10000VisaSink(self)

    # --- ownership: the box grants remote control to one owner, so take it once ---

    def acquire(self, holder: object) -> None:
        """Open the session and take the remote lock; ``open`` reports True only for the first quadrant in."""
        if not self._visa.open(holder):
            return
        try:
            with self._visa.lock():
                self.write_checked("SYST:LOCK ON")
                owner = self._visa.query("SYST:LOCK:OWN?").strip()
                if owner != "REMOTE":
                    raise RuntimeError(f"{FRIENDLY_NAME} did not grant the remote lock (owner: {owner})")
        except BaseException:
            # Leave no stranded holder, or a retry reports not-first-owner and skips this setup.
            self._visa.close(holder, on_last_release=self._release_remote_lock)
            raise

    def release(self, holder: object) -> None:
        """Drop a quadrant's ownership; the last one out unlocks the box, then tears the session down."""
        self._visa.close(holder, on_last_release=self._release_remote_lock)

    def _release_remote_lock(self) -> None:
        """Best-effort ``SYST:LOCK OFF``; a failure here must not block transport teardown."""
        try:
            self._visa.write("SYST:LOCK OFF")
        except Exception as exc:
            logger.warning("Could not release the %s remote lock: %s", FRIENDLY_NAME, exc)

    # --- device-wide operations both quadrants share ---

    def lock(self) -> threading.RLock:
        """The transport lock, for quadrants that need a multi-step sequence to stay atomic."""
        return self._visa.lock()

    def output_enable(self, enable: bool) -> None:
        """Enable or disable the DC terminal. One terminal, so both quadrants drive this."""
        self.write_checked("OUTP ON" if enable else "OUTP OFF")

    def get_output_status(self) -> bool:
        return self.query_checked("OUTP?").strip().upper() == "ON"

    def get_voltage(self) -> float:
        """Measured terminal voltage. One meter, and voltage has no per-quadrant sign."""
        return _strip_unit(self.query_checked("MEAS:VOLT?"))

    def get_current_raw(self) -> float:
        """Measured current as the device reports it: positive sourcing, negative sinking (REV 24 §5.4.4.2)."""
        return _strip_unit(self.query_checked("MEAS:CURR?"))

    def set_operation_mode(self, mode: str) -> None:
        """Select U/I/P or U/I/R. Device-global: it decides whether power or resistance is the third set value."""
        if mode not in {"UIP", "UIR"}:
            raise ValueError(f"operation mode must be 'UIP' or 'UIR', got {mode!r}")
        with self._visa.lock():
            current = self.query_checked("SYST:CONF:MODE?").strip().upper().replace("/", "")
            if current == mode:
                return
            self.write_checked(f"SYST:CONF:MODE {mode}")

    # --- I/O ---

    def write_checked(self, command: str) -> None:
        with self._visa.lock():
            self._visa.write(command)
            self.check_errors()

    def query_checked(self, command: str) -> str:
        with self._visa.lock():
            value = self._visa.query(command)
            self.check_errors()
        return value

    def check_errors(self) -> None:
        """One error queue per box, drained in one place."""
        err = self._visa.query("SYST:ERR?")
        if not err.startswith("0"):
            raise RuntimeError(f"{FRIENDLY_NAME} reported error: {err}")


class EAPSB10000VisaSource(PSUDriverBase):
    """Source quadrant of a ``EAPSB10000Visa``. Construct via ``EAPSB10000Visa(...).source``."""

    def __init__(self, device: EAPSB10000Visa) -> None:
        self._dev = device

    def open(self) -> None:
        self._dev.acquire(self)

    def close(self) -> None:
        self._dev.release(self)

    def set_voltage(self, voltage: float, channel: int) -> None:
        _check_channel(channel)
        self._dev.write_checked(f"VOLT {voltage:.3f}")

    def get_voltage(self, channel: int) -> float:
        _check_channel(channel)
        return self._dev.get_voltage()

    def set_current_limit(self, current_limit: float, channel: int) -> None:
        _check_channel(channel)
        self._dev.write_checked(f"CURR {current_limit:.3f}")

    def get_current(self, channel: int) -> float:
        """Source-positive: current out of the supply is positive, regeneration reads negative."""
        _check_channel(channel)
        return self._dev.get_current_raw()

    def output_enable(self, enable: bool, channel: int) -> None:
        _check_channel(channel)
        self._dev.output_enable(enable)

    def get_output_status(self, channel: int) -> bool:
        _check_channel(channel)
        return self._dev.get_output_status()

    def set_overvoltage_protection_level(self, voltage: float, channel: int) -> None:
        _check_channel(channel)
        self._dev.write_checked(f"VOLT:PROT {voltage:.3f}")

    def get_overvoltage_protection_level(self, channel: int) -> float:
        _check_channel(channel)
        return _strip_unit(self._dev.query_checked("VOLT:PROT?"))

    def set_overvoltage_protection_enabled(self, enabled: bool, channel: int) -> None:
        raise FeatureNotSupportedError(
            f"set_overvoltage_protection_enabled is not supported by the {FRIENDLY_NAME}: "
            "OVP is an always-active threshold"
        )

    def get_overvoltage_protection_enabled(self, channel: int) -> bool:
        raise FeatureNotSupportedError(
            f"get_overvoltage_protection_enabled is not supported by the {FRIENDLY_NAME}: "
            "OVP is an always-active threshold"
        )

    def set_overvoltage_protection_delay(self, delay: float, channel: int) -> None:
        raise FeatureNotSupportedError(f"set_overvoltage_protection_delay is not supported by the {FRIENDLY_NAME}")

    def get_overvoltage_protection_delay(self, channel: int) -> float:
        raise FeatureNotSupportedError(f"get_overvoltage_protection_delay is not supported by the {FRIENDLY_NAME}")

    def set_overcurrent_protection_level(self, current: float, channel: int) -> None:
        _check_channel(channel)
        self._dev.write_checked(f"CURR:PROT {current:.3f}")

    def get_overcurrent_protection_level(self, channel: int) -> float:
        _check_channel(channel)
        return _strip_unit(self._dev.query_checked("CURR:PROT?"))

    def set_overcurrent_protection_enabled(self, enabled: bool, channel: int) -> None:
        raise FeatureNotSupportedError(
            f"set_overcurrent_protection_enabled is not supported by the {FRIENDLY_NAME}: "
            "OCP is an always-active threshold"
        )

    def get_overcurrent_protection_enabled(self, channel: int) -> bool:
        raise FeatureNotSupportedError(
            f"get_overcurrent_protection_enabled is not supported by the {FRIENDLY_NAME}: "
            "OCP is an always-active threshold"
        )

    def set_remote_sense_enabled(self, enabled: bool, channel: int) -> None:
        raise FeatureNotSupportedError(f"set_remote_sense_enabled is not supported by the {FRIENDLY_NAME}")

    def get_remote_sense_enabled(self, channel: int) -> bool:
        raise FeatureNotSupportedError(f"get_remote_sense_enabled is not supported by the {FRIENDLY_NAME}")


class EAPSB10000VisaSink(ELoadDriverBase):
    """Sink quadrant of a ``EAPSB10000Visa``. Supports CC, CP, and CR; the PSB has no sink voltage set value."""

    def __init__(self, device: EAPSB10000Visa) -> None:
        self._dev = device

    def open(self) -> None:
        self._dev.acquire(self)

    def close(self) -> None:
        self._dev.release(self)

    def set_mode(self, mode: LoadMode, channel: int) -> None:
        """CR needs the device in U/I/R to unlock SINK:RESistance; every other mode uses U/I/P."""
        _check_channel(channel)
        _check_mode(mode)
        self._dev.set_operation_mode("UIR" if mode is LoadMode.CR else "UIP")

    def set_level(self, mode: LoadMode, value: float, channel: int, curr_limit: float | None) -> None:
        """Write the sink set value for ``mode`` (CC: A, CP: W, CR: Ω). ``curr_limit`` is unused: CV is unsupported."""
        _check_channel(channel)
        _check_mode(mode)
        self._dev.write_checked(f"{_SINK_CMD[mode]} {value:.3f}")

    def output_enable(self, enable: bool, channel: int) -> None:
        """Drives the shared DC terminal, the same one the source quadrant controls."""
        _check_channel(channel)
        self._dev.output_enable(enable)

    def get_voltage(self, channel: int) -> float:
        _check_channel(channel)
        return self._dev.get_voltage()

    def get_current(self, channel: int) -> float:
        """Sink-positive per the Instro E-Load convention; the PSB reports sink as negative."""
        _check_channel(channel)
        return -self._dev.get_current_raw()

    def set_range(self, mode: LoadMode, value: float, channel: int) -> None:
        raise FeatureNotSupportedError(
            f"set_range is not supported by the {FRIENDLY_NAME}: ranges follow the set value automatically"
        )

    def set_slewrate(self, direction: SlewRateDirection, rate: float, channel: int) -> None:
        raise FeatureNotSupportedError(f"set_slewrate is not supported by the {FRIENDLY_NAME}")

    def short_output(self, enable: bool, channel: int) -> None:
        raise FeatureNotSupportedError(f"short_output is not supported by the {FRIENDLY_NAME}")


def _strip_unit(value: str) -> float:
    """Parse a PSB measurement reply, which carries a unit suffix (e.g. ``43.50V``)."""
    return float(value.strip().rstrip(string.ascii_letters))


def _check_mode(mode: LoadMode) -> None:
    """Reject CV: the PSB's sink quadrant has set values for current, power, and resistance only."""
    if mode is LoadMode.CV:
        raise FeatureNotSupportedError(
            f"CV is not supported by the {FRIENDLY_NAME}: the sink quadrant has no voltage set value. "
            "Hold the terminals at a voltage through the PSU interface instead."
        )


def _check_channel(channel: int) -> None:
    if channel != 1:
        raise ValueError(f"The {FRIENDLY_NAME} only has a single channel, got channel {channel}")
