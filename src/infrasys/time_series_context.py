"""The transaction object for time series operations.

A :class:`TimeSeriesStorageContext` owns everything about one batch of work: the
additions staged but not yet written, the index entries they will produce, and the
record of what this batch has already committed so that a failure can undo exactly
its own writes. Storage keeps no batch state and holds no reference to any context;
ownership points one way, from the context to the storage it writes through.

Staged additions are visible only through the context that staged them. An operation
that is handed no context runs against committed state, which is what makes a
transient per-call context (the default when a caller does not open a block) safe:
it can never observe or disturb another batch's staged work.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

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
    """One staged addition: the store's bulk item plus where it lives in the index."""

    item: dict[str, Any]
    stored: _StoredSeries
    owner_key: OwnerKey
    assoc_key: AssocKey


class TimeSeriesStorageContext:
    """Owns one batch of time series work against a storage backend.

    Additions staged on a context are held in memory until :meth:`flush` writes them to
    the store in a single bulk call. Until then they are visible through this context and
    no other, and the storage's committed index is untouched --- which is what makes
    :meth:`discard` able to abandon a batch without a store round trip.

    Examples
    --------
    >>> with system.open_time_series_store() as context:
    ...     system.add_time_series(ts, gen, context=context)
    """

    def __init__(self, storage: "TimeSeriesStoreStorage") -> None:
        self._storage = storage
        # Staged and not yet written to the store.
        self._pending: list[_PendingAdd] = []
        # Index entries for the staged additions: owner_key -> {assoc_key: _StoredSeries}.
        self._staged: dict[OwnerKey, dict[AssocKey, _StoredSeries]] = {}
        # What this context has already written, so a later failure can undo its own work
        # and nobody else's.
        self._committed: list[_PendingAdd] = []
        self._closed = False

    @property
    def storage(self) -> "TimeSeriesStoreStorage":
        """Return the storage this context writes through."""
        return self._storage

    @property
    def has_staged_data(self) -> bool:
        """Return True if additions are staged but not yet written."""
        return bool(self._pending)

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
                "open_time_series_store block that created them; open a new one."
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
        """Stage additions without writing them to the store."""
        self.check_open()
        self._pending.extend(entries)
        for entry in entries:
            self._staged.setdefault(entry.owner_key, {})[entry.assoc_key] = entry.stored

    def staged_for(self, owner_key: OwnerKey) -> dict[AssocKey, _StoredSeries]:
        """Return this context's staged associations for one owner.

        The context knows only what it has staged. Resolving that against committed
        associations is the storage's job, since the storage owns that index.
        """
        return self._staged.get(owner_key, {})

    def flush(self) -> None:
        """Write staged additions to the store in one bulk call.

        A no-op when nothing is staged. Any operation that needs the arrays to be
        physically present --- a read, a reader build, serialization --- flushes the
        context it was handed, and only that context.
        """
        self.check_open()
        if not self._pending:
            return
        pending = self._pending
        # Reset before writing so that a failed write cannot leave the entries staged a
        # second time, and so the context stays usable for further additions.
        self._pending = []
        self._staged = {}
        self._storage.write_pending(pending)
        self._committed.extend(pending)

    def commit(self) -> None:
        """Flush and close the context."""
        try:
            self.flush()
        finally:
            self._closed = True

    def discard(self) -> None:
        """Abandon this batch, undoing everything it wrote.

        Staged additions never reached the store, so they are dropped outright. Anything
        this context already flushed --- a read or a reader build inside the block forces
        an early write --- is removed from the store and the index. Only this context's
        own writes are undone.

        TODO: removals performed through this context are applied to the store
        immediately and are not restored here; add full rollback of removals.
        """
        self._pending = []
        self._staged = {}
        committed = self._committed
        self._committed = []
        self._closed = True
        if committed:
            self._storage.undo_committed(committed)
