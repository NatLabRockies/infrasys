# Design: Bulk Time Series Adds and Reads

This page explains how infrasys moves many time series in and out of the store efficiently:
how additions are batched into one catalog transaction, and how reads are grouped so the
store decompresses each dataset once. It describes the design behind
{py:meth}`infrasys.system.System.add_time_series`,
{py:meth}`infrasys.system.System.list_time_series`, and
{py:meth}`infrasys.system.System.open_time_series_store`.

## The layers and what each one owns

Four layers cooperate, and each owns exactly one kind of state:

- **`System` / `TimeSeriesManager`** own no time series state at all. They validate inputs,
  translate infrasys types to storage-level names, and ensure every operation runs inside a
  context (creating a transient one when the caller did not supply one).
- **`TimeSeriesStorageContext`** owns *one batch of work*: the additions staged but not yet
  written, and the record of what the batch has already written so a failure can undo exactly
  its own writes.
- **`TimeSeriesStoreStorage`** owns the in-memory index of *committed* associations
  (owner → association → `_StoredSeries` descriptor). It holds no batch state and no reference
  to any context; ownership points one way, from the context to the storage it writes through.
- **The `infrastore` Rust store** is the single source of truth for array data and association
  metadata. It owns identity: arrays are content-addressed, and each association is identified
  by a store key. infrasys assigns no ids of its own.

This split is deliberate. Earlier designs cached mutable state in the storage layer; now the
only cached values are derived data with a clear invalidation story (the committed index, which
is rebuilt from the store on deserialization, and the store key memoized on each descriptor).

## Bulk adds

Every write operation takes a context. A caller who opens
{py:meth}`~infrasys.system.System.open_time_series_store` batches many calls; a caller who
passes nothing gets a transient context that commits at the end of that single call, so the
one-call behavior is unchanged.

```python
with system.open_time_series_store() as context:
    for gen, ts in profiles:
        system.add_time_series(ts, gen, context=context)
# exiting the block commits: one bulk write, one catalog transaction
```

An `add_time_series` call does no I/O. It validates every owner (so a duplicate on the last
owner cannot leave the earlier ones half-added), then stages one `_PendingAdd` per owner on the
context. Staged additions are visible only through the context that staged them: metadata
queries resolve the committed index *overlaid with* the calling context's staged entries, in
one place (`_visible_assocs`). A concurrent context, or a call with no context, sees only
committed state.

The staged batch reaches the store when the context **flushes**, which happens at the first of:

- the block exits cleanly (commit),
- an operation needs the arrays physically present --- a read, a reader build, a removal,
  counts, or serialization inside the block forces an early flush.

A flush hands the whole batch to `Store.add_time_series_bulk`, which writes the arrays and
records every association in a single catalog transaction. The Rust store applies the batch
atomically: if any item is rejected, nothing is written. Only after the store accepts the batch
does the storage layer update its committed index, using the keys the store returned (memoized
so later reads never scan for them).

If the block raises, the context **discards**: staged additions are dropped outright, and
anything the block had already flushed is removed from the store and the index. Only this
context's own writes are undone. Removals are applied immediately and are currently *not*
restored on discard.

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
        ADD["system.add_time_series(ts, gen, context=ctx)"]
        LIST["system.list_time_series(gen, context=ctx)"]
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

1. The caller's context is flushed --- a reader is built from the store's catalog, so staged
   additions would otherwise be invisible to it.
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
- **Failure surfaces cleanly.** A rejected bulk write leaves the store, the index, and every
  other context untouched; a raised block undoes its own flushed additions and nothing else.
- **Stepping loops pay at build time, not per step.** A reader validates its filter, resolves
  its keys, and derives its lookup state once; each timestamp then costs one store call per
  columnar group or slot, with no metadata work in the loop.
