# Serialization
This page describes how `infrasys` serializes a system and its components to JSON when a user calls
`System.to_json()` and `System.from_json()`.

## Components
`infrasys` converts its nested dictionaries of components-by-type into a flat array. Each component
records metadata about its actual Python type into a field called `__metadata__`. Here is an example
of a serialized `Location` object. Note that it includes the module and type. `infrasys` uses this
information during de-serialization to dynamically import the type and construct it. This allows
serialization to work with types defined outside of `infrasys` as long as the user has imported
those types.

```json
{
  "id": 1,
  "name": "",
  "x": 0.0,
  "y": 0.0,
  "crs": null,
  "__metadata__": {
    "module": "infrasys.location",
    "type": "Location",
    "serialized_type": "base"
  }
}
```

### Composed components
There are many cases where one component will contain an instance of another component. For example,
a `Bus` may contain a `Location` or a `Generator` may contain a `Bus`. When serializing each
component, `infrasys` checks the type of each of that component's fields. If a value is another
component (which means that it must also be attached to system), `infrasys` replaces that instance
with its integer `id`. It does this to avoid duplicating data in the JSON file.

Here is an example of a serialized `Bus`. Note the value for the `coordinates` field. It contains the
type and `id` of the actual `coordinates`. During de-serialization, `infrasys` will detect this
condition and only attempt to de-serialize the bus once all `Location` instances have been
de-serialized.

```json
{
  "id": 2,
  "name": "bus1",
  "voltage": 1.1,
  "__metadata__": {
    "module": "tests.models.simple_system",
    "type": "SimpleBus",
    "serialized_type": "base"
  },
  "coordinates": {
    "__metadata__": {
      "module": "infrasys.location",
      "type": "Location",
      "serialized_type": "composed_component",
      "id": 1
    }
  }
}
```

#### Denormalized component data
There are cases where users may prefer to have the full, denormalized JSON data for a component.
All components are of type `pydantic.BaseModel` and so implement the method `model_dump_json`.

Here is an example of a bus serialized that way (`bus.model_dump_json(indent=2)`):

```json
{
  "id": 2,
  "name": "bus1",
  "voltage": 1.1,
  "coordinates": {
    "id": 1,
    "name": "",
    "x": 0.0,
    "y": 0.0,
    "crs": null
  }
}
```

### Pint Quantities
`infrasys` encodes metadata into component JSON when that component contains a `pint.Quantity`
instance. Here is an example of such a component:

```json
{
  "id": 1,
  "name": "test",
  "__metadata__": {
    "module": "tests.test_serialization",
    "type": "ComponentWithPintQuantity",
    "serialized_type": "base"
  },
  "distance": {
    "value": 2,
    "units": "meter",
    "__metadata__": {
      "module": "infrasys.quantities",
      "type": "Distance",
      "serialized_type": "quantity"
    }
  }
}
```

## Time Series
`infrasys` stores all time series arrays and their metadata in the Rust-backed `time-series-store`
backend (NetCDF arrays plus a SQLite database). When the user calls `system.to_json()`, `infrasys`
copies those store files into the user-specified directory alongside the JSON file.
