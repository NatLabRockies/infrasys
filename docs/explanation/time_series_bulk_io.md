# Design: Bulk Time Series Adds and Reads

This page explains how infrasys moves many time series in and out of the store efficiently:
how additions are batched into one catalog transaction, and how reads are grouped so the
store decompresses each dataset once. It describes the design behind
{py:meth}`infrasys.system.System.add_time_series`,
{py:meth}`infrasys.system.System.list_time_series`, and
{py:meth}`infrasys.system.System.time_series_transaction`.

## The layers and what each one owns

Four layers cooperate, and each owns exactly one kind of state:

- **`System` / `TimeSeriesManager`** own no time series state at all. They validate inputs,
  translate infrasys types to storage-level names, and ensure every operation runs inside a
  context (creating a transient one when the manager is not bound to a batch).
- **`TimeSeriesTransaction`** is the user-facing facade for one batch: the object yielded by
  `time_series_transaction`, exposing the same methods as `System`. It binds the manager to its
  context once, at construction, and holds no state of its own.
- **`TimeSeriesStorageContext`** owns *one batch of work*: the additions staged but not yet
  written, and the record of what the batch has already written so a failure can undo exactly
  its own writes. It is also the *receiver* for every operation --- `context.add_time_series(...)`
  rather than a storage call taking the context as an argument.
- **`TimeSeriesStoreStorage`** owns the in-memory index of *committed* associations
  (owner → association → `_StoredSeries` descriptor). It holds no batch state and no reference
  to any context; ownership points one way, from the context to the storage it writes through.

Hanging the operations off the context is what keeps the batching plumbing out of the
`**features` namespace that belongs to the caller: a time series feature named `context` is just
a feature. The storage-side implementations are private and take their context positionally, for
the same reason. It also makes a context reaching the wrong storage unrepresentable instead of
merely checked --- the one remaining pairing, `TimeSeriesManager.bind_context`, validates it.
- **The `infrastore` Rust store** is the single source of truth for array data and association
  metadata. It owns identity: arrays are content-addressed, and each association is identified
  by a store key. infrasys assigns no ids of its own.

This split is deliberate. Earlier designs cached mutable state in the storage layer; now the
only cached values are derived data with a clear invalidation story (the committed index, which
is rebuilt from the store on deserialization, and the store key memoized on each descriptor).

## Bulk adds

Every write operation runs inside a context. A caller who opens
{py:meth}`~infrasys.system.System.time_series_transaction` gets a transaction object and
calls the time series methods on it, batching them; a `System` method called on its own gets
a transient context that commits at the end of that single call, so the one-call behavior is
unchanged.

```python
with system.time_series_transaction() as txn:
    for gen, ts in profiles:
        txn.add_time_series(ts, gen)
# exiting the block commits: one bulk write, one catalog transaction
```

A `txn.add_time_series` call does no I/O. It validates every owner (so a duplicate on the last
owner cannot leave the earlier ones half-added), then stages one `_PendingAdd` per owner on the
transaction's context. Staged additions are visible only through the transaction that staged
them: metadata queries resolve the committed index *overlaid with* the calling context's staged
entries, in one place (`_visible_assocs`). A concurrent batch, or a `System` call inside the
block, sees only committed state.

The staged batch reaches the store when the context **flushes**, which happens at the first of:

- the block exits cleanly (commit),
- an operation needs the arrays physically present --- a read, a reader build, a removal, or
  counts inside the block forces an early flush,
- the buffer reaches its **auto-flush limits**: 10,000 staged additions or 256 MiB of staged
  array data, whichever comes first (both configurable on
  {py:meth}`~infrasys.system.System.time_series_transaction`).

The auto-flush limits are what let a block add hundreds of thousands of series without
holding them all in memory. The two limits serve different masters. The byte limit bounds
memory, which a count cannot do when individual arrays are long. The count limit protects
the stored layout: each flush becomes one HDF5 dataset whose *chunk width equals the batch
width*, so 10,000 f64 series produce 80 KiB chunks --- near the store's 1 MiB chunk cap and
within ~2% of unlimited-batch write throughput --- while flushing every 1,000 would freeze
8 KiB chunks into the file for every future reader. Splitting a block into several flushes
loses nothing else: array dedup is store-wide by content hash, and each flush is a nested
savepoint inside the block's transaction, so there is still exactly one SQLite commit.

A flush hands the whole batch to `Store.add_time_series_bulk`, which writes the arrays and
records every association in a single catalog transaction. The Rust store applies the batch
atomically: if any item is rejected, nothing is written. Only after the store accepts the batch
does the storage layer update its committed index, using the keys the store returned (memoized
so later reads never scan for them).

A block opened with `time_series_transaction` runs inside an **`infrastore` transaction**, and
that is what makes a failure recoverable. If the block raises, buffered additions are dropped
(they never reached the store) and the transaction is rolled back, undoing everything the block
did write --- **including removals**, which are irreversible outside a transaction because the
store frees an array once its last association goes. Inside one the free is deferred to the
commit, so the bytes are still there when the catalog rewinds. The in-memory index is rebuilt
from the store afterwards, since entries recorded as work was flushed now describe a catalog
state that no longer exists.

An early flush therefore costs nothing in recoverability: it lands inside the open transaction
and rolls back with the rest. This is why a read or a counts call inside a block is harmless.

Two constraints come with the mechanism:

- **Blocks nest LIFO.** SQLite savepoints are a stack, so an inner block must finish before the
  one enclosing it. `with` statements produce that shape anyway; two *interleaved* batches that
  each commit or discard on their own schedule are not supported. That is the price of exact
  rollback, and a client-side undo log --- which is what this replaced --- cannot restore a
  removal at any price.
- **Serialization must happen outside a block.** Copying the artifact closes and reopens it,
  which would discard the transaction, so `to_json` inside an open block raises rather than
  writing a copy of state that might still roll back.

## Bulk reads

`list_time_series` returns every matching array for an owner through one bulk store call
rather than one call per series:

1. `list_metadata` filters the owner's visible associations (committed ⊕ staged) by name,
   type, and features. This touches only lightweight descriptors --- never array data.
2. The context is flushed, so staged arrays are readable.
3. Each descriptor is *planned*: the store key is resolved, and a `start_time`/`length`
   request is translated into a UTC time range.
4. Plans are grouped by time range, because `Store.bulk_read` applies one range to every key
   it is given. Unsliced reads all share a range of `None`, so the common case --- read every
   matching series whole --- goes to the store as a single call, and the store decompresses
   each backing dataset once instead of once per series.
5. Results are converted back to infrasys types (`SingleTimeSeries`, `Deterministic`, ...) in
   the caller's original order, reattaching units where the series was stored from a `pint`
   quantity.

Single-series reads (`get_time_series`) follow the same plan/convert path with one key. For
the transpose access pattern --- every component's value at one timestamp, stepped through
time --- infrasys provides the cross-sectional readers described
[below](#bulk-readers-design), which read per timestamp instead of per series.

## The write and read paths in one picture

```{mermaid}
flowchart TB
    subgraph UserCode["User code"]
        ADD["txn.add_time_series(ts, gen)"]
        LIST["txn.list_time_series(gen)"]
    end

    subgraph Context["TimeSeriesStorageContext — owns the batch"]
        STAGED["Staged additions<br/>(_PendingAdd per owner)"]
        FLUSH["flush()<br/>commit, or first read/reader/serialize"]
    end

    subgraph Storage["TimeSeriesStoreStorage — owns the committed index"]
        VISIBLE["_visible_assocs<br/>committed index ⊕ this context's staged"]
        INDEX["Committed index<br/>owner → association → _StoredSeries"]
        PLAN["Plan reads: resolve keys,<br/>group by time range"]
    end

    subgraph Rust["infrastore (Rust) — source of truth"]
        BULKADD["add_time_series_bulk<br/>one atomic catalog transaction"]
        BULKREAD["bulk_read<br/>decompress each dataset once"]
    end

    ADD -->|"stage, no I/O"| STAGED
    STAGED --> FLUSH
    FLUSH -->|"whole batch"| BULKADD
    BULKADD -->|"store keys, input order"| INDEX

    LIST --> VISIBLE
    VISIBLE --> INDEX
    VISIBLE -.->|"overlay"| STAGED
    VISIBLE --> PLAN
    PLAN -->|"one call per distinct range"| BULKREAD
    BULKREAD -->|"arrays, original order"| LIST

    classDef user fill:#dbeafe,stroke:#2563eb,color:#1e3a5f
    classDef ctx fill:#ede9fe,stroke:#7c3aed,color:#3b1d69
    classDef storage fill:#dcfce7,stroke:#16a34a,color:#14432a
    classDef rust fill:#ffedd5,stroke:#ea580c,color:#7c2d12

    class ADD,LIST user
    class STAGED,FLUSH ctx
    class VISIBLE,INDEX,PLAN storage
    class BULKADD,BULKREAD rust
```

Blue is user code, purple is the context (batch state), green is the storage layer (committed
index), and orange is the Rust store (source of truth). The dashed edge is the one place where
staged and committed state meet: a query resolves the committed index overlaid with the calling
context's staged additions, so a batch is visible to itself and to nothing else.

```{eval-rst}
.. _bulk-readers-design:
```

## Bulk reads by timestamp: the cross-sectional readers

The paths above are series-oriented: they hand back one owner's whole array. A stepping
simulation needs the transpose --- every component's value at one timestamp, then the next ---
and cannot afford to hold every array in memory to get it.
{py:meth}`~infrasys.system.System.build_time_series_reader` and
{py:meth}`~infrasys.system.System.build_forecast_reader` exist for exactly that access
pattern; usage is covered in the
[how-to](#read-time-series-by-timestamp), and this section explains the design.

Building a reader splits the cost so that everything expensive happens once, up front:

1. The calling transaction's context is flushed --- a reader is built from the store's
   catalog, so staged additions would otherwise be invisible to it.
2. The Rust store snapshots the associations matching the filter (name, glob, owner type,
   features, one resolution per reader) and validates them together: static readers require
   one shared grid (initial timestamp, resolution, length), forecast readers one shared
   window timeline. A filter spanning two grids, or matching nothing, is rejected at build
   time rather than surfacing mid-loop.
3. The storage layer fetches the snapshot's keys once and derives the Python-side lookup
   state: the component id tuple aligned to each columnar group, per-component units pulled
   from the committed index, and --- for forecasts --- the slot map.
4. The result is an immutable snapshot object. Adding or removing time series afterwards does
   not change what a live reader covers; build a new one.

Each step is then deliberately thin. `read(timestamp)` makes one store call that positions the
reader at that timestamp, then zips the store's columnar arrays against the prebuilt id
tuples. No metadata is consulted, no keys are resolved, and no per-value Python objects are
created --- units are exposed once on the reader rather than attached to every value, and
`read_columns` skips even the per-step dict for loops where that shows up.

Forecast readers add one more collapse: components whose forecasts share a backing array ---
after {py:meth}`~infrasys.system.System.transform_single_time_series`, or wherever a fleet
was given one profile --- map to a single *slot*. The store performs one physical read per
slot rather than one per component, and every component in a slot receives the same array
object rather than a copy (which is why the windows must be treated as read-only).

```{mermaid}
flowchart TB
    subgraph UserCode["User code"]
        BUILD["system.build_time_series_reader(resolution, name=...)"]
        STEP["reader.read(timestamp) — once per step"]
    end

    subgraph Storage["TimeSeriesStoreStorage"]
        FLUSHR["Flush caller's context<br/>(staged series must be in the catalog)"]
        DERIVE["Derive lookup state once:<br/>component id tuples, units, slots"]
    end

    subgraph Reader["Reader — immutable snapshot"]
        SNAP["Rust reader + id tuples<br/>+ units + slot map"]
        ZIP["Zip columnar arrays<br/>against prebuilt id tuples"]
    end

    subgraph Rust["infrastore (Rust)"]
        CATALOG["Snapshot matching associations,<br/>validate one shared grid/timeline"]
        READ["static_read / forecast_read:<br/>one physical read per group/slot"]
    end

    BUILD --> FLUSHR
    FLUSHR --> CATALOG
    CATALOG --> DERIVE
    DERIVE --> SNAP

    STEP --> READ
    READ -->|"columnar values"| ZIP
    ZIP -->|"{component id: value}"| STEP

    classDef user fill:#dbeafe,stroke:#2563eb,color:#1e3a5f
    classDef storage fill:#dcfce7,stroke:#16a34a,color:#14432a
    classDef reader fill:#ccfbf1,stroke:#0d9488,color:#134e4a
    classDef rust fill:#ffedd5,stroke:#ea580c,color:#7c2d12

    class BUILD,STEP user
    class FLUSHR,DERIVE storage
    class SNAP,ZIP reader
    class CATALOG,READ rust
```

The colors follow the first diagram --- blue for user code, green for the storage layer,
orange for the Rust store --- with teal for the reader snapshot, the one new piece of state
this path introduces. The top half runs once at build time; the bottom half is the per-step
loop, which touches only the teal and orange boxes.

## Why this shape

- **One catalog transaction per block.** Adding thousands of profiles pays one SQLite
  transaction instead of thousands, which is where the bulk write speedup comes from.
- **No hidden batch state.** Because the storage layer never holds staged data, there is
  nothing to invalidate when a batch is discarded, and two batches cannot observe each other's
  uncommitted work.
- **Metadata never reads arrays.** Listing, existence checks, and counts run entirely against
  the descriptor index; array I/O happens only in the planned bulk reads.
- **Failure surfaces cleanly.** A rejected bulk write leaves the store and the index untouched;
  a raised block rolls its transaction back, undoing its adds *and* its removals exactly.
- **Stepping loops pay at build time, not per step.** A reader validates its filter, resolves
  its keys, and derives its lookup state once; each timestamp then costs one store call per
  columnar group or slot, with no metadata work in the loop.
