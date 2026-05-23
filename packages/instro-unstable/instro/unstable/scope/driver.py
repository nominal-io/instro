"""Scope driver contract (``ScopeDriverBase``). Concrete drivers compose their own SCPI/VISA transport."""

from __future__ import annotations

import abc

from instro.unstable.scope.types import (
    AcquisitionMode,
    AcquisitionState,
    Coupling,
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
