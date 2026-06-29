# How to Configure Time Series Storage

Infrasys stores all time-series data and metadata in the
**time-series-store** backend
({py:class}`~infrasys.time_series_store_storage.TimeSeriesStoreStorage`), implemented by the Rust
`time-series-store` package. It is the single source of truth for both the arrays and their
owner/feature associations.

## Persistent Storage

Systems use `time-series-store` by default:

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

The backend currently supports `SingleTimeSeries` and
`NonSequentialTimeSeries`. Deterministic forecast persistence will be available after support is
added to `time-series-store`.

## Read-Only Mode

Serialized systems can open the persistent store without copying it:

```python
system = System.from_json("system.json", time_series_read_only=True)
```

Read-only systems reject time-series additions and removals.
