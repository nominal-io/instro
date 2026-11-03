"""File storage mechanisms."""

import abc
import tempfile
from pathlib import Path


class Storage(abc.ABC):
    """Base class for data storage."""

    @abc.abstractmethod
    def get_path_for_filename(self, filename: str) -> Path:
        """Get the path to the file for the specified name."""
        ...


class DiskStorage(Storage):
    """Class to handle local disk storage of  data."""

    def __init__(
        self,
        path: str | None = None,
    ):
        if path is not None:
            self.storage_path = Path(path)
            self.storage_path.mkdir(parents=True, exist_ok=True)
        else:
            # get a temp dir
            self.storage_path = Path(tempfile.mkdtemp())

    def get_path_for_filename(self, filename: str | Path) -> Path:
        """Get the path to the file for the specified name."""
        return self.storage_path / Path(filename)
