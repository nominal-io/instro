"""Oscilloscope (``InstroScope``) instrument interface and driver contract."""

from __future__ import annotations

import abc
import threading
import time

from instro.lib import Command, Instrument, Measurement
from instro.lib.instrument import publish_command, publish_measurement
from instro.lib.publishers import Publisher
from instro.scope.types import (
    AcquisitionMode,
    AcquisitionState,
    ChannelConfig,
    Coupling,
    ScopeConfig,
    ScopeMeasurementType,
    TriggerMode,
    TriggerSlope,
    TriggerStatus,
    TriggerType,
    WaveformData,
)


class ScopeDriverBase(abc.ABC):
    """Vendor scope driver contract. Concrete drivers compose a transport (typically ``VisaDriver``).

    Channels are 1-indexed analog input numbers throughout — the wrapper
    instrument (``InstroScope``) is responsible for range-checking against the
    declared ``num_channels``.
    """

    @abc.abstractmethod
    def open(self) -> None:
        """Open the underlying transport. Idempotent.

        Concrete drivers also perform any one-shot instrument setup here
        (e.g. ``*CLS``, remote-mode handshake).
        """

    @abc.abstractmethod
    def close(self) -> None:
        """Close the underlying transport. Idempotent."""

    @abc.abstractmethod
    def check_errors(self) -> None:
        """Drain the vendor's error queue; raise ``RuntimeError`` if any error is pending.

        Used between setup commands and any blocking query — calling a data
        query while the scope's error queue holds a syntax error would hang.
        """

    # --- Channel vertical settings ---

    @abc.abstractmethod
    def set_vertical_scale(self, volts_per_div: float, channel: int) -> None:
        """Set ``channel``'s vertical scale to ``volts_per_div`` (V/div).

        Drivers may snap to the nearest hardware-supported step. Callers that
        need the actual applied value should ``get_vertical_scale()`` afterward.
        """

    @abc.abstractmethod
    def get_vertical_scale(self, channel: int) -> float:
        """Read back ``channel``'s vertical scale (V/div)."""

    @abc.abstractmethod
    def set_vertical_offset(self, offset: float, channel: int) -> None:
        """Set ``channel``'s vertical offset to ``offset`` (volts)."""

    @abc.abstractmethod
    def get_vertical_offset(self, channel: int) -> float:
        """Read back ``channel``'s vertical offset (volts)."""

    @abc.abstractmethod
    def set_coupling(self, coupling: Coupling, channel: int) -> None:
        """Set AC/DC input coupling on ``channel``."""

    @abc.abstractmethod
    def get_coupling(self, channel: int) -> Coupling:
        """Read back input coupling on ``channel``."""

    @abc.abstractmethod
    def set_probe_attenuation(self, factor: float, channel: int) -> None:
        """Set ``channel``'s probe attenuation ratio (e.g. 1, 10, 100, 1000)."""

    @abc.abstractmethod
    def get_probe_attenuation(self, channel: int) -> float:
        """Read back ``channel``'s probe attenuation ratio."""

    # --- Horizontal (timebase) settings ---

    @abc.abstractmethod
    def set_horizontal_scale(self, seconds_per_div: float) -> None:
        """Set the timebase to ``seconds_per_div``. Applies globally to all channels."""

    @abc.abstractmethod
    def get_horizontal_scale(self) -> float:
        """Read back the timebase (seconds/div)."""

    # --- Sample rate ---

    @abc.abstractmethod
    def get_sample_rate(self) -> float:
        """Read back the current sample rate (samples per second).

        This is the effective hardware rate the scope is acquiring at; it
        depends on the timebase, memory depth, and interpolation settings.
        """

    # --- Acquisition ---

    @abc.abstractmethod
    def set_acquisition_mode(self, mode: AcquisitionMode) -> None:
        """Set the acquisition mode.

        Drivers should raise ``NotImplementedError`` for ``AcquisitionMode``
        values their scope doesn't support (e.g. Keysight 1200X has no
        ENVELOPE mode).
        """

    @abc.abstractmethod
    def get_acquisition_mode(self) -> AcquisitionMode:
        """Read back the current acquisition mode."""

    @abc.abstractmethod
    def set_average_count(self, count: int) -> None:
        """Set the number of waveforms to average. Only takes effect in ``AcquisitionMode.AVERAGE``."""

    @abc.abstractmethod
    def get_average_count(self) -> int:
        """Read back the waveforms-to-average count."""

    @abc.abstractmethod
    def run(self) -> None:
        """Start continuous (free-running) acquisition."""

    @abc.abstractmethod
    def stop(self) -> None:
        """Stop acquisition. Leaves the captured data intact for ``fetch_waveform`` / ``measure``."""

    @abc.abstractmethod
    def single(self) -> None:
        """Arm a single-shot acquisition.

        Non-blocking — use ``get_acquisition_state`` to poll for STOPPED, or
        prefer ``digitize()`` which combines arming and waiting.
        """

    @abc.abstractmethod
    def digitize(self, timeout: float) -> None:
        """Arm a single acquisition and block until the trigger fires and the capture completes.

        Acquisition is global — all enabled channels capture simultaneously. On
        success the scope is left stopped with valid data ready for readout.

        Args:
            timeout: Maximum seconds to wait for the trigger to fire.

        Raises:
            TimeoutError: Trigger did not fire within ``timeout``. The driver
                clears any pending operation so the session stays usable.
        """

    @abc.abstractmethod
    def get_acquisition_state(self) -> AcquisitionState:
        """Read back the acquisition run state (RUNNING / STOPPED)."""

    # --- Waveform data ---

    @abc.abstractmethod
    def fetch_waveform(self, channel: int) -> WaveformData:
        """Fetch the most recently acquired waveform from ``channel``.

        Returns:
            ``WaveformData`` with ``times`` in nanoseconds relative to the
            trigger point (negative = pre-trigger) and ``voltages`` already
            scaled through the configured probe attenuation.
        """

    # --- Measurements ---

    def setup_measurement(self, measurement_type: ScopeMeasurementType, channel: int) -> None:
        """Ensure a measurement slot exists for ``measurement_type``/``channel`` before the scope triggers.

        Required for instruments (e.g. Tektronix) that compute measurements
        during acquisition — the slot must be present at trigger time or the
        first ``measure()`` returns stale/invalid data. Default is a no-op for
        instruments (e.g. Keysight 1200X) that compute on demand.
        """
        pass

    @abc.abstractmethod
    def measure(self, measurement_type: ScopeMeasurementType, channel: int) -> float:
        """Read a built-in measurement (VPP, VMAX, VMIN, VAVG, VRMS, …) on ``channel``.

        Returns ``math.nan`` when the scope reports its invalid-measurement
        sentinel (no valid acquisition yet, channel off, etc.).
        """

    # --- Trigger ---

    @abc.abstractmethod
    def set_trigger_source(self, channel: int) -> None:
        """Set the trigger source to analog ``channel``.

        Drivers typically cache this value because the trigger-level SCPI on
        some scopes requires the source channel in the same command.
        """

    @abc.abstractmethod
    def set_trigger_type(self, trigger_type: TriggerType) -> None:
        """Set the trigger type (EDGE, PULSE, …)."""

    @abc.abstractmethod
    def set_trigger_level(self, level: float) -> None:
        """Set the trigger threshold to ``level`` (volts). Applies to the configured trigger source."""

    @abc.abstractmethod
    def set_trigger_slope(self, slope: TriggerSlope) -> None:
        """Set the trigger edge slope (RISING / FALLING / EITHER)."""

    @abc.abstractmethod
    def set_trigger_mode(self, mode: TriggerMode) -> None:
        """Set the trigger sweep mode (AUTO / NORMAL).

        AUTO forces an acquisition if no trigger fires within the timeout;
        NORMAL waits indefinitely for a real trigger.
        """

    @abc.abstractmethod
    def force_trigger(self) -> None:
        """Force a trigger event immediately, regardless of the configured conditions."""

    @abc.abstractmethod
    def get_trigger_status(self) -> TriggerStatus:
        """Read back the trigger status (ARMED / READY / TRIGGERED / …)."""

    # --- File operations ---

    @abc.abstractmethod
    def save_screenshot(self, filepath: str, to_instrument: bool = False) -> bytes:
        """Capture a screenshot.

        Args:
            filepath: Output path. When ``to_instrument=False`` this is a host
                path; when ``True`` it is a path on the scope's filesystem
                (USB stick, internal storage).
            to_instrument: When ``True``, the scope writes the file itself and
                this returns ``b""``. When ``False``, the image is transferred
                to the host, written to ``filepath``, and the raw bytes are
                returned for in-memory use.
        """

    @abc.abstractmethod
    def save_settings(self, name: str, to_instrument: bool = False) -> bytes:
        """Save the current scope setup. Path semantics mirror :meth:`save_screenshot`."""

    @abc.abstractmethod
    def load_settings(self, name: str, from_instrument: bool = False) -> None:
        """Recall a scope setup from ``name``.

        With ``from_instrument=True`` the scope reads from its own filesystem;
        otherwise the host reads ``name`` and pushes the bytes to the scope.
        After loading, the calling ``InstroScope`` should invalidate its
        tracked ``ScopeConfig`` and resync if a fresh view is needed.
        """


class InstroScope(Instrument):
    """Oscilloscope instrument. Tracks scope state locally; call ``sync_configuration()`` after ``open()`` to refresh."""

    def __init__(
        self,
        name: str,
        driver: ScopeDriverBase,
        num_channels: int,
        publishers: list[Publisher] | None = None,
        **kwargs,
    ):
        """Initialize an InstroScope.

        Args:
            name: Channel-name prefix for published data.
            driver: Concrete scope driver; owns its own transport::

                scope = InstroScope(
                    "scope",
                    driver=Keysight1200X("USB0::0x2A8D::0x039B::CN64191203::INSTR"),
                    num_channels=4,
                )

            num_channels: Analog-input channel count.
            publishers: Publishers that receive emitted Measurement/Command data.
            **kwargs: Default tags applied to every emitted Measurement/Command.
                Pass ``dataset_rid="<rid>"`` to auto-create a NominalCorePublisher
                (uses the on-disk 'default' Nominal credential).
        """
        super().__init__(name, publishers=publishers, **kwargs)

        self._driver = driver
        self._num_channels = num_channels
        self._resource_lock = threading.Lock()
        self._config = ScopeConfig(
            channels={ch: ChannelConfig() for ch in range(1, num_channels + 1)},
        )
        self._acquisition_armed: bool = False
        self._last_acquisition_ts: int | None = None

    def _get_channel_config(self, channel: int) -> ChannelConfig:
        """Return (creating if missing) the ``ChannelConfig`` for ``channel``."""
        if channel not in self._config.channels:
            self._config.channels[channel] = ChannelConfig()
        return self._config.channels[channel]

    def open(self) -> None:
        """Open the underlying driver."""
        self._driver.open()

    def close(self) -> None:
        """Close the underlying driver and stop the daemon."""
        self._driver.close()
        super().close()

    def _check_errors(self) -> None:
        """Raise if the driver's SCPI error queue holds anything."""
        self._driver.check_errors()

    def sync_configuration(self) -> ScopeConfig:
        """Bulk-query the instrument and overwrite the tracked ``ScopeConfig`` with live state.

        Note that trigger source/type/level/slope/mode aren't bulk-queried — not all scopes expose
        every trigger field, so call the per-field getters where supported.
        """
        with self._resource_lock:
            # Per-channel state
            for ch in range(1, self._num_channels + 1):
                ch_cfg = self._get_channel_config(ch)
                ch_cfg.vertical_scale = self._driver.get_vertical_scale(channel=ch)
                ch_cfg.vertical_offset = self._driver.get_vertical_offset(channel=ch)
                ch_cfg.coupling = self._driver.get_coupling(channel=ch)
                ch_cfg.probe_attenuation = self._driver.get_probe_attenuation(channel=ch)

            # Timebase
            self._config.horizontal_scale = self._driver.get_horizontal_scale()

            # Acquisition
            self._config.acquisition_mode = self._driver.get_acquisition_mode()
            self._config.average_count = self._driver.get_average_count()

            # Trigger
            # Note: trigger source/type/level/slope/mode are not bulk-queried here
            # because not all scopes expose all trigger fields via simple queries.
            # Individual get_trigger_* methods can be called if drivers support them.

            self._check_errors()

        return self._config

    # --- Channel vertical settings ---

    @publish_command
    def set_vertical_scale(self, volts_per_div: float, channel: int, **kwargs) -> Command:
        """Set the vertical scale (V/div) on ``channel``."""
        with self._resource_lock:
            self._driver.set_vertical_scale(volts_per_div, channel=channel)
            timestamp = time.time_ns()
            self._check_errors()

        self._get_channel_config(channel).vertical_scale = volts_per_div

        descriptor = f"ch{channel}_vscale.cmd" if self.legacy_naming else f"ch{channel}.vscale.cmd"
        return self._package_command(descriptor, volts_per_div, timestamp, **kwargs)

    @publish_measurement
    def get_vertical_scale(self, channel: int, **kwargs) -> Measurement | None:
        """Query the vertical scale (V/div) on ``channel``."""
        with self._resource_lock:
            val = self._driver.get_vertical_scale(channel=channel)
            timestamp = time.time_ns()
            self._check_errors()

        self._get_channel_config(channel).vertical_scale = val

        descriptor = f"ch{channel}_vscale" if self.legacy_naming else f"ch{channel}.vscale"
        return self._package_measurement(descriptor, val, timestamp, **kwargs)

    @publish_command
    def set_vertical_offset(self, offset: float, channel: int, **kwargs) -> Command:
        """Set the vertical offset (volts) on ``channel``."""
        with self._resource_lock:
            self._driver.set_vertical_offset(offset, channel=channel)
            timestamp = time.time_ns()
            self._check_errors()

        self._get_channel_config(channel).vertical_offset = offset

        descriptor = f"ch{channel}_voffset.cmd" if self.legacy_naming else f"ch{channel}.voffset.cmd"
        return self._package_command(descriptor, offset, timestamp, **kwargs)

    @publish_measurement
    def get_vertical_offset(self, channel: int, **kwargs) -> Measurement | None:
        """Query the vertical offset (volts) on ``channel``."""
        with self._resource_lock:
            val = self._driver.get_vertical_offset(channel=channel)
            timestamp = time.time_ns()
            self._check_errors()

        self._get_channel_config(channel).vertical_offset = val

        descriptor = f"ch{channel}_voffset" if self.legacy_naming else f"ch{channel}.voffset"
        return self._package_measurement(descriptor, val, timestamp, **kwargs)

    @publish_command
    def set_coupling(self, coupling: Coupling, channel: int, **kwargs) -> Command:
        """Set AC/DC input coupling on ``channel``."""
        with self._resource_lock:
            self._driver.set_coupling(coupling, channel=channel)
            timestamp = time.time_ns()
            self._check_errors()

        self._get_channel_config(channel).coupling = coupling

        descriptor = f"ch{channel}_coupling.cmd" if self.legacy_naming else f"ch{channel}.coupling.cmd"
        return self._package_command(descriptor, coupling.value, timestamp, **kwargs)

    @publish_command
    def get_coupling(self, channel: int, **kwargs) -> Command:
        """Query the input coupling mode on ``channel`` (published as a string)."""
        with self._resource_lock:
            val = self._driver.get_coupling(channel=channel)
            timestamp = time.time_ns()
            self._check_errors()

        self._get_channel_config(channel).coupling = val

        descriptor = f"ch{channel}_coupling.cmd" if self.legacy_naming else f"ch{channel}.coupling.cmd"
        return self._package_command(descriptor, val.value, timestamp, **kwargs)

    @publish_command
    def set_probe_attenuation(self, factor: float, channel: int, **kwargs) -> Command:
        """Set the probe attenuation ratio (e.g. 1, 10, 100) on ``channel``."""
        with self._resource_lock:
            self._driver.set_probe_attenuation(factor, channel=channel)
            timestamp = time.time_ns()
            self._check_errors()

        self._get_channel_config(channel).probe_attenuation = factor

        # Legacy descriptor abbreviated probe_attenuation → probe_atten.
        descriptor = f"ch{channel}_probe_atten.cmd" if self.legacy_naming else f"ch{channel}.probe_attenuation.cmd"
        return self._package_command(descriptor, factor, timestamp, **kwargs)

    @publish_measurement
    def get_probe_attenuation(self, channel: int, **kwargs) -> Measurement | None:
        """Query the probe attenuation ratio on ``channel``."""
        with self._resource_lock:
            val = self._driver.get_probe_attenuation(channel=channel)
            timestamp = time.time_ns()
            self._check_errors()

        self._get_channel_config(channel).probe_attenuation = val

        descriptor = f"ch{channel}_probe_atten" if self.legacy_naming else f"ch{channel}.probe_attenuation"
        return self._package_measurement(descriptor, val, timestamp, **kwargs)

    # --- Horizontal (timebase) settings ---

    @publish_command
    def set_horizontal_scale(self, seconds_per_div: float, **kwargs) -> Command:
        """Set the timebase (seconds/div)."""
        with self._resource_lock:
            self._driver.set_horizontal_scale(seconds_per_div)
            timestamp = time.time_ns()
            self._check_errors()

        self._config.horizontal_scale = seconds_per_div

        return self._package_command("hscale.cmd", seconds_per_div, timestamp, **kwargs)

    @publish_measurement
    def get_horizontal_scale(self, **kwargs) -> Measurement | None:
        """Query the timebase (seconds/div)."""
        with self._resource_lock:
            val = self._driver.get_horizontal_scale()
            timestamp = time.time_ns()
            self._check_errors()

        self._config.horizontal_scale = val

        return self._package_measurement("hscale", val, timestamp, **kwargs)

    # --- Sample rate ---

    @publish_measurement
    def get_sample_rate(self, **kwargs) -> Measurement | None:
        """Query the current sample rate (Sa/s)."""
        with self._resource_lock:
            val = self._driver.get_sample_rate()
            timestamp = time.time_ns()
            self._check_errors()

        return self._package_measurement("sample_rate", val, timestamp, **kwargs)

    # --- Acquisition ---

    @publish_command
    def set_acquisition_mode(self, mode: AcquisitionMode, **kwargs) -> Command:
        """Set the acquisition mode (NORMAL/AVERAGE/HIRES/PEAK_DETECT/ENVELOPE)."""
        with self._resource_lock:
            self._driver.set_acquisition_mode(mode)
            timestamp = time.time_ns()
            self._check_errors()

        self._config.acquisition_mode = mode

        return self._package_command("acquisition_mode.cmd", mode.value, timestamp, **kwargs)

    @publish_command
    def get_acquisition_mode(self, **kwargs) -> Command:
        """Query the current acquisition mode (published as a string)."""
        with self._resource_lock:
            val = self._driver.get_acquisition_mode()
            timestamp = time.time_ns()
            self._check_errors()

        self._config.acquisition_mode = val

        return self._package_command("acquisition_mode.cmd", val.value, timestamp, **kwargs)

    @publish_command
    def set_average_count(self, count: int, **kwargs) -> Command:
        """Set the average count (waveforms averaged) used in AVERAGE acquisition mode."""
        with self._resource_lock:
            self._driver.set_average_count(count)
            timestamp = time.time_ns()
            self._check_errors()

        self._config.average_count = count

        return self._package_command("average_count.cmd", count, timestamp, **kwargs)

    @publish_measurement
    def get_average_count(self, **kwargs) -> Measurement | None:
        """Query the average count."""
        with self._resource_lock:
            val = self._driver.get_average_count()
            timestamp = time.time_ns()
            self._check_errors()

        self._config.average_count = val

        return self._package_measurement("average_count", float(val), timestamp, **kwargs)

    @publish_command
    def run(self, **kwargs) -> Command:
        """Start continuous acquisition."""
        with self._resource_lock:
            self._driver.run()
            timestamp = time.time_ns()
            self._check_errors()

        return self._package_command("acquisition_control.cmd", "RUN", timestamp, **kwargs)

    @publish_command
    def stop_acquisition(self, **kwargs) -> Command:
        """Stop acquisition; records the acquisition timestamp if one was armed."""
        with self._resource_lock:
            self._driver.stop()
            timestamp = time.time_ns()
            self._check_errors()

        if self._acquisition_armed:
            self._last_acquisition_ts = timestamp
            self._acquisition_armed = False

        return self._package_command("acquisition_control.cmd", "STOP", timestamp, **kwargs)

    @publish_command
    def single(self, **kwargs) -> Command:
        """Arm a single-shot acquisition."""
        with self._resource_lock:
            self._driver.single()
            timestamp = time.time_ns()
            self._check_errors()

        self._acquisition_armed = True

        return self._package_command("acquisition_control.cmd", "SINGLE", timestamp, **kwargs)

    @publish_command
    def get_acquisition_state(self, **kwargs) -> Command:
        """Query RUNNING/STOPPED. If STOPPED while armed, records the acquisition timestamp."""
        with self._resource_lock:
            val = self._driver.get_acquisition_state()
            timestamp = time.time_ns()
            self._check_errors()

        if self._acquisition_armed and val == AcquisitionState.STOPPED:
            self._last_acquisition_ts = timestamp
            self._acquisition_armed = False

        return self._package_command("acquisition_state.cmd", val.value, timestamp, **kwargs)

    def _wait_for_acquisition(self, timeout: float) -> None:
        """If armed, block via the driver's ``digitize()`` until the acquisition completes or ``TimeoutError``.

        Acquisition is global — all enabled channels capture simultaneously.
        Must be called while holding ``_resource_lock``.
        """
        if not self._acquisition_armed:
            return

        # digitize() arms, waits for trigger, and acquires — blocking.
        # It raises TimeoutError if the trigger doesn't fire in time.
        self._driver.digitize(timeout)
        self._last_acquisition_ts = time.time_ns()
        self._acquisition_armed = False

    # --- Waveform data ---

    @publish_measurement
    def fetch_waveform(self, channel: int, timeout: float = 5.0, **kwargs) -> Measurement:
        """Fetch the acquired waveform from ``channel``.

        Timestamps are ns relative to the trigger point (negative = pre-trigger).
        If an acquisition is armed, blocks up to ``timeout`` seconds for it to
        complete; raises ``TimeoutError`` if it doesn't.
        """
        with self._resource_lock:
            self._wait_for_acquisition(timeout)
            waveform = self._driver.fetch_waveform(channel=channel)
            self._check_errors()

        timestamp = self._last_acquisition_ts if self._last_acquisition_ts is not None else time.time_ns()

        timestamps = [timestamp + time for time in waveform.times]  # offset to the last acquisition time

        channel_key = f"{self.name}.ch{channel}_waveform" if self.legacy_naming else f"{self.name}.ch{channel}.waveform"
        return Measurement(
            channel_data={channel_key: waveform.voltages},
            timestamps=timestamps,
            tags={**self.default_tags, "t_acquisition": str(timestamp), **(kwargs or {})},
        )

    # --- Measurements ---

    @publish_measurement
    def measure(
        self,
        measurement_type: ScopeMeasurementType,
        channel: int = 1,
        timeout: float = 5.0,
        **kwargs,
    ) -> Measurement | None:
        """Take a built-in measurement on ``channel``.

        Timestamps default to the last recorded acquisition time (falls back to
        ``time.time_ns()`` if none). If an acquisition is armed, blocks up to
        ``timeout`` seconds for it to complete; raises ``TimeoutError`` if it doesn't.
        """
        with self._resource_lock:
            self._driver.setup_measurement(measurement_type, channel=channel)
            self._wait_for_acquisition(timeout)
            val = self._driver.measure(measurement_type, channel=channel)
            self._check_errors()

        timestamp = self._last_acquisition_ts if self._last_acquisition_ts is not None else time.time_ns()

        suffix = measurement_type.value.lower()
        descriptor = f"ch{channel}_{suffix}" if self.legacy_naming else f"ch{channel}.{suffix}"
        return self._package_measurement(descriptor, val, timestamp, **kwargs)

    # --- Trigger ---

    @publish_command
    def set_trigger_source(self, channel: int, **kwargs) -> Command:
        """Set the trigger source to ``channel``."""
        with self._resource_lock:
            self._driver.set_trigger_source(channel)
            timestamp = time.time_ns()
            self._check_errors()

        self._config.trigger.source = channel

        return self._package_command("trigger_source.cmd", channel, timestamp, **kwargs)

    @publish_command
    def set_trigger_type(self, trigger_type: TriggerType, **kwargs) -> Command:
        """Set the trigger type (EDGE, PULSE, …)."""
        with self._resource_lock:
            self._driver.set_trigger_type(trigger_type)
            timestamp = time.time_ns()
            self._check_errors()

        self._config.trigger.type = trigger_type

        return self._package_command("trigger_type.cmd", trigger_type.value, timestamp, **kwargs)

    @publish_command
    def set_trigger_level(self, level: float, **kwargs) -> Command:
        """Set the trigger level (volts)."""
        with self._resource_lock:
            self._driver.set_trigger_level(level)
            timestamp = time.time_ns()
            self._check_errors()

        self._config.trigger.level = level

        return self._package_command("trigger_level.cmd", level, timestamp, **kwargs)

    @publish_command
    def set_trigger_slope(self, slope: TriggerSlope, **kwargs) -> Command:
        """Set the trigger edge slope (RISING/FALLING/EITHER)."""
        with self._resource_lock:
            self._driver.set_trigger_slope(slope)
            timestamp = time.time_ns()
            self._check_errors()

        self._config.trigger.slope = slope

        return self._package_command("trigger_slope.cmd", slope.value, timestamp, **kwargs)

    @publish_command
    def set_trigger_mode(self, mode: TriggerMode, **kwargs) -> Command:
        """Set the trigger mode (AUTO/NORMAL)."""
        with self._resource_lock:
            self._driver.set_trigger_mode(mode)
            timestamp = time.time_ns()
            self._check_errors()

        self._config.trigger.mode = mode

        return self._package_command("trigger_mode.cmd", mode.value, timestamp, **kwargs)

    @publish_command
    def force_trigger(self, **kwargs) -> Command:
        """Force a trigger event immediately."""
        with self._resource_lock:
            self._driver.force_trigger()
            timestamp = time.time_ns()
            self._check_errors()

        return self._package_command("trigger_control.cmd", "FORCE", timestamp, **kwargs)

    @publish_command
    def get_trigger_status(self, **kwargs) -> Command:
        """Query the trigger status (published as a string)."""
        with self._resource_lock:
            val = self._driver.get_trigger_status()
            timestamp = time.time_ns()
            self._check_errors()

        return self._package_command("trigger_status.cmd", val.value, timestamp, **kwargs)

    # --- File operations ---

    @publish_command
    def save_screenshot(self, filepath: str, to_instrument: bool = False, **kwargs) -> Command:
        """Capture a screenshot and save it. ``to_instrument=True`` writes to the scope's filesystem; otherwise to the host."""
        with self._resource_lock:
            self._driver.save_screenshot(filepath, to_instrument=to_instrument)
            timestamp = time.time_ns()
            self._check_errors()

        return self._package_command("screenshot.cmd", filepath, timestamp, **kwargs)

    @publish_command
    def save_settings(self, name: str, to_instrument: bool = False, **kwargs) -> Command:
        """Save scope setup. ``to_instrument=True`` writes to the scope's filesystem; otherwise to the host."""
        with self._resource_lock:
            self._driver.save_settings(name, to_instrument=to_instrument)
            timestamp = time.time_ns()
            self._check_errors()

        return self._package_command("save_settings.cmd", name, timestamp, **kwargs)

    @publish_command
    def load_settings(self, name: str, from_instrument: bool = False, **kwargs) -> Command:
        """Recall a scope setup. ``from_instrument=True`` reads from the scope's filesystem; otherwise from the host.

        Invalidates the tracked ``ScopeConfig`` since instrument state changed externally;
        call ``sync_configuration()`` to refresh.
        """
        with self._resource_lock:
            self._driver.load_settings(name, from_instrument=from_instrument)
            timestamp = time.time_ns()
            self._check_errors()

        # Invalidate tracked state since the instrument config changed externally
        self._config = ScopeConfig(
            channels={ch: ChannelConfig() for ch in range(1, self._num_channels + 1)},
        )

        return self._package_command("load_settings.cmd", name, timestamp, **kwargs)
