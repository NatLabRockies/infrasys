# How to list existing time series data

Suppose that you have added multiple time series arrays to your components using differing
names and attributes. How can you see what is present?

This example uses a test module in the `infrasys` repository.

The call to `system.add_time_series` returns a key. You can store those keys yourself or look them
up later with `system.list_time_series_keys`. Here's how to do it.

```python
from datetime import datetime, timedelta

import numpy as np

from infrasys import SingleTimeSeries
from tests.models.simple_system import SimpleSystem, SimpleGenerator, SimpleBus

system = SimpleSystem()
bus = SimpleBus(name="test-bus", voltage=1.1)
gen = SimpleGenerator(name="gen", active_power=1.0, rating=1.0, bus=bus, available=True)
system.add_components(bus, gen)

length = 10
initial_time = datetime(year=2020, month=1, day=1)
timestamps = [initial_time + timedelta(hours=i) for i in range(length)]
name = "active_power"
ts1 = SingleTimeSeries.from_time_array(np.random.rand(length), name, timestamps)
ts2 = SingleTimeSeries.from_time_array(np.random.rand(length), name, timestamps)
key1 = system.add_time_series(ts1, gen, scenario="low")
key2 = system.add_time_series(ts2, gen, scenario="high")

# Use the keys directly.
ts1_b = system.get_time_series_by_key(gen, key1)
ts2_b = system.get_time_series_by_key(gen, key2)

# Identify the keys later.
for key in system.list_time_series_keys(gen):
    print(f"{gen.label}: {key}")
```

```
SimpleGenerator.gen: name='active_power' initial_time=datetime.datetime(2020, 1, 1, 0, 0) resolution=datetime.timedelta(seconds=3600) time_series_type=<class 'infrasys.time_series_models.SingleTimeSeries'> user_attributes={'scenario': 'high'} length=10
SimpleGenerator.gen: name='active_power' initial_time=datetime.datetime(2020, 1, 1, 0, 0) resolution=datetime.timedelta(seconds=3600) time_series_type=<class 'infrasys.time_series_models.SingleTimeSeries'> user_attributes={'scenario': 'low'} length=10
```

You can also retrieve time series by specifying the parameters as shown here:

```python
system.time_series.get(gen, name="active_power", scenario="high")
```

```
SingleTimeSeries(name='active_power', normalization=None, data=array([0.29276233, 0.97400382, 0.76499075, 0.95080431, 0.61749027,
       0.73899945, 0.57877704, 0.3411286 , 0.80701393, 0.53051773]), resolution=datetime.timedelta(seconds=3600), initial_time=datetime.datetime(2020, 1, 1, 0, 0), length=10)
```
