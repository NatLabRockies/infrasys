# How to Configure Time Series Storage

Infrasys stores all time-series data and metadata in the
**infrastore** backend
({py:class}`~infrasys.time_series_store_storage.TimeSeriesStoreStorage`), implemented by the Rust
`infrastore` package. It is the single source of truth for both the arrays and their
owner/feature associations.

## Persistent Storage

Systems use `infrastore` by default:

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

`infrasys` persists `SingleTimeSeries`, `NonSequentialTimeSeries`, and `Deterministic` forecasts.
Forecasts can be added explicitly with `system.add_time_series(Deterministic.from_array(...),
component)`, or derived from stored `SingleTimeSeries` with
`system.transform_single_time_series(horizon, interval)`; both are retrieved with
`time_series_type=Deterministic`.

## Read-Only Mode

Serialized systems can open the persistent store without copying it:

```python
system = System.from_json("system.json", time_series_read_only=True)
```

Read-only systems reject time-series additions and removals.
