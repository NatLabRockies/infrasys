import functools
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import datetime
from functools import singledispatchmethod
from pathlib import Path
from typing import Any, Generator, Literal, Optional

import h5py
from dateutil.relativedelta import relativedelta
from loguru import logger

from infrasys.exceptions import ISNotStored
from infrasys.time_series_models import (
    Deterministic,
    DeterministicMetadata,
    DeterministicTimeSeriesType,
    SingleTimeSeries,
    SingleTimeSeriesMetadata,
    TimeSeriesData,
    TimeSeriesMetadata,
    TimeSeriesStorageType,
)
from infrasys.time_series_storage_base import TimeSeriesStorageBase
from infrasys.utils.h5_utils import copy_h5_group, extract_h5_dataset_to_bytes, open_h5_file
from infrasys.utils.sqlite import create_in_memory_db
from infrasys.utils.time_utils import to_iso_8601

from .time_series_metadata_store import TimeSeriesMetadataStore

TIME_SERIES_DATA_FORMAT_VERSION = "1.0.0"
TIME_SERIES_VERSION_KEY = "data_format_version"


def file_handle(func):
    """Decorator to ensure a valid HDF5 file handle (context) is available."""

    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        context = kwargs.pop("context", None)
        if context is not None:
            return func(self, *args, context=context, **kwargs)
        else:
            with self.open_time_series_store() as file_handle:
                return func(self, *args, context=file_handle, **kwargs)

    return wrapper


class HDF5TimeSeriesStorage(TimeSeriesStorageBase):
    """Stores time series in an h5 file."""

    STORAGE_FILE = "time_series_storage.h5"
    HDF5_TS_ROOT_PATH = "time_series"
    HDF5_TS_METADATA_ROOT_PATH = "time_series_metadata"

    def __init__(
        self,
        directory: Path,
        **kwargs,
    ) -> None:
        """Initialize the HDF5 time series storage.

        Parameters
        ----------
        directory : Path
            Directory to store the HDF5 file
        """
        self.directory = directory
        self._fpath = self.directory / self.STORAGE_FILE
        self._file_handle = None
        self._check_root()

    @classmethod
    def deserialize(
        cls,
        data: dict[str, Any],
        time_series_dir: Path,
        dst_time_series_directory: Path | None,
        read_only: bool,
        **kwargs: Any,
    ) -> tuple["HDF5TimeSeriesStorage", "TimeSeriesMetadataStore"]:
        """Deserialize HDF5 storage from serialized data."""

        # Copy the HDF5 file to a temporary or permanent location before the
        # temp directory is cleaned up
        if dst_time_series_directory is not None:
            dst_dir = dst_time_series_directory
            dst_dir.mkdir(parents=True, exist_ok=True)
        else:
            import tempfile

            dst_dir = Path(tempfile.mkdtemp())

        src_h5_file = time_series_dir / cls.STORAGE_FILE
        dst_h5_file = dst_dir / cls.STORAGE_FILE

        if src_h5_file.exists():
            shutil.copy2(src_h5_file, dst_h5_file)

            logger.debug("Copied HDF5 file from {} to {}", src_h5_file, dst_h5_file)

        storage = cls(directory=dst_dir, **kwargs)
        metadata_store = TimeSeriesMetadataStore(storage.get_metadata_store(), initialize=False)
        return storage, metadata_store

    @contextmanager
    def open_time_series_store(
        self, mode: Literal["r", "r+", "a", "w", "w-"] = "a"
    ) -> Generator[h5py.File, None, None]:
        assert self._fpath
        self._file_handle = None

        # H5PY ensures closing of the file after the with statement.
        with open_h5_file(self._fpath, mode=mode) as file_handle:
            yield file_handle

    def get_time_series_directory(self) -> Path:
        return self.directory

    def _check_root(self) -> None:
        """Check the root group exist on the hdf5."""
        with self.open_time_series_store() as file_handle:
            if self.HDF5_TS_ROOT_PATH not in file_handle:
                root = file_handle.create_group(self.HDF5_TS_ROOT_PATH)
                root.attrs[TIME_SERIES_VERSION_KEY] = TIME_SERIES_DATA_FORMAT_VERSION

            if self.HDF5_TS_METADATA_ROOT_PATH not in file_handle:
                file_handle.create_group(self.HDF5_TS_METADATA_ROOT_PATH)
        return

    def _serialize_compression_settings(self, compression_level: int = 5) -> None:
        """Add default compression settings."""
        with self.open_time_series_store() as file_handle:
            root = file_handle[self.HDF5_TS_ROOT_PATH]
            root.attrs["compression_enabled"] = False
            root.attrs["compression_type"] = "DEFLATE"
            root.attrs["compression_level"] = compression_level
            root.attrs["compression_shuffle"] = True
        return None

    @staticmethod
    def add_serialized_data(data: dict[str, Any]) -> None:
        """Add metadata to indicate the storage type.

        Parameters
        ----------
        data : dict[str, Any]
            Metadata dictionary to which the storage type will be added

        Notes
        -----
        This method adds a key `time_series_storage_type` with the value
        corresponding to the storage type `HDF5` to the metadata dictionary.
        """
        data["time_series_storage_type"] = str(TimeSeriesStorageType.HDF5)

    def add_time_series(
        self,
        metadata: TimeSeriesMetadata,
        time_series: TimeSeriesData,
        context: Any = None,
        compression_level: int = 5,
    ) -> None:
        """Store a time series array.

        Parameters
        ----------
        metadata : infrasys.time_series_models.TimeSeriesMetadata
            Metadata for the time series
        time_series : infrasys.time_series_models.TimeSeriesData
            Time series data to store
        context : Any, optional
            Optional context parameter, by default None
        compression_level: int, defaults to 5
            Optional compression level for `gzip` (0 for no compression, 10, for max compression)

        See Also
        --------
        _add_time_series_dispatch : Dispatches the call to the correct handler based on metadata type.
        """
        if context is not None:
            self._add_time_series_dispatch(
                metadata, time_series, context=context, compression_level=compression_level
            )
        else:
            with self.open_time_series_store() as file_handle:
                self._add_time_series_dispatch(
                    metadata, time_series, context=file_handle, compression_level=compression_level
                )

    @singledispatchmethod
    def _add_time_series_dispatch(
        self,
        metadata: TimeSeriesMetadata,
        time_series: TimeSeriesData,
        context: Any = None,
        compression_level: int = 5,
    ) -> None:
        """Dispatches the call to the correct handler based on metadata type.

        Parameters
        ----------
        metadata : infrasys.time_series_models.TimeSeriesMetadata
            Metadata for the time series
        time_series : infrasys.time_series_models.TimeSeriesData
            Time series data to store
        context : Any, optional
            Optional context parameter, by default None
        compression_level: int, defaults to 5
            Optional compression level for `gzip` (0 for no compression, 10, for max compression)

        Raises
        ------
        NotImplementedError
            If no handler is implemented for the given metadata type
        """
        msg = f"Bug: need to implement add_time_series for {type(metadata)}"
        raise NotImplementedError(msg)

    @_add_time_series_dispatch.register(SingleTimeSeriesMetadata)
    def _(
        self,
        metadata: SingleTimeSeriesMetadata,
        time_series: SingleTimeSeries,
        context: Any = None,
        compression_level: int = 5,
        **kwargs: Any,
    ) -> None:
        """Store a SingleTimeSeries array.

        Parameters
        ----------
        metadata : infrasys.time_series_models.SingleTimeSeriesMetadata
            Metadata for the single time series
        time_series : infrasys.time_series_models.SingleTimeSeries
            Single time series data to store
        context : Any
            HDF5 file handle
        compression_level: int, defaults to 5
            Optional compression level for `gzip` (0 for no compression, 10, for max compression)

        See Also
        --------
        add_time_series : Public method for adding time series.
        """
        assert context is not None

        root = context[self.HDF5_TS_ROOT_PATH]
        uuid = str(metadata.time_series_uuid)

        if uuid not in root:
            group = root.create_group(uuid)

            group.create_dataset(
                "data", data=time_series.data_array, compression=compression_level
            )

            group.attrs["type"] = metadata.type
            group.attrs["initial_timestamp"] = metadata.initial_timestamp.isoformat()
            group.attrs["resolution"] = (
                to_iso_8601(metadata.resolution)
                if isinstance(metadata.resolution, relativedelta)
                else metadata.resolution.total_seconds()
            )

            # NOTE: This was added for compatibility with
            # InfrastructureSystems. In reality, this should not affect any
            # other implementation
            group.attrs["module"] = "InfrastructureSystems"
            group.attrs["data_type"] = "Float64"

    @_add_time_series_dispatch.register(DeterministicMetadata)
    def _(
        self,
        metadata: DeterministicMetadata,
        time_series: Deterministic,
        context: Any = None,
        compression_level: int = 5,
        **kwargs: Any,
    ) -> None:
        """Store a Deterministic array.

        Parameters
        ----------
        metadata : infrasys.time_series_models.DeterministicMetadata
            Metadata for the deterministic time series
        time_series : infrasys.time_series_models.DeterministicTimeSeries
            Deterministic time series data to store
        context : Any
            HDF5 file handle
        compression_level: int, defaults to 5
            Optional compression level for `gzip` (0 for no compression, 10, for max compression)

        See Also
        --------
        add_time_series : Public method for adding time series.
        """
        assert context is not None

        root = context[self.HDF5_TS_ROOT_PATH]
        uuid = str(metadata.time_series_uuid)

        if uuid not in root:
            group = root.create_group(uuid)

            group.create_dataset(
                "data", data=time_series.data_array, compression=compression_level
            )

            group.attrs["type"] = metadata.type
            group.attrs["initial_timestamp"] = metadata.initial_timestamp.isoformat()
            group.attrs["resolution"] = metadata.resolution.total_seconds()
            group.attrs["horizon"] = metadata.horizon.total_seconds()
            group.attrs["interval"] = metadata.interval.total_seconds()
            group.attrs["window_count"] = metadata.window_count

    def get_metadata_store(self) -> sqlite3.Connection:
        """Get the metadata store.

        Returns
        -------
        TimeSeriesMetadataStore
            The metadata store
        """
        with self.open_time_series_store() as file_handle:
            ts_metadata = extract_h5_dataset_to_bytes(file_handle, self.HDF5_TS_METADATA_ROOT_PATH)
            conn = create_in_memory_db(":memory:")
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                temp_file_path = tmp.name
                tmp.write(ts_metadata)
                backup_conn = sqlite3.connect(temp_file_path)
                with conn:
                    backup_conn.backup(conn)
                backup_conn.close()
        return conn

    def get_time_series(
        self,
        metadata: TimeSeriesMetadata,
        start_time: Optional[datetime] = None,
        length: Optional[int] = None,
        context: Any = None,
    ) -> TimeSeriesData:
        """Return a time series array using the appropriate handler based on metadata type."""
        if context is not None:
            return self._get_time_series_dispatch(metadata, start_time, length, context=context)
        else:
            with self.open_time_series_store() as file_handle:
                return self._get_time_series_dispatch(
                    metadata, start_time, length, context=file_handle
                )

    @singledispatchmethod
    def _get_time_series_dispatch(
        self,
        metadata: TimeSeriesMetadata,
        start_time: Optional[datetime] = None,
        length: Optional[int] = None,
        context: Any = None,
    ) -> TimeSeriesData:
        msg = f"Bug: need to implement get_time_series for {type(metadata)}"
        raise NotImplementedError(msg)

    @_get_time_series_dispatch.register(SingleTimeSeriesMetadata)
    def _(
        self,
        metadata: SingleTimeSeriesMetadata,
        start_time: Optional[datetime] = None,
        length: Optional[int] = None,
        context: Any = None,
    ) -> SingleTimeSeries:
        """Return a SingleTimeSeries array."""
        assert context is not None

        root = context[self.HDF5_TS_ROOT_PATH]
        uuid = str(metadata.time_series_uuid)

        if uuid not in root:
            msg = f"Time series with {uuid=} not found"
            raise ISNotStored(msg)

        dataset = root[uuid]["data"]

        index, length = metadata.get_range(start_time=start_time, length=length)
        data = dataset[index : index + length]
        if metadata.units is not None:
            data = metadata.units.quantity_type(data, metadata.units.units)
        return SingleTimeSeries(
            uuid=metadata.time_series_uuid,
            name=metadata.name,
            resolution=metadata.resolution,
            initial_timestamp=start_time or metadata.initial_timestamp,
            data=data,
            normalization=metadata.normalization,
        )

    @_get_time_series_dispatch.register(DeterministicMetadata)
    def _(
        self,
        metadata: DeterministicMetadata,
        start_time: Optional[datetime] = None,
        length: Optional[int] = None,
        context: Any = None,
    ) -> DeterministicTimeSeriesType:
        """Return a Deterministic time series array."""
        assert context is not None

        root = context[self.HDF5_TS_ROOT_PATH]
        uuid = str(metadata.time_series_uuid)

        if uuid not in root:
            msg = f"Time series with {uuid=} not found"
            raise ISNotStored(msg)

        dataset = root[uuid]["data"]
        data = dataset[:]

        # Regular Deterministic with stored multidimensional data
        if data.ndim == 1:
            msg = "Single-dimensional data is not supported for Deterministic time series"
            raise ValueError(msg)

        if metadata.units is not None:
            data = metadata.units.quantity_type(data, metadata.units.units)

        return Deterministic(
            uuid=metadata.time_series_uuid,
            name=metadata.name,
            resolution=metadata.resolution,
            initial_timestamp=metadata.initial_timestamp,
            horizon=metadata.horizon,
            interval=metadata.interval,
            window_count=metadata.window_count,
            data=data,
            normalization=metadata.normalization,
        )

    @file_handle
    def remove_time_series(self, metadata: TimeSeriesMetadata, context: Any = None) -> None:
        """Remove a time series array.

        Parameters
        ----------
        metadata : infrasys.time_series_models.TimeSeriesMetadata
            Metadata for the time series to remove.
        context : Any, optional
            Optional HDF5 file handle; if not provided, one is opened.

        Raises
        ------
        ISNotStored
            If the time series with the specified UUID doesn't exist.
        """
        root = context[self.HDF5_TS_ROOT_PATH]
        uuid = str(metadata.time_series_uuid)
        if uuid not in root:
            msg = f"Time series with {uuid=} not found"
            raise ISNotStored(msg)
        del root[uuid]
        meta_group = context[self.HDF5_TS_METADATA_ROOT_PATH]
        if uuid in meta_group:
            del meta_group[uuid]

    def serialize(
        self, data: dict[str, Any], dst: Path | str, src: Optional[Path | str] = None
    ) -> None:
        """Serialize all time series to the destination directory.

        Parameters
        ----------
        data : dict[str, Any]
            Additional data to serialize (not used in this implementation)
        dst : Path or str
            Destination directory or file path
        src : Path or str, optional
            Optional source directory or file path

        Notes
        -----
        This implementation copies the entire time series storage directory to the destination.
        """
        dst_path = Path(dst) / self.STORAGE_FILE if Path(dst).is_dir() else Path(dst)
        self.output_file = dst_path
        self._serialize_compression_settings()
        with self.open_time_series_store() as f:
            with open_h5_file(dst_path, mode="a") as dst_file:
                if self.HDF5_TS_ROOT_PATH in f:
                    src_group = f[self.HDF5_TS_ROOT_PATH]
                    if isinstance(src_group, h5py.Group):
                        if self.HDF5_TS_ROOT_PATH in dst_file:
                            del dst_file[self.HDF5_TS_ROOT_PATH]
                        dst_group = dst_file.create_group(self.HDF5_TS_ROOT_PATH)
                        copy_h5_group(src_group, dst_group)
                    if self.HDF5_TS_METADATA_ROOT_PATH in dst_file:
                        del dst_file[self.HDF5_TS_METADATA_ROOT_PATH]
        data["time_series_storage_file"] = str(dst_path)
        self.add_serialized_data(data)
