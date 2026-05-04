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

1. **Explicitly with forecast data** using `Deterministic.from_array()` when you have pre-computed forecast values.
2. **From a SingleTimeSeries** using `Deterministic.from_single_time_series()` to create a "perfect forecast" based on historical data by extracting overlapping windows.

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
ts = DeterministicTimeSeries.from_array(
# Create the data with units
data = ActivePower(np.array(forecast_data), "watts")
name = "active_power_forecast"
ts = Deterministic.from_array(
    data, name, initial_time, resolution, horizon, interval, window_count
)
```

### Creating "Perfect Forecasts" from SingleTimeSeries

The `from_single_time_series()` classmethod is useful when you want to create a "perfect forecast" based on historical data for testing or validation purposes. It extracts overlapping forecast windows from an existing `SingleTimeSeries`.

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
horizon = timedelta(hours=8)
interval = timedelta(hours=1)
ts_deterministic = Deterministic.from_single_time_series(
    ts, interval=interval, horizon=horizon
)
```

In this example, `ts_deterministic` creates a forecast for `active_power` by extracting forecast windows from the original `SingleTimeSeries` `ts` at different offsets determined by `interval` and `horizon`. The forecast data is materialized as a 2D array where each row is a forecast window.

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

Users can customize time series behavior with these flags passed to the `System` constructor:

- `time_series_in_memory`: The `System` stores each array of data in an Arrow file by default. This
  is a binary file that enables efficient storage and row access. Set this flag to store the data in
  memory instead.
- `time_series_read_only`: The default behavior allows users to add and remove time series data.
  Set this flag to disable mutation. That can be useful if you are de-serializing a system, won't be
  changing it, and want to avoid copying the data.
- `time_series_directory`: The `System` stores time series data on the computer's tmp filesystem by
  default. This filesystem may be of limited size. If your data will exceed that limit, such as what
  is likely to happen on an HPC compute node, set this parameter to an alternate location (such as
  `/tmp/scratch` on NREL's HPC systems).

Refer to the [Time Series API](#time-series-api) for more information.
