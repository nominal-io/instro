# This code is derived from concepts in the py-lab-hal codebase (Apache 2.0 licensed)
# Original py-lab-hal repository: https://github.com/google/py-lab-hal

"""Base instrument interface and background daemon support."""

import functools
import logging
import threading
import time
from importlib.metadata import version
from typing import Callable

from instro.lib.publishers import NominalCorePublisher, Publisher
from instro.lib.publishers.channel_buffer import (
    DequeInMemoryPublisher,
)
from instro.lib.types import BackgroundDaemonConfig, Command, Measurement


def publish_command(func: Callable) -> Callable:
    """Decorator that publishes the `Command` returned by an instrument method.

    Channel naming is the call site's responsibility; the wrong return type raises
    rather than silently publishing as the wrong kind.
    """

    @functools.wraps(func)
    def wrapper(self: "Instrument", *args, **kwargs):
        result = func(self, *args, **kwargs)
        if not isinstance(result, Command):
            raise TypeError(f"@publish_command on {func.__qualname__} must return Command, got {type(result).__name__}")
        self.publish(result)
        return result

    return wrapper


def publish_measurement(func: Callable) -> Callable:
    """Decorator that publishes the `Measurement` (or list) returned by an instrument method.

    Lists are published item by item. ``None`` passes through unpublished (e.g.
    a measurement that could not be obtained). Channel naming is the call site's
    responsibility.
    """

    @functools.wraps(func)
    def wrapper(self: "Instrument", *args, **kwargs):
        result = func(self, *args, **kwargs)
        if result is None:
            return None
        items = result if isinstance(result, list) else [result]
        for item in items:
            if not isinstance(item, Measurement):
                raise TypeError(
                    f"@publish_measurement on {func.__qualname__} must return Measurement or list[Measurement], "
                    f"got {type(item).__name__}"
                )
            self.publish(item)
        return result

    return wrapper


logger = logging.getLogger(__name__)


class Instrument:
    """Base class for Nominal instrument interfaces. Owns publishing and the background daemon."""

    def __init__(
        self,
        name: str,
        publishers: list[Publisher] | None = None,
        background_config: BackgroundDaemonConfig | None = None,
        legacy_naming: bool = False,
        **kwargs,
    ):
        """Initialize an Instrument.

        Args:
            name: Channel-name prefix for published data.
            publishers: Publishers that receive emitted Measurement/Command data.
            background_config: Background-daemon settings; default if omitted.
            legacy_naming: When True, publish channels under pre-v1.0 names (e.g.
                ``main.ch1_v`` instead of ``main.ch1.voltage`` for PSU). Categories
                with no v1.0 rename (DMM, Modbus) ignore the flag. Scheduled for
                removal in v2.0.
            **kwargs: Default tags applied to every emitted Measurement/Command.
                Pass ``dataset_rid="<rid>"`` to auto-create a NominalCorePublisher
                (uses the on-disk 'default' Nominal credential).
        """
        self.name = name
        self.legacy_naming = legacy_naming

        self.publishers = publishers or []
        self.default_tags: dict[str, str] = {}

        self._add_package_tags()

        self._background_config = background_config or BackgroundDaemonConfig()
        self._background_thread: threading.Thread | None = None
        self._background_stop_event = threading.Event()
        self._background_methods: list[tuple[Callable, tuple, dict]] = []

        self._channel_buffer_length: int = 10
        self._channel_buffer: DequeInMemoryPublisher | None = None

        self._process_kwargs(**kwargs)
        logger.info(
            "Initialized instrument '%s' (n_publishers=%d, default_tag_keys=%s)",
            self.name,
            len(self.publishers),
            sorted(self.default_tags.keys()),
        )

    def _process_kwargs(self, **kwargs):
        """Route known kwargs to their handler; store the rest as default tags."""
        # Known kwargs mapped to their handler methods
        known_kwargs = {
            "dataset_rid": self._add_core_publisher,
        }

        # Handle known kwargs
        for key, value in kwargs.items():
            if key in known_kwargs:
                known_kwargs[key](value)
            else:
                self.default_tags[key] = str(value)

    def _add_package_tags(self):
        """Tag every publication with this distribution's name and version."""
        try:
            module = self.__class__.__module__
            if module:
                module_name = module.split(".")[0].replace("_", "-")
                self.default_tags[module_name] = version(module_name)
            else:
                pass
        except Exception:
            pass

    def add_publisher(self, publisher: Publisher):
        """Register a publisher to receive this instrument's Measurement/Command data."""
        self.publishers.append(publisher)
        logger.info(
            "Added publisher '%s' to instrument '%s' (n_publishers=%d)",
            publisher.__class__.__name__,
            self.name,
            len(self.publishers),
        )

    def _add_core_publisher(self, rid: str):
        """Attach a NominalCorePublisher for ``rid`` (typically triggered by ``dataset_rid=``)."""
        logger.info("Adding NominalCorePublisher to instrument '%s' for dataset RID '%s'", self.name, rid)
        self.add_publisher(NominalCorePublisher(rid))

    def publish(self, data: Measurement | Command, **kwargs):
        """Fan ``data`` out to every configured publisher; ``kwargs`` pass through."""
        for publisher in self.publishers:
            publisher.publish(data, **kwargs)

    def _package_command(self, channel: str, data: float | bool | str, timestamp: int, **kwargs) -> Command:
        """Build a single-channel `Command` namespaced under this instrument.

        The published channel key is ``{self.name}.{channel}``. The caller writes the
        full descriptor including any ``.cmd`` suffix, so the literal published name
        appears at the call site (e.g. ``f"ch{channel}.voltage.cmd"``).
        """
        if not isinstance(data, (float, str)):
            data = float(data)
        return Command(
            channel_data={f"{self.name}.{channel}": data},
            timestamp=timestamp,
            tags={**self.default_tags, **kwargs},
        )

    def _package_measurement(self, channel: str, data: float | bool, timestamp: int, **kwargs) -> Measurement:
        """Build a single-channel `Measurement` namespaced under this instrument.

        The published channel key is ``{self.name}.{channel}``. The caller writes the
        full descriptor, so the literal published name appears at the call site.
        """
        return Measurement(
            channel_data={f"{self.name}.{channel}": [float(data)]},
            timestamps=[timestamp],
            tags={**self.default_tags, **kwargs},
        )

    @publish_measurement
    def _publish_daemon_timing(self, loop_time_s: float, work_time_s: float, timestamp: int) -> Measurement:
        """Publish per-iteration diagnostic timing from the background daemon loop."""
        return Measurement(
            channel_data={
                f"{self.name}.loop_time": [loop_time_s],
                f"{self.name}.daemon_work_time": [work_time_s],
            },
            timestamps=[timestamp],
            tags={**self.default_tags},
        )

    def open(self) -> None:
        """No-op base hook. Category subclasses override to open their transport."""

    def close(self) -> None:
        """Stop the background daemon and close every publisher.

        Category subclasses override to also close their transport, calling
        ``super().close()`` to keep daemon and publisher cleanup running.
        """
        logger.info("Closing instrument '%s'", self.name)
        self.stop()

        logger.info("Closing %d publishers for instrument '%s'", len(self.publishers), self.name)
        for publisher in self.publishers:
            logger.info(
                "Closing publisher '%s' for instrument '%s'",
                publisher.__class__.__name__,
                self.name,
            )
            publisher.close()
        logger.info("Closed instrument '%s'", self.name)

    def __enter__(self) -> "Instrument":
        """Open the instrument on entry to a ``with`` block."""
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        """Close the instrument when leaving the ``with`` block."""
        self.close()

    @property
    def background_interval(self):
        """Seconds between background daemon iterations (0 = no wait)."""
        return self._background_config.interval

    @background_interval.setter
    def background_interval(self, seconds: float):
        self._background_config.interval = seconds

    def add_background_daemon_function(self, method: Callable, *args, **kwargs):
        """Append ``method`` to the daemon's call list. Use ``define_background_daemon`` to replace instead."""
        self._background_methods.append((method, args, kwargs))

    def _setup_channel_buffer(self, buffer_length: int):
        # Create a channel buffer if one doesn't already exist. Add it to list of publishers.
        if self._channel_buffer is None:
            self._channel_buffer = DequeInMemoryPublisher(buffer_length)
        if self._channel_buffer not in self.publishers:
            logger.info("Setting up channel buffer for instrument '%s' (length=%d)", self.name, buffer_length)
            self.add_publisher(self._channel_buffer)

    def start(self):
        """Start the background daemon thread. No-op if already running."""
        # Check if a thread is already running
        if self._background_thread and self._background_thread.is_alive():
            logger.info("Background daemon already running for instrument '%s'", self.name)
            return

        self._setup_channel_buffer(self._channel_buffer_length)

        self._background_stop_event.clear()
        self._background_thread = threading.Thread(
            target=self._background_daemon_loop, daemon=True, name=f"{self.name}_background"
        )
        self._background_thread.start()
        logger.info(
            "Started background daemon for instrument '%s' (thread=%s, interval_s=%s)",
            self.name,
            self._background_thread.name,
            self._background_config.interval,
        )

    def stop(self):
        """Signal the background daemon to stop and join it. No-op if not running."""
        if self._background_thread and self._background_thread.is_alive():
            logger.info("Stopping background daemon for instrument '%s'", self.name)
            self._background_stop_event.set()
            self._background_thread.join()

            # close() wakes blocked get_channel() waiters but is one-way, so drop the
            # buffer entirely; the next start() builds and registers a fresh one.
            assert self._channel_buffer
            self._channel_buffer.close()
            if self._channel_buffer in self.publishers:
                self.publishers.remove(self._channel_buffer)
            self._channel_buffer = None
            logger.info("Stopped background daemon for instrument '%s'", self.name)
        else:
            logger.info("Background daemon not running for instrument '%s'; stop() is a no-op", self.name)

    def get_channel(
        self, channel_name: str, length: int = 1, wait_for_new_samples: bool = False, timeout: float = 10.0
    ) -> Measurement:
        """Return the most recent ``length`` samples for ``channel_name`` from the in-memory buffer.

        If the channel does not exist yet, or no sample is available, the code will always block until ``timeout`` expires.

        Args:
            channel_name: Name of the channel to retrieve.
            length: Number of trailing samples to return.
            wait_for_new_samples: Block until at least ``length`` new values arrive.
            timeout: Seconds to wait when insufficient data exists or ``wait_for_new_samples=True``.

        Raises:
            RuntimeError: No background buffer; ``start()`` was not called.
            ChannelNotFoundError:
                channel had no values and no data appeared before ``timeout``.
                ``wait_for_new_samples=True`` and channel did not appear within ``timeout``.
            ChannelValueTimeoutError: ``wait_for_new_samples=True`` and values did not arrive within ``timeout``.
        """
        if self._background_thread and self._background_thread.is_alive():
            assert self._channel_buffer
            if not channel_name.startswith(f"{self.name}."):
                channel_name = f"{self.name}.{channel_name}"
            return self._channel_buffer.get(channel_name, length, wait_for_new_samples, timeout)

        raise RuntimeError("No channel buffer exists. Ensure start() was called on this instrument.")

    def get_single_channel_value(self, channel_name: str) -> float | None:
        """Return the most recent sample for ``channel_name`` from the in-memory buffer.

        This will not wait, if data is not available then ``None`` is returned.

        Args:
            channel_name: Name of the channel to retrieve.

        Raises:
            RuntimeError: No background buffer; ``start()`` was not called.
        """
        try:
            cached_measurement = self.get_channel(channel_name, length=1, wait_for_new_samples=False, timeout=0)
            return cached_measurement.channel_data[channel_name][0]
        except RuntimeError:  # expect: this means instrument not started, good to report
            raise
        except:  # other exceptions just mean data isn't available
            pass

        return None

    def _background_daemon_loop(self):
        """Daemon loop: run registered functions every ``background_interval``, publish loop timing."""
        while not self._background_stop_event.is_set():
            daemon_loop_start = time.time_ns()
            self._background_daemon()
            daemon_work_stop = time.time_ns()

            daemon_work_time_s = (daemon_work_stop - daemon_loop_start) * 1e-9
            self._background_stop_event.wait(max(0, self._background_config.interval - daemon_work_time_s))

            daemon_loop_stop = time.time_ns()
            daemon_loop_time_s = (daemon_loop_stop - daemon_loop_start) * 1e-9
            self._publish_daemon_timing(daemon_loop_time_s, daemon_work_time_s, daemon_loop_stop)

    def _background_daemon(self):
        """Invoke each registered daemon function once.

        Exceptions are caught per-function so one bad daemon doesn't stop the others.
        """
        for method, args, kwargs in self._background_methods:
            try:
                method(*args, **kwargs)
            except Exception as e:
                if self._background_stop_event.is_set():
                    return
                logger.exception(
                    "Background daemon error in instrument '%s' while calling %s: %s",
                    self.name,
                    method.__name__,
                    e,
                )

    def define_background_daemon(self, method: Callable, *args, **kwargs):
        """Replace all daemon functions with a single ``method`` (called with the given args)."""
        self._background_methods.clear()
        self.add_background_daemon_function(method, *args, **kwargs)
