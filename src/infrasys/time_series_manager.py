"""Manages time series arrays"""

from contextlib import contextmanager
from datetime import datetime, timedelta
from functools import singledispatch
from pathlib import Path
from typing import Any, Generator, Literal, Optional, Type

from loguru import logger

from .component import Component
from .exceptions import ISInvalidParameter, ISOperationNotAllowed
from .supplemental_attribute import SupplementalAttribute
from .time_series_models import (
    Deterministic,
    DeterministicTimeSeriesKey,
    NonSequentialTimeSeries,
    NonSequentialTimeSeriesKey,
    SingleTimeSeries,
    SingleTimeSeriesKey,
    TimeSeriesData,
    TimeSeriesKey,
    TimeSeriesStorageContext,
    TimeSeriesStorageType,
)
from .time_series_reader import ForecastReader, TimeSeriesReader
from .time_series_store_storage import TimeSeriesCounts, TimeSeriesStoreStorage


TIME_SERIES_KWARGS = {
    "time_series_read_only": False,
    "time_series_directory": None,
    "time_series_storage_type": TimeSeriesStorageType.TIME_SERIES_STORE,
    # NetCDF compression for the time-series-store backend. "deflate" (default)
    # compresses arrays at time_series_compression_level (0-9) with optional
    # byte shuffle; "none" disables compression.
    "time_series_compression": "deflate",
    "time_series_compression_level": 3,
    "time_series_shuffle": True,
}


def _process_time_series_kwarg(key: str, **kwargs: Any) -> Any:
    return kwargs.get(key, TIME_SERIES_KWARGS[key])


class TimeSeriesManager:
    """Manages time series for a system."""

    def __init__(
        self,
        storage: Optional[TimeSeriesStoreStorage] = None,
        initialize: bool = True,
        **kwargs,
    ) -> None:
        self._read_only = _process_time_series_kwarg("time_series_read_only", **kwargs)
        self._storage: TimeSeriesStoreStorage = storage or self.create_new_storage(**kwargs)
        self._context: TimeSeriesStorageContext | None = None

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
    def create_new_storage(**kwargs) -> TimeSeriesStoreStorage:
        base_directory: Path | None = _process_time_series_kwarg("time_series_directory", **kwargs)
        storage_type = _process_time_series_kwarg("time_series_storage_type", **kwargs)
        if storage_type != TimeSeriesStorageType.TIME_SERIES_STORE:
            msg = f"Unsupported time series storage type: {storage_type}"
            raise ISInvalidParameter(msg)
        compression = {
            "compression": _process_time_series_kwarg("time_series_compression", **kwargs),
            "compression_level": _process_time_series_kwarg(
                "time_series_compression_level", **kwargs
            ),
            "shuffle": _process_time_series_kwarg("time_series_shuffle", **kwargs),
        }
        return TimeSeriesStoreStorage.create_with_temp_directory(base_directory, **compression)

    @property
    def storage(self) -> TimeSeriesStoreStorage:
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
        if not isinstance(time_series, (SingleTimeSeries, NonSequentialTimeSeries, Deterministic)):
            msg = f"Time-series persistence is not implemented for {ts_type.__name__}"
            raise NotImplementedError(msg)

        self._storage.add_time_series(
            time_series, *owners, context=_get_data_context(context), **features
        )
        return make_time_series_key(time_series, features)

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
        ISOperationNotAllowed
            Raised if the inputs match more than one time series.

        See Also
        --------
        list_time_series
        """
        metadata = self._storage.get_metadata(
            owner,
            name=name,
            time_series_type=time_series_type.__name__ if time_series_type else None,
            **features,
        )
        return self._get_by_metadata(
            metadata, owner, start_time=start_time, length=length, context=context
        )

    def get_by_key(
        self,
        owner: Component | SupplementalAttribute,
        key: TimeSeriesKey,
        connection: TimeSeriesStorageContext | None = None,
    ) -> TimeSeriesData:
        """Return a time series array by key."""
        metadata = self._storage.get_metadata(
            owner,
            name=key.name,
            time_series_type=key.time_series_type.__name__,
            **key.features,
        )
        return self._get_by_metadata(metadata, owner, context=connection)

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
        return self._storage.has_metadata(
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
        records = self._storage.list_metadata(
            owner,
            name=name,
            time_series_type=time_series_type.__name__,
            **features,
        )
        return self._storage.get_time_series_bulk(
            records,
            owner,
            start_time=start_time,
            length=length,
            context=_get_data_context(connection),
        )

    def list_time_series_keys(
        self,
        owner: Component | SupplementalAttribute,
        name: str | None = None,
        time_series_type: Type[TimeSeriesData] = SingleTimeSeries,
        **features: Any,
    ) -> list[TimeSeriesKey]:
        """Return all time series keys that match the inputs."""
        return self.list_time_series_metadata(owner, name, time_series_type, **features)

    def list_time_series_metadata(
        self,
        owner: Component | SupplementalAttribute,
        name: str | None = None,
        time_series_type: Type[TimeSeriesData] = SingleTimeSeries,
        **features: Any,
    ) -> list[TimeSeriesKey]:
        """Return the keys describing all time series that match the inputs."""
        return [
            self._storage.key_for(record)
            for record in self._storage.list_metadata(
                owner,
                name=name,
                time_series_type=time_series_type.__name__,
                **features,
            )
        ]

    def get_time_series_counts(self) -> TimeSeriesCounts:
        """Return summary counts of stored time series."""
        return self._storage.get_time_series_counts()

    def build_reader(
        self,
        resolution: timedelta,
        *,
        name: str | None = None,
        name_glob: str | None = None,
        component_type: Type[Component] | None = None,
        **features: Any,
    ) -> TimeSeriesReader:
        """Build a cross-sectional reader over the matching ``SingleTimeSeries``."""
        return self._storage.build_reader(
            resolution,
            name=name,
            name_glob=name_glob,
            owner_type=None if component_type is None else component_type.__name__,
            **features,
        )

    def build_forecast_reader(
        self,
        resolution: timedelta,
        *,
        time_series_type: Type[TimeSeriesData] = Deterministic,
        name: str | None = None,
        name_glob: str | None = None,
        component_type: Type[Component] | None = None,
        **features: Any,
    ) -> ForecastReader:
        """Build a cross-sectional reader over the matching forecasts."""
        return self._storage.build_forecast_reader(
            resolution,
            time_series_type=time_series_type.__name__,
            name=name,
            name_glob=name_glob,
            owner_type=None if component_type is None else component_type.__name__,
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
        self._storage.remove(
            *owners,
            name=name,
            time_series_type=time_series_type.__name__,
            context=_get_data_context(context),
            **features,
        )
        logger.info("Removed time series {}.{}", time_series_type, name)

    def copy(
        self,
        dst: Component | SupplementalAttribute,
        src: Component | SupplementalAttribute,
        name_mapping: dict[str, str] | None = None,
    ) -> None:
        """Copy all time series from src to dst.

        Notes
        -----
        name_mapping is currently not implemented.
        """
        self._handle_read_only()
        raise NotImplementedError

    def transform_single_time_series(self, horizon: timedelta, interval: timedelta) -> int:
        """Derive ``Deterministic`` forecasts from every stored ``SingleTimeSeries``.

        Each ``SingleTimeSeries`` gains a forecast view sharing the same underlying array.
        After transforming, retrieve a forecast with ``get(..., time_series_type=Deterministic)``.
        Returns the number of series transformed.

        Raises
        ------
        ISOperationNotAllowed
            Raised if the manager was created in read-only mode.
        """
        self._handle_read_only()
        return self._storage.transform_single_time_series(horizon, interval)

    def _get_by_metadata(
        self,
        record: Any,
        owner: Component | SupplementalAttribute,
        start_time: datetime | None = None,
        length: int | None = None,
        context: TimeSeriesStorageContext | None = None,
    ) -> TimeSeriesData:
        return self._storage.get_time_series(
            record,
            owner,
            start_time=start_time,
            length=length,
            context=_get_data_context(context),
        )

    def serialize(
        self,
        data: dict[str, Any],
        dst: Path | str,
        src: Path | str | None = None,
    ) -> None:
        """Serialize the time series data to dst."""
        self._storage.serialize(data, dst, src=src)

    @classmethod
    def deserialize(
        cls,
        data: dict[str, Any],
        parent_dir: Path | str,
        **kwargs: Any,
    ) -> "TimeSeriesManager":
        """Deserialize the class."""
        dst_time_series_directory = _process_time_series_kwarg("time_series_directory", **kwargs)
        if dst_time_series_directory is not None and not Path(dst_time_series_directory).exists():
            msg = f"time_series_directory={dst_time_series_directory} does not exist"
            raise FileNotFoundError(msg)
        read_only = _process_time_series_kwarg("time_series_read_only", **kwargs)
        time_series_dir = Path(parent_dir) / data["directory"]

        storage, _ = TimeSeriesStoreStorage.deserialize(
            data=data,
            time_series_dir=time_series_dir,
            dst_time_series_directory=dst_time_series_directory,
            read_only=read_only,
            **kwargs,
        )
        return cls(storage=storage, initialize=False, **kwargs)

    @contextmanager
    def open_time_series_store(
        self, mode: Literal["r", "r+", "a", "w", "w-"] = "a"
    ) -> Generator[TimeSeriesStorageContext, None, None]:
        """Open a connection to the time series store for batched operations."""
        with self.storage.open_time_series_store(mode=mode) as context:
            snapshot = self._storage.snapshot_index()
            try:
                self._context = TimeSeriesStorageContext(data_context=context)
                yield self._context
            except Exception as e:
                # Undo any time series added during the failed batch.
                logger.error(e)
                self._storage.rollback_to(snapshot)
                raise
            finally:
                self._context = None

    def _handle_read_only(self) -> None:
        if self._read_only:
            msg = "Cannot modify time series in read-only mode."
            raise ISOperationNotAllowed(msg)


@singledispatch
def make_time_series_key(time_series, features: dict[str, Any]) -> TimeSeriesKey:
    msg = f"make_time_series_key not implemented for {type(time_series)}"
    raise NotImplementedError(msg)


@make_time_series_key.register(SingleTimeSeries)
def _(time_series: SingleTimeSeries, features: dict[str, Any]) -> TimeSeriesKey:
    return SingleTimeSeriesKey(
        initial_timestamp=time_series.initial_timestamp,
        resolution=time_series.resolution,
        length=time_series.length,
        features=features,
        name=time_series.name,
        time_series_type=SingleTimeSeries,
    )


@make_time_series_key.register(NonSequentialTimeSeries)
def _(time_series: NonSequentialTimeSeries, features: dict[str, Any]) -> TimeSeriesKey:
    return NonSequentialTimeSeriesKey(
        length=time_series.length,
        features=features,
        name=time_series.name,
        time_series_type=NonSequentialTimeSeries,
    )


@make_time_series_key.register(Deterministic)
def _(time_series: Deterministic, features: dict[str, Any]) -> TimeSeriesKey:
    return DeterministicTimeSeriesKey(
        initial_timestamp=time_series.initial_timestamp,
        resolution=time_series.resolution,
        horizon=time_series.horizon,
        interval=time_series.interval,
        window_count=time_series.window_count,
        features=features,
        name=time_series.name,
        time_series_type=Deterministic,
    )


def _get_data_context(conn: TimeSeriesStorageContext | None) -> Any:
    return None if conn is None else conn.data_context
