"""Publisher that streams Measurement/Command data to Nominal Core datasets."""

import inspect
import pathlib
from datetime import timedelta
from typing import Literal

from nominal_streaming import NominalDatasetStream

from instro.lib.types import Command, Measurement

from ..nominal import _resolve_nominal_client_and_dataset


class NominalCorePublisher:
    def __init__(
        self,
        dataset_rid: str,
        batch_size: int | None = None,
        max_wait: timedelta | None = None,
        file_fallback: pathlib.Path | None = None,
        profile: str | None = None,
        api_key: str | None = None,
    ):
        """Stream Measurement/Command data to a Nominal Core dataset.

        Omitted optional args fall through to the Nominal Core Python API defaults.

        Args:
            dataset_rid: Target dataset RID.
            batch_size: Max items per write batch.
            max_wait: Max time a batch can age before being flushed.
            file_fallback: ``.avro`` path used when connectivity is intermittent.
            profile: Named profile from the on-disk config (defaults to ``"default"``).
            api_key: Inline API key; falls back to on-disk credentials when omitted.
                See https://docs.nominal.io/core/sdk/python-client/authentication#using-the-api-key
        """
        self._rid = dataset_rid

        # Enforce valid configuration for file fallback functionality
        data_format: Literal["rust_experimental"] | None = "rust_experimental"
        if file_fallback is not None:
            if not str(file_fallback).endswith(".avro"):
                raise ValueError(f"The 'file_fallback' path must end with '.avro'. You provided: '{file_fallback}'.")

        self._client, self._dataset = _resolve_nominal_client_and_dataset(self._rid, profile, api_key)

        ws_signature = inspect.signature(self._dataset.get_write_stream)

        self._write_stream = self._dataset.get_write_stream(
            batch_size=batch_size or ws_signature.parameters["batch_size"].default,
            max_wait=max_wait or ws_signature.parameters["max_wait"].default,
            data_format=data_format or ws_signature.parameters["data_format"].default,
            file_fallback=file_fallback or ws_signature.parameters["file_fallback"].default,
        )

        # We need to open the stream manually here because we are using the "rust_experimental" data format
        assert isinstance(self._write_stream, NominalDatasetStream)
        # `NominalDatasetStream.open()` installs a process-wide SIGINT handler
        # that calls `self._impl.cancel()` (see nominal_streaming/nominal_dataset_stream.py).
        # If Ctrl-C fires while another thread is calling `enqueue_batch()`, that async
        # `cancel()` can race the in-flight call and trigger Rust's "Already mutably borrowed" error.
        #
        # We prevent this by opening the underlying stream without installing Nominal's
        # SIGINT handler. This requires us to call the _impl.open() method directly.
        # This is a bit of a hack, but it's the only way to prevent the race condition.
        self._write_stream._impl.open()

    def publish(self, data: Measurement | Command, **kwargs):
        if isinstance(data, Measurement):
            self.__publish_measurement(data)
        elif isinstance(data, Command):
            self.__publish_command(data)

    def __publish_measurement(self, data: Measurement):
        for ch_name in data.channel_data:
            self._write_stream.enqueue_batch(
                channel_name=ch_name,
                timestamps=data.timestamps,
                values=data.channel_data[ch_name],
                tags=data.tags,
            )

    def __publish_command(self, data: Command):
        for ch_name in data.channel_data:
            self._write_stream.enqueue_batch(
                channel_name=ch_name,
                timestamps=[data.timestamp],
                values=[data.channel_data[ch_name]],
                tags=data.tags,
            )

    def close(self):
        self._write_stream.close()
