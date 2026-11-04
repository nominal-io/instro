"""Publisher that streams Measurement/Command data to Nominal Core datasets."""

import inspect
import logging
import pathlib
from datetime import timedelta
from typing import Literal

from nominal.core import Dataset, NominalClient
from nominal_streaming import NominalDatasetStream

from instro.lib import _display
from instro.lib.types import Command, Measurement

from ..nominal import _resolve_nominal_client_and_dataset

logger = logging.getLogger(__name__)


class NominalCorePublisher:
    def __init__(
        self,
        dataset_rid: str | None = None,
        batch_size: int | None = None,
        max_wait: timedelta | None = None,
        file_fallback: pathlib.Path | None = None,
        profile: str | None = None,
        api_key: str | None = None,
        *,
        dataset: Dataset | None = None,
        client: NominalClient | None = None,
        base_url: str | None = None,
    ):
        """Stream Measurement/Command data to a Nominal dataset.

        Name the dataset with ``dataset_rid`` or pass a `Dataset` you already fetched as ``dataset``. Credentials
        come from, in order: ``client``, ``api_key`` (with ``base_url``), or the on-disk ``profile``, which
        defaults to ``"default"``. Omitted optional args fall through to the Nominal Python client defaults.

        Args:
            dataset_rid: Target dataset RID.
            batch_size: Max items per write batch.
            max_wait: Max time a batch can age before being flushed.
            file_fallback: ``.avro`` path used when connectivity is intermittent.
            profile: Named profile from the on-disk config (defaults to ``"default"``).
            api_key: Inline API key; falls back to on-disk credentials when omitted.
                See https://docs.nominal.io/core/sdk/python-client/authentication#using-the-api-key
            dataset: An already-fetched dataset to write to, instead of ``dataset_rid``.
            client: An existing `NominalClient` to fetch ``dataset_rid`` with, instead of ``profile`` or ``api_key``.
            base_url: API base URL for ``api_key``, such as ``"https://api.gov.nominal.io/api"``.

        Raises:
            ValueError: If both or neither of ``dataset_rid`` and ``dataset`` are given, or credential options
                conflict.

        Example:
            .. code-block:: python

                from nominal.core import NominalClient
                from instro.lib.publishers import NominalCorePublisher

                publisher = NominalCorePublisher(dataset_rid="ri.catalog.main.dataset.abc123", profile="staging")

                client = NominalClient.from_profile("staging")
                publisher = NominalCorePublisher(dataset=client.get_dataset("ri.catalog.main.dataset.abc123"))
        """
        if (dataset_rid is None) == (dataset is None):
            raise ValueError("Pass exactly one of dataset_rid or dataset.")
        if file_fallback is not None and not str(file_fallback).endswith(".avro"):
            raise ValueError(f"The 'file_fallback' path must end with '.avro'. You provided: '{file_fallback}'.")

        self.client: NominalClient | None
        """The client used to fetch the dataset, or `None` when ``dataset`` was passed in directly."""
        self.dataset: Dataset
        if dataset is not None:
            if profile or api_key or base_url:
                raise ValueError("A Dataset carries its own credentials; don't pass profile, api_key or base_url.")
            self.client = client
            self.dataset = dataset
        else:
            assert dataset_rid is not None
            self.client, self.dataset = _resolve_nominal_client_and_dataset(
                dataset_rid, profile, api_key, client=client, base_url=base_url
            )

        self._client_provided: bool = client is not None
        self.dataset_rid: str = self.dataset.rid
        self.profile: str | None = None if (dataset or client or api_key) else (profile or "default")
        """The on-disk profile the credentials came from, or `None` when they came from elsewhere."""
        self.base_url: str | None = base_url
        self.file_fallback: pathlib.Path | None = file_fallback
        self.closed: bool = False

        ws_signature = inspect.signature(self.dataset.get_write_stream)
        self.batch_size: int = batch_size if batch_size is not None else ws_signature.parameters["batch_size"].default
        self.max_wait: timedelta = max_wait if max_wait is not None else ws_signature.parameters["max_wait"].default

        data_format: Literal["rust_experimental"] = "rust_experimental"
        self.write_stream: NominalDatasetStream
        write_stream = self.dataset.get_write_stream(
            batch_size=self.batch_size,
            max_wait=self.max_wait,
            data_format=data_format,
            file_fallback=file_fallback
            if file_fallback is not None
            else ws_signature.parameters["file_fallback"].default,
        )

        # We need to open the stream manually here because we are using the "rust_experimental" data format
        assert isinstance(write_stream, NominalDatasetStream)
        self.write_stream = write_stream
        # `NominalDatasetStream.open()` installs a process-wide SIGINT handler
        # that calls `self._impl.cancel()` (see nominal_streaming/nominal_dataset_stream.py).
        # If Ctrl-C fires while another thread is calling `enqueue_batch()`, that async
        # `cancel()` can race the in-flight call and trigger Rust's "Already mutably borrowed" error.
        #
        # We prevent this by opening the underlying stream without installing Nominal's
        # SIGINT handler. This requires us to call the _impl.open() method directly.
        # This is a bit of a hack, but it's the only way to prevent the race condition.
        #
        # TODO: nominal-streaming 0.9.x replaced that `cancel()` with `stop_accepting_writes()`,
        # so the race is gone and this hack now suppresses the graceful Ctrl-C drain. Switch to
        # the public `open()` once the behavior change has been validated on real hardware.
        self.write_stream._impl.open()

    def publish(self, data: Measurement | Command, **kwargs):
        if isinstance(data, Measurement):
            self.__publish_measurement(data)
        elif isinstance(data, Command):
            self.__publish_command(data)

    def __publish_measurement(self, data: Measurement):
        for ch_name in data.channel_data:
            self.write_stream.enqueue_batch(
                channel_name=ch_name,
                timestamps=data.timestamps,
                values=data.channel_data[ch_name],
                tags=data.tags,
            )

    def __publish_command(self, data: Command):
        for ch_name in data.channel_data:
            self.write_stream.enqueue_batch(
                channel_name=ch_name,
                timestamps=[data.timestamp],
                values=[data.channel_data[ch_name]],
                tags=data.tags,
            )

    def close(self):
        if self.closed:
            logger.warning(
                "Dropping close request because NominalCorePublisher is already closed (dataset_rid=%s)",
                self.dataset_rid,
            )
            return
        self.write_stream.close()
        self.closed = True

    def _credentials(self) -> str:
        if self.profile is not None:
            return f"profile {self.profile!r}"
        if self._client_provided:
            return "provided client"
        if self.client is None:
            return "from the dataset passed in"
        return "API key" if self.base_url is None else f"API key, {self.base_url}"

    def __repr__(self) -> str:
        """Name the dataset and where the credentials came from; never the API key."""
        return (
            f"NominalCorePublisher(dataset={self.dataset.name!r}, dataset_rid={self.dataset_rid!r}, "
            f"credentials={self._credentials()!r}, closed={self.closed})"
        )

    def _display_summary(self) -> str:
        dataset = _display.named(self.dataset.name, self.dataset_rid, lambda: self.dataset.nominal_url)
        state = " (closed)" if self.closed else ""
        return f"Nominal dataset {dataset}{_display.escape(state)}"

    def _repr_html_(self) -> str | None:
        """Show the dataset, with a link to it in Nominal, and the stream settings."""
        return _display.render(
            kind="NominalCorePublisher",
            title=self.dataset.name,
            fallback=repr(self),
            fields=[
                ("Dataset RID", _display.identifier(self.dataset_rid)),
                ("Credentials", _display.escape(self._credentials())),
                ("Batch size", _display.escape(self.batch_size)),
                ("Max wait", _display.seconds(self.max_wait.total_seconds())),
                ("File fallback", _display.code(self.file_fallback) if self.file_fallback else None),
            ],
            badges=["closed"] if self.closed else [],
            url=lambda: self.dataset.nominal_url,
        )
