"""Defines the base class for time series storage."""

import abc
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Generator, Literal, Optional

from infrasys.time_series_models import TimeSeriesData, TimeSeriesMetadata


class TimeSeriesStorageBase(abc.ABC):
    """Base class for time series storage"""

    def validate_time_series(self, time_series: TimeSeriesData) -> None:
        """Validate backend compatibility before metadata is committed."""

    @abc.abstractmethod
    def add_time_series(
        self,
        metadata: TimeSeriesMetadata,
        time_series: TimeSeriesData,
        context: Any = None,
    ) -> None:
        """Store a time series array."""

    @abc.abstractmethod
    def get_time_series_directory(self) -> Path | None:
        """Return the directory containing time series files. Will be None for in-memory time
        series.
        """

    @abc.abstractmethod
    def get_time_series(
        self,
        metadata: TimeSeriesMetadata,
        start_time: datetime | None = None,
        length: int | None = None,
        context: Any = None,
    ) -> TimeSeriesData:
        """Return a time series array."""

    @abc.abstractmethod
    def remove_time_series(self, metadata: TimeSeriesMetadata, context: Any = None) -> None:
        """Remove a time series array."""

    @abc.abstractmethod
    def serialize(
        self, data: dict[str, Any], dst: Path | str, src: Optional[Path | str] = None
    ) -> None:
        """Serialize all time series to the destination directory."""

    @classmethod
    @abc.abstractmethod
    def deserialize(
        cls,
        data: dict[str, Any],
        time_series_dir: Path,
        dst_time_series_directory: Path | None,
        read_only: bool,
        **kwargs: Any,
    ) -> tuple["TimeSeriesStorageBase", Optional[Any]]:
        """Deserialize time series storage from serialized data.

        Parameters
        ----------
        data : dict[str, Any]
            Serialized storage data
        time_series_dir : Path
            Directory containing the serialized time series files
        dst_time_series_directory : Path | None
            Destination directory for time series files (None for temp directory)
        read_only : bool
            Whether to open in read-only mode
        **kwargs : Any
            Additional storage-specific parameters

        Returns
        -------
        tuple[TimeSeriesStorageBase, Optional[Any]]
            A tuple of (storage instance, optional metadata store)
            The metadata store is only used by HDF5 storage backend
        """

    @contextmanager
    def open_time_series_store(
        self, mode: Literal["r", "r+", "a", "w", "w-"] = "a"
    ) -> Generator[Any, None, None]:
        """Open a connection to the time series store."""
        yield None
