# Time Series

Infrastructure systems supports time series data expressed as a one-dimensional array of floats
using the class {py:class}`infrasys.time_series_models.SingleTimeSeries`. Users must provide a `name`
that is typically the field of a component being modeled. For example, if the user has a time array
associated with the active power of a generator, they would assign
`name = "active_power"`.

Here is an example of how to create an instance of {py:class}`infrasys.time_series_models.SingleTimeSeries`:

```python
    import random
    time_series = SingleTimeSeries.from_array(
        data=[random.random() for x in range(24)],
        name="active_power",
        initial_time=datetime(year=2030, month=1, day=1),
        resolution=timedelta(hours=1),
    )
```

Users can attach their own attributes to each time array. For example,
there might be different profiles for different scenarios or model years.

```python
    time_series = SingleTimeSeries.from_array(
        data=[random.random() for x in range(24)],
        name="active_power",
        initial_time=datetime(year=2030, month=1, day=1),
        resolution=timedelta(hours=1),
        scenario="high",
        model_year="2035",
    )
```

## Deterministic Time Series

In addition to `SingleTimeSeries`, infrasys also supports deterministic time series,
which are used to represent forecasts or scenarios with a known future.

The {py:class}`infrasys.time_series_models.Deterministic` class represents a time series where
the data is explicitly stored as a 2D array, with each row representing a forecast window and
each column representing a time step within that window.

You can create a Deterministic time series in two ways:

1. **Explicitly with forecast data** using `Deterministic.from_array()` when you have pre-computed forecast values, then attach it with `system.add_time_series()`.
2. **From stored SingleTimeSeries** using `System.transform_single_time_series()` to create "perfect forecasts" derived from historical data.

### Creating Deterministic Time Series with Explicit Data

This approach is used when you have explicit forecast data available. Each forecast window is stored as a row in a 2D array.

Example:

```python
import numpy as np
from datetime import datetime, timedelta
from infrasys.time_series_models import Deterministic
from infrasys.quantities import ActivePower

initial_time = datetime(year=2020, month=9, day=1)
resolution = timedelta(hours=1)
horizon = timedelta(hours=8)  # 8 hours horizon (8 values per forecast)
interval = timedelta(hours=1)  # 1 hour between forecasts
window_count = 3  # 3 forecast windows

# Create forecast data as a 2D array where:
# - Each row is a forecast window
# - Each column is a time step in the forecast horizon
forecast_data = [
    [100.0, 101.0, 101.3, 90.0, 98.0, 87.0, 88.0, 67.0],  # 2020-09-01T00 forecast
    [101.0, 101.3, 99.0, 98.0, 88.9, 88.3, 67.1, 89.4],  # 2020-09-01T01 forecast
    [99.0, 67.0, 89.0, 99.9, 100.0, 101.0, 112.0, 101.3],  # 2020-09-01T02 forecast
]

# Create the data with units
data = ActivePower(np.array(forecast_data), "watts")
name = "active_power_forecast"
ts = Deterministic.from_array(
    data, name, initial_time, resolution, horizon, interval, window_count
)
system.add_time_series(ts, generator)
```

### Creating "Perfect Forecasts" from SingleTimeSeries

When you want a "perfect forecast" derived from historical data, call
`System.transform_single_time_series(horizon, interval)`. This re-describes **every**
`SingleTimeSeries` already stored on the system as a deterministic forecast that shares the same
underlying array — the overlapping forecast windows are computed by the Rust `infrastore`
backend, not materialized in Python. After transforming, retrieve a forecast by passing
`time_series_type=Deterministic` to `get_time_series`.

Example:

```python
from datetime import datetime, timedelta
from infrasys.time_series_models import Deterministic, SingleTimeSeries

initial_timestamp = datetime(year=2020, month=1, day=1)
name = "active_power"
ts = SingleTimeSeries.from_array(
    data=range(8784),
    name=name,
    resolution=timedelta(hours=1),
    initial_timestamp=initial_timestamp,
)
system.add_time_series(ts, generator)

# Derive perfect forecasts from all stored SingleTimeSeries.
system.transform_single_time_series(horizon=timedelta(hours=8), interval=timedelta(hours=1))

forecast = system.get_time_series(generator, name="active_power", time_series_type=Deterministic)
```

`transform_single_time_series` returns the number of series transformed. The original
`SingleTimeSeries` remains retrievable with `time_series_type=SingleTimeSeries`; the forecast view
is returned as a `Deterministic` whose data is a 2D array with one forecast window per row.

## Reading by Timestamp

`get_time_series` and its variants are series-oriented: they return one component's whole
array. Simulations need the transpose — every component's value at one timestamp, then the
next — and cannot afford to hold every array in memory to get it.

`System.build_time_series_reader(resolution, ...)` returns a reader whose
`read(timestamp)` is `{component id: value}`, and
`System.build_forecast_reader(resolution, ...)` returns one whose `read(timestamp)` is
`{component id: forecast window}`. Build a reader once against a filter (`name`,
`name_glob`, `component_type`, or feature key/value pairs) and drive it forward through
time; each read touches only the values for the requested timestamp.

Forecast readers additionally collapse components that share a backing array into a single
*slot* and perform one physical read per slot, which matters after
`transform_single_time_series` and wherever many components were given the same profile.

See [How to read time series by timestamp](#read-time-series-by-timestamp).

## Batching and Transactions

`System.time_series_transaction()` yields a transaction object exposing the same time series
methods as `System`; every call made on it joins the batch. Without a transaction, each call
commits on its own; with one, the store pays one catalog transaction for the whole block
instead of one per series. This matters when adding many arrays.

```python
with system.time_series_transaction() as txn:
    for generator, profile in profiles.items():
        txn.add_time_series(profile, generator)
```

The block is also a **transaction**. If it raises, everything it did is undone — additions
it had already been forced to write, and **removals**, which are recoverable only in here.
Outside a block a removal is permanent as soon as it happens: the store frees the array once
its last reference goes. Inside one that free is deferred until the block succeeds.

```python
with system.time_series_transaction() as txn:
    txn.add_time_series(new_profile, generator)
    txn.remove_time_series(generator, name="old_profile")
    ...
# both applied, or -- if anything raised -- neither
```

Calling a method on the transaction is what puts it in the batch. A `System` call inside the
block runs on its own and sees **committed** data only:

```python
with system.time_series_transaction() as txn:
    txn.add_time_series(ts, generator)

    txn.has_time_series(generator, name="load")     # True
    system.has_time_series(generator, name="load")  # False - not committed yet
```

Two constraints come with this:

- **Blocks nest, innermost first.** An inner block must finish before the one enclosing it.
  Two batches open at once that each commit or discard on their own schedule are not
  supported.
- **Serialize outside the block.** `to_json` while a block is open raises: writing a durable
  copy of state that might still roll back is not something it will do for you.

## Resolution

Infrastructure systems support two types of objects for passing the resolution:
:class:`datetime.timedelta` and :class:`dateutil.relativedelta.relativedelta`.
These types allow users to define durations with varying levels of granularity
and semantic meaning.
While `timedelta` is best suited for precise, fixed-length
intervals (e.g., seconds, minutes, hours, days), `relativedelta` is more
appropriate for calendar-aware durations such as months or years, which do not
have a fixed number of days.

Internally, all durations, regardless of whether they are specified using
`timedelta` or `relativedelta`, are normalized and serialized into a strict [ISO
8601 format](https://en.wikipedia.org/wiki/ISO_8601#Durations).
This provides a consistent and standardized representation of
durations across the system, ensuring compatibility and simplifying transport,
storage, and validation.
For example, a `timedelta` of 1 month will be converted to the ISO format string
`P1M` and a `timedelta` of 1 hour will be converted to `P0DT1H`.

## Behaviors

The `System` stores all time series arrays and their metadata in the Rust-backed
`infrastore` backend (NetCDF arrays plus a SQLite database). Users can customize time series
behavior with these keyword arguments passed to the `System` constructor:

- `time_series_read_only`: The default behavior allows users to add and remove time series data.
  Set this flag to disable mutation. That can be useful if you are de-serializing a system, won't be
  changing it, and want to avoid copying the data.
- `time_series_directory`: The `System` stores time series data on the computer's tmp filesystem by
  default. This filesystem may be of limited size. If your data will exceed that limit, such as what
  is likely to happen on an HPC compute node, set this parameter to an alternate location (such as
  `/tmp/scratch` on NREL's HPC systems).
- `time_series_compression`, `time_series_compression_level`, `time_series_shuffle`: Control NetCDF
  compression of the stored arrays. By default the backend uses `"deflate"` compression at level `3`
  with byte shuffle enabled; set `time_series_compression="none"` to disable compression.

Refer to the [Time Series API](#time-series-api) for more information.
