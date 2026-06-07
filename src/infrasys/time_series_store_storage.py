"""Time series storage backed by the time-series-store Rust extension."""

import atexit
import shutil
from datetime import datetime, timezone
from pathlib import Path
from tempfile import mkdtemp
from typing import Any

import numpy as np
from loguru import logger
from time_series_store import (  # type: ignore[import-untyped]
    NonSequentialTimeSeries as RustNonSequentialTimeSeries,
    OwnerCategory,
    SingleTimeSeries as RustSingleTimeSeries,
    TimeSeriesStore,
)

from infrasys.exceptions import ISNotStored
from infrasys.time_series_models import (
    NonSequentialTimeSeries,
    NonSequentialTimeSeriesMetadata,
    SingleTimeSeries,
    SingleTimeSeriesMetadata,
    TimeSeriesData,
    TimeSeriesMetadata,
    TimeSeriesStorageType,
)
from infrasys.time_series_storage_base import TimeSeriesStorageBase
from infrasys.utils.path_utils import clean_tmp_folder


class TimeSeriesStoreStorage(TimeSeriesStorageBase):
    """Store time series in the NetCDF/SQLite time-series-store format."""

    STORAGE_FILE = "time_series_store.nc"

    def __init__(self, directory: Path, store: TimeSeriesStore) -> None:
        self._directory = directory
        self._store = store

    @classmethod
    def create_with_temp_directory(
        cls, base_directory: Path | None = None
    ) -> "TimeSeriesStoreStorage":
        if base_directory is not None:
            base_directory.mkdir(parents=True, exist_ok=True)
        directory = Path(mkdtemp(dir=base_directory))
        logger.debug("Creating tmp folder at {}", directory)
        atexit.register(clean_tmp_folder, directory)
        return cls._create(directory)

    @classmethod
    def create_with_permanent_directory(cls, directory: Path) -> "TimeSeriesStoreStorage":
        directory.mkdir(parents=True, exist_ok=True)
        return cls._create(directory)

    @classmethod
    def _create(cls, directory: Path) -> "TimeSeriesStoreStorage":
        store = TimeSeriesStore.create(path=directory / cls.STORAGE_FILE)
        return cls(directory, store)

    @classmethod
    def deserialize(
        cls,
        data: dict[str, Any],
        time_series_dir: Path,
        dst_time_series_directory: Path | None,
        read_only: bool,
        **kwargs: Any,
    ) -> tuple["TimeSeriesStoreStorage", None]:
        """Open serialized storage directly or copy it to a writable temporary directory."""
        if read_only:
            directory = time_series_dir
        else:
            directory = Path(mkdtemp(dir=dst_time_series_directory))
            logger.debug("Creating tmp folder at {}", directory)
            atexit.register(clean_tmp_folder, directory)
            cls._copy_store(time_series_dir, directory)

        store = TimeSeriesStore.open(
            path=directory / cls.STORAGE_FILE,
            read_only=read_only,
        )
        return cls(directory, store), None

    def get_time_series_directory(self) -> Path:
        return self._directory

    def add_time_series(
        self,
        metadata: TimeSeriesMetadata,
        time_series: TimeSeriesData,
        context: Any = None,
    ) -> None:
        if isinstance(time_series, SingleTimeSeries):
            rust_time_series = RustSingleTimeSeries(
                _as_utc(time_series.initial_timestamp),
                time_series.resolution,
                np.asarray(time_series.data_array, dtype=np.float64),
            )
        elif isinstance(time_series, NonSequentialTimeSeries):
            rust_time_series = RustNonSequentialTimeSeries(
                [_as_utc(x) for x in time_series.timestamps.astype("datetime64[us]").tolist()],
                np.asarray(time_series.data_array, dtype=np.float64),
            )
        else:
            msg = f"add_time_series not implemented for {type(time_series)}"
            raise NotImplementedError(msg)

        self._store.add_time_series(
            owner_uuid=str(metadata.time_series_uuid),
            owner_type=metadata.type,
            owner_category=OwnerCategory.Component,
            name=metadata.name,
            time_series=rust_time_series,
        )

    def get_time_series(
        self,
        metadata: TimeSeriesMetadata,
        start_time: datetime | None = None,
        length: int | None = None,
        context: Any = None,
    ) -> TimeSeriesData:
        key = self._get_key(metadata)
        time_range = None
        result_initial_timestamp = None
        if isinstance(metadata, SingleTimeSeriesMetadata):
            index, result_length = metadata.get_range(start_time=start_time, length=length)
            result_initial_timestamp = metadata.initial_timestamp + index * metadata.resolution
            time_range = (
                _as_utc(result_initial_timestamp),
                _as_utc(result_initial_timestamp + result_length * metadata.resolution),
            )

        stored = self._store.get_time_series(key, time_range=time_range)
        data = np.asarray(stored.data)
        if metadata.units is not None:
            data = metadata.units.quantity_type(data, metadata.units.units)

        if isinstance(metadata, SingleTimeSeriesMetadata):
            assert result_initial_timestamp is not None
            return SingleTimeSeries(
                uuid=metadata.time_series_uuid,
                name=metadata.name,
                resolution=metadata.resolution,
                initial_timestamp=result_initial_timestamp,
                data=data,
                normalization=metadata.normalization,
            )
        if isinstance(metadata, NonSequentialTimeSeriesMetadata):
            return NonSequentialTimeSeries(
                uuid=metadata.time_series_uuid,
                name=metadata.name,
                data=data,
                timestamps=np.asarray(
                    [_as_naive_utc(x) for x in stored.timestamps],
                    dtype=object,
                ),
                normalization=metadata.normalization,
            )

        msg = f"get_time_series not implemented for {type(metadata)}"
        raise NotImplementedError(msg)

    def remove_time_series(self, metadata: TimeSeriesMetadata, context: Any = None) -> None:
        self._store.remove_time_series(self._get_key(metadata))

    def serialize(
        self, data: dict[str, Any], dst: Path | str, src: Path | str | None = None
    ) -> None:
        self._store.flush()
        source = self._directory if src is None else Path(src)
        destination = Path(dst)
        destination.mkdir(parents=True, exist_ok=True)
        self._copy_store(source, destination)
        self.add_serialized_data(data)

    @staticmethod
    def add_serialized_data(data: dict[str, Any]) -> None:
        data["time_series_storage_type"] = TimeSeriesStorageType.TIME_SERIES_STORE.value

    def _get_key(self, metadata: TimeSeriesMetadata):
        keys = self._store.get_time_series_keys(str(metadata.time_series_uuid))
        if not keys:
            msg = f"No time series with {metadata.time_series_uuid} is stored"
            raise ISNotStored(msg)
        if len(keys) != 1:
            msg = f"Expected one stored key for {metadata.time_series_uuid}, got {len(keys)}"
            raise RuntimeError(msg)
        return keys[0]

    @classmethod
    def _copy_store(cls, source: Path, destination: Path) -> None:
        for name in (cls.STORAGE_FILE, f"{cls.STORAGE_FILE}.sqlite"):
            src = source / name
            dst = destination / name
            if src.resolve() != dst.resolve():
                shutil.copyfile(src, dst)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _as_naive_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None)
