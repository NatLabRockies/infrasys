"""Time series storage backed by the time-series-store Rust extension.

This backend is the single source of truth for both time series *data* and the
association *metadata* (which owner has which series, plus features/units). The
Rust store owns identity: each stored array is content-addressed (``data_hash``)
and each association is identified by a :class:`TimeSeriesKey`. infrasys assigns
no ids/uuids of its own.

This class keeps an in-memory index of lightweight :class:`_StoredSeries`
records so metadata queries (``get``/``list``/``has``/counts) do not read array
data. The index is populated as series are added and rehydrated from the store
on deserialization. The store's own metadata records carry everything a
:class:`_StoredSeries` needs, so no path here reads array data for metadata.

Writes go through the store's bulk API. A single :meth:`add_time_series` call
commits all of its owners in one batch, and :meth:`open_time_series_store`
buffers additions so that a whole block of them commits together --- the store
pays one catalog transaction per batch instead of one per series. The in-memory
index is updated as soon as a series is staged, so metadata queries inside a
batch see pending additions; any operation that must touch the store flushes the
buffer first.
"""

import atexit
import json
import re
import shutil
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import mkdtemp
from typing import Any, Generator, Literal

import numpy as np
import orjson
import pint
from loguru import logger
from infrastore import (  # type: ignore[import-untyped]
    Deterministic as RustDeterministic,
    NonSequentialTimeSeries as RustNonSequentialTimeSeries,
    OwnerCategory,
    SingleTimeSeries as RustSingleTimeSeries,
    Store,
    TimeSeriesType as RustTimeSeriesType,
)

from infrasys.component import Component
from infrasys.exceptions import (
    ISAlreadyAttached,
    ISInvalidParameter,
    ISNotStored,
    ISOperationNotAllowed,
)
from infrasys.serialization import serialize_value
from infrasys.supplemental_attribute import SupplementalAttribute
from infrasys.time_series_models import (
    Deterministic,
    DeterministicTimeSeriesKey,
    NonSequentialTimeSeries,
    NonSequentialTimeSeriesKey,
    QuantityMetadata,
    SingleTimeSeries,
    SingleTimeSeriesKey,
    TimeSeriesData,
    TimeSeriesKey,
    TimeSeriesStorageType,
    single_time_series_range,
)
from infrasys.time_series_reader import ForecastReader, TimeSeriesReader
from infrasys.utils.path_utils import clean_tmp_folder
from infrasys.utils.time_utils import as_naive_utc, as_utc, to_iso_8601

# Store-side time-series type names that infrasys exposes as ``Deterministic``.
_FORECAST_TYPES = frozenset({"Deterministic", "DeterministicSingleTimeSeries"})


@dataclass
class TimeSeriesCounts:
    """Summarizes stored time series.

    ``time_series_count`` is the number of unique stored arrays (content hash);
    arrays shared by multiple owners are counted once. ``reference_count`` is the
    total number of (owner, series) associations, so the difference reveals how
    much sharing/deduplication is in effect.
    """

    time_series_count: int
    reference_count: int
    # Keys are (owner_type, time_series_type, initial_timestamp, resolution)
    time_series_type_count: dict[tuple[str, str, str | None, str | None], int]


@dataclass
class _StoredSeries:
    """The infrasys-side descriptor of one owner's reference to a time series."""

    name: str
    time_series_type: str
    owner_type: str
    length: int
    features: dict[str, Any] = field(default_factory=dict)
    units: QuantityMetadata | None = None
    resolution: timedelta | None = None
    initial_timestamp: datetime | None = None
    # Forecast-only parameters (populated for Deterministic/DeterministicSingleTimeSeries).
    horizon: timedelta | None = None
    interval: timedelta | None = None
    window_count: int | None = None
    # The store's key for this association, cached to avoid re-scanning the owner's keys on
    # every read. None until the series is committed and the key is known.
    store_key: Any = None


@dataclass
class _PendingAdd:
    """One staged addition: the store's bulk item plus where it lives in the index."""

    item: dict[str, Any]
    stored: _StoredSeries
    owner_key: tuple[int, str]
    assoc_key: tuple


class TimeSeriesStoreStorage:
    """Store time series in the NetCDF/SQLite time-series-store format."""

    STORAGE_FILE = "time_series_store.nc"

    def __init__(self, directory: Path, store: Store) -> None:
        self._directory = directory
        self._store = store
        # (owner_id, owner_category_name) -> {assoc_key -> _StoredSeries}
        self._index: dict[tuple[int, str], dict[tuple, _StoredSeries]] = {}
        # Additions staged by open_time_series_store; None when no batch is open.
        self._pending: list[_PendingAdd] | None = None

    @property
    def store(self) -> Store:
        """Return the underlying time-series-store object.

        Component and supplemental attribute associations are stored in its SQLite catalog.
        """
        return self._store

    @contextmanager
    def open_time_series_store(
        self, mode: Literal["r", "r+", "a", "w", "w-"] = "a"
    ) -> Generator[Any, None, None]:
        """Buffer additions made inside the block and commit them in one batch.

        The in-memory index is updated as each series is staged, so ``has``/``list``/``get``
        behave the same inside the block as outside; a read or any other store operation
        flushes the buffer first. If the block raises, staged additions are dropped without
        ever reaching the store.
        """
        if self._pending is not None:
            # Nested block; the outermost one owns the flush.
            yield None
            return

        self._pending = []
        try:
            yield None
        except Exception:
            self._pending = None
            raise
        try:
            self._flush_pending()
        finally:
            self._pending = None

    @classmethod
    def create_with_temp_directory(
        cls,
        base_directory: Path | None = None,
        *,
        compression: str = "deflate",
        compression_level: int = 3,
        shuffle: bool = True,
    ) -> "TimeSeriesStoreStorage":
        if base_directory is not None:
            base_directory = Path(base_directory)
            base_directory.mkdir(parents=True, exist_ok=True)
        directory = Path(mkdtemp(dir=base_directory))
        logger.debug("Creating tmp folder at {}", directory)
        atexit.register(clean_tmp_folder, directory)
        return cls._create(
            directory,
            compression=compression,
            compression_level=compression_level,
            shuffle=shuffle,
        )

    @classmethod
    def _create(
        cls,
        directory: Path,
        *,
        compression: str = "deflate",
        compression_level: int = 3,
        shuffle: bool = True,
    ) -> "TimeSeriesStoreStorage":
        store = Store.create(
            path=directory / cls.STORAGE_FILE,
            compression=compression,
            compression_level=compression_level,
            shuffle=shuffle,
        )
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

        store = Store.open(
            path=directory / cls.STORAGE_FILE,
            read_only=read_only,
        )
        storage = cls(directory, store)
        storage.rehydrate()
        return storage, None

    def get_time_series_directory(self) -> Path:
        return self._directory

    # ------------------------------------------------------------------
    # Metadata operations
    # ------------------------------------------------------------------
    def add_time_series(
        self,
        time_series: TimeSeriesData,
        *owners: Any,
        context: Any = None,
        **features: Any,
    ) -> None:
        """Add a time series for one or more owners.

        All owners are committed in one batch, so nothing is stored if any of them
        already holds a matching association.

        Raises
        ------
        ISAlreadyAttached
            Raised if a matching association already exists for one of the owners.
        """
        if not owners:
            msg = "add_time_series requires at least one owner"
            raise ISOperationNotAllowed(msg)

        rust_time_series = _to_rust_time_series(time_series)
        units = _units_from_data(time_series)
        units_str = _serialize_units(units)
        ts_type = _data_type_name(time_series)

        # Validate every owner before touching the index so that a duplicate on the last
        # owner does not leave the earlier ones half-added.
        staged: list[_PendingAdd] = []
        seen: set[tuple[tuple[int, str], tuple]] = set()
        for owner in owners:
            owner_id, category = _owner_identity(owner)
            stored = _StoredSeries(
                name=time_series.name,
                time_series_type=ts_type,
                owner_type=type(owner).__name__,
                length=time_series.length,
                features=dict(features),
                units=units,
                resolution=getattr(time_series, "resolution", None),
                initial_timestamp=getattr(time_series, "initial_timestamp", None),
                horizon=getattr(time_series, "horizon", None),
                interval=getattr(time_series, "interval", None),
                window_count=getattr(time_series, "window_count", None),
            )
            owner_key = (owner_id, _category_name(category))
            assoc_key = _assoc_key(stored.name, stored.time_series_type, stored.features)
            if (owner_key, assoc_key) in seen or assoc_key in self._index.get(owner_key, {}):
                msg = (
                    f"Time series {stored.time_series_type}.{stored.name} with "
                    f"features={stored.features} is already stored for owner id {owner_id}."
                )
                raise ISAlreadyAttached(msg)
            seen.add((owner_key, assoc_key))
            item = {
                "owner_id": owner_id,
                "owner_type": stored.owner_type,
                "owner_category": category,
                "time_series": rust_time_series,
                "features": dict(stored.features),
                "units": units_str,
            }
            staged.append(
                _PendingAdd(item=item, stored=stored, owner_key=owner_key, assoc_key=assoc_key)
            )

        for entry in staged:
            self._index.setdefault(entry.owner_key, {})[entry.assoc_key] = entry.stored

        if self._pending is not None:
            self._pending.extend(staged)
        else:
            self._commit(staged)

    def _commit(self, pending: list[_PendingAdd]) -> None:
        """Write staged additions to the store, undoing the index entries on failure."""
        if not pending:
            return
        try:
            keys = self._store.add_time_series_bulk([entry.item for entry in pending])
        except Exception:
            # The store batch is all-or-nothing, so drop every index entry it covered.
            for entry in pending:
                assoc_map = self._index.get(entry.owner_key)
                if assoc_map is not None:
                    assoc_map.pop(entry.assoc_key, None)
            raise
        # The store returns the new keys in input order; caching them here spares every
        # later read a scan of the owner's keys.
        for entry, key in zip(pending, keys):
            entry.stored.store_key = key

    def _flush_pending(self) -> None:
        """Commit anything staged by an open batch. No-op when nothing is buffered."""
        if not self._pending:
            return
        pending = self._pending
        # Reset before committing so that the batch stays open for further additions and a
        # failed commit cannot leave the entries staged a second time.
        self._pending = []
        self._commit(pending)

    def get_metadata(
        self,
        owner: Any,
        name: str | None = None,
        time_series_type: str | None = None,
        **features: Any,
    ) -> _StoredSeries:
        """Return the single stored-series descriptor matching the inputs.

        Raises
        ------
        ISNotStored
            Raised if nothing matches.
        ISOperationNotAllowed
            Raised if more than one matches.
        """
        matches = self.list_metadata(
            owner, name=name, time_series_type=time_series_type, **features
        )
        if not matches:
            msg = "No time series matching the inputs is stored"
            raise ISNotStored(msg)
        if len(matches) > 1:
            msg = f"Found more than one time series matching inputs: {len(matches)}"
            raise ISOperationNotAllowed(msg)
        return matches[0]

    def list_metadata(
        self,
        *owners: Any,
        name: str | None = None,
        time_series_type: str | None = None,
        **features: Any,
    ) -> list[_StoredSeries]:
        """Return all stored-series descriptors matching the inputs across the owners."""
        if not owners:
            msg = "At least one owner must be passed."
            raise ISOperationNotAllowed(msg)
        results: list[_StoredSeries] = []
        for owner in owners:
            owner_id, category = _owner_identity(owner)
            assoc_map = self._index.get((owner_id, _category_name(category)), {})
            for stored in assoc_map.values():
                if _matches(stored, name, time_series_type, features):
                    results.append(stored)
        return results

    def has_metadata(
        self,
        owner: Any,
        name: str | None = None,
        time_series_type: str | None = None,
        **features: Any,
    ) -> bool:
        """Return True if any stored series matches the inputs."""
        return bool(
            self.list_metadata(owner, name=name, time_series_type=time_series_type, **features)
        )

    def remove(
        self,
        *owners: Any,
        name: str | None = None,
        time_series_type: str | None = None,
        context: Any = None,
        **features: Any,
    ) -> list[_StoredSeries]:
        """Remove all associations matching the inputs and return their descriptors.

        Raises
        ------
        ISNotStored
            Raised if nothing matches.
        """
        removed: list[_StoredSeries] = []
        for owner in owners:
            owner_id, category = _owner_identity(owner)
            assoc_map = self._index.get((owner_id, _category_name(category)), {})
            to_remove = [
                key
                for key, stored in assoc_map.items()
                if _matches(stored, name, time_series_type, features)
            ]
            for key in to_remove:
                stored = assoc_map.pop(key)
                rust_key = self._resolve_key(owner_id, category, stored)
                self._store.remove_time_series(rust_key)
                removed.append(stored)
        if not removed:
            msg = "No metadata matching the inputs is stored"
            raise ISNotStored(msg)
        return removed

    def snapshot_index(self) -> set[tuple[tuple[int, str], tuple]]:
        """Return a snapshot of the current associations for rollback."""
        return {
            (owner_key, assoc_key)
            for owner_key, assoc_map in self._index.items()
            for assoc_key in assoc_map
        }

    def rollback_to(self, snapshot: set[tuple[tuple[int, str], tuple]]) -> None:
        """Remove every association added since ``snapshot`` was taken.

        Additions still staged in an open batch never reached the store, so they are
        dropped from the index without a store round trip.
        """
        staged = {(entry.owner_key, entry.assoc_key) for entry in self._pending or ()}
        if self._pending:
            self._pending = []
        for owner_key, assoc_map in list(self._index.items()):
            owner_id, category_name = owner_key
            category = (
                OwnerCategory.Component
                if category_name == "Component"
                else OwnerCategory.SupplementalAttribute
            )
            for assoc_key in list(assoc_map):
                if (owner_key, assoc_key) in snapshot:
                    continue
                stored = assoc_map.pop(assoc_key)
                if (owner_key, assoc_key) in staged:
                    continue
                try:
                    rust_key = self._resolve_key(owner_id, category, stored)
                except ISNotStored:
                    continue
                self._store.remove_time_series(rust_key)

    def key_for(self, stored: _StoredSeries) -> TimeSeriesKey:
        """Build the public :class:`TimeSeriesKey` for a stored-series descriptor."""
        return _key_from_stored(stored)

    def get_time_series_counts(self) -> TimeSeriesCounts:
        """Return summary counts of stored time series.

        Unique arrays come from the store's content-addressed array groups; the
        per-type breakdown counts associations from the in-memory index.
        """
        self._flush_pending()
        groups = self._store.list_array_groups()
        unique_arrays = len(groups)
        references = sum(len(group["keys"]) for group in groups)

        type_count: dict[tuple[str, str, str | None, str | None], int] = {}
        for assoc_map in self._index.values():
            for stored in assoc_map.values():
                key = (
                    stored.owner_type,
                    stored.time_series_type,
                    stored.initial_timestamp.isoformat() if stored.initial_timestamp else None,
                    to_iso_8601(stored.resolution) if stored.resolution else None,
                )
                type_count[key] = type_count.get(key, 0) + 1
        return TimeSeriesCounts(
            time_series_count=unique_arrays,
            reference_count=references,
            time_series_type_count=type_count,
        )

    def rehydrate(self) -> None:
        """Rebuild the in-memory index from the persisted store.

        The store's keys are fetched once for the whole catalog and attached to the
        descriptors, so later reads never have to scan for them.
        """
        self._flush_pending()
        self._index.clear()
        keys = {
            (
                (key.owner_id, _category_name(key.owner_category)),
                _assoc_key(key.name, _ts_type_name(key.time_series_type), dict(key.features)),
            ): key
            for key in self._store.list_keys()
        }
        for record in self._store.list_time_series():
            stored = self._record_from_store(record)
            owner_key = (record["owner_id"], record["owner_category"])
            assoc_key = _assoc_key(stored.name, stored.time_series_type, stored.features)
            stored.store_key = keys.get((owner_key, assoc_key))
            self._index.setdefault(owner_key, {})[assoc_key] = stored

    def transform_single_time_series(self, horizon: timedelta, interval: timedelta) -> int:
        """Derive ``Deterministic`` forecasts from every stored ``SingleTimeSeries``.

        Mirrors the Rust store's store-wide transform: each ``SingleTimeSeries`` gains a forecast
        association sharing the same underlying array. Returns the number of series transformed.
        """
        self._flush_pending()
        count = self._store.transform_single_time_series(horizon=horizon, interval=interval)
        self.rehydrate()
        return count

    # ------------------------------------------------------------------
    # Readers
    # ------------------------------------------------------------------
    def build_reader(
        self,
        resolution: timedelta,
        *,
        name: str | None = None,
        name_glob: str | None = None,
        owner_type: str | None = None,
        **features: Any,
    ) -> TimeSeriesReader:
        """Build a cross-sectional reader over the matching ``SingleTimeSeries``."""
        self._flush_pending()
        reader = self._store.build_static_reader(
            resolution,
            owner_category=OwnerCategory.Component,
            owner_type=owner_type,
            name=name,
            name_glob=name_glob,
            features=features or None,
        )
        group_component_ids = [
            tuple(key.owner_id for key in group["keys"]) for group in reader.groups()
        ]
        units = {
            key.owner_id: self._units_for_key(key)
            for group in reader.groups()
            for key in group["keys"]
        }
        return TimeSeriesReader(self._store, reader, group_component_ids, units)

    def build_forecast_reader(
        self,
        resolution: timedelta,
        *,
        time_series_type: str = "Deterministic",
        name: str | None = None,
        name_glob: str | None = None,
        owner_type: str | None = None,
        **features: Any,
    ) -> ForecastReader:
        """Build a cross-sectional reader over the matching forecasts."""
        self._flush_pending()
        reader = self._store.build_forecast_reader(
            _rust_time_series_type(time_series_type),
            resolution,
            owner_category=OwnerCategory.Component,
            owner_type=owner_type,
            name=name,
            name_glob=name_glob,
            features=features or None,
        )
        entries = reader.entries()
        component_ids = tuple(key.owner_id for key in entries)
        slots = tuple(reader.entry_slot(index) for index in range(len(entries)))
        units = {key.owner_id: self._units_for_key(key) for key in entries}
        return ForecastReader(self._store, reader, component_ids, slots, units)

    def _units_for_key(self, key: Any) -> QuantityMetadata | None:
        """Return the units recorded for a store key, or None if it is not indexed."""
        assoc_map = self._index.get((key.owner_id, "Component"), {})
        assoc_key = _assoc_key(key.name, _ts_type_name(key.time_series_type), dict(key.features))
        stored = assoc_map.get(assoc_key)
        return stored.units if stored is not None else None

    # ------------------------------------------------------------------
    # Data operations
    # ------------------------------------------------------------------
    def get_time_series(
        self,
        stored: _StoredSeries,
        owner: Any,
        start_time: datetime | None = None,
        length: int | None = None,
        context: Any = None,
    ) -> TimeSeriesData:
        owner_id, category = _owner_identity(owner)
        key, time_range, result_initial_timestamp = self._plan_read(
            stored, owner_id, category, start_time, length
        )
        rust_result = self._store.get_time_series(key, time_range=time_range)
        return self._build_result(stored, rust_result, result_initial_timestamp)

    def get_time_series_bulk(
        self,
        records: list[_StoredSeries],
        owner: Any,
        start_time: datetime | None = None,
        length: int | None = None,
        context: Any = None,
    ) -> list[TimeSeriesData]:
        """Return the arrays for several stored series belonging to one owner.

        Reads that share a time range are fetched in a single store call, which lets the
        store decompress each dataset once instead of once per series.
        """
        if not records:
            return []
        owner_id, category = _owner_identity(owner)
        plans = [
            self._plan_read(stored, owner_id, category, start_time, length) for stored in records
        ]
        # bulk_read applies one time range to every key, so read each distinct range as its
        # own batch. Unsliced reads all share a range of None and go out together.
        batches: dict[Any, list[int]] = {}
        for position, (_, time_range, _) in enumerate(plans):
            batches.setdefault(time_range, []).append(position)

        results: list[Any] = [None] * len(records)
        for time_range, positions in batches.items():
            rust_results = self._store.bulk_read(
                [plans[position][0] for position in positions], time_range=time_range
            )
            for position, rust_result in zip(positions, rust_results):
                results[position] = self._build_result(
                    records[position], rust_result, plans[position][2]
                )
        return results

    def _plan_read(
        self,
        stored: _StoredSeries,
        owner_id: int,
        category: OwnerCategory,
        start_time: datetime | None,
        length: int | None,
    ) -> tuple[Any, tuple[datetime, datetime] | None, datetime | None]:
        """Return the store key, time range, and resulting start time for one read."""
        key = self._resolve_key(owner_id, category, stored)
        if stored.time_series_type in _FORECAST_TYPES and (
            start_time is not None or length is not None
        ):
            msg = "start_time/length slicing is not supported for forecast time series"
            raise NotImplementedError(msg)
        if stored.time_series_type != "SingleTimeSeries":
            return key, None, None
        assert stored.initial_timestamp is not None and stored.resolution is not None
        if start_time is None and length is None:
            # No range keeps unsliced reads in one batch and returns the whole series.
            return key, None, stored.initial_timestamp
        index, result_length = single_time_series_range(
            stored.initial_timestamp, stored.resolution, stored.length, start_time, length
        )
        result_initial_timestamp = stored.initial_timestamp + index * stored.resolution
        time_range = (
            as_utc(result_initial_timestamp),
            as_utc(result_initial_timestamp + result_length * stored.resolution),
        )
        return key, time_range, result_initial_timestamp

    def _build_result(
        self,
        stored: _StoredSeries,
        rust_result: Any,
        result_initial_timestamp: datetime | None,
    ) -> TimeSeriesData:
        """Convert one store result into the infrasys time series model."""
        data = np.asarray(rust_result.data)
        if stored.units is not None:
            data = stored.units.quantity_type(data, stored.units.units)

        if stored.time_series_type == "SingleTimeSeries":
            assert result_initial_timestamp is not None
            return SingleTimeSeries(
                name=stored.name,
                resolution=stored.resolution,
                initial_timestamp=result_initial_timestamp,
                data=data,
            )
        if stored.time_series_type == "NonSequentialTimeSeries":
            return NonSequentialTimeSeries(
                name=stored.name,
                data=data,
                timestamps=np.asarray(
                    [as_naive_utc(x) for x in rust_result.timestamps],
                    dtype=object,
                ),
            )
        if stored.time_series_type in _FORECAST_TYPES:
            # The Rust store returns (horizon_steps, count); infrasys uses (window_count,
            # horizon_steps), so transpose back.
            return Deterministic(
                name=stored.name,
                data=data.T,
                initial_timestamp=as_naive_utc(rust_result.initial_timestamp),
                resolution=_parse_resolution(rust_result.resolution),
                horizon=_parse_resolution(rust_result.horizon),
                interval=_parse_resolution(rust_result.interval),
                window_count=rust_result.count,
            )

        msg = f"get_time_series not implemented for {stored.time_series_type}"
        raise NotImplementedError(msg)

    def serialize(
        self, data: dict[str, Any], dst: Path | str, src: Path | str | None = None
    ) -> None:
        self._flush_pending()
        self._store.flush()
        source = self._directory if src is None else Path(src)
        destination = Path(dst)
        destination.mkdir(parents=True, exist_ok=True)
        if source.resolve() == self._directory.resolve():
            # Windows denies reads on the NetCDF/SQLite files while the store holds
            # them open, so a flush is not enough: release the handles, copy, and
            # reopen. POSIX would tolerate copying in place, but the close/reopen is
            # cheap next to the copy and keeps one code path across platforms.
            with self._closed_store():
                self._copy_store(source, destination)
        else:
            self._copy_store(source, destination)
        self.add_serialized_data(data)

    @contextmanager
    def _closed_store(self) -> Generator[None, None, None]:
        """Release the store's file handles for the duration of the block.

        The reopen runs even if the body raises, so a failed copy leaves the storage
        usable rather than stranding it on a closed handle.
        """
        read_only = self._store.read_only
        self._store.close()
        try:
            yield
        finally:
            self._store = Store.open(
                path=self._directory / self.STORAGE_FILE,
                read_only=read_only,
            )

    @staticmethod
    def add_serialized_data(data: dict[str, Any]) -> None:
        data["time_series_storage_type"] = TimeSeriesStorageType.TIME_SERIES_STORE.value

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _resolve_key(self, owner_id: int, category: OwnerCategory, stored: _StoredSeries):
        # A staged series has no key until it is committed.
        self._flush_pending()
        if stored.store_key is not None:
            return stored.store_key
        keys = self._store.get_time_series_keys(owner_id, category)
        for key in keys:
            if (
                key.name == stored.name
                and _ts_type_name(key.time_series_type) == stored.time_series_type
                and dict(key.features) == dict(stored.features)
            ):
                stored.store_key = key
                return key
        msg = (
            f"No time series {stored.time_series_type}.{stored.name} is stored "
            f"for owner id {owner_id}"
        )
        raise ISNotStored(msg)

    def _record_from_store(self, record: dict[str, Any]) -> _StoredSeries:
        """Build a stored-series descriptor from a store metadata record.

        The record carries every field the descriptor needs, including the forecast
        parameters, so this never reads array data.
        """
        ts_type = record["time_series_type"]
        units = _deserialize_units(record.get("units"))
        resolution = _parse_resolution(record["resolution"]) if record.get("resolution") else None
        initial_timestamp = None
        horizon = interval = None
        window_count = None
        if record.get("initial_timestamp"):
            initial_timestamp = as_naive_utc(datetime.fromisoformat(record["initial_timestamp"]))
        if ts_type in _FORECAST_TYPES:
            horizon = _parse_resolution(record["horizon"])
            interval = _parse_resolution(record["interval"])
            window_count = record["count"]
        return _StoredSeries(
            name=record["name"],
            time_series_type=ts_type,
            owner_type=record["owner_type"],
            length=record["length"],
            features=dict(record.get("features") or {}),
            units=units,
            resolution=resolution,
            initial_timestamp=initial_timestamp,
            horizon=horizon,
            interval=interval,
            window_count=window_count,
        )

    @classmethod
    def _copy_store(cls, source: Path, destination: Path) -> None:
        for name in (cls.STORAGE_FILE, f"{cls.STORAGE_FILE}.sqlite"):
            src = source / name
            dst = destination / name
            if src.resolve() != dst.resolve():
                shutil.copyfile(src, dst)


def _key_from_stored(stored: _StoredSeries) -> TimeSeriesKey:
    if stored.time_series_type == "SingleTimeSeries":
        assert stored.initial_timestamp is not None and stored.resolution is not None
        return SingleTimeSeriesKey(
            name=stored.name,
            time_series_type=SingleTimeSeries,
            features=stored.features,
            length=stored.length,
            initial_timestamp=stored.initial_timestamp,
            resolution=stored.resolution,
        )
    if stored.time_series_type == "NonSequentialTimeSeries":
        return NonSequentialTimeSeriesKey(
            name=stored.name,
            time_series_type=NonSequentialTimeSeries,
            features=stored.features,
            length=stored.length,
        )
    if stored.time_series_type in _FORECAST_TYPES:
        assert stored.initial_timestamp is not None and stored.resolution is not None
        assert (
            stored.interval is not None
            and stored.horizon is not None
            and stored.window_count is not None
        )
        return DeterministicTimeSeriesKey(
            name=stored.name,
            time_series_type=Deterministic,
            features=stored.features,
            initial_timestamp=stored.initial_timestamp,
            resolution=stored.resolution,
            interval=stored.interval,
            horizon=stored.horizon,
            window_count=stored.window_count,
        )
    msg = f"key not implemented for {stored.time_series_type}"
    raise NotImplementedError(msg)


def _data_type_name(time_series: TimeSeriesData) -> str:
    if isinstance(time_series, SingleTimeSeries):
        return "SingleTimeSeries"
    if isinstance(time_series, NonSequentialTimeSeries):
        return "NonSequentialTimeSeries"
    if isinstance(time_series, Deterministic):
        return "Deterministic"
    msg = f"add_time_series not implemented for {type(time_series)}"
    raise NotImplementedError(msg)


def _to_rust_time_series(time_series: TimeSeriesData):
    if isinstance(time_series, SingleTimeSeries):
        return RustSingleTimeSeries(
            as_utc(time_series.initial_timestamp),
            time_series.resolution,
            np.asarray(time_series.data_array, dtype=np.float64),
            time_series.name,
        )
    if isinstance(time_series, NonSequentialTimeSeries):
        return RustNonSequentialTimeSeries(
            [as_utc(x) for x in time_series.timestamps.astype("datetime64[us]").tolist()],
            np.asarray(time_series.data_array, dtype=np.float64),
            time_series.name,
        )
    if isinstance(time_series, Deterministic):
        # infrasys stores forecasts as (window_count, horizon_steps); the Rust store expects
        # the transpose (horizon_steps, count).
        data = np.ascontiguousarray(np.asarray(time_series.data_array, dtype=np.float64).T)
        return RustDeterministic(
            as_utc(time_series.initial_timestamp),
            time_series.resolution,
            time_series.horizon,
            time_series.interval,
            time_series.window_count,
            data,
            time_series.name,
        )
    msg = f"add_time_series not implemented for {type(time_series)}"
    raise NotImplementedError(msg)


def _units_from_data(time_series: TimeSeriesData) -> QuantityMetadata | None:
    if isinstance(time_series.data, pint.Quantity):
        return QuantityMetadata(
            module=type(time_series.data).__module__,
            quantity_type=type(time_series.data),
            units=str(time_series.data.units),
        )
    return None


def _category_name(category: OwnerCategory) -> str:
    match category:
        case OwnerCategory.Component:
            return "Component"
        case OwnerCategory.SupplementalAttribute:
            return "SupplementalAttribute"
        case _:
            msg = f"Unhandled category: {category}"
            raise NotImplementedError(msg)


def _ts_type_name(rust_type: Any) -> str:
    return str(rust_type).rsplit(".", 1)[-1]


def _rust_time_series_type(name: str) -> Any:
    """Return the store's time-series-type enum member for an infrasys type name."""
    rust_type = getattr(RustTimeSeriesType, name, None)
    if rust_type is None:
        msg = f"Unsupported time series type for readers: {name}"
        raise ISInvalidParameter(msg)
    return rust_type


def _owner_identity(owner: Any) -> tuple[int, OwnerCategory]:
    if owner.id is None:
        msg = f"{owner.label} does not have an id assigned."
        raise ISOperationNotAllowed(msg)
    if isinstance(owner, Component):
        category = OwnerCategory.Component
    elif isinstance(owner, SupplementalAttribute):
        category = OwnerCategory.SupplementalAttribute
    else:
        msg = f"Invalid owner type: {type(owner)}"
        raise ISInvalidParameter(msg)
    return owner.id, category


def _assoc_key(name: str, time_series_type: str, features: dict[str, Any]) -> tuple:
    return (name, time_series_type, tuple(sorted(features.items())))


def _type_matches(stored_type: str, filter_type: str | None) -> bool:
    """Match a stored time-series type against a query filter.

    A ``Deterministic`` filter matches both explicitly-stored forecasts and forecasts derived
    from a ``SingleTimeSeries`` via ``transform_single_time_series`` (which the store tags as
    ``DeterministicSingleTimeSeries``). infrasys surfaces both as ``Deterministic``.
    """
    if filter_type is None:
        return True
    if filter_type == "Deterministic":
        return stored_type in _FORECAST_TYPES
    return stored_type == filter_type


def _matches(
    stored: _StoredSeries,
    name: str | None,
    time_series_type: str | None,
    features: dict[str, Any],
) -> bool:
    if name is not None and stored.name != name:
        return False
    if not _type_matches(stored.time_series_type, time_series_type):
        return False
    for key, value in features.items():
        if stored.features.get(key) != value:
            return False
    return True


def _serialize_units(units: QuantityMetadata | None) -> str | None:
    if units is None:
        return None
    return orjson.dumps(serialize_value(units)).decode()


def _deserialize_units(units: str | None) -> QuantityMetadata | None:
    if not units:
        return None
    return QuantityMetadata.model_validate(json.loads(units))


_ISO_DURATION = re.compile(
    r"^P(?:(?P<weeks>\d+)W)?(?:(?P<days>\d+)D)?"
    r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?$"
)


def _parse_resolution(resolution: str | None) -> timedelta:
    """Parse a standard ISO 8601 duration (e.g. ``PT1H``) emitted by the store."""
    if resolution is None:
        msg = "resolution is required for SingleTimeSeries metadata"
        raise ISNotStored(msg)
    match = _ISO_DURATION.match(resolution)
    if match is None:
        msg = f"Could not parse resolution {resolution!r}"
        raise ISNotStored(msg)
    parts = {k: float(v) for k, v in match.groupdict().items() if v is not None}
    return timedelta(
        weeks=parts.get("weeks", 0),
        days=parts.get("days", 0),
        hours=parts.get("hours", 0),
        minutes=parts.get("minutes", 0),
        seconds=parts.get("seconds", 0),
    )
