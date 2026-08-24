"""Time series storage backed by the infrastore Rust extension.

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

Writes go through the store's bulk API, and every operation belongs to a
:class:`TimeSeriesStorageContext` that owns its batch. Callers reach these operations
through the context, not through this class: the entry points here are private and take
their context positionally, so a caller's ``**features`` may contain a key named
``context`` without colliding with the plumbing. A single add call commits all of its
owners together, and a caller who opens a context can stage many calls so the store pays
one catalog transaction for the block instead of one per series.

This class holds no batch state and no reference to any context. The index it keeps
describes *committed* associations only; staged additions live on the context until
that context flushes them, so a batch is visible to itself and to nothing else.

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
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import mkdtemp
from typing import Any

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
from infrasys.time_series_context import (
    AUTO_FLUSH_BYTES,
    AUTO_FLUSH_THRESHOLD,
    AssocKey,
    OwnerKey,
    TimeSeriesStorageContext,
    _PendingAdd,
    _StoredSeries,
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
        # Committed associations only. (owner_id, owner_category_name) -> {assoc_key -> ...}
        # Staged additions live on the context that staged them, never here.
        self._index: dict[OwnerKey, dict[AssocKey, _StoredSeries]] = {}

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
        """Write a context's buffered additions to the store in one bulk call.

        Called by :meth:`TimeSeriesStorageContext.flush`. The index is updated only after
        the store accepts the batch, so a rejected batch leaves no trace here. Inside a
        transactional context this write is still undoable — the store rolls it back with
        the rest of the transaction, and ``discard`` rebuilds the index to match.
        """
        if not pending:
            return
        keys = self._store.add_time_series_bulk([entry.item for entry in pending])
        # The store returns the new keys in input order; caching them here spares every
        # later read a scan of the owner's keys.
        for entry, key in zip(pending, keys):
            entry.stored.store_key = key
            self._index.setdefault(entry.owner_key, {})[entry.assoc_key] = entry.stored

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
        else:
            directory = Path(mkdtemp(dir=dst_time_series_directory))
            logger.debug("Creating tmp folder at {}", directory)
            atexit.register(clean_tmp_folder, directory)
            cls._copy_store(time_series_dir, directory)

        store = Store.open(
            path=directory / cls.STORAGE_FILE,
            read_only=read_only,
            # A read-only open leaves the catalog attached: nothing mutates it, so
            # there is nothing to gain by reading it into RAM. A writable open is a
            # scratch copy, so it gets the same in-memory catalog a fresh store
            # does — see `_create`. The copied `.sqlite` seeds it and is ignored
            # from then on; `persist_to` writes the catalog back out at save.
            catalog="attached" if read_only else "memory",
        )
        storage = cls(directory, store)
        storage.rehydrate()
        return storage, None

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
        units = _units_from_data(time_series)
        units_str = _serialize_units(units)
        ts_type = _data_type_name(time_series)

        # Validate every owner before staging any of them so that a duplicate on the last
        # owner does not leave the earlier ones half-added.
        staged: list[_PendingAdd] = []
        seen: set[tuple[OwnerKey, AssocKey]] = set()
        # All owners share one array, so its size is charged to the first entry only.
        nbytes = _estimate_nbytes(time_series)
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
            if (owner_key, assoc_key) in seen or assoc_key in self._visible_assocs(
                owner_key, context
            ):
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
                _PendingAdd(
                    item=item,
                    stored=stored,
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
    ) -> _StoredSeries:
        """Return the single stored-series descriptor matching the inputs.

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
    ) -> list[_StoredSeries]:
        """Return all stored-series descriptors matching the inputs across the owners.

        Resolves against ``context``'s staged additions as well as the committed index, so
        a caller sees its own uncommitted work and no one else's.
        """
        if not owners:
            msg = "At least one owner must be passed."
            raise ISOperationNotAllowed(msg)
        results: list[_StoredSeries] = []
        for owner in owners:
            owner_id, category = _owner_identity(owner)
            for stored in self._visible_assocs(
                (owner_id, _category_name(category)), context
            ).values():
                if _matches(stored, name, time_series_type, features):
                    results.append(stored)
        return results

    def _visible_assocs(
        self, owner_key: OwnerKey, context: TimeSeriesStorageContext
    ) -> dict[AssocKey, _StoredSeries]:
        """Return one owner's associations: committed, overlaid with ``context``'s staged.

        The single place that resolves the two sources against each other. The result may
        be the live committed map, so treat it as read-only.
        """
        committed = self._index.get(owner_key, {})
        staged = context.staged_for(owner_key)
        if not staged:
            return committed
        return {**committed, **staged}

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

        Committed rows are answered by one of the store's existence probes — an index
        ``SELECT 1 ... LIMIT 1`` that hydrates nothing, features filter included — so this
        is safe in hot per-component loops. Staged additions are visible only to their own
        context and absent from the store until flush, so they are checked in memory first.
        """
        owner_id, category = _owner_identity(owner)
        staged = context.staged_for((owner_id, _category_name(category)))
        if staged and any(
            _matches(stored, name, time_series_type, features) for stored in staged.values()
        ):
            return True

        def probe(rust_type: Any) -> bool:
            return self._store.has_any_time_series(
                owner_id=owner_id,
                owner_category=category,
                time_series_type=rust_type,
                name=name,
                features=features or None,
            )

        if time_series_type is None:
            return probe(None)
        if time_series_type == "Deterministic":
            # infrasys surfaces both stored forecast tags as ``Deterministic`` (see
            # _type_matches). The store filters on one exact tag at a time, so probe each.
            return any(probe(getattr(RustTimeSeriesType, ts_type)) for ts_type in _FORECAST_TYPES)
        rust_type = getattr(RustTimeSeriesType, time_series_type, None)
        # A name the store does not know cannot have been stored.
        return rust_type is not None and probe(rust_type)

    def _remove(
        self,
        context: TimeSeriesStorageContext,
        /,
        *owners: Any,
        name: str | None = None,
        time_series_type: str | None = None,
        **features: Any,
    ) -> list[_StoredSeries]:
        """Remove all associations matching the inputs and return their descriptors.

        Staged additions on ``context`` are flushed first, so a series added and removed
        inside one block is removed rather than silently committed by a later flush.

        Raises
        ------
        ISNotStored
            Raised if nothing matches.
        """
        context.flush()
        # Resolve every matching association before touching anything, then remove them
        # from the store in one bulk call. The index is updated only after the store
        # accepts the batch, so a failure leaves the two consistent.
        doomed: list[tuple[OwnerKey, AssocKey, _StoredSeries]] = []
        rust_keys = []
        seen: set[tuple[OwnerKey, AssocKey]] = set()
        for owner in owners:
            owner_id, category = _owner_identity(owner)
            owner_key = (owner_id, _category_name(category))
            for assoc_key, stored in self._index.get(owner_key, {}).items():
                if (owner_key, assoc_key) in seen or not _matches(
                    stored, name, time_series_type, features
                ):
                    continue
                seen.add((owner_key, assoc_key))
                doomed.append((owner_key, assoc_key, stored))
                rust_keys.append(self._resolve_committed_key(owner_id, category, stored))
        if not doomed:
            msg = "No metadata matching the inputs is stored"
            raise ISNotStored(msg)
        self._store.remove_time_series_bulk(rust_keys)
        for owner_key, assoc_key, _ in doomed:
            self._index[owner_key].pop(assoc_key)
        return [stored for _, _, stored in doomed]

    def key_for(self, stored: _StoredSeries) -> TimeSeriesKey:
        """Build the public :class:`TimeSeriesKey` for a stored-series descriptor."""
        return _key_from_stored(stored)

    def _get_time_series_counts(self, context: TimeSeriesStorageContext, /) -> TimeSeriesCounts:
        """Return summary counts of stored time series.

        Unique arrays come from the store's content-addressed array groups, so anything
        ``context`` has staged is flushed first to be counted.
        """
        context.flush()
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
        descriptors, so later reads never have to scan for them. The rebuilt index
        reflects the store alone, so callers must flush any staged additions first or
        those additions are dropped.
        """
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
        count = self._store.transform_single_time_series(horizon=horizon, interval=interval)
        self.rehydrate()
        return count

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
        group_component_ids = [
            tuple(key.owner_id for key in group["keys"]) for group in reader.groups()
        ]
        units = {
            key.owner_id: self._units_for_key(key)
            for group in reader.groups()
            for key in group["keys"]
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
    def _get_time_series(
        self,
        context: TimeSeriesStorageContext,
        /,
        stored: _StoredSeries,
        owner: Any,
        start_time: datetime | None = None,
        length: int | None = None,
    ) -> TimeSeriesData:
        # The array has to be in the store before it can be read back.
        context.flush()
        owner_id, category = _owner_identity(owner)
        key, time_range, result_initial_timestamp = self._plan_read(
            stored, owner_id, category, start_time, length
        )
        rust_result = self._store.get_time_series(key, time_range=time_range)
        return self._build_result(stored, rust_result, result_initial_timestamp)

    def _get_time_series_bulk(
        self,
        context: TimeSeriesStorageContext,
        /,
        records: list[_StoredSeries],
        owner: Any,
        start_time: datetime | None = None,
        length: int | None = None,
    ) -> list[TimeSeriesData]:
        """Return the arrays for several stored series belonging to one owner.

        Reads that share a time range are fetched in a single store call, which lets the
        store decompress each dataset once instead of once per series.
        """
        if not records:
            return []
        # The arrays have to be in the store before they can be read back.
        context.flush()
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
        """Return the store key, time range, and resulting start time for one read.

        Both read paths flush their context before planning, so the association is
        committed by the time this runs.
        """
        key = self._resolve_committed_key(owner_id, category, stored)
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
        # `advance` rather than `+`: the bound has to land on the instant grid the store
        # slices, and Python's aware addition is wall-clock arithmetic. The bounds keep
        # the series' own spelling, which is what the store requires of them.
        result_initial_timestamp = advance(stored.initial_timestamp, index * stored.resolution)
        time_range = (
            result_initial_timestamp,
            advance(result_initial_timestamp, result_length * stored.resolution),
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
                timestamps=np.asarray(rust_result.timestamps, dtype=object),
            )
        if stored.time_series_type in _FORECAST_TYPES:
            # The Rust store returns (horizon_steps, count); infrasys uses (window_count,
            # horizon_steps), so transpose back.
            return Deterministic(
                name=stored.name,
                data=data.T,
                initial_timestamp=rust_result.initial_timestamp,
                resolution=_parse_resolution(rust_result.resolution),
                horizon=_parse_resolution(rust_result.horizon),
                interval=_parse_resolution(rust_result.interval),
                window_count=rust_result.count,
            )

        msg = f"get_time_series not implemented for {stored.time_series_type}"
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
            self._store.persist_to(destination / self.STORAGE_FILE)
        else:
            # Serializing from a directory this storage does not own; the live
            # store cannot write those bytes, so a plain file copy is all there is.
            self._copy_store(source, destination)
        self.add_serialized_data(data)

    @staticmethod
    def add_serialized_data(data: dict[str, Any]) -> None:
        data["time_series_storage_type"] = TimeSeriesStorageType.TIME_SERIES_STORE.value

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _resolve_committed_key(
        self, owner_id: int, category: OwnerCategory, stored: _StoredSeries
    ):
        """Return the store key for an association that has already been written.

        Callers must have flushed any staged additions first; a series that is still
        staged has no key yet and will not be found here.
        """
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
            # The catalog renders the instant and records the spelling beside it; both
            # are needed to hand back the datetime the caller originally wrote.
            initial_timestamp = from_catalog_timestamp(
                record["initial_timestamp"], record.get("time_reference")
            )
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
    if isinstance(time_series, SingleTimeSeries):
        return RustSingleTimeSeries(
            time_series.initial_timestamp,
            time_series.resolution,
            np.asarray(time_series.data_array, dtype=np.float64),
            time_series.name,
        )
    if isinstance(time_series, NonSequentialTimeSeries):
        return RustNonSequentialTimeSeries(
            _timestamps_as_datetimes(time_series.timestamps),
            np.asarray(time_series.data_array, dtype=np.float64),
            time_series.name,
        )
    if isinstance(time_series, Deterministic):
        # infrasys stores forecasts as (window_count, horizon_steps); the Rust store expects
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


def _category_from_name(name: str) -> OwnerCategory:
    """Inverse of :func:`_category_name`."""
    match name:
        case "Component":
            return OwnerCategory.Component
        case "SupplementalAttribute":
            return OwnerCategory.SupplementalAttribute
        case _:
            msg = f"Unhandled category name: {name}"
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
