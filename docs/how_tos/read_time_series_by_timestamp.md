```{eval-rst}
.. _read-time-series-by-timestamp:
```

# How to read time series data by timestamp

Suppose that you are stepping a simulation through time and need every component's value at
the current timestamp, then the next one. The rest of the time series API is
series-oriented: `get_time_series` hands back one component's whole array. Driving a
stepping loop with it leaves two bad options — hold every array in memory, or re-read each
component's series on every step.

`System.build_time_series_reader` returns a reader built for exactly this. You build it once
against a filter and then drive it forward through time; each `read` touches only the values
for the requested timestamp.

## Stepping through `SingleTimeSeries`

This example uses a test module in the `infrasys` repository.

```python
from datetime import datetime, timedelta

import numpy as np

from infrasys import SingleTimeSeries
from tests.models.simple_system import SimpleSystem, SimpleGenerator, SimpleBus

initial_time = datetime(year=2024, month=1, day=1)
resolution = timedelta(hours=1)
length = 24

system = SimpleSystem()
bus = SimpleBus(name="bus", voltage=1.1)
system.add_component(bus)
generators = [
    SimpleGenerator(name=f"gen{i}", active_power=1.0, rating=1.0, bus=bus, available=True)
    for i in range(3)
]
system.add_components(*generators)

# Adding through the transaction batches all three adds into one write. Calling
# system.add_time_series directly instead commits each add on its own.
with system.time_series_transaction() as txn:
    for i, generator in enumerate(generators):
        data = np.arange(length, dtype=float) + i * 100
        ts = SingleTimeSeries.from_array(data, "load", initial_time, resolution)
        txn.add_time_series(ts, generator)

reader = system.build_time_series_reader(resolution, name="load")

for timestamp in reader.timestamps[:3]:
    print(timestamp, reader.read(timestamp))
```

```
2024-01-01 00:00:00 {2: 0.0, 3: 100.0, 4: 200.0}
2024-01-01 01:00:00 {2: 1.0, 3: 101.0, 4: 201.0}
2024-01-01 02:00:00 {2: 2.0, 3: 102.0, 4: 202.0}
```

`read` returns `{component id: value}`, so the caller is free of any iteration order. Map
ids back to components with `system.get_component_by_id`.

The reader also reports the grid its series share and the components it covers:

```python
print(reader.component_ids)
print(reader.grid)
```

```
(2, 3, 4)
{'initial_timestamp': '2024-01-01T00:00:00+00:00', 'resolution': 'PT1H', 'length': 24}
```

## Choosing what a reader covers

Pass one resolution per reader, plus any of `name`, `name_glob`, `component_type`, and
arbitrary feature key/value pairs:

```python
system.build_time_series_reader(resolution, name="load")
system.build_time_series_reader(resolution, name_glob="lo*")
system.build_time_series_reader(resolution, component_type=SimpleGenerator)
system.build_time_series_reader(resolution, scenario="high")
```

Three constraints are worth knowing up front, because all three surface as an
`InvalidParameterError` at build time rather than mid-loop:

- All matched series must share one grid — the same initial timestamp, resolution, and
  length. A filter that spans two different grids is rejected.
- All matched series must agree on how their timestamps are spelled, because a reader
  materializes one timestamp axis. A cohort mixing wall-clock (naive) series with
  instant-bearing (aware) ones is rejected; narrow it with `zoneless`:

  ```python
  system.build_time_series_reader(resolution, name="load", zoneless=True)   # wall clocks
  system.build_time_series_reader(resolution, name="load", zoneless=False)  # instants
  ```

  The timestamps a reader hands back are always spelled the way its `read` expects them, so
  driving the loop from `reader.timestamps` never hits this. See
  [Time zones](#time-series-time-zones).
- A filter matching nothing is rejected, rather than returning a reader that reads nothing.

A reader is a snapshot of the associations that matched when it was built. Adding or
removing time series afterwards does not change what a live reader covers; build a new one.

## Avoiding a dict per step

`read_columns` is the escape hatch for loops where building a dict on every step is
measurable. It hands back the store's arrays directly, with no per-value Python objects.
Each array is aligned to its tuple of component ids:

```python
for component_ids, values in reader.read_columns(reader.timestamps[0]):
    print(component_ids, values)
```

```
(2, 3, 4) [  0. 100. 200.]
```

## Units

Values from `read` are raw magnitudes. Attaching a `pint` quantity per value would dominate
the cost of a stepping loop, so the readers expose the stored units instead and leave the
conversion to the caller:

```python
print(reader.units)
```

```
{2: QuantityMetadata(module='infrasys.quantities', quantity_type=<class 'infrasys.quantities.ActivePower'>, units='watt'), ...}
```

Components whose series were stored without units map to `None`.

## Stepping through forecasts

`System.build_forecast_reader` is the forecast counterpart: `read(timestamp)` returns
`{component id: window array}` for the forecast window starting at that timestamp. It
accepts the same filters, plus `time_series_type` (default `Deterministic`, which also
covers the forecasts derived by `transform_single_time_series`).

```python
system.transform_single_time_series(horizon=timedelta(hours=4), interval=resolution)

reader = system.build_forecast_reader(resolution, name="load")
windows = reader.read(reader.timestamps[0])
for component_id, window in windows.items():
    print(component_id, window)
```

```
2 [0. 1. 2. 3.]
3 [100. 101. 102. 103.]
4 [200. 201. 202. 203.]
```

### Shared profiles collapse to slots

Components whose forecasts are backed by the same array collapse to a single *slot*. The
store performs one physical read per slot rather than one per component, so a fleet sharing
one profile materializes one window, not N. Rebuild the system above with five generators
that all carry the same profile, and the collapse is visible on the reader:

```python
print(len(reader.component_ids), reader.num_slots)
print(reader.slots)
print(reader.components_by_slot())
```

```
5 1
{2: 0, 3: 0, 4: 0, 5: 0, 6: 0}
{0: [2, 3, 4, 5, 6]}
```

The deduplication is exposed rather than hidden. `read` still returns an entry per
component, but every component in a slot gets the *same array object* rather than a copy —
so treat the arrays as read-only:

```python
when = reader.timestamps[0]
windows = reader.read(when)
assert windows[generators[0].id] is windows[generators[1].id]
```

If your work is per unique window rather than per component, `read_slots` gives you the
deduplicated form directly:

```python
components_by_slot = reader.components_by_slot()
for slot, window in reader.read_slots(when).items():
    for component_id in components_by_slot[slot]:
        ...
```

## Why this is faster

Stepping 5000 components through 168 timestamps takes about 0.16 s through a reader
(0.95 ms per step, or 0.45 ms per step via `read_columns`). Preloading the same arrays into
memory takes about 0.52 s before the loop even starts — and that preload cost is the part
that stops scaling as the fleet or the horizon grows. Over the same fleet, transformed
forecasts collapse 5000 components to 2501 slots.

Refer to the [Time Series API](#time-series-readers-api) for the full reader interface.
