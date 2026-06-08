"""Manages time series arrays"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from functools import singledispatch
from pathlib import Path
from typing import Any, Generator, Literal, Optional, Type

from loguru import logger

from . import TIME_SERIES_ASSOCIATIONS_TABLE
from .component import Component
from .exceptions import ISInvalidParameter, ISOperationNotAllowed
from .in_memory_time_series_storage import InMemoryTimeSeriesStorage
from .supplemental_attribute import SupplementalAttribute
from .time_series_metadata_store import TimeSeriesMetadataStore
from .time_series_models import (
    DeterministicMetadata,
    DeterministicTimeSeriesKey,
    NonSequentialTimeSeries,
    NonSequentialTimeSeriesKey,
    NonSequentialTimeSeriesMetadata,
    SingleTimeSeries,
    SingleTimeSeriesKey,
    SingleTimeSeriesMetadata,
    TimeSeriesData,
    TimeSeriesKey,
    TimeSeriesMetadata,
    TimeSeriesStorageContext,
    TimeSeriesStorageType,
)
from .time_series_storage_base import TimeSeriesStorageBase
from .time_series_store_storage import TimeSeriesStoreStorage
from .utils.sqlite import has_table


TIME_SERIES_KWARGS = {
    "time_series_read_only": False,
    "time_series_directory": None,
    "time_series_storage_type": TimeSeriesStorageType.TIME_SERIES_STORE,
    # NetCDF compression for the time-series-store backend. "deflate" (default)
    # compresses arrays at time_series_compression_level (0-9) with optional
    # byte shuffle; "none" disables compression. Ignored by the memory backend.
    "time_series_compression": "deflate",
    "time_series_compression_level": 3,
    "time_series_shuffle": True,
}


TIME_SERIES_REGISTRY: dict[TimeSeriesStorageType, type[TimeSeriesStorageBase]] = {
    TimeSeriesStorageType.MEMORY: InMemoryTimeSeriesStorage,
    TimeSeriesStorageType.TIME_SERIES_STORE: TimeSeriesStoreStorage,
}


def _process_time_series_kwarg(key: str, **kwargs: Any) -> Any:
    return kwargs.get(key, TIME_SERIES_KWARGS[key])


class TimeSeriesManager:
    """Manages time series for a system."""

    def __init__(
        self,
        con: sqlite3.Connection,
        storage: Optional[TimeSeriesStorageBase] = None,
        initialize: bool = True,
        metadata_store: TimeSeriesMetadataStore | None = None,
        **kwargs,
    ) -> None:
        self._con = con
        self._metadata_store = metadata_store or TimeSeriesMetadataStore(
            con, initialize=initialize
        )
        self._read_only = _process_time_series_kwarg("time_series_read_only", **kwargs)
        self._storage = storage or self.create_new_storage(**kwargs)
        self._context: TimeSeriesStorageContext | None = None

        # TODO: create parsing mechanism? CSV, CSV + JSON

    def close(self) -> None:
        """Release resources held by the storage backend."""
        storage = getattr(self, "_storage", None)
        for attr in ("close", "dispose"):
            func = getattr(storage, attr, None)
            if callable(func):
                try:
                    func()
                except Exception:
                    logger.debug("Error closing time series storage", exc_info=True)
                break

    @staticmethod
    def create_new_storage(permanent: bool = False, **kwargs):
        base_directory: Path | None = _process_time_series_kwarg("time_series_directory", **kwargs)
        storage_type = _process_time_series_kwarg("time_series_storage_type", **kwargs)
        compression = {
            "compression": _process_time_series_kwarg("time_series_compression", **kwargs),
            "compression_level": _process_time_series_kwarg(
                "time_series_compression_level", **kwargs
            ),
            "shuffle": _process_time_series_kwarg("time_series_shuffle", **kwargs),
        }

        match storage_type:
            case TimeSeriesStorageType.MEMORY:
                return InMemoryTimeSeriesStorage()
            case TimeSeriesStorageType.TIME_SERIES_STORE:
                if permanent:
                    if base_directory is None:
                        msg = "Can't convert to permanent storage without a base directory"
                        raise ISInvalidParameter(msg)
                    return TimeSeriesStoreStorage.create_with_permanent_directory(
                        base_directory, **compression
                    )
                return TimeSeriesStoreStorage.create_with_temp_directory(
                    base_directory, **compression
                )
            case _:
                msg = f"{storage_type=}"
                raise NotImplementedError(msg)

    @property
    def metadata_store(self) -> TimeSeriesMetadataStore:
        """Return the time series metadata store."""
        return self._metadata_store

    @property
    def storage(self) -> TimeSeriesStorageBase:
        """Return the time series storage object."""
        return self._storage

    def add(
        self,
        time_series: TimeSeriesData,
        *owners: Component | SupplementalAttribute,
        context: TimeSeriesStorageContext | None = None,
        **features: Any,
    ) -> TimeSeriesKey:
        """Store a time series array for one or more components or supplemental attributes.

        Parameters
        ----------
        time_series : TimeSeriesData
            Time series data to store.
        owners : Component | SupplementalAttribute
            Add the time series to all of these components or supplemental attributes.
        connection
            Optional connection to use for the operation.
        features : Any
            Key/value pairs to store with the time series data. Must be JSON-serializable.

        Raises
        ------
        ISAlreadyAttached
            Raised if the variable name and user attributes match any time series already
            attached to one of the components or supplemental attributes.
        ISOperationNotAllowed
            Raised if the manager was created in read-only mode.
        """
        self._handle_read_only()
        context = context or self._context
        if not owners:
            msg = "add_time_series requires at least one component or supplemental attribute"
            raise ISOperationNotAllowed(msg)

        ts_type = type(time_series)
        if not issubclass(ts_type, TimeSeriesData):
            msg = f"The first argument must be an instance of TimeSeriesData: {ts_type}"
            raise ValueError(msg)
        if not isinstance(time_series, (SingleTimeSeries, NonSequentialTimeSeries)):
            msg = f"Time-series persistence is not implemented for {ts_type.__name__}"
            raise NotImplementedError(msg)
        metadata_type = ts_type.get_time_series_metadata_type()
        metadata = metadata_type.from_data(time_series, **features)

        data_is_stored = self._metadata_store.has_time_series(time_series.uuid)
        # Call this first because it could raise an exception.
        self._metadata_store.add(metadata, *owners, connection=_get_metadata_connection(context))
        if not data_is_stored:
            self._storage.add_time_series(
                metadata,
                time_series,
                context=_get_data_context(context),
            )
        return make_time_series_key(metadata)

    def get(
        self,
        owner: Component | SupplementalAttribute,
        name: str | None = None,
        time_series_type: Type[TimeSeriesData] | None = None,
        start_time: datetime | None = None,
        length: int | None = None,
        context: TimeSeriesStorageContext | None = None,
        **features,
    ) -> TimeSeriesData:
        """Return a time series array.

        Raises
        ------
        ISNotStored
            Raised if no time series matches the inputs.
            Raised if the inputs match more than one time series.
        ISOperationNotAllowed
            Raised if the inputs match more than one time series.

        See Also
        --------
        list_time_series
        """
        metadata = self._metadata_store.get_metadata(
            owner,
            name=name,
            time_series_type=time_series_type.__name__ if time_series_type else None,
            **features,
        )
        return self._get_by_metadata(
            metadata, start_time=start_time, length=length, context=context
        )

    def get_by_key(
        self,
        owner: Component | SupplementalAttribute,
        key: TimeSeriesKey,
        connection: TimeSeriesStorageContext | None = None,
    ) -> TimeSeriesData:
        """Return a time series array by key."""
        metadata = self._metadata_store.get_metadata(
            owner,
            name=key.name,
            time_series_type=key.time_series_type.__name__,
            **key.features,
        )
        return self._get_by_metadata(metadata, context=connection)

    def has_time_series(
        self,
        owner: Component | SupplementalAttribute,
        name: str | None = None,
        time_series_type: Type[TimeSeriesData] = SingleTimeSeries,
        **features,
    ) -> bool:
        """Return True if the component or supplemental atttribute has time series matching the
        inputs.
        """
        return self._metadata_store.has_time_series_metadata(
            owner,
            name=name,
            time_series_type=time_series_type.__name__,
            **features,
        )

    def list_time_series(
        self,
        owner: Component | SupplementalAttribute,
        name: str | None = None,
        time_series_type: Type[TimeSeriesData] = SingleTimeSeries,
        start_time: datetime | None = None,
        length: int | None = None,
        connection: TimeSeriesStorageContext | None = None,
        **features: Any,
    ) -> list[TimeSeriesData]:
        """Return all time series that match the inputs."""
        metadata = self.list_time_series_metadata(
            owner,
            name=name,
            time_series_type=time_series_type,
            **features,
        )
        return [
            self._get_by_metadata(x, start_time=start_time, length=length, context=connection)
            for x in metadata
        ]

    def list_time_series_keys(
        self,
        owner: Component | SupplementalAttribute,
        name: str | None = None,
        time_series_type: Type[TimeSeriesData] = SingleTimeSeries,
        **features: Any,
    ) -> list[TimeSeriesKey]:
        """Return all time series keys that match the inputs."""
        return [
            make_time_series_key(x)
            for x in self.list_time_series_metadata(owner, name, time_series_type, **features)
        ]

    def list_time_series_metadata(
        self,
        owner: Component | SupplementalAttribute,
        name: str | None = None,
        time_series_type: Type[TimeSeriesData] = SingleTimeSeries,
        **features: Any,
    ) -> list[TimeSeriesMetadata]:
        """Return all time series metadata that match the inputs."""
        return self._metadata_store.list_metadata(
            owner,
            name=name,
            time_series_type=time_series_type.__name__,
            **features,
        )

    def remove(
        self,
        *owners: Component | SupplementalAttribute,
        name: str | None = None,
        time_series_type: Type[TimeSeriesData] = SingleTimeSeries,
        context: TimeSeriesStorageContext | None = None,
        **features: Any,
    ):
        """Remove all time series arrays matching the inputs.

        Raises
        ------
        ISNotStored
            Raised if no time series match the inputs.
        ISOperationNotAllowed
            Raised if the manager was created in read-only mode.
        """
        self._handle_read_only()
        metadata = self._metadata_store.remove(
            *owners,
            name=name,
            time_series_type=time_series_type.__name__,
            connection=_get_metadata_connection(context),
            **features,
        )
        time_series = {x.time_series_uuid: x for x in metadata}
        missing_uuids = self._metadata_store.list_missing_time_series(time_series.keys())
        for uuid in missing_uuids:
            self._storage.remove_time_series(time_series[uuid], context=_get_data_context(context))
            logger.info("Removed time series {}.{}", time_series_type, name)

    def copy(
        self,
        dst: Component | SupplementalAttribute,
        src: Component | SupplementalAttribute,
        name_mapping: dict[str, str] | None = None,
    ) -> None:
        """Copy all time series from src to dst.

        Parameters
        ----------
        dst
            Destination component or supplemental attribute
        src
            Source component or supplemental attribute
        name_mapping : dict[str, str]
            Optionally map src names to different dst names.
            If provided and src has a time_series with a name not present in name_mapping, that
            time_series will not copied. If name_mapping is nothing then all time_series will be
            copied with src's names.

        Notes
        -----
        name_mapping is currently not implemented.
        """
        self._handle_read_only()
        raise NotImplementedError

    def _get_by_metadata(
        self,
        metadata: TimeSeriesMetadata,
        start_time: datetime | None = None,
        length: int | None = None,
        context: TimeSeriesStorageContext | None = None,
    ) -> TimeSeriesData:
        return self._storage.get_time_series(
            metadata,
            start_time=start_time,
            length=length,
            context=_get_data_context(context),
        )

    def serialize(
        self,
        data: dict[str, Any],
        dst: Path | str,
        db_name: str,
        src: Path | str | None = None,
    ) -> None:
        """Serialize the time series data to dst."""
        if isinstance(self.storage, InMemoryTimeSeriesStorage):
            new_storage = self.convert_storage(
                time_series_storage_type=TimeSeriesStorageType.TIME_SERIES_STORE,
                time_series_directory=dst,
                in_place=False,
                permanent=True,
            )
            assert isinstance(new_storage, TimeSeriesStoreStorage)
            new_storage.add_serialized_data(data)
            self._metadata_store.serialize(Path(dst) / db_name)
        else:
            self._metadata_store.serialize(Path(dst) / db_name)
            self._storage.serialize(data, dst, src=src)

    @classmethod
    def deserialize(
        cls,
        con: sqlite3.Connection,
        data: dict[str, Any],
        parent_dir: Path | str,
        **kwargs: Any,
    ) -> "TimeSeriesManager":
        """Deserialize the class. Must also call add_reference_counts after deserializing
        components.
        """
        if (
            _process_time_series_kwarg("time_series_storage_type", **kwargs)
            == TimeSeriesStorageType.MEMORY
        ):
            msg = "De-serialization does not support in-memory time series storage."
            raise ISOperationNotAllowed(msg)

        dst_time_series_directory = _process_time_series_kwarg("time_series_directory", **kwargs)
        if dst_time_series_directory is not None and not Path(dst_time_series_directory).exists():
            msg = f"time_series_directory={dst_time_series_directory} does not exist"
            raise FileNotFoundError(msg)
        read_only = _process_time_series_kwarg("time_series_read_only", **kwargs)
        time_series_dir = Path(parent_dir) / data["directory"]

        ts_type = data.get(
            "time_series_storage_type",
            TimeSeriesStorageType.TIME_SERIES_STORE,
        )

        storage_class = TIME_SERIES_REGISTRY.get(ts_type)
        if storage_class is None:
            msg = f"time_series_storage_type={ts_type} is not supported"
            raise NotImplementedError(msg)

        storage, metadata_store = storage_class.deserialize(
            data=data,
            time_series_dir=time_series_dir,
            dst_time_series_directory=dst_time_series_directory,
            read_only=read_only,
            **kwargs,
        )

        if metadata_store is None or not has_table(
            metadata_store._con, TIME_SERIES_ASSOCIATIONS_TABLE
        ):
            logger.warning(
                "Time series metadata store missing table %s; using restored metadata database.",
                TIME_SERIES_ASSOCIATIONS_TABLE,
            )
            metadata_store = TimeSeriesMetadataStore(con, initialize=False)

        # Create the manager instance
        mgr = cls(con, storage=storage, metadata_store=metadata_store, initialize=False, **kwargs)

        # Load metadata and handle storage conversion if requested
        mgr.metadata_store._load_metadata_into_memory()
        if (
            "time_series_storage_type" in kwargs
            and _process_time_series_kwarg("time_series_storage_type", **kwargs) != ts_type
        ):
            mgr.convert_storage(**kwargs)
        return mgr

    @contextmanager
    def open_time_series_store(
        self, mode: Literal["r", "r+", "a", "w", "w-"] = "a"
    ) -> Generator[TimeSeriesStorageContext, None, None]:
        """Open a connection to the time series metadata and data stores."""
        with self.storage.open_time_series_store(mode=mode) as context:
            try:
                original_uuids = self._metadata_store.list_existing_time_series_uuids()
                self._context = TimeSeriesStorageContext(
                    metadata_conn=self._con, data_context=context
                )
                yield self._context
                self._con.commit()
            except Exception as e:
                # If we fail, we remove any new added time series (if any) and rollback the metadata.
                logger.error(e)
                new_uuids = (
                    set(self._metadata_store.list_existing_time_series_uuids()) - original_uuids
                )
                for uuid in new_uuids:
                    metadata_list = self._metadata_store.list_metadata_with_time_series_uuid(uuid)
                    for metadata in metadata_list:
                        self._storage.remove_time_series(metadata, context=context)
                        self._metadata_store.remove_by_metadata(metadata, connection=self._con)
                self._con.rollback()
                raise
            finally:
                self._context = None

    def _handle_read_only(self) -> None:
        if self._read_only:
            msg = "Cannot modify time series in read-only mode."
            raise ISOperationNotAllowed(msg)

    def convert_storage(self, in_place: bool = True, **kwargs) -> TimeSeriesStorageBase:
        """
        Create a new storage instance and copy all time series from the current to new storage.

        Parameters
        ----------
        in_place : bool
            If True, replace the current storage with the new storage.

        Returns
        -------
        TimeSeriesStorageBase
            The new storage instance.
        """
        new_storage = self.create_new_storage(**kwargs)
        for time_series_type in (SingleTimeSeries, NonSequentialTimeSeries):
            for time_series_uuid in self.metadata_store.unique_uuids_by_type(
                time_series_type.__name__
            ):
                metadata = self.metadata_store.list_metadata_with_time_series_uuid(
                    time_series_uuid, limit=1
                )
                if len(metadata) != 1:
                    msg = f"Expected 1 metadata for {time_series_uuid}, got {len(metadata)}"
                    raise Exception(msg)

                time_series = self._storage.get_time_series(metadata[0])
                new_storage.add_time_series(metadata[0], time_series)

        if in_place:
            self._storage = new_storage

        return new_storage


@singledispatch
def make_time_series_key(metadata) -> TimeSeriesKey:
    msg = f"make_time_series_keys not implemented for {type(metadata)}"
    raise NotImplementedError(msg)


@make_time_series_key.register(SingleTimeSeriesMetadata)
def _(metadata: SingleTimeSeriesMetadata) -> TimeSeriesKey:
    return SingleTimeSeriesKey(
        initial_timestamp=metadata.initial_timestamp,
        resolution=metadata.resolution,
        length=metadata.length,
        features=metadata.features,
        name=metadata.name,
        time_series_type=SingleTimeSeries,
    )


@make_time_series_key.register(NonSequentialTimeSeriesMetadata)
def _(metadata: NonSequentialTimeSeriesMetadata) -> TimeSeriesKey:
    return NonSequentialTimeSeriesKey(
        length=metadata.length,
        features=metadata.features,
        name=metadata.name,
        time_series_type=NonSequentialTimeSeries,
    )


@make_time_series_key.register(DeterministicMetadata)
def _(metadata: DeterministicMetadata) -> TimeSeriesKey:
    return DeterministicTimeSeriesKey(
        initial_timestamp=metadata.initial_timestamp,
        resolution=metadata.resolution,
        interval=metadata.interval,
        horizon=metadata.horizon,
        window_count=metadata.window_count,
        features=metadata.features,
        name=metadata.name,
        time_series_type=metadata.get_time_series_data_type(),
    )


def _get_data_context(conn: TimeSeriesStorageContext | None) -> Any:
    return None if conn is None else conn.data_context


def _get_metadata_connection(conn: TimeSeriesStorageContext | None) -> sqlite3.Connection | None:
    return None if conn is None else conn.metadata_conn
