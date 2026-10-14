"""Time series storage backed by the infrastore Rust extension.

This backend is the single source of truth for both time series *data* and the
association *metadata* (which owner has which series, plus features/units). The
Rust store owns identity: each stored array is content-addressed (``data_hash``)
and each association is identified by an integer catalog id. infrasys assigns
no ids/uuids of its own.

Committed metadata lives in infrastore and is queried there for ``get``/``list``/``has``/
counts. The only infrasys-side metadata is the transaction context's set of association
identities for additions that have not reached the store yet. Store metadata rows are
used directly for reads and public-key conversion.

Writes go through the store's bulk API, and every operation belongs to a
:class:`TimeSeriesStorageContext` that owns its batch. Callers reach these operations
through the context, not through this class: the entry points here are private and take
their context positionally, so a caller's ``**features`` may contain a key named
``context`` without colliding with the plumbing. A single add stages all its owners
together. A caller who opens a context can stage many calls so each flush reaches the
store as one bulk write instead of one write per series.

This class holds no batch state and no reference to any context. Staged additions live
on the context until it flushes them, so a batch is visible to itself and to nothing else.

Timestamps cross this boundary in the spelling the caller wrote them in. The store
records how each series' timestamps were spelled --- an instant in UTC, an instant at a
fixed offset, an instant in a named IANA zone, or a wall clock naming no instant --- and
hands the same spelling back on read, so infrasys neither attaches a zone to a naive
timestamp nor strips one from an aware timestamp. A naive datetime is a wall clock and
comes back naive; an aware one comes back aware in the same zone. Read bounds must be
spelled the way the series is; the store refuses to coerce across that line, and
:func:`infrasys.utils.time_utils.advance` is what keeps a derived bound on the instant
grid the store slices.
"""

import atexit
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import mkdtemp
from typing import TYPE_CHECKING, Any

import numpy as np
import orjson
import pint
from loguru import logger
from infrastore import (
    Deterministic as RustDeterministic,
    DuplicateAssociationError,
    NonSequentialTimeSeries as RustNonSequentialTimeSeries,
    OwnerCategory,
    SingleTimeSeries as RustSingleTimeSeries,
    Store,
    TimeSeriesType as RustTimeSeriesType,
)

if TYPE_CHECKING:
    from infrastore import StaticReaderGroup, TimeSeriesAddItem, TimeSeriesMetadata

from infrasys.component import Component
from infrasys.exceptions import (
    ISAlreadyAttached,
    ISInvalidParameter,
    ISNotStored,
    ISOperationNotAllowed,
)
from infrasys.serialization import serialize_value
from infrasys.supplemental_attribute import SupplementalAttribute
from infrasys.time_series_context import (
    AUTO_FLUSH_BYTES,
    AUTO_FLUSH_THRESHOLD,
    AssocKey,
    OwnerKey,
    TimeSeriesStorageContext,
    _PendingAdd,
)
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
from infrasys.utils.time_utils import advance, from_catalog_timestamp, to_iso_8601

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


class TimeSeriesStoreStorage:
    """Store time series in the HDF5/SQLite infrastore format."""

    STORAGE_FILE = "time_series_store.h5"

    def __init__(self, directory: Path, store: Store) -> None:
        self._directory = directory
        self._store = store

    @property
    def store(self) -> Store:
        """Return the underlying infrastore object.

        Component and supplemental attribute associations are stored in its SQLite catalog.
        """
        return self._store

    @property
    def read_only(self) -> bool:
        """Return True if the store refuses writes."""
        return self._store.read_only

    def raise_if_read_only(self) -> None:
        """Raise if the store refuses writes.

        Every manager in a system writes through this one store --- component
        parent/child associations and supplemental attribute associations as well as time
        series --- so a read-only open makes all of them fail. The managers mutate their
        in-memory containers before the store call that persists the change, so they call
        this *first*: a refusal has to land before anything is touched, or the system is
        left describing a store it no longer agrees with.

        Raises
        ------
        ISOperationNotAllowed
            Raised if the store was opened read-only.
        """
        if self._store.read_only:
            msg = "Cannot modify a system whose time series store was opened read-only."
            raise ISOperationNotAllowed(msg)

    def new_context(
        self,
        auto_flush_threshold: int = AUTO_FLUSH_THRESHOLD,
        auto_flush_bytes: int = AUTO_FLUSH_BYTES,
    ) -> TimeSeriesStorageContext:
        """Return a new context bound to this storage."""
        return TimeSeriesStorageContext(
            self,
            auto_flush_threshold=auto_flush_threshold,
            auto_flush_bytes=auto_flush_bytes,
        )

    def write_pending(self, pending: list[_PendingAdd]) -> None:
        """Write buffered requests through infrastore's ID-based bulk API."""
        if not pending:
            return
        try:
            items: list[TimeSeriesAddItem] = [entry.item for entry in pending]
            ids = self._store.add_time_series_bulk(items)
        except DuplicateAssociationError as error:
            raise ISAlreadyAttached(str(error)) from error
        if len(ids) != len(pending):
            msg = "infrastore returned a different number of IDs than added time series"
            raise RuntimeError(msg)

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
            path=str(directory / cls.STORAGE_FILE),
            compression=compression,
            compression_level=compression_level,
            shuffle=shuffle,
            # This directory is scratch: it is either a mkdtemp that `atexit`
            # removes, or a working copy of a serialized system. A crash loses the
            # in-memory `System` regardless, so journaling the catalog to disk on
            # every commit buys durability nobody can consume. Holding it in RAM
            # skips the WAL and fsync work; `persist_to` writes it out at save.
            # Arrays still stream to the HDF5 file, so this does not require the
            # data to fit in memory.
            catalog="memory",
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
            store = Store.open(
                path=str(directory / cls.STORAGE_FILE),
                read_only=True,
                catalog="attached",
            )
        else:
            directory = Path(mkdtemp(dir=dst_time_series_directory))
            logger.debug("Creating tmp folder at {}", directory)
            atexit.register(clean_tmp_folder, directory)
            # Work on an independent copy so writes cannot damage the serialized source.
            # The in-memory catalog matches the scratch-store configuration in `_create`.
            store = Store.open_copy(
                str(time_series_dir / cls.STORAGE_FILE),
                str(directory / cls.STORAGE_FILE),
                catalog="memory",
            )

        return cls(directory, store), None

    def get_time_series_directory(self) -> Path:
        return self._directory

    def close(self) -> None:
        """Close the underlying store, releasing its file handles."""
        self._store.close()

    # ------------------------------------------------------------------
    # Metadata operations
    # ------------------------------------------------------------------
    # These are the implementations behind the identically named methods on
    # TimeSeriesStorageContext, which is the only supported caller. The context is
    # positional-only so that its name stays free for a caller's time series features:
    # `**features` may legitimately contain a key called "context".
    def _add_time_series(
        self,
        context: TimeSeriesStorageContext,
        /,
        time_series: TimeSeriesData,
        *owners: Any,
        **features: Any,
    ) -> None:
        """Stage a time series for one or more owners on ``context``.

        All owners are staged together, so nothing is stored if any of them already holds
        a matching association. The additions reach the store when the context flushes.

        Raises
        ------
        ISAlreadyAttached
            Raised if a matching association already exists for one of the owners, either
            committed or already staged on this context.
        """
        if not owners:
            msg = "add_time_series requires at least one owner"
            raise ISOperationNotAllowed(msg)

        rust_time_series = _to_rust_time_series(time_series)
        time_series_type = _data_type_name(time_series)

        # Validate every owner before staging any of them so that a duplicate on the last
        # owner does not leave the earlier ones half-added.
        staged: list[_PendingAdd] = []
        seen: set[tuple[OwnerKey, AssocKey]] = set()
        # All owners share one array, so its size is charged to the first entry only.
        nbytes = _estimate_nbytes(time_series)
        for owner in owners:
            owner_id, category = _owner_identity(owner)
            owner_type = type(owner).__name__
            owner_key = (owner_id, _category_name(category))
            assoc_key = _assoc_key(time_series.name, time_series_type, dict(features))
            already_staged = assoc_key in context.staged_for(owner_key)
            already_committed = _has_exact_time_series(
                self._store,
                owner_id,
                category,
                time_series_type,
                time_series.name,
                features,
            )
            if (owner_key, assoc_key) in seen or already_staged or already_committed:
                msg = (
                    f"Time series {time_series_type}.{time_series.name} with "
                    f"features={features} is already stored for owner id {owner_id}."
                )
                raise ISAlreadyAttached(msg)
            seen.add((owner_key, assoc_key))
            item: TimeSeriesAddItem = {
                "owner_id": owner_id,
                "owner_type": owner_type,
                "owner_category": category,
                "time_series": rust_time_series,
                "features": dict(features),
            }
            staged.append(
                _PendingAdd(
                    item=item,
                    owner_key=owner_key,
                    assoc_key=assoc_key,
                    nbytes=nbytes if not staged else 0,
                )
            )

        context.stage(staged)

    def _get_metadata(
        self,
        context: TimeSeriesStorageContext,
        /,
        owner: Any,
        name: str | None = None,
        time_series_type: str | None = None,
        **features: Any,
    ) -> "TimeSeriesMetadata":
        """Return the single infrastore metadata row matching the inputs.

        Raises
        ------
        ISNotStored
            Raised if nothing matches.
        ISOperationNotAllowed
            Raised if more than one matches.
        """
        matches = self._list_metadata(
            context, owner, name=name, time_series_type=time_series_type, **features
        )
        if not matches:
            msg = "No time series matching the inputs is stored"
            raise ISNotStored(msg)
        if len(matches) > 1:
            msg = f"Found more than one time series matching inputs: {len(matches)}"
            raise ISOperationNotAllowed(msg)
        return matches[0]

    def _list_metadata(
        self,
        context: TimeSeriesStorageContext,
        /,
        *owners: Any,
        name: str | None = None,
        time_series_type: str | None = None,
        **features: Any,
    ) -> list["TimeSeriesMetadata"]:
        """Return matching infrastore metadata rows across owners.

        The context flushes buffered additions first, so every returned row and ID comes
        directly from infrastore.
        """
        if not owners:
            msg = "At least one owner must be passed."
            raise ISOperationNotAllowed(msg)
        context.flush()
        results: list[TimeSeriesMetadata] = []
        for owner in owners:
            owner_id, category = _owner_identity(owner)
            results.extend(
                self._list_committed_metadata(owner_id, category, name, time_series_type, features)
            )
        return results

    def _list_committed_metadata(
        self,
        owner_id: int,
        category: OwnerCategory,
        name: str | None,
        time_series_type: str | None,
        features: dict[str, Any],
    ) -> list["TimeSeriesMetadata"]:
        """List an owner's metadata rows directly from infrastore."""
        rust_type = _store_type_filter(time_series_type)
        if time_series_type is not None and rust_type is None:
            return []
        return self._store.list_metadata(
            owner_id=owner_id,
            owner_category=category,
            time_series_type=rust_type,
            name=name,
            features=features or None,
        )

    def _has_metadata(
        self,
        context: TimeSeriesStorageContext,
        /,
        owner: Any,
        name: str | None = None,
        time_series_type: str | None = None,
        **features: Any,
    ) -> bool:
        """Return True if any stored series matches the inputs.

        The store handles committed rows, including the ``Deterministic`` family filter.
        Staged additions are visible only to their own context and are checked first.
        """
        owner_id, category = _owner_identity(owner)
        staged = context.staged_for((owner_id, _category_name(category)))
        if any(
            _matches_assoc_key(assoc_key, name, time_series_type, features) for assoc_key in staged
        ):
            return True
        rust_type = _store_type_filter(time_series_type)
        if time_series_type is not None and rust_type is None:
            return False
        return self._store.has_any_time_series(
            owner_id=owner_id,
            owner_category=category,
            time_series_type=rust_type,
            name=name,
            features=features or None,
        )

    def _remove(
        self,
        context: TimeSeriesStorageContext,
        /,
        *owners: Any,
        name: str | None = None,
        time_series_type: str | None = None,
        **features: Any,
    ) -> int:
        """Remove matching associations through the store's catalog filters.

        Staged additions on ``context`` are flushed first, so a series added and removed
        inside one block is removed rather than committed by a later flush.

        Raises
        ------
        ISNotStored
            Raised if nothing matches.
        """
        context.flush()
        rust_type = _store_type_filter(time_series_type)
        if time_series_type is not None and rust_type is None:
            msg = "No metadata matching the inputs is stored"
            raise ISNotStored(msg)

        owner_filters: list[tuple[int, OwnerCategory]] = []
        seen: set[OwnerKey] = set()
        for owner in owners:
            owner_id, category = _owner_identity(owner)
            owner_key = (owner_id, _category_name(category))
            if owner_key not in seen:
                seen.add(owner_key)
                owner_filters.append((owner_id, category))

        removed = 0
        with self._store.transaction():
            for owner_id, category in owner_filters:
                removed += self._store.remove_by_filter(
                    owner_id=owner_id,
                    owner_category=category,
                    time_series_type=rust_type,
                    name=name,
                    features=features or None,
                )
            if not removed:
                msg = "No metadata matching the inputs is stored"
                raise ISNotStored(msg)
        return removed

    def key_for(self, metadata: "TimeSeriesMetadata") -> TimeSeriesKey:
        """Build the public :class:`TimeSeriesKey` from a Store metadata row."""
        return _key_from_metadata(metadata)

    def _get_time_series_counts(self, context: TimeSeriesStorageContext, /) -> TimeSeriesCounts:
        """Return summary counts of stored time series.

        Unique arrays come from the store's content-addressed array groups, so anything
        ``context`` has staged is flushed first to be counted.
        """
        context.flush()
        records = self._store.list_metadata()
        type_count: dict[tuple[str, str, str | None, str | None], int] = {}
        for record in records:
            initial_timestamp = _initial_timestamp_from_metadata(record)
            resolution = record.get("resolution")
            key = (
                record["owner_type"],
                record["time_series_type"],
                initial_timestamp.isoformat() if initial_timestamp else None,
                to_iso_8601(_parse_resolution(resolution)) if resolution else None,
            )
            type_count[key] = type_count.get(key, 0) + 1
        return TimeSeriesCounts(
            time_series_count=self._store.num_distinct_arrays(),
            reference_count=len(records),
            time_series_type_count=type_count,
        )

    def _transform_single_time_series(
        self, context: TimeSeriesStorageContext, /, horizon: timedelta, interval: timedelta
    ) -> int:
        """Derive ``Deterministic`` forecasts from every stored ``SingleTimeSeries``.

        Mirrors the Rust store's store-wide transform: each ``SingleTimeSeries`` gains a forecast
        association sharing the same underlying array. Returns the number of series transformed.
        The transform runs store-wide, so anything ``context`` has staged is flushed first to
        be included.

        ``interval`` is passed to the store as given, including ``timedelta(0)``, which the store
        reads as a request for a single window spanning ``horizon``.
        """
        context.flush()
        return self._store.transform_single_time_series(horizon=horizon, interval=interval)

    # ------------------------------------------------------------------
    # Readers
    # ------------------------------------------------------------------
    def _build_reader(
        self,
        context: TimeSeriesStorageContext,
        /,
        resolution: timedelta,
        *,
        name: str | None = None,
        name_glob: str | None = None,
        owner_type: str | None = None,
        zoneless: bool | None = None,
        **features: Any,
    ) -> TimeSeriesReader:
        """Build a cross-sectional reader over the matching ``SingleTimeSeries``.

        The store builds the reader from its own catalog, so anything ``context`` has
        staged is flushed first or it would be invisible to the reader.

        ``zoneless`` narrows a cohort that spans both spellings. A reader materializes
        one timestamp axis, so the store refuses to build one over a mix of wall-clock
        series and instant-bearing ones. Pass ``True`` for the zoneless group or
        ``False`` for everything that names an instant --- which includes any series
        that left the reference unset --- and each half builds on its own.
        """
        context.flush()
        reader = self._store.build_static_reader(
            resolution,
            owner_category=OwnerCategory.Component,
            owner_type=owner_type,
            name=name,
            name_glob=name_glob,
            zoneless=zoneless,
            features=features or None,
        )
        groups: list[StaticReaderGroup] = reader.groups()
        group_metadata: list[list[TimeSeriesMetadata]] = [
            self._store.list_metadata_by_ids(group["ids"]) for group in groups
        ]
        group_component_ids = [
            tuple(record["owner_id"] for record in records) for records in group_metadata
        ]
        units = {
            record["owner_id"]: self._units_for_metadata(record)
            for records in group_metadata
            for record in records
        }
        return TimeSeriesReader(self._store, reader, group_component_ids, units)

    def _build_forecast_reader(
        self,
        context: TimeSeriesStorageContext,
        /,
        resolution: timedelta,
        *,
        time_series_type: str = "Deterministic",
        name: str | None = None,
        name_glob: str | None = None,
        owner_type: str | None = None,
        zoneless: bool | None = None,
        **features: Any,
    ) -> ForecastReader:
        """Build a cross-sectional reader over the matching forecasts.

        The store builds the reader from its own catalog, so anything ``context`` has
        staged is flushed first or it would be invisible to the reader.

        ``zoneless`` narrows a cohort that spans both spellings. A reader materializes
        one timestamp axis, so the store refuses to build one over a mix of wall-clock
        series and instant-bearing ones. Pass ``True`` for the zoneless group or
        ``False`` for everything that names an instant --- which includes any series
        that left the reference unset --- and each half builds on its own.
        """
        context.flush()
        reader = self._store.build_forecast_reader(
            _rust_time_series_type(time_series_type),
            resolution,
            owner_category=OwnerCategory.Component,
            owner_type=owner_type,
            name=name,
            name_glob=name_glob,
            zoneless=zoneless,
            features=features or None,
        )
        entries: list[int] = reader.entries()
        records: list[TimeSeriesMetadata] = self._store.list_metadata_by_ids(entries)
        component_ids = tuple(record["owner_id"] for record in records)
        slots = tuple(reader.entry_slot(index) for index in range(len(entries)))
        units = {record["owner_id"]: self._units_for_metadata(record) for record in records}
        return ForecastReader(self._store, reader, component_ids, slots, units)

    @staticmethod
    def _units_for_metadata(record: "TimeSeriesMetadata") -> QuantityMetadata | None:
        """Return Infrasys quantity metadata carried in Store application data."""
        return _deserialize_units(record.get("application_data"))

    # ------------------------------------------------------------------
    # Data operations
    # ------------------------------------------------------------------
    def _get_time_series(
        self,
        context: TimeSeriesStorageContext,
        /,
        metadata: "TimeSeriesMetadata",
        owner: Any,
        start_time: datetime | None = None,
        length: int | None = None,
    ) -> TimeSeriesData:
        context.flush()
        owner_id, category = _owner_identity(owner)
        association_id, time_range, result_initial_timestamp, read_length = self._plan_read(
            metadata, owner_id, category, start_time, length
        )
        rust_result = self._store.read_by_id(
            association_id,
            start_time=None if time_range is None else time_range[0],
            len=read_length,
            owner_id=owner_id,
            owner_category=category,
        )
        return self._build_result(metadata, rust_result, result_initial_timestamp)

    def _get_time_series_bulk(
        self,
        context: TimeSeriesStorageContext,
        /,
        records: list["TimeSeriesMetadata"],
        owner: Any,
        start_time: datetime | None = None,
        length: int | None = None,
    ) -> list[TimeSeriesData]:
        """Read matching IDs in batches, grouping sliced reads by time range."""
        if not records:
            return []
        context.flush()
        owner_id, category = _owner_identity(owner)
        plans = [
            self._plan_read(record, owner_id, category, start_time, length) for record in records
        ]
        batches: dict[tuple[datetime, datetime] | None, list[int]] = {}
        for position, (_, time_range, _, _) in enumerate(plans):
            batches.setdefault(time_range, []).append(position)

        results: dict[int, TimeSeriesData] = {}
        for time_range, positions in batches.items():
            ids = [plans[position][0] for position in positions]
            if time_range is None:
                rust_results = self._store.read_by_ids(ids)
            else:
                rust_results = self._store.read_by_ids_range(ids, time_range)
            for position, rust_result in zip(positions, rust_results, strict=True):
                results[position] = self._build_result(
                    records[position], rust_result, plans[position][2]
                )
        return [results[position] for position in range(len(records))]

    def _plan_read(
        self,
        metadata: "TimeSeriesMetadata",
        owner_id: int,
        category: OwnerCategory,
        start_time: datetime | None,
        length: int | None,
    ) -> tuple[int, tuple[datetime, datetime] | None, datetime | None, int | None]:
        """Return the association ID, optional range, result timestamp, and read length."""
        if metadata["owner_id"] != owner_id or metadata["owner_category"] != _category_name(
            category
        ):
            msg = (
                f"No time series {metadata['time_series_type']}.{metadata['name']} is stored "
                f"for owner id {owner_id}"
            )
            raise ISNotStored(msg)

        association_id = metadata["id"]
        time_series_type = metadata["time_series_type"]
        if time_series_type in _FORECAST_TYPES and (start_time is not None or length is not None):
            msg = "start_time/length slicing is not supported for forecast time series"
            raise NotImplementedError(msg)
        if time_series_type != "SingleTimeSeries":
            return association_id, None, None, None

        initial_timestamp = _initial_timestamp_from_metadata(metadata)
        resolution_value = metadata.get("resolution")
        if initial_timestamp is None or resolution_value is None:
            msg = f"Incomplete SingleTimeSeries metadata for {metadata['name']}"
            raise ISNotStored(msg)
        resolution = _parse_resolution(resolution_value)
        if start_time is None and length is None:
            return association_id, None, initial_timestamp, None

        series_length = metadata["length"]
        if series_length is None:
            msg = f"Incomplete SingleTimeSeries metadata for {metadata['name']}"
            raise ISNotStored(msg)
        index, read_length = single_time_series_range(
            initial_timestamp,
            resolution,
            series_length,
            start_time,
            length,
        )
        # `advance` rather than `+`: Python's aware arithmetic is wall-clock arithmetic.
        result_initial_timestamp = advance(initial_timestamp, index * resolution)
        time_range = (
            result_initial_timestamp,
            advance(result_initial_timestamp, read_length * resolution),
        )
        return association_id, time_range, result_initial_timestamp, read_length

    def _build_result(
        self,
        metadata: "TimeSeriesMetadata",
        rust_result: Any,
        result_initial_timestamp: datetime | None,
    ) -> TimeSeriesData:
        """Convert infrastore data to the infrasys Pydantic model."""
        data = np.asarray(rust_result.data)
        units = _deserialize_units(rust_result.application_data)
        if units is not None:
            data = units.quantity_type(data, units.units)

        time_series_type = metadata["time_series_type"]
        if time_series_type == "SingleTimeSeries":
            if result_initial_timestamp is None:
                msg = f"Missing initial timestamp for {metadata['name']}"
                raise ISNotStored(msg)
            return SingleTimeSeries(
                name=rust_result.name,
                resolution=_parse_resolution(rust_result.resolution),
                initial_timestamp=result_initial_timestamp,
                data=data,
            )
        if time_series_type == "NonSequentialTimeSeries":
            return NonSequentialTimeSeries(
                name=rust_result.name,
                data=data,
                timestamps=np.asarray(rust_result.timestamps, dtype=object),
            )
        if time_series_type in _FORECAST_TYPES:
            # Infrasys stores (window_count, horizon_steps); infrastore reads (horizon_steps, count).
            return Deterministic(
                name=rust_result.name,
                data=data.T,
                initial_timestamp=rust_result.initial_timestamp,
                resolution=_parse_resolution(rust_result.resolution),
                horizon=_parse_resolution(rust_result.horizon),
                interval=_parse_resolution(rust_result.interval),
                window_count=rust_result.count,
            )

        msg = f"get_time_series not implemented for {time_series_type}"
        raise NotImplementedError(msg)

    def _serialize(
        self,
        context: TimeSeriesStorageContext,
        /,
        data: dict[str, Any],
        dst: Path | str,
        src: Path | str | None = None,
    ) -> None:
        """Write the store to ``dst``.

        Anything ``context`` has buffered is flushed first so it is included in the save.

        Raises
        ------
        ISOperationNotAllowed
            Raised if a time series transaction is open. The saved artifact would then
            contain rows a rollback can still take back, and a durable copy of state that
            may still be reverted is not a coherent thing to produce. The store rejects
            this too; checking here turns it into a message that names the fix.
        """
        if self._store.in_transaction:
            msg = (
                "Cannot serialize while a time series transaction is open. Move the call "
                "outside the time_series_transaction block so the copy reflects committed "
                "state."
            )
            raise ISOperationNotAllowed(msg)
        context.flush()
        self._store.flush()
        source = self._directory if src is None else Path(src)
        destination = Path(dst)
        destination.mkdir(parents=True, exist_ok=True)
        if source.resolve() == self._directory.resolve():
            # The live store writes itself out. `persist_to` stages both halves,
            # fsyncs them, and renames them into place under one generation stamp,
            # so a save interrupted between the two renames is caught on the next
            # open instead of read as a valid store. It also releases and reopens
            # the HDF5 handle internally, which is what this branch used to need a
            # close/copy/reopen dance for on Windows.
            #
            # Note the destination is replaced, so a failed save may have destroyed
            # what was there. Recovery is to call this again — the scratch store is
            # still live and unchanged.
            self._store.persist_to(str(destination / self.STORAGE_FILE))
        elif source.resolve() != destination.resolve():
            # Serialize an external store through infrastore instead of copying its files.
            source_store = Store.open(
                str(source / self.STORAGE_FILE),
                read_only=True,
                catalog="attached",
            )
            try:
                source_store.persist_to(str(destination / self.STORAGE_FILE))
            finally:
                source_store.close()
        self.add_serialized_data(data)

    @staticmethod
    def add_serialized_data(data: dict[str, Any]) -> None:
        data["time_series_storage_type"] = TimeSeriesStorageType.TIME_SERIES_STORE.value

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------


def _key_from_metadata(record: "TimeSeriesMetadata") -> TimeSeriesKey:
    time_series_type = record["time_series_type"]
    features = dict(record.get("features") or {})
    name = record["name"]
    if time_series_type == "SingleTimeSeries":
        initial_timestamp = _initial_timestamp_from_metadata(record)
        resolution = record.get("resolution")
        length = record["length"]
        if initial_timestamp is None or resolution is None or length is None:
            msg = f"Incomplete SingleTimeSeries metadata for {name}"
            raise ISNotStored(msg)
        return SingleTimeSeriesKey(
            name=name,
            time_series_type=SingleTimeSeries,
            features=features,
            length=length,
            initial_timestamp=initial_timestamp,
            resolution=_parse_resolution(resolution),
        )
    if time_series_type == "NonSequentialTimeSeries":
        length = record["length"]
        if length is None:
            msg = f"Incomplete NonSequentialTimeSeries metadata for {name}"
            raise ISNotStored(msg)
        return NonSequentialTimeSeriesKey(
            name=name,
            time_series_type=NonSequentialTimeSeries,
            features=features,
            length=length,
        )
    if time_series_type in _FORECAST_TYPES:
        initial_timestamp = _initial_timestamp_from_metadata(record)
        resolution = record.get("resolution")
        interval = record.get("interval")
        horizon = record.get("horizon")
        window_count = record.get("count")
        if (
            initial_timestamp is None
            or resolution is None
            or interval is None
            or horizon is None
            or window_count is None
        ):
            msg = f"Incomplete forecast metadata for {name}"
            raise ISNotStored(msg)
        return DeterministicTimeSeriesKey(
            name=name,
            time_series_type=Deterministic,
            features=features,
            initial_timestamp=initial_timestamp,
            resolution=_parse_resolution(resolution),
            interval=_parse_resolution(interval),
            horizon=_parse_resolution(horizon),
            window_count=window_count,
        )
    msg = f"key not implemented for {time_series_type}"
    raise NotImplementedError(msg)


def _initial_timestamp_from_metadata(record: "TimeSeriesMetadata") -> datetime | None:
    timestamp = record.get("initial_timestamp")
    if timestamp is None:
        return None
    return from_catalog_timestamp(timestamp, record.get("time_reference"))


def _estimate_nbytes(time_series: TimeSeriesData) -> int:
    """Estimate the array bytes a staged series keeps buffered.

    Drives the context's byte-based auto-flush, so it only needs to track the dominant
    cost — the array data — not exact process overhead.
    """
    for attr in ("data", "data_array"):
        data = getattr(time_series, attr, None)
        if data is None:
            continue
        array = getattr(data, "magnitude", data)
        nbytes = getattr(array, "nbytes", None)
        if nbytes is not None:
            return int(nbytes)
    return 8 * getattr(time_series, "length", 0)


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
    if not isinstance(time_series, (SingleTimeSeries, NonSequentialTimeSeries, Deterministic)):
        msg = f"add_time_series not implemented for {type(time_series)}"
        raise NotImplementedError(msg)
    quantity_metadata = _units_from_data(time_series)
    application_data = _serialize_units(quantity_metadata)
    units = None if quantity_metadata is None else quantity_metadata.units
    if isinstance(time_series, SingleTimeSeries):
        return RustSingleTimeSeries(
            time_series.initial_timestamp,
            time_series.resolution,
            np.asarray(time_series.data_array, dtype=np.float64),
            time_series.name,
            application_data=application_data,
            units=units,
        )
    if isinstance(time_series, NonSequentialTimeSeries):
        return RustNonSequentialTimeSeries(
            _timestamps_as_datetimes(time_series.timestamps),
            np.asarray(time_series.data_array, dtype=np.float64),
            time_series.name,
            application_data=application_data,
            units=units,
        )
    if isinstance(time_series, Deterministic):
        # infrasys stores forecasts as (window_count, horizon_steps); infrastore expects
        # the transpose (horizon_steps, count).
        data = np.ascontiguousarray(np.asarray(time_series.data_array, dtype=np.float64).T)
        return RustDeterministic(
            time_series.initial_timestamp,
            time_series.resolution,
            time_series.horizon,
            time_series.interval,
            time_series.window_count,
            data,
            time_series.name,
            application_data=application_data,
            units=units,
        )
    msg = f"add_time_series not implemented for {type(time_series)}"
    raise NotImplementedError(msg)


def _timestamps_as_datetimes(timestamps: np.ndarray) -> list[datetime]:
    """Return a ``NonSequentialTimeSeries`` timestamp array as Python datetimes.

    An object array already holds ``datetime`` objects and keeps whatever ``tzinfo`` the
    caller wrote, so it is handed over as it stands. A ``datetime64`` array cannot carry
    a zone at all, so it converts to naive datetimes --- wall clocks, which is exactly
    what the store records as zoneless.
    """
    if timestamps.dtype == object:
        return list(timestamps)
    return timestamps.astype("datetime64[us]").tolist()


def _units_from_data(
    time_series: SingleTimeSeries | NonSequentialTimeSeries | Deterministic,
) -> QuantityMetadata | None:
    data = time_series.data
    if isinstance(data, pint.Quantity):
        return QuantityMetadata(
            module=type(data).__module__,
            quantity_type=type(data),
            units=str(data.units),
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


def _store_type_filter(name: str | None) -> RustTimeSeriesType | None:
    """Return the infrastore filter for an infrasys time-series type name."""
    if name is None:
        return None
    return getattr(RustTimeSeriesType, name, None)


def _has_exact_time_series(
    store: Store,
    owner_id: int,
    category: OwnerCategory,
    time_series_type: str,
    name: str,
    features: dict[str, Any],
) -> bool:
    """Check for an exact duplicate, not another member of a type family."""
    rust_type = _store_type_filter(time_series_type)
    if rust_type is None:
        return False
    # Exact feature matching includes the empty set. Deterministic filtering is a family
    # query, so only an explicit row is an exact duplicate; cross-type conflicts remain
    # infrastore's responsibility.
    return any(
        record["time_series_type"] == time_series_type
        for record in store.list_metadata(
            owner_id=owner_id,
            owner_category=category,
            time_series_type=rust_type,
            name=name,
            features=features,
            features_exact=True,
        )
    )


def _rust_time_series_type(name: str) -> RustTimeSeriesType:
    """Return the store's time-series-type enum member for an infrasys type name."""
    rust_type = _store_type_filter(name)
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


def _matches_assoc_key(
    assoc_key: AssocKey,
    name: str | None,
    time_series_type: str | None,
    features: dict[str, Any],
) -> bool:
    assoc_name, assoc_type, feature_pairs = assoc_key
    if name is not None and assoc_name != name:
        return False
    if time_series_type is not None and assoc_type != time_series_type:
        return False
    staged_features = dict(feature_pairs)
    return all(staged_features.get(key) == value for key, value in features.items())


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
