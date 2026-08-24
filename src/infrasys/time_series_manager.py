"""Manages time series arrays"""

from contextlib import contextmanager
from copy import copy
from datetime import datetime, timedelta
from functools import singledispatch
from pathlib import Path
from typing import Any, Generator, Optional, Type

from loguru import logger

from .component import Component
from .exceptions import ISInvalidParameter, ISOperationNotAllowed
from .supplemental_attribute import SupplementalAttribute
from .time_series_context import (
    AUTO_FLUSH_BYTES,
    AUTO_FLUSH_THRESHOLD,
    TimeSeriesStorageContext,
)
from .time_series_models import (
    Deterministic,
    DeterministicTimeSeriesKey,
    NonSequentialTimeSeries,
    NonSequentialTimeSeriesKey,
    SingleTimeSeries,
    SingleTimeSeriesKey,
    TimeSeriesData,
    TimeSeriesKey,
    TimeSeriesStorageType,
)
from .time_series_reader import ForecastReader, TimeSeriesReader
from .time_series_store_storage import TimeSeriesCounts, TimeSeriesStoreStorage


TIME_SERIES_KWARGS = {
    "time_series_read_only": False,
    "time_series_directory": None,
    "time_series_storage_type": TimeSeriesStorageType.TIME_SERIES_STORE,
    # HDF5 compression for the infrastore backend. "deflate" (default)
    # compresses arrays at time_series_compression_level (0-9) with optional
    # byte shuffle; "none" disables compression.
    "time_series_compression": "deflate",
    "time_series_compression_level": 3,
    "time_series_shuffle": True,
}


def _process_time_series_kwarg(key: str, **kwargs: Any) -> Any:
    return kwargs.get(key, TIME_SERIES_KWARGS[key])


def _type_name(time_series_type: Type[TimeSeriesData] | None) -> str | None:
    """Return the storage-level type name filter; None matches every type."""
    return None if time_series_type is None else time_series_type.__name__


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
        # Set only on the view returned by bind_context; None means every operation gets
        # its own transient context.
        self._context: TimeSeriesStorageContext | None = None

    def close(self) -> None:
        """Release resources held by the storage backend."""
        self._storage.close()

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

    def bind_context(self, context: TimeSeriesStorageContext) -> "TimeSeriesManager":
        """Return a view of this manager whose operations all run on ``context``.

        Used by :class:`~infrasys.time_series_transaction.TimeSeriesTransaction` so that a
        batch is expressed once, at the binding, instead of threaded through every call as
        an argument. The view shares this manager's storage and read-only setting; only
        the context differs.

        Raises
        ------
        ISOperationNotAllowed
            Raised if the context was opened against another system's storage.
        """
        context.check_owns(self._storage)
        bound = copy(self)
        bound._context = context
        return bound

    @contextmanager
    def _ensure_context(self) -> Generator[TimeSeriesStorageContext, None, None]:
        """Yield the bound context, or a transient one covering just this operation.

        A bound context is left open: the block that created it decides when to commit. A
        transient context is committed on success and discarded on failure, so an
        operation invoked outside a batch behaves exactly as it did before contexts
        became explicit.
        """
        if self._context is not None:
            self._context.check_open()
            yield self._context
            return

        transient = self._storage.new_context()
        try:
            yield transient
        except Exception:
            transient.discard()
            raise
        transient.commit()

    def add(
        self,
        time_series: TimeSeriesData,
        *owners: Component | SupplementalAttribute,
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

        with self._ensure_context() as ctx:
            ctx.add_time_series(time_series, *owners, **features)
        return make_time_series_key(time_series, features)

    def get(
        self,
        owner: Component | SupplementalAttribute,
        name: str | None = None,
        time_series_type: Type[TimeSeriesData] | None = None,
        start_time: datetime | None = None,
        length: int | None = None,
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
        with self._ensure_context() as ctx:
            metadata = ctx.get_metadata(
                owner,
                name=name,
                time_series_type=_type_name(time_series_type),
                **features,
            )
            return ctx.get_time_series(metadata, owner, start_time=start_time, length=length)

    def get_by_key(
        self,
        owner: Component | SupplementalAttribute,
        key: TimeSeriesKey,
    ) -> TimeSeriesData:
        """Return a time series array by key."""
        with self._ensure_context() as ctx:
            metadata = ctx.get_metadata(
                owner,
                name=key.name,
                time_series_type=key.time_series_type.__name__,
                **key.features,
            )
            return ctx.get_time_series(metadata, owner)

    def has_time_series(
        self,
        owner: Component | SupplementalAttribute,
        name: str | None = None,
        time_series_type: Type[TimeSeriesData] | None = SingleTimeSeries,
        **features,
    ) -> bool:
        """Return True if the component or supplemental atttribute has time series matching the
        inputs. Pass ``time_series_type=None`` to match any type.
        """
        with self._ensure_context() as ctx:
            return ctx.has_metadata(
                owner,
                name=name,
                time_series_type=_type_name(time_series_type),
                **features,
            )

    def list_time_series(
        self,
        owner: Component | SupplementalAttribute,
        name: str | None = None,
        time_series_type: Type[TimeSeriesData] | None = SingleTimeSeries,
        start_time: datetime | None = None,
        length: int | None = None,
        **features: Any,
    ) -> list[TimeSeriesData]:
        """Return all time series that match the inputs. Pass ``time_series_type=None``
        to match any type.
        """
        with self._ensure_context() as ctx:
            records = ctx.list_metadata(
                owner,
                name=name,
                time_series_type=_type_name(time_series_type),
                **features,
            )
            return ctx.get_time_series_bulk(
                records,
                owner,
                start_time=start_time,
                length=length,
            )

    def list_time_series_keys(
        self,
        owner: Component | SupplementalAttribute,
        name: str | None = None,
        time_series_type: Type[TimeSeriesData] | None = SingleTimeSeries,
        **features: Any,
    ) -> list[TimeSeriesKey]:
        """Return all time series keys that match the inputs."""
        return self.list_time_series_metadata(owner, name, time_series_type, **features)

    def list_time_series_metadata(
        self,
        owner: Component | SupplementalAttribute,
        name: str | None = None,
        time_series_type: Type[TimeSeriesData] | None = SingleTimeSeries,
        **features: Any,
    ) -> list[TimeSeriesKey]:
        """Return the keys describing all time series that match the inputs.
        Pass ``time_series_type=None`` to match any type.
        """
        with self._ensure_context() as ctx:
            return [
                ctx.key_for(record)
                for record in ctx.list_metadata(
                    owner,
                    name=name,
                    time_series_type=_type_name(time_series_type),
                    **features,
                )
            ]

    def get_time_series_counts(self) -> TimeSeriesCounts:
        """Return summary counts of stored time series."""
        with self._ensure_context() as ctx:
            return ctx.get_time_series_counts()

    def build_reader(
        self,
        resolution: timedelta,
        *,
        name: str | None = None,
        name_glob: str | None = None,
        component_type: Type[Component] | None = None,
        zoneless: bool | None = None,
        **features: Any,
    ) -> TimeSeriesReader:
        """Build a cross-sectional reader over the matching ``SingleTimeSeries``."""
        with self._ensure_context() as ctx:
            return ctx.build_reader(
                resolution,
                name=name,
                name_glob=name_glob,
                owner_type=None if component_type is None else component_type.__name__,
                zoneless=zoneless,
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
        zoneless: bool | None = None,
        **features: Any,
    ) -> ForecastReader:
        """Build a cross-sectional reader over the matching forecasts."""
        with self._ensure_context() as ctx:
            return ctx.build_forecast_reader(
                resolution,
                time_series_type=time_series_type.__name__,
                name=name,
                name_glob=name_glob,
                owner_type=None if component_type is None else component_type.__name__,
                zoneless=zoneless,
                **features,
            )

    def remove(
        self,
        *owners: Component | SupplementalAttribute,
        name: str | None = None,
        time_series_type: Type[TimeSeriesData] | None = SingleTimeSeries,
        **features: Any,
    ):
        """Remove all time series arrays matching the inputs. Pass
        ``time_series_type=None`` to match any type.

        Raises
        ------
        ISNotStored
            Raised if no time series match the inputs.
        ISOperationNotAllowed
            Raised if the manager was created in read-only mode.
        """
        self._handle_read_only()
        with self._ensure_context() as ctx:
            removed = ctx.remove(
                *owners,
                name=name,
                time_series_type=_type_name(time_series_type),
                **features,
            )
        logger.info(
            "Removed {} time series matching type={} name={}",
            len(removed),
            _type_name(time_series_type),
            name,
        )

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

    def transform_single_time_series(
        self,
        horizon: timedelta,
        interval: timedelta,
    ) -> int:
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
        with self._ensure_context() as ctx:
            return ctx.transform_single_time_series(horizon, interval)

    def serialize(
        self,
        data: dict[str, Any],
        dst: Path | str,
        src: Path | str | None = None,
    ) -> None:
        """Serialize the time series data to dst."""
        with self._ensure_context() as ctx:
            ctx.serialize(data, dst, src=src)

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
    def time_series_transaction(
        self,
        auto_flush_threshold: int = AUTO_FLUSH_THRESHOLD,
        auto_flush_bytes: int = AUTO_FLUSH_BYTES,
    ) -> Generator[TimeSeriesStorageContext, None, None]:
        """Open a context that batches every operation passed to it, inside a store
        transaction.

        The context commits on a clean exit. If the block raises, the transaction is
        rolled back and the whole block is undone — buffered additions never reached the
        store, and everything that did, **including removals**, is reversed. Removals are
        recoverable only in here; outside a transaction the store frees the array.
        """
        context = self._storage.new_context(
            auto_flush_threshold=auto_flush_threshold,
            auto_flush_bytes=auto_flush_bytes,
        )
        context.begin()
        try:
            yield context
        except Exception as e:
            logger.error(e)
            context.discard()
            raise
        context.commit()

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
