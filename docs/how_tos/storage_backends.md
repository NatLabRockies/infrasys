# How to Configure Time Series Storage

Infrasys provides two time-series storage modes:

1. **time-series-store** ({py:class}`~infrasys.time_series_store_storage.TimeSeriesStoreStorage`):
   the default persistent backend, implemented by the Rust `time-series-store` package.
2. **In-memory storage**
   ({py:class}`~infrasys.in_memory_time_series_storage.InMemoryTimeSeriesStorage`): temporary
   process-local storage for tests and small workflows.

## Persistent Storage

Persistent systems use `time-series-store` by default:

```python
from infrasys import System

system = System()
```

You can select it explicitly and choose the parent directory for its NetCDF and SQLite files:

```python
from pathlib import Path

from infrasys import System
from infrasys.time_series_models import TimeSeriesStorageType

system = System(
    time_series_storage_type=TimeSeriesStorageType.TIME_SERIES_STORE,
    time_series_directory=Path("/path/to/time-series"),
)
```

If no directory is provided, infrasys creates a temporary directory and removes it when the
process exits.

The persistent backend currently supports `SingleTimeSeries` and
`NonSequentialTimeSeries`. Deterministic forecast persistence will be available after support is
added to `time-series-store`.

## In-Memory Storage

Use in-memory storage when persistence is not required:

```python
from infrasys import System
from infrasys.time_series_models import TimeSeriesStorageType

system = System(time_series_storage_type=TimeSeriesStorageType.MEMORY)
```

In-memory systems are converted to `time-series-store` automatically when serialized.

## Converting Storage

Systems can move between the persistent and in-memory implementations:

```python
system.convert_storage(time_series_storage_type=TimeSeriesStorageType.MEMORY)
system.convert_storage(
    time_series_storage_type=TimeSeriesStorageType.TIME_SERIES_STORE,
)
```

Conversion preserves supported time-series arrays and their infrasys metadata.

## Read-Only Mode

Serialized systems can open the persistent store without copying it:

```python
system = System.from_json("system.json", time_series_read_only=True)
```

Read-only systems reject time-series additions and removals.
