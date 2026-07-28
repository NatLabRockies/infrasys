"""The user-facing handle for a batch of time series operations.

``System.time_series_transaction`` yields a :class:`TimeSeriesTransaction`. The
transaction is the API surface for the block: call the time series methods on it, the
way a ``zipfile.ZipFile`` or SQLAlchemy ``Session`` is used, rather than passing a
token back to ``System`` methods. Everything called on the transaction lands in one
bulk write and one store transaction; a ``System`` method called inside the block runs
on its own and sees only committed data.

The transaction is a thin facade: batching and rollback live in
:class:`~infrasys.time_series_context.TimeSeriesStorageContext`, which stays internal.
"""

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Optional, Type

from infrasys.component import Component
from infrasys.supplemental_attribute import SupplementalAttribute
from infrasys.time_series_models import (
    Deterministic,
    SingleTimeSeries,
    TimeSeriesData,
    TimeSeriesKey,
)

if TYPE_CHECKING:
    from infrasys.time_series_context import TimeSeriesStorageContext
    from infrasys.time_series_manager import TimeSeriesManager
    from infrasys.time_series_reader import ForecastReader, TimeSeriesReader


class TimeSeriesTransaction:
    """One open batch of time series work, yielded by ``System.time_series_transaction``.

    Every method mirrors the ``System`` method of the same name and routes through this
    transaction's batch: additions are buffered into one bulk write, reads see the
    batch's staged work, and if the block raises, everything the transaction did is
    rolled back.

    Examples
    --------
    >>> with system.time_series_transaction() as txn:
    ...     for gen, ts in profiles:
    ...         txn.add_time_series(ts, gen)
    """

    def __init__(self, manager: "TimeSeriesManager", context: "TimeSeriesStorageContext") -> None:
        # Binding once is what lets every method below read exactly like its System
        # counterpart: the batch is expressed here, not repeated as an argument on each
        # call --- which also keeps `context` out of the caller's **features.
        self._mgr = manager.bind_context(context)
        self._context = context

    @property
    def has_staged_data(self) -> bool:
        """Return True if additions are buffered but not yet written to the store."""
        return self._context.has_staged_data

    def add_time_series(
        self,
        time_series: TimeSeriesData,
        *owners: Component | SupplementalAttribute,
        **features: Any,
    ) -> TimeSeriesKey:
        """Store a time series array for one or more components or supplemental attributes.

        Mirrors ``System.add_time_series``; the addition is buffered and written with the
        rest of the batch.
        """
        return self._mgr.add(time_series, *owners, **features)

    def copy_time_series(
        self,
        dst: Component | SupplementalAttribute,
        src: Component | SupplementalAttribute,
        name_mapping: dict[str, str] | None = None,
    ) -> None:
        """Copy all time series from src to dst. Mirrors ``System.copy_time_series``."""
        return self._mgr.copy(dst, src, name_mapping=name_mapping)

    def get_time_series(
        self,
        owner: Component | SupplementalAttribute,
        name: str | None = None,
        time_series_type: Type[TimeSeriesData] | None = None,
        start_time: datetime | None = None,
        length: int | None = None,
        **features: Any,
    ) -> Any:
        """Return a time series array, including ones staged in this transaction.

        Mirrors ``System.get_time_series``.
        """
        return self._mgr.get(
            owner,
            name=name,
            time_series_type=time_series_type,
            start_time=start_time,
            length=length,
            **features,
        )

    def get_time_series_by_key(
        self,
        owner: Component | SupplementalAttribute,
        key: TimeSeriesKey,
    ) -> Any:
        """Return a time series array by key. Mirrors ``System.get_time_series_by_key``."""
        return self._mgr.get_by_key(owner, key)

    def has_time_series(
        self,
        owner: Component | SupplementalAttribute,
        name: Optional[str] = None,
        time_series_type: Type[TimeSeriesData] | None = SingleTimeSeries,
        **features: Any,
    ) -> bool:
        """Return True if the owner has matching time series, staged or committed.

        Mirrors ``System.has_time_series``.
        """
        return self._mgr.has_time_series(
            owner,
            name=name,
            time_series_type=time_series_type,
            **features,
        )

    def list_time_series(
        self,
        component: Component,
        name: str | None = None,
        time_series_type: Type[TimeSeriesData] | None = SingleTimeSeries,
        start_time: datetime | None = None,
        length: int | None = None,
        **features: Any,
    ) -> list[TimeSeriesData]:
        """Return all matching time series. Mirrors ``System.list_time_series``."""
        return self._mgr.list_time_series(
            component,
            name=name,
            time_series_type=time_series_type,
            start_time=start_time,
            length=length,
            **features,
        )

    def list_time_series_keys(
        self,
        owner: Component | SupplementalAttribute,
        name: str | None = None,
        time_series_type: Type[TimeSeriesData] | None = SingleTimeSeries,
        **features: Any,
    ) -> list[TimeSeriesKey]:
        """Return all matching time series keys. Mirrors ``System.list_time_series_keys``."""
        return self._mgr.list_time_series_keys(
            owner,
            name=name,
            time_series_type=time_series_type,
            **features,
        )

    def list_time_series_metadata(
        self,
        component: Component,
        name: str | None = None,
        time_series_type: Type[TimeSeriesData] | None = SingleTimeSeries,
        **features: Any,
    ) -> list[TimeSeriesKey]:
        """Return all matching metadata. Mirrors ``System.list_time_series_metadata``."""
        return self._mgr.list_time_series_metadata(
            component,
            name=name,
            time_series_type=time_series_type,
            **features,
        )

    def remove_time_series(
        self,
        *owners: Component | SupplementalAttribute,
        name: str | None = None,
        time_series_type: Type[TimeSeriesData] | None = SingleTimeSeries,
        **features: Any,
    ) -> None:
        """Remove matching time series; reversible if the block rolls back.

        Mirrors ``System.remove_time_series``.
        """
        return self._mgr.remove(
            *owners,
            name=name,
            time_series_type=time_series_type,
            **features,
        )

    def transform_single_time_series(
        self,
        horizon: timedelta,
        interval: timedelta,
    ) -> int:
        """Derive ``Deterministic`` forecasts from every stored ``SingleTimeSeries``.

        Mirrors ``System.transform_single_time_series``.
        """
        return self._mgr.transform_single_time_series(horizon, interval)

    def build_time_series_reader(
        self,
        resolution: timedelta,
        *,
        name: str | None = None,
        name_glob: str | None = None,
        component_type: Type[Component] | None = None,
        **features: Any,
    ) -> "TimeSeriesReader":
        """Build a per-timestamp reader covering the batch's staged series as well.

        Mirrors ``System.build_time_series_reader``.
        """
        return self._mgr.build_reader(
            resolution,
            name=name,
            name_glob=name_glob,
            component_type=component_type,
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
    ) -> "ForecastReader":
        """Build a per-window forecast reader. Mirrors ``System.build_forecast_reader``."""
        return self._mgr.build_forecast_reader(
            resolution,
            time_series_type=time_series_type,
            name=name,
            name_glob=name_glob,
            component_type=component_type,
            **features,
        )
