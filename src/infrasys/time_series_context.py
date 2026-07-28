"""The transaction object for time series operations.

A :class:`TimeSeriesStorageContext` owns one batch of work. What it owns is now only
the part the store cannot do for itself: the **client-side add buffer**, which exists
so that many additions reach the store as one bulk call. That is what buys block-sized
NetCDF writes and feature-set dedup, and a store transaction deliberately does not
provide it.

Atomicity is the store's job. A context opened by ``time_series_transaction`` begins an
``infrastore`` transaction and commits or rolls it back on exit, so undoing a failed
block is one call rather than a compensating-removal log. Two things follow that used
to need machinery here:

* **A mid-block flush is harmless.** Flushed work is still inside the transaction, so
  it rolls back with everything else. Reads, counts, and reader builds inside a block
  no longer cost anything in recoverability — which is what makes the buffer safe to
  drain whenever an operation needs the arrays present.
* **Nothing has to be undone by hand.** The record of what a block already wrote, and
  the compensating removals that replayed it backwards, are gone; rollback is one call
  to the store.

The staged overlay stays, and only for what the store genuinely cannot answer: an
addition still sitting in this buffer has not reached the store, so a duplicate against
it would otherwise go undetected until the flush. Once flushed, the store is
authoritative and the overlay is empty.

Removals roll back too, which they cannot outside a transaction: the store defers
freeing an array until the outermost commit, so the bytes are still there if the
catalog rewinds.

A context created implicitly for a single operation (the default when a caller opens no
block) does **not** begin a transaction. One operation is already atomic on its own, and
taking a write lock for it would be both wasted work and wrong on a read-only store.

The context is also the *receiver* for the operations themselves: every call goes to
``context.add_time_series(...)`` rather than to a storage method that takes the context
as an argument. That is what keeps the batching plumbing out of the ``**features``
namespace a caller owns --- a time series feature named ``context`` is just a feature ---
and it makes handing a context to the wrong storage unrepresentable rather than merely
checked.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from infrasys.exceptions import ISOperationNotAllowed
from infrasys.time_series_models import QuantityMetadata, TimeSeriesData

if TYPE_CHECKING:
    from infrasys.time_series_reader import ForecastReader, TimeSeriesReader
    from infrasys.time_series_store_storage import TimeSeriesCounts, TimeSeriesStoreStorage

# (owner_id, owner_category_name)
OwnerKey = tuple[int, str]
# (name, time_series_type, sorted feature pairs)
AssocKey = tuple


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
    """One buffered addition: the store's bulk item plus where it lives in the index."""

    item: dict[str, Any]
    stored: _StoredSeries
    owner_key: OwnerKey
    assoc_key: AssocKey
    # Estimated bytes of array data this entry keeps buffered. A multi-owner add shares
    # one array across its entries, so the size is carried by the first entry only.
    nbytes: int = 0


# A context flushes on its own when its buffer reaches either limit below, whichever
# comes first. Inside a transaction an early flush costs nothing in recoverability, so
# these only split the I/O, never the atomicity.
#
# The count limit keeps the store's layout healthy: each flush becomes one NetCDF
# dataset whose chunk width equals the batch width, so 10,000 f64 series produce 80 KiB
# chunks — near the store's 1 MiB chunk cap and within ~2% of unlimited-batch write
# throughput. The byte limit is what actually bounds memory, which the count cannot do
# when individual arrays are long: buffered arrays live until the flush, so the buffer
# holds at most ~AUTO_FLUSH_BYTES of array data no matter how large each series is.
AUTO_FLUSH_THRESHOLD = 10_000
AUTO_FLUSH_BYTES = 256 * 1024 * 1024


class TimeSeriesStorageContext:
    """Owns one batch of time series work against a storage backend.

    Additions are buffered until :meth:`flush` writes them to the store in a single bulk
    call; a batch that grows past ``auto_flush_threshold`` flushes on its own so an
    arbitrarily large block holds a bounded amount of data in memory. A transactional
    context (one from ``time_series_transaction``) wraps everything it does in a store
    transaction, so :meth:`discard` undoes the whole block — flushed work and removals
    included.

    Every time series operation is a method on the context (see `Operations` below), so
    nothing has to pass a context around to reach the store.

    This is internal plumbing: users see only the
    :class:`~infrasys.time_series_transaction.TimeSeriesTransaction` facade, which routes
    every call it receives through its context.
    """

    def __init__(
        self,
        storage: "TimeSeriesStoreStorage",
        transactional: bool = False,
        auto_flush_threshold: int = AUTO_FLUSH_THRESHOLD,
        auto_flush_bytes: int = AUTO_FLUSH_BYTES,
    ) -> None:
        if auto_flush_threshold < 1:
            msg = f"auto_flush_threshold must be positive: {auto_flush_threshold}"
            raise ValueError(msg)
        if auto_flush_bytes < 1:
            msg = f"auto_flush_bytes must be positive: {auto_flush_bytes}"
            raise ValueError(msg)
        self._storage = storage
        # Buffered additions, not yet handed to the store. Batching only; atomicity is
        # the transaction's job.
        self._pending: list[_PendingAdd] = []
        # An index over `_pending` so a duplicate can be caught before the flush that
        # would let the store catch it. Cleared whenever the buffer drains.
        self._staged: dict[OwnerKey, dict[AssocKey, _StoredSeries]] = {}
        self._transactional = transactional
        self._auto_flush_threshold = auto_flush_threshold
        self._auto_flush_bytes = auto_flush_bytes
        self._staged_bytes = 0
        self._closed = False

    @property
    def storage(self) -> "TimeSeriesStoreStorage":
        """Return the storage this context writes through."""
        return self._storage

    @property
    def has_staged_data(self) -> bool:
        """Return True if additions are buffered but not yet written."""
        return bool(self._pending)

    def begin(self) -> None:
        """Open the store transaction backing this context.

        Called by ``time_series_transaction``. Contexts created for a single operation
        skip this: that operation is already atomic, and beginning a transaction would
        take a write lock needlessly — and fail outright on a read-only store.
        """
        self.check_open()
        self._transactional = True
        self._storage.store.begin_transaction()

    def check_open(self) -> None:
        """Raise if this context has already been committed or discarded.

        Raises
        ------
        ISOperationNotAllowed
            Raised if the context is closed.
        """
        if self._closed:
            msg = (
                "This time series context is closed. Contexts are valid only inside the "
                "time_series_transaction block that created them; open a new one."
            )
            raise ISOperationNotAllowed(msg)

    def check_owns(self, storage: "TimeSeriesStoreStorage") -> None:
        """Raise if this context belongs to a different storage backend.

        Operations run through the context and reach its own storage by construction, so
        this guards only the one place a context is paired with something else:
        :meth:`~infrasys.time_series_manager.TimeSeriesManager.bind_context`.

        Raises
        ------
        ISOperationNotAllowed
            Raised if the context was opened against another system's storage.
        """
        self.check_open()
        if self._storage is not storage:
            msg = "This time series context belongs to a different system's storage."
            raise ISOperationNotAllowed(msg)

    def stage(self, entries: list[_PendingAdd]) -> None:
        """Buffer additions without writing them to the store.

        A buffer that reaches ``auto_flush_threshold`` entries or ``auto_flush_bytes``
        of array data is written out immediately, after the whole ``entries`` group is
        staged — the group came from one ``add`` call and was validated together, so it
        lands in one piece.
        """
        self.check_open()
        self._pending.extend(entries)
        for entry in entries:
            self._staged.setdefault(entry.owner_key, {})[entry.assoc_key] = entry.stored
            self._staged_bytes += entry.nbytes
        if (
            len(self._pending) >= self._auto_flush_threshold
            or self._staged_bytes >= self._auto_flush_bytes
        ):
            self.flush()

    def staged_for(self, owner_key: OwnerKey) -> dict[AssocKey, _StoredSeries]:
        """Return this context's buffered associations for one owner.

        The context knows only what it has buffered. Resolving that against what the
        store already holds is the storage's job, since the storage owns that index.
        """
        return self._staged.get(owner_key, {})

    def flush(self) -> None:
        """Write buffered additions to the store in one bulk call.

        A no-op when nothing is buffered. Any operation that needs the arrays physically
        present — a read, a reader build, a removal, serialization — flushes first. Inside
        a transactional context that costs nothing in recoverability: the write lands in
        the open transaction and rolls back with it.
        """
        self.check_open()
        if not self._pending:
            return
        pending = self._pending
        # Reset before writing so a failed write cannot leave the entries buffered a
        # second time, and so the context stays usable for further additions. The
        # overlay goes with it: once written, the store's index is authoritative.
        self._pending = []
        self._staged = {}
        self._staged_bytes = 0
        self._storage.write_pending(pending)

    def commit(self) -> None:
        """Flush buffered additions, commit the transaction, and close the context."""
        try:
            self.flush()
            if self._transactional:
                self._storage.store.commit_transaction()
        finally:
            self._closed = True

    def discard(self) -> None:
        """Abandon this batch, undoing everything it did.

        Buffered additions never reached the store, so they are dropped outright.
        Everything the block did write — including removals, which are reversible only
        inside a transaction — is undone by rolling the store transaction back.

        The in-memory index is rebuilt from the store afterwards, because entries added
        or dropped as work was flushed describe a catalog state that no longer exists.

        A failure in the rollback itself is logged rather than raised: this runs while an
        exception is already propagating, and the error that caused the unwind is the one
        the caller needs to see.
        """
        self._pending = []
        self._staged = {}
        self._staged_bytes = 0
        self._closed = True
        if not self._transactional:
            return
        try:
            self._storage.store.rollback_transaction()
        except Exception as e:  # noqa: BLE001 - must not mask the original exception
            logger.error(
                "Rolling back the time series transaction failed; the store may retain "
                "partial work from this block: {}",
                e,
            )
            return
        self._storage.rehydrate()

    # ------------------------------------------------------------------
    # Operations
    # ------------------------------------------------------------------
    # Each entry point checks that the batch is still open and then hands itself to the
    # storage, which owns the index and the store. The storage side is private: the
    # context is the only supported way in, which is what keeps `context` out of the
    # caller's `**features`.

    def add_time_series(
        self,
        time_series: TimeSeriesData,
        *owners: Any,
        **features: Any,
    ) -> None:
        """Stage a time series for one or more owners on this batch.

        All owners are staged together, so nothing is stored if any of them already holds
        a matching association. The additions reach the store when the batch flushes.

        Raises
        ------
        ISAlreadyAttached
            Raised if a matching association already exists for one of the owners, either
            committed or already staged here.
        """
        self.check_open()
        self._storage._add_time_series(self, time_series, *owners, **features)

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
        self.check_open()
        return self._storage._get_metadata(
            self, owner, name=name, time_series_type=time_series_type, **features
        )

    def list_metadata(
        self,
        *owners: Any,
        name: str | None = None,
        time_series_type: str | None = None,
        **features: Any,
    ) -> list[_StoredSeries]:
        """Return all stored-series descriptors matching the inputs across the owners.

        Resolves against this batch's staged additions as well as the committed index, so
        a caller sees its own uncommitted work and no one else's.
        """
        self.check_open()
        return self._storage._list_metadata(
            self, *owners, name=name, time_series_type=time_series_type, **features
        )

    def has_metadata(
        self,
        owner: Any,
        name: str | None = None,
        time_series_type: str | None = None,
        **features: Any,
    ) -> bool:
        """Return True if any stored series matches the inputs."""
        self.check_open()
        return self._storage._has_metadata(
            self, owner, name=name, time_series_type=time_series_type, **features
        )

    def remove(
        self,
        *owners: Any,
        name: str | None = None,
        time_series_type: str | None = None,
        **features: Any,
    ) -> list[_StoredSeries]:
        """Remove all associations matching the inputs and return their descriptors.

        Staged additions are flushed first, so a series added and removed inside one
        block is removed rather than silently committed by a later flush.

        Raises
        ------
        ISNotStored
            Raised if nothing matches.
        """
        self.check_open()
        return self._storage._remove(
            self, *owners, name=name, time_series_type=time_series_type, **features
        )

    def get_time_series(
        self,
        stored: _StoredSeries,
        owner: Any,
        start_time: datetime | None = None,
        length: int | None = None,
    ) -> TimeSeriesData:
        """Return the array for one stored series, flushing this batch first."""
        self.check_open()
        return self._storage._get_time_series(
            self, stored, owner, start_time=start_time, length=length
        )

    def get_time_series_bulk(
        self,
        records: list[_StoredSeries],
        owner: Any,
        start_time: datetime | None = None,
        length: int | None = None,
    ) -> list[TimeSeriesData]:
        """Return the arrays for several stored series belonging to one owner."""
        self.check_open()
        return self._storage._get_time_series_bulk(
            self, records, owner, start_time=start_time, length=length
        )

    def get_time_series_counts(self) -> "TimeSeriesCounts":
        """Return summary counts of stored time series, flushing this batch first."""
        self.check_open()
        return self._storage._get_time_series_counts(self)

    def transform_single_time_series(self, horizon: timedelta, interval: timedelta) -> int:
        """Derive ``Deterministic`` forecasts from every stored ``SingleTimeSeries``.

        The transform runs store-wide, so anything staged here is flushed first to be
        included. Returns the number of series transformed.
        """
        self.check_open()
        return self._storage._transform_single_time_series(self, horizon, interval)

    def build_reader(
        self,
        resolution: timedelta,
        *,
        name: str | None = None,
        name_glob: str | None = None,
        owner_type: str | None = None,
        **features: Any,
    ) -> "TimeSeriesReader":
        """Build a cross-sectional reader over the matching ``SingleTimeSeries``.

        The store builds the reader from its own catalog, so anything staged here is
        flushed first or it would be invisible to the reader.
        """
        self.check_open()
        return self._storage._build_reader(
            self,
            resolution,
            name=name,
            name_glob=name_glob,
            owner_type=owner_type,
            **features,
        )

    def build_forecast_reader(
        self,
        resolution: timedelta,
        *,
        time_series_type: str = "Deterministic",
        name: str | None = None,
        name_glob: str | None = None,
        owner_type: str | None = None,
        **features: Any,
    ) -> "ForecastReader":
        """Build a cross-sectional reader over the matching forecasts.

        The store builds the reader from its own catalog, so anything staged here is
        flushed first or it would be invisible to the reader.
        """
        self.check_open()
        return self._storage._build_forecast_reader(
            self,
            resolution,
            time_series_type=time_series_type,
            name=name,
            name_glob=name_glob,
            owner_type=owner_type,
            **features,
        )

    def serialize(
        self,
        data: dict[str, Any],
        dst: Path | str,
        src: Path | str | None = None,
    ) -> None:
        """Copy the store to ``dst``, including anything staged here.

        Raises
        ------
        ISOperationNotAllowed
            Raised if a time series transaction is open. Copying the artifact means
            closing and reopening it, which discards the transaction --- and a durable
            copy of state that may still be rolled back is not a coherent thing to
            produce.
        """
        self.check_open()
        self._storage._serialize(self, data, dst, src=src)

    def key_for(self, stored: _StoredSeries) -> Any:
        """Build the public ``TimeSeriesKey`` for a stored-series descriptor."""
        return self._storage.key_for(stored)
