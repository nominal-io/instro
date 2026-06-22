# This code is derived from concepts in the py-lab-hal codebase (Apache 2.0 licensed)
# Original py-lab-hal repository: https://github.com/google/py-lab-hal

"""Nominal Core integration helpers: client/dataset resolution and Measurement shaping."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, TypedDict

import requests.exceptions
import urllib3.exceptions
from nominal import exceptions
from nominal.core import Dataset, NominalClient
from nominal.experimental.logging import install_nominal_log_handler as _install_nominal_log_handler

from instro.lib.types import Measurement


class CoreEnqueueDict(TypedDict):
    timestamp: int
    channel_values: dict[str, float]
    tags: dict[str, str] | None


class CoreEnqueueBatchDict(TypedDict):
    channel_name: str
    timestamps: list[int]
    values: list[float]
    tags: dict[str, str] | None


def _resolve_nominal_client_and_dataset(
    dataset_rid: str,
    profile: str | None = None,
    api_key: str | None = None,
) -> tuple[NominalClient, Dataset]:
    """Resolve a NominalClient and dataset from a dataset RID."""
    try:
        resolved_client = (
            NominalClient.from_token(api_key)
            if api_key
            else NominalClient.from_profile(profile if profile else "default")
        )
    except (exceptions.NominalConfigError, FileNotFoundError) as e:
        url = "https://docs.nominal.io/core/sdk/python-client/authentication"
        raise RuntimeError(
            f"Failed to create NominalClient: {e}.\n\n"
            "Please check your API key or Nominal profile. "
            "If this is your first time using the Nominal Core API, "
            "you need to generate an API key and store your credentials on disk or pass them directly.\n\n"
            f"See \033]8;;{url}\033\\{url}\033]8;;\033\\ for instructions and more information.\n"
        ) from e

    try:
        dataset = resolved_client.get_dataset(dataset_rid)
    except (
        ConnectionError,
        urllib3.exceptions.MaxRetryError,
        requests.exceptions.ConnectionError,
    ) as e:
        raise ConnectionError(
            "Could not connect to Nominal Core to fetch target dataset.\n"
            "Please check your network connection to Nominal Core.\n"
            "Alternatively, use the local FilePublisher to write to a local file instead."
        ) from e

    return resolved_client, dataset


def install_nominal_core_log_handler(
    dataset_rid: str,
    *,
    log_channel: str = "logs",
    level: int = logging.INFO,
    logger: logging.Logger | None = None,
    default_args: Mapping[str, str] | None = None,
) -> Any:
    """Install Nominal's logging handler, looking up the dataset by RID."""
    _, dataset = _resolve_nominal_client_and_dataset(dataset_rid)
    return _install_nominal_log_handler(
        dataset=dataset,
        log_channel=log_channel,
        level=level,
        logger=logger,
        default_args=default_args,
    )


def measurement_to_core_enqueue_from_dict(
    measurement: Measurement,
) -> list[CoreEnqueueDict]:
    """Shape a Measurement into per-timestamp dicts for ``NominalClient.enqueue_from_dict()``."""
    measurements = []
    for i, timestamp in enumerate(measurement.timestamps):
        channel_values = {channel: data[i] for channel, data in measurement.channel_data.items()}
        measurements.append(
            CoreEnqueueDict(
                timestamp=timestamp,
                channel_values=channel_values,
                tags=measurement.tags,
            )
        )

    return measurements


def measurement_to_core_enqueue_batch(
    measurement: Measurement,
) -> list[CoreEnqueueBatchDict]:
    """Shape a Measurement into per-channel dicts for ``NominalClient.enqueue_batch()``."""
    measurements = []
    for channel, data in measurement.channel_data.items():
        measurements.append(
            CoreEnqueueBatchDict(
                channel_name=channel,
                timestamps=measurement.timestamps,
                values=data,
                tags=measurement.tags,
            )
        )

    return measurements
