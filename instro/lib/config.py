"""Shared Pydantic config blocks for JSON config-driven instrument construction."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from instro.lib.publishers import Publisher

__all__ = [
    "FilePublisherConfig",
    "NominalCorePublisherConfig",
    "PublisherConfigType",
    "TimingConfig",
    "build_publisher",
    "load_config",
]

ConfigT = TypeVar("ConfigT", bound=BaseModel)


def load_config(config: ConfigT | dict | Path | str, model_cls: type[ConfigT]) -> ConfigT:
    """Validate ``config`` (a dict, file path, or existing ``model_cls`` instance, which gets deep-copied) into ``model_cls``."""
    if isinstance(config, dict):
        return model_cls.model_validate(config)
    if isinstance(config, (Path, str)):
        with open(Path(config)) as f:
            return model_cls.model_validate(json.load(f))
    return config.model_copy(deep=True)


class TimingConfig(BaseModel):
    """Timing configuration for instrument background polling."""

    model_config = ConfigDict(extra="forbid")
    poll_interval: float = Field(ge=0.01, description="Polling interval in seconds")


class NominalCorePublisherConfig(BaseModel):
    """Publisher config for streaming to a Nominal Core dataset."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["NominalCorePublisher"] = "NominalCorePublisher"
    dataset_rid: str = Field(description="Target Nominal Core dataset RID.")
    batch_size: int | None = Field(default=None, description="Publish batch size override.")
    profile: str | None = Field(
        default=None, description="On-disk Nominal credential profile name; defaults to 'default'."
    )
    max_wait_s: float | None = Field(
        default=None, gt=0, description="Max seconds a batch can age before being flushed."
    )
    file_fallback: str | None = Field(
        default=None, description="Path of an .avro file written to when Nominal can't be reached."
    )


class FilePublisherConfig(BaseModel):
    """Publisher config for writing measurements to a local file."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["FilePublisher"] = "FilePublisher"
    directory: str = Field(description="Output directory for the written file.")
    format: Literal["json", "jsonl", "csv", "avro"] = "avro"
    custom_file_name: str | None = Field(default=None, description="Filename without extension.")


PublisherConfigType = Annotated[NominalCorePublisherConfig | FilePublisherConfig, Field(discriminator="type")]


def build_publisher(config: NominalCorePublisherConfig | FilePublisherConfig) -> Publisher:
    """Construct the runtime Publisher described by a publisher config block."""
    from instro.lib.publishers import FilePublisher, NominalCorePublisher

    if isinstance(config, NominalCorePublisherConfig):
        return NominalCorePublisher(
            dataset_rid=config.dataset_rid,
            batch_size=config.batch_size,
            max_wait=timedelta(seconds=config.max_wait_s) if config.max_wait_s is not None else None,
            file_fallback=Path(config.file_fallback) if config.file_fallback is not None else None,
            profile=config.profile,
        )
    return FilePublisher(directory=config.directory, format=config.format, custom_file_name=config.custom_file_name)
