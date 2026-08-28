# System
The System class provides a data store for components and time series data.

Refer to the [System API](#system-api) for complete information.

## Items to consider for parent packages

### Composition vs Inheritance
Parent packages must choose one of the following:

1. Derive a custom System class that inherits from `infrasys.System`. Re-implement methods
as desired. Add custom attributes to the System that will be serialized to JSON.

    - Reimplement `System.add_components` in order to perform custom validation or custom behavior.
      This is only needed for validation that needs information from both the system and the
      component. Note that the `System` constructor provides the keyword argument
      `auto_add_composed_components` that dictates how to handle the condition where a component
      contains another component which is not already attached to the system.

    - Reimplement `System.serialize_system_attributes` and `System.deserialize_system_attributes`.
      `infrasys` will call those methods during `to_json` and `from_json` and serialize/de-serialize
      the contents.

    - Reimplement `System.data_format_version` and `System.handle_data_format_upgrade`. `infrasys`
      will call the upgrade function if it detects a version change during de-serialization.

2. Implement an independent System class and compose the `infrasys.System`. This can be beneficial
if you want to make the underlying system opaque to users.

    - This pattern requires that you call `System.to_json()` with the keyword argument `data` set
      to a dictionary containing your system's attributes. `infrasys` will add its contents to a
      field called `system` inside that dictionary.

3. Use `infrasys.System` directly. This is probably not what most packages want because they will
not be able to serialize custom attributes or implement specialized behavior as discussed above.

### Units
`infrasys` uses the [pint library](https://pint.readthedocs.io/en/stable/) to help manage units.
Package developers should consider storing fields that are quantities as subtypes of
[Base.Quantity](#base-quantity-api). Pint performs unit conversion automatically when performing
arithmetic.

If you want to be able to generate JSON schema for a model that contains a Pint quantity, you must
add an annotation as shown below. Otherwise, Pydantic will raise an exception.

```python
from pydantic import WithJsonSchema
from infrasys import Component

class ComponentWithPintQuantity(Component):

    distance: Annotated[Distance, WithJsonSchema({"type": "string"})]

Component.model_json_schema()
```

**Notes**:
- `infrasys` includes some basic quantities in [infrasys.quantities](#quantity-api).
- Pint will automatically convert a list or list of lists of values into a `numpy.ndarray`.
infrasys will handle serialization/de-serialization of these types.


### Component Associations
The system tracks associations between components in order to optimize lookups.

For example, suppose a Generator class has a field for a Bus. It is trivial to find a generator's
bus. However, if you need to find all generators connected to specific bus, you would have to
traverse all generators in the system and check their bus values.

Every time you add a component to a system, `infrasys` inspects the component type for composed
components. It checks for directly connected components, such as `Generator.bus`, and lists of
components. (It does not inspect other composite data structures like dictionaries.)

`infrasys` stores these component associations in a SQLite table and so lookups are fast.

Here is how to complete this example:

```python
generators = system.list_parent_components(bus)
```

If you only want to find specific types, you can pass that type as well.
```python
generators = system.list_parent_components(bus, component_type=Generator)
```

**Warning**: There is one potentially problematic case.

Suppose that you have a system with generators and buses and then reassign the buses, as in
```
gen1.bus = other_bus
```

`infrasys` cannot detect such reassignments and so the component associations will be incorrect.
You must inform `infrasys` to rebuild its internal table.
```
system.rebuild_component_associations()
```
