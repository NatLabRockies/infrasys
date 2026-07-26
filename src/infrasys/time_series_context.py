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
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from loguru import logger

from infrasys.exceptions import ISOperationNotAllowed
from infrasys.time_series_models import QuantityMetadata

if TYPE_CHECKING:
    from infrasys.time_series_store_storage import TimeSeriesStoreStorage

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


class TimeSeriesStorageContext:
    """Owns one batch of time series work against a storage backend.

    Additions are buffered until :meth:`flush` writes them to the store in a single bulk
    call. A transactional context (one from ``time_series_transaction``) wraps everything
    it does in a store transaction, so :meth:`discard` undoes the whole block — flushed
    work and removals included.

    Examples
    --------
    >>> with system.time_series_transaction() as context:
    ...     system.add_time_series(ts, gen, context=context)
    """

    def __init__(self, storage: "TimeSeriesStoreStorage", transactional: bool = False) -> None:
        self._storage = storage
        # Buffered additions, not yet handed to the store. Batching only; atomicity is
        # the transaction's job.
        self._pending: list[_PendingAdd] = []
        # An index over `_pending` so a duplicate can be caught before the flush that
        # would let the store catch it. Cleared whenever the buffer drains.
        self._staged: dict[OwnerKey, dict[AssocKey, _StoredSeries]] = {}
        self._transactional = transactional
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
        """Buffer additions without writing them to the store."""
        self.check_open()
        self._pending.extend(entries)
        for entry in entries:
            self._staged.setdefault(entry.owner_key, {})[entry.assoc_key] = entry.stored

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
