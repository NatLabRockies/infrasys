# Custom System

This tutorial describes how to create and use a custom system in a parent package.

1. Define the system. This example defines some custom attributes to illustrate serialization and
   de-serialization behaviors.

```python
from typing import Any

from infrasys import System

class CustomSystem(System):
    """Custom System"""

    def __init__(self, my_attribute=5, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.data_format_version = "1.1.0"
        self.my_attribute = my_attribute

    def serialize_system_attributes(self) -> dict[str, Any]:
        return {"my_attribute": self.my_attribute}

    def deserialize_system_attributes(self, data: dict[str, Any]) -> None:
        self.my_attribute = data["my_attribute"]

    def handle_data_format_upgrade(self, data: dict[str, Any], from_version, to_version)) -> None:
        ...
```

**Notes**:

- The system's custom attribute `my_attribute` will be serialized and de-serialized automatically.
- `infrasys` will call handle_data_format_upgrade during de-serialization so that this package
  can handle format changes that might occur in the future.

2. Define some component classes.

```python
from uuid import UUID
from infrasys.component import  Component
from infrasys.location import Location

class Bus(Component):
    """Represents a bus."""

    voltage: float
    coordinates: Location | None = None

    @classmethod
    def example(cls) -> "Bus":
        return Bus(
            name="my-bus",
            voltage=1.1,
            coordinates=Location(x=0.0, y=0.0),
        )

class Generator(Component):
    """Represents a generator."""

    available: bool
    bus: Bus
    active_power: float
    rating: float

    @classmethod
    def example(cls) -> "Generator":
        return Generator(
            name="simple-gen",
            available=True,
            bus=Bus.example(),
            active_power=1.0,
            rating=0.0,
        )
```

**Notes**:

- Each component defines the `example` method. This is highly recommended so that users can see
  what a component might look like in the REPL.

- The `Bus` class implements a custom check when it is added to the system. It raises an exception
  if its `Location` object is not already attached to the system. The same could be done for
  generators and buses.

3. Build a system.

```python
import random
from datetime import datetime, timedelta

from infrasys.location import Location
from infrasys.time_series_models import SingleTimeSeries

system = CustomSystem(name="my_system", my_attribute=10)
location = Location(x=0.0, y=0.0)
bus = Bus(name="bus1", voltage=1.1, coordinates=location)
gen = Generator(name="gen1", available=True, bus=bus, active_power=1.2, rating=1.1)
system.add_components(location, bus, gen)
time_series = SingleTimeSeries.from_array(
    data=[random.random() for x in range(24)],
    name="active_power",
    initial_time=datetime(year=2030, month=1, day=1),
    resolution=timedelta(hours=1),
)
system.add_time_series(time_series, gen)
```

4. Serialize and de-serialize the system.

```python
system.to_json("system.json")
system2 = CustomSystem.from_json("system.json")
assert system.get_component(Generator, "gen1").active_power == \
    system2.get_component(Generator, "gen1").active_power
```
